import datetime
import logging

import oemof.solph as solph
import pandas as pd
from celery import shared_task
from django.utils import timezone

from ports.models import Grid
from ports.models import Result
from ports.models import ResultData
from ports.models import Scenario

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True)
def db_to_energysystem(self, scenario_id):
    scenario = Scenario.objects.get(id=scenario_id)
    Scenario.objects.filter(id=scenario_id).update(task_id=self.request.id)
    EPS = 1e-10
    now = timezone.now()
    start = datetime.datetime(now.year, now.month, now.day)
    end = start + datetime.timedelta(days=1)  # simulate for one day
    time_step = 15  # timestep in minutes
    time_index = pd.date_range(
        start=start,
        end=end,
        freq=datetime.timedelta(minutes=time_step),
        inclusive="both",
    )

    es = solph.EnergySystem(
        timeindex=time_index,
        infer_last_interval=False,
    )

    # setup grids
    # lookup table for busses: carrier -> area -> bus
    area_busses = {carrier: dict() for carrier in Grid.CarrierChoices}
    # lookup table for grid busses: grid ID -> bus
    grid_busses = dict()
    # inverse: bus -> grid
    bus_grids = dict()
    # lookup table for flows: flow label -> (source, target)
    # source/target may be component, grid or None
    flows = dict()
    for grid in scenario.grid_set.order_by("id"):
        label = f"{grid.internal_id}_{grid.carrier}"
        bus = solph.Bus(label=label)
        es.add(bus)
        for area in grid.areas.all():
            # assert each area only has at most one bus for each carrier
            if area.id in area_busses[grid.carrier]:
                raise Exception(
                    f"Area {area.name} ({area.internal_id}) has multiple {grid.carrier} grids"
                )
            area_busses[grid.carrier][area.id] = bus
            grid_busses[grid.id] = bus
            bus_grids[bus] = grid

    # connect grids
    for grid in scenario.grid_set.filter(connected_to__isnull=False):
        grid_bus = grid_busses[grid.id]
        connected_grid = grid.connected_to
        connected_bus = grid_busses[
            connected_grid.id
        ]  # will fail if there is no grid in connected area
        # create connection
        conn = solph.components.Link(
            label=f"Conn_{grid.internal_id}_{connected_grid.internal_id}_{grid.carrier}",
            inputs={
                grid_bus: solph.Flow(variable_costs=EPS),  # avoid cycles with minimal costs
                connected_bus: solph.Flow(variable_costs=EPS),
            },
            outputs={
                grid_bus: solph.Flow(variable_costs=EPS),
                connected_bus: solph.Flow(variable_costs=EPS),
            },
            conversion_factors={
                (connected_bus, grid_bus): int(grid.feed_in),
                (grid_bus, connected_bus): 1,
            },
        )
        es.add(conn)
        flows[(grid_bus, conn)] = (grid, grid.connected_to)
        flows[(conn, grid_bus)] = (grid.connected_to, grid)

    # create grid connections for root grids
    for grid in scenario.grid_set.filter(connected_to__isnull=True):
        grid_bus = grid_busses[grid.id]
        source = solph.components.Source(
            label=f"GC_{grid.internal_id}_source", outputs={grid_bus: solph.Flow()}
        )
        es.add(source)
        flows[(source, grid_bus)] = (None, grid)
        if grid.feed_in:
            sink = solph.components.Sink(
                label=f"GC_{grid.internal_id}_sink",
                inputs={
                    grid_bus: solph.Flow(
                        variable_costs=0,  # no associated cost or gain for feed-in
                    )
                },
            )
            es.add(sink)
            flows[(grid_bus, sink)] = (grid, None)

    #  add load templates (only electricity)
    for load in scenario.load_set.all():
        bus = area_busses[Grid.CarrierChoices.ELECTRICITY][load.area_id]
        sink = solph.components.Sink(
            label=load.internal_id,
            inputs={
                bus: solph.flows.Flow(
                    nominal_capacity=1,
                    fix=load.resample(start, end, time_step),
                )
            },
        )
        es.add(sink)
        flows[(bus, sink)] = (bus_grids[bus], load)

    # connect components to grids
    # will fail if grid is missing
    def add_converter(component, carrier_in, carrier_out, efficiency):
        # convenience function to add 1:1 converter
        bus_in = area_busses[carrier_in][component.area_id]
        bus_out = area_busses[carrier_out][component.area_id]
        converter = solph.components.Converter(
            label=component.internal_id,
            inputs={bus_in: solph.flows.Flow()},
            outputs={bus_out: solph.flows.Flow()},
            conversion_factors={bus_in: 1, bus_out: efficiency},
        )
        es.add(converter)
        flows[(bus_in, converter)] = (bus_grids[bus_in], component)
        flows[(converter, bus_out)] = (component, bus_grids[bus_out])

    for generator in scenario.generator_set.all():
        # fuel -> electricity
        add_converter(
            generator, generator.carrier, Grid.CarrierChoices.ELECTRICITY, generator.efficiency
        )
    for heating in scenario.heating_set.all():
        # energy source -> heat
        add_converter(heating, heating.carrier, Grid.CarrierChoices.HEAT, heating.efficiency)
    for fuel_cell in scenario.fuelcell_set.all():
        # H2 -> electricity
        add_converter(
            fuel_cell,
            Grid.CarrierChoices.H2,
            Grid.CarrierChoices.ELECTRICITY,
            fuel_cell.efficiency_thermal,
        )
    for electrolyzer in scenario.electrolyzer_set.all():
        # electricity -> H2
        add_converter(
            electrolyzer,
            Grid.CarrierChoices.ELECTRICITY,
            Grid.CarrierChoices.H2,
            electrolyzer.efficiency_thermal,
        )
    for heatpump in scenario.heatpump_set.all():
        # electricity -> heat
        add_converter(
            heatpump, Grid.CarrierChoices.ELECTRICITY, Grid.CarrierChoices.HEAT, heatpump.efficiency
        )
    for chp in scenario.chp_set.all():
        # fuel -> heat and electricity
        # there is a GenericCHP component, but it needs more information
        bus_in = area_busses[chp.carrier][chp.area_id]
        bus_heat = area_busses[Grid.CarrierChoices.HEAT][chp.area_id]
        bus_el = area_busses[Grid.CarrierChoices.ELECTRICITY][chp.area_id]
        converter = solph.components.Converter(
            label=chp.internal_id,
            inputs={bus_in: solph.flows.Flow()},
            outputs={bus_heat: solph.flows.Flow(), bus_el: solph.flows.Flow()},
            conversion_factors={
                bus_in: 1,
                bus_heat: chp.efficiency_thermal,
                bus_el: chp.efficiency,
            },
        )
        es.add(converter)
        flows[(bus_in, converter)] = (bus_grids[bus_in], chp)
        flows[(converter, bus_heat)] = (chp, bus_grids[bus_heat])
        flows[(converter, bus_el)] = (chp, bus_grids[bus_el])
    for pv in scenario.solar_set.all():
        # generates electricity
        if pv.profile is None:
            continue
        bus_out = area_busses[Grid.CarrierChoices.ELECTRICITY][pv.area_id]
        source = solph.components.Source(
            label=pv.internal_id,
            outputs={
                bus_out: solph.Flow(
                    nominal_capacity=1,
                    fix=pv.profile.resample(start, end, time_step),
                )
            },
        )
        es.add(source)
        flows[(source, bus_out)] = (pv, bus_grids[bus_out])
    for storage in scenario.storage_set.all():
        bus = area_busses[storage.carrier][storage.area_id]
        gs = solph.components.GenericStorage(
            label=storage.internal_id,
            nominal_capacity=storage.capacity_installed,
            balanced=True,  # SoC at beginning and end must be equal
            inflow_conversion_factor=storage.efficiency_store,
            outflow_conversion_factor=storage.efficiency_load,
            inputs={
                bus: solph.Flow(
                    nominal_capacity=storage.capacity_installed,
                    variable_costs=EPS,  # avoid cycle charging
                )
            },
            outputs={
                bus: solph.Flow(
                    nominal_capacity=storage.capacity_installed,
                )
            },
        )
        es.add(gs)
        flows[(bus, gs)] = (bus_grids[bus], storage)
        flows[(gs, bus)] = (storage, bus_grids[bus])

    try:
        model = solph.Model(es)
        model.solve()
        results = solph.Results(model)
        ok = True
    except Exception as e:
        logger.error(e)
        ok = False

    if not ok:
        return

    """
    # plot energy system as graph
    import matplotlib.pyplot as plt
    import networkx as nx
    from oemof.network.graph import create_nx_graph
    plt.figure()
    graph = create_nx_graph(es)
    nx.draw(graph, with_labels=True, font_size=8)
    plt.show()
    """

    """
    # plot grid power
    import matplotlib.pyplot as plt
    plt.figure()
    busses = grid_busses.values()
    for i, bus in enumerate(busses):
        plt.subplot(len(busses), 1, i+1)
        k0 = [k for k in results["flow"].keys() if k[0] == bus]
        k1 = [k for k in results["flow"].keys() if k[1] == bus]
        plt.plot(results["flow"][k0], label=results["flow"][k0].columns)
        plt.plot(-results["flow"][k1], label=results["flow"][k1].columns)
        plt.legend()
        plt.title(bus)
    plt.show()
    """

    # save result in DB
    result, _ = Result.objects.update_or_create(  # only one result per scenario (1:1)
        scenario=scenario,
        defaults={
            "started_at": now,  # set at start of function
            "finished_at": timezone.now(),
        },
    )
    # delete old result data
    result.resultdata_set.all().delete()

    # flows
    for bus, flow in results["flow"].items():
        try:
            from_node, to_node = flows[bus]
        except KeyError:
            continue
        from_node = from_node.internal_id if from_node is not None else None
        to_node = to_node.internal_id if to_node is not None else None
        ResultData.objects.create(
            result=result,
            from_node=from_node,
            to_node=to_node,
            attribute="flow",
            value=flow.to_list(),
        )
        print(from_node, to_node, sum(flow))

    # storage
    for storage in scenario.storage_set.all():
        ResultData.objects.create(
            result=result,
            from_node=storage.internal_id,
            to_node=None,
            attribute="storage",
            value=results["storage_content"][storage.internal_id].to_list(),
        )

    # invest
    ResultData.objects.create(
        result=result,
        from_node=None,
        to_node=None,
        attribute="costs",
        value=[float(results["objective"])],
    )


def plot_result(scenario_id):
    import matplotlib.pyplot as plt

    scenario = Scenario.objects.get(id=scenario_id)
    if scenario.result is None:
        return
    data = scenario.result.resultdata_set.all()
    grids = scenario.grid_set.order_by("id")
    plt.figure()
    plt.title(data.get(attribute="costs").value[0])
    for i, grid in enumerate(grids):
        from_grid = dict()
        to_grid = dict()
        for d in data.filter(from_node=grid.internal_id, attribute="flow"):
            from_grid[d.to_node] = d.value
        for d in data.filter(to_node=grid.internal_id, attribute="flow"):
            to_grid[d.from_node] = d.value

        plt.subplot(grids.count(), 1, i + 1)
        plt.plot([x for x in map(sum, zip(*from_grid.values(), strict=False))])
        plt.plot([x for x in map(sum, zip(*to_grid.values(), strict=False))])
        plt.title(grid.name)
    plt.show()
