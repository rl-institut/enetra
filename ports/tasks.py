import datetime

import oemof.solph as solph
import pandas as pd

from ports.models import Grid
from ports.models import Scenario


def db_to_energysystem(scenario: Scenario):
    EPS = 1e-10
    now = datetime.datetime.now()
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
    # lookup table for grid busses: grid -> bus
    grid_busses = dict()
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

    # create grid connections for root grids
    for grid in scenario.grid_set.filter(connected_to__isnull=True):
        grid_bus = grid_busses[grid.id]
        es.add(
            solph.components.Source(
                label=f"GC_{grid.internal_id}_source", outputs={grid_bus: solph.Flow()}
            )
        )
        if grid.feed_in:
            es.add(
                solph.components.Sink(
                    label=f"GC_{grid.internal_id}_sink",
                    inputs={
                        grid_bus: solph.Flow(
                            variable_costs=0,  # no associated cost or gain for feed-in
                        )
                    },
                )
            )

    #  add load templates (only electricity)
    for load in scenario.load_set.all():
        bus = area_busses[Grid.CarrierChoices.ELECTRICITY][load.area_id]
        es.add(
            solph.components.Sink(
                label=load.internal_id,
                inputs={
                    bus: solph.flows.Flow(
                        nominal_capacity=1,
                        fix=load.resample(start, end, time_step),
                    )
                },
            )
        )

    # connect components to grids
    # will fail if grid is missing
    for generator in scenario.generator_set.all():
        # fuel -> electricity
        bus_in = area_busses[generator.carrier][generator.area_id]
        bus_out = area_busses[Grid.CarrierChoices.ELECTRICITY][generator.area_id]
        es.add(
            solph.components.Converter(
                label=generator.internal_id,
                inputs={bus_in: solph.flows.Flow()},
                outputs={bus_out: solph.flows.Flow()},
                conversion_factors={bus_in: 1, bus_out: generator.efficiency},
            )
        )
    for heating in scenario.heating_set.all():
        # energy source -> heat
        bus_in = area_busses[heating.carrier][heating.area_id]
        bus_out = area_busses[Grid.CarrierChoices.HEAT][heating.area_id]
        es.add(
            solph.components.Converter(
                label=heating.internal_id,
                inputs={bus_in: solph.flows.Flow()},
                outputs={bus_out: solph.flows.Flow()},
                conversion_factors={bus_in: 1, bus_out: heating.efficiency},
            )
        )
    for chp in scenario.chp_set.all():
        # fuel -> heat and electricity
        # there is a GenericCHP component, but it needs more information
        bus_in = area_busses[chp.carrier][chp.area_id]
        bus_heat = area_busses[Grid.CarrierChoices.HEAT][chp.area_id]
        bus_el = area_busses[Grid.CarrierChoices.ELECTRICITY][chp.area_id]
        es.add(
            solph.components.Converter(
                label=chp.internal_id,
                inputs={bus_in: solph.flows.Flow()},
                outputs={bus_heat: solph.flows.Flow(), bus_el: solph.flows.Flow()},
                conversion_factors={
                    bus_in: 1,
                    bus_heat: chp.efficiency_thermal,
                    bus_el: chp.efficiency,
                },
            )
        )
    for fuel_cell in scenario.fuelcell_set.all():
        # H2 -> electricity
        bus_in = area_busses[Grid.CarrierChoices.H2][fuel_cell.area_id]
        bus_out = area_busses[Grid.CarrierChoices.ELECTRICITY][fuel_cell.area_id]
        es.add(
            solph.components.Converter(
                label=fuel_cell.internal_id,
                inputs={bus_in: solph.flows.Flow()},
                outputs={bus_out: solph.flows.Flow()},
                conversion_factors={
                    bus_in: 1,
                    bus_out: fuel_cell.efficiency_thermal,
                },
            )
        )
    for electrolyzer in scenario.electrolyzer_set.all():
        # electricity -> H2
        bus_in = area_busses[Grid.CarrierChoices.ELECTRICITY][electrolyzer.area_id]
        bus_out = area_busses[Grid.CarrierChoices.H2][electrolyzer.area_id]
        es.add(
            solph.components.Converter(
                label=electrolyzer.internal_id,
                inputs={bus_in: solph.flows.Flow()},
                outputs={bus_out: solph.flows.Flow()},
                conversion_factors={
                    bus_in: 1,
                    bus_out: electrolyzer.efficiency_thermal,
                },
            )
        )
    for heatpump in scenario.heatpump_set.all():
        # electricity -> heat
        bus_in = area_busses[Grid.CarrierChoices.ELECTRICITY][heatpump.area_id]
        bus_out = area_busses[Grid.CarrierChoices.HEAT][heatpump.area_id]
        es.add(
            solph.components.Converter(
                label=heatpump.internal_id,
                inputs={bus_in: solph.flows.Flow()},
                outputs={bus_out: solph.flows.Flow()},
                conversion_factors={
                    bus_in: 1,
                    bus_out: heatpump.efficiency,
                },
            )
        )
    for pv in scenario.solar_set.all():
        # generates electricity
        if pv.profile is not None:
            bus_out = area_busses[Grid.CarrierChoices.ELECTRICITY][pv.area_id]
            es.add(
                solph.components.Source(
                    label=pv.internal_id,
                    outputs={
                        bus_out: solph.Flow(
                            nominal_capacity=1,
                            fix=pv.profile.resample(start, end, time_step),
                        )
                    },
                )
            )
    for storage in scenario.storage_set.all():
        bus = area_busses[storage.carrier][storage.area_id]
        es.add(
            solph.components.GenericStorage(
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
        )

    model = solph.Model(es)
    model.solve()
    results = solph.Results(model)
    # dummy to use results if following plotting is removed
    print(results["Solver"][0]["Status"])
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
