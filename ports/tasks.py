import datetime
import logging
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from uuid import uuid4

import oemof.solph as solph
import pandas as pd
from celery import shared_task
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model
from django.db.models import TextChoices
from django.utils import timezone

from core.models import Task
from ports.models import publish_change
from ports.util import resample as util_resample

from .models import Grid
from .models import Result
from .models import ResultData
from .models import Scenario

logger = logging.getLogger(__name__)


def start_simulation(scenario):
    task, created = scenario.tasks.update_or_create(
        type=Task.Type.RUN_SIMULATION, defaults={"celery_task_id": str(uuid4())}
    )
    if not created:
        task.reset()
    celery_task = oemof_task.apply_async(args=[scenario.id], task_id=task.celery_task_id)
    return celery_task


@shared_task(bind=True, ignore_result=True)
def oemof_task(self, scenario_id, **kwargs) -> None:
    task = None
    try:
        scenario = Scenario.objects.get(id=scenario_id)
        task: Task = scenario.tasks.filter(type=Task.Type.RUN_SIMULATION).first()
        if task:
            task.total_work = 4
            task.message = "Aufbau des Energiesystems"
            task.status = Task.Status.RUNNING
            task.progress = 1
            task.save()
            publish_change(scenario.id)

        started_at = timezone.now()
        es, flows, errors = db_to_energysystem(scenario, started_at, **kwargs)
        if errors:
            err_text = "\n".join(f"{e.item!r}: {e.type.label} {e.details}" for e in errors)
            raise ImproperlyConfigured(
                f"Can not start simulation when energysystem has errors: {err_text}"
            )

        if task:
            task.message = "Simulation des Energiesystems"
            task.progress += 1
            task.save()
            publish_change(scenario.id)

        model = solve_energysystem(es)
        if model is not None:
            if task:
                task.message = "Speicherung der Ergebnisse"
                task.progress += 1
                task.save()
                publish_change(scenario.id)

            store_results(scenario, model, flows, started_at)
            # plot_result(scenario_id)

        if task:
            if model:
                task.message = "Simulation erfolgreich beendet"
                task.set_success()
            else:
                task.message = "Simulation fehlgeschlagen"
                task.set_failed()
            publish_change(scenario.id)
    except:
        if task:
            task.set_failed()
        raise


def _model_error_choices() -> list[tuple[str, tuple[str, str]]]:
    """(member name, (value, label)) for ModelErrorType.

    Bus related errors are created per grid carrier, so the type alone identifies the group and
    its label can be shown directly, e.g. "Fehlender Netzanschluss für Wärme".
    """
    choices = [
        ("MISSING_PROFILE", ("missing profile", "Zeitreihe fehlt oder ist ungültig")),
        ("MISSING_VALUE", ("missing value", "Pflichtwerte fehlen")),
        (
            "INVALID_CONNECTION_CARRIER",
            ("invalid connection carrier", "Verbunden mit einem Netz anderen Energieträgers"),
        ),
        (
            "INVALID_CONNECTION_SCENARIO",
            ("invalid connection scenario", "Verbunden mit einem Netz aus einem anderen Szenario"),
        ),
    ]
    for carrier in Grid.CarrierChoices:
        choices += [
            (
                f"MISSING_BUS_{carrier.name}",
                (f"missing bus {carrier.value}", f"Fehlender Netzanschluss für {carrier.label}"),
            ),
            (
                f"MULTIPLE_BUSSES_{carrier.name}",
                (f"multiple busses {carrier.value}", f"Mehrere Netze für {carrier.label}"),
            ),
        ]
    return choices


# Members are generated (see _model_error_choices), static type checkers don't know them.
ModelErrorType = TextChoices("ModelErrorType", _model_error_choices())


def missing_bus_type(carrier: str) -> "ModelErrorType":
    return ModelErrorType[f"MISSING_BUS_{Grid.CarrierChoices(carrier).name}"]


def multiple_busses_type(carrier: str) -> "ModelErrorType":
    return ModelErrorType[f"MULTIPLE_BUSSES_{Grid.CarrierChoices(carrier).name}"]


@dataclass(frozen=True)
class ModelError:
    """A misconfiguration found while building the energy system.

    The item is skipped (not added to the energy system). Only `details` varies per error type.
    """

    item: Model
    type: ModelErrorType
    details: dict[str, Any] = field(default_factory=dict)


def db_to_energysystem(
    scenario, started_at, **kwargs
) -> tuple[solph.EnergySystem, dict, list[ModelError]]:
    EPS = kwargs.get("eps", settings.OEMOF_EPS)
    days = kwargs.get("days", settings.OEMOF_DAYS)  # how many days to simulate
    time_step = kwargs.get("ts", settings.OEMOF_TS)  # timestep in minutes
    start = datetime.datetime(started_at.year, started_at.month, started_at.day)
    end = start + datetime.timedelta(days=days)
    errors: list[ModelError] = []
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
    area_busses: dict[str, dict[int, solph.Bus]] = {
        carrier: dict() for carrier in Grid.CarrierChoices
    }
    # lookup table for grid busses: grid ID -> bus
    grid_busses: dict[int, solph.Bus] = dict()
    # inverse: bus -> grid
    bus_grids: dict[solph.Bus, Grid] = dict()
    # lookup table for flows: flow label -> (source, target)
    # source/target may be component, grid or None
    flows = dict()

    def report(item, error_type: ModelErrorType, **details) -> None:
        errors.append(ModelError(item=item, type=error_type, details=details))

    def report_missing_values(item, field_names: list[str]) -> None:
        """Merge into an existing MISSING_VALUE error of item, so each item is reported once."""
        for error in errors:
            if error.item is item and error.type == ModelErrorType.MISSING_VALUE:
                error.details["fields"] += [
                    f for f in field_names if f not in error.details["fields"]
                ]
                return
        report(item, ModelErrorType.MISSING_VALUE, fields=list(field_names))

    def bus_or_report_err(carrier, component) -> solph.Bus | None:
        """Bus of the component's area for carrier.

        Reports MISSING_VALUE if the carrier is empty or unknown (e.g. "" from a form without a
        selection), MISSING_BUS_<CARRIER> if the area has no grid for it.
        """
        if carrier not in Grid.CarrierChoices.values:
            report_missing_values(component, ["carrier"])
            return None
        bus = area_busses[carrier].get(component.area_id)
        if bus is None:
            report(component, missing_bus_type(carrier), carrier=carrier)
        return bus

    def values_or_err(component, *field_names) -> bool:
        """True if all fields are set. Reports MISSING_VALUE with all missing fields otherwise.

        Empty strings count as missing, CharFields with choices store "" when nothing is selected.
        """
        missing = [name for name in field_names if getattr(component, name) in (None, "")]
        if missing:
            report_missing_values(component, missing)
        return not missing

    # setup grids
    grids = list(scenario.grid_set.select_related("connected_to").prefetch_related("areas"))
    for grid in grids:
        label = f"{grid.internal_id}_{grid.carrier}"
        bus = solph.Bus(label=label)
        es.add(bus)
        grid_busses[grid.id] = bus
        bus_grids[bus] = grid
        for area in grid.areas.all():
            if area.id in area_busses[grid.carrier]:
                # only the first grid of a carrier is used for the area
                report(
                    area,
                    multiple_busses_type(grid.carrier),
                    carrier=grid.carrier,
                    grids=[bus_grids[area_busses[grid.carrier][area.id]], grid],
                )
                continue
            area_busses[grid.carrier][area.id] = bus
    # create grid connections
    for grid in grids:
        if grid.connected_to_id is None:
            continue
        grid_bus = grid_busses[grid.id]

        # Validate that the connected grids are found inside the scenario and that connected grids have the same carrier
        # connect grids (i.e. their busses) via Link
        connected_bus = grid_busses.get(grid.connected_to_id)
        if connected_bus is None:
            report(grid, ModelErrorType.INVALID_CONNECTION_SCENARIO)
            continue
        connected_grid = bus_grids[connected_bus]
        if connected_grid.carrier != grid.carrier:
            report(
                grid,
                ModelErrorType.INVALID_CONNECTION_CARRIER,
                connected_to=connected_grid,
            )
            continue
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
        flows[(grid_bus, conn)] = (grid, connected_grid)
        flows[(conn, grid_bus)] = (connected_grid, grid)
        # other half of link flows are ignored because output=input
        # they are redundant for connection between grids
    # create grid connections for root grids
    for grid in grids:
        if grid.connected_to_id is not None:
            continue
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

    #  add load templates
    for load in scenario.load_set.select_related("timeseries", "area"):
        bus = bus_or_report_err(load.carrier, load)
        if bus is None:
            continue
        try:
            fix = load.resample_and_factor(start, end, time_step)
        except (KeyError, TypeError, ValueError) as e:
            report(load, ModelErrorType.MISSING_PROFILE, profile=load.timeseries, reason=str(e))
            continue

        sink = solph.components.Sink(
            label=load.internal_id,
            inputs={
                bus: solph.flows.Flow(
                    nominal_capacity=1,
                    fix=fix,
                )
            },
        )
        es.add(sink)
        flows[(bus, sink)] = (bus_grids[bus], load)

    def add_converter(component, carrier_in, carrier_out, efficiency):
        # convenience function to add 1:1 converter
        # look up both busses first, so both missing busses are reported
        bus_in = bus_or_report_err(carrier_in, component)
        bus_out = bus_or_report_err(carrier_out, component)
        if bus_in is None or bus_out is None or efficiency is None:
            return
        converter = solph.components.Converter(
            label=component.internal_id,
            inputs={bus_in: solph.flows.Flow()},
            outputs={bus_out: solph.flows.Flow()},
            conversion_factors={bus_in: 1, bus_out: efficiency},
        )
        es.add(converter)
        flows[(bus_in, converter)] = (bus_grids[bus_in], component)
        flows[(converter, bus_out)] = (component, bus_grids[bus_out])

    for generator in scenario.generator_set.select_related("area"):
        if values_or_err(generator, "efficiency"):
            # fuel -> electricity
            add_converter(
                generator, generator.carrier, Grid.CarrierChoices.ELECTRICITY, generator.efficiency
            )
    for heating in scenario.heating_set.select_related("area"):
        if values_or_err(heating, "efficiency"):
            # energy source -> heat
            add_converter(heating, heating.carrier, Grid.CarrierChoices.HEAT, heating.efficiency)
    for fuel_cell in scenario.fuelcell_set.select_related("area"):
        if values_or_err(fuel_cell, "efficiency"):
            # H2 -> electricity
            add_converter(
                fuel_cell,
                Grid.CarrierChoices.H2,
                Grid.CarrierChoices.ELECTRICITY,
                fuel_cell.efficiency,
            )
    for electrolyzer in scenario.electrolyzer_set.select_related("area"):
        if values_or_err(electrolyzer, "efficiency"):
            # electricity -> H2
            add_converter(
                electrolyzer,
                Grid.CarrierChoices.ELECTRICITY,
                Grid.CarrierChoices.H2,
                electrolyzer.efficiency,
            )
    for heatpump in scenario.heatpump_set.select_related("area"):
        if values_or_err(heatpump, "efficiency"):
            # electricity -> heat
            add_converter(
                heatpump,
                Grid.CarrierChoices.ELECTRICITY,
                Grid.CarrierChoices.HEAT,
                heatpump.efficiency,
            )

    for chp in scenario.chp_set.select_related("area"):
        # fuel -> heat and electricity
        # there is a GenericCHP component, but it needs more information
        bus_in = bus_or_report_err(chp.carrier, chp)
        bus_heat = bus_or_report_err(Grid.CarrierChoices.HEAT, chp)
        bus_el = bus_or_report_err(Grid.CarrierChoices.ELECTRICITY, chp)
        values_ok = values_or_err(chp, "efficiency", "power_to_heat_ratio")
        if not values_ok or bus_in is None or bus_heat is None or bus_el is None:
            continue
        converter = solph.components.Converter(
            label=chp.internal_id,
            inputs={bus_in: solph.flows.Flow()},
            outputs={bus_heat: solph.flows.Flow(), bus_el: solph.flows.Flow()},
            conversion_factors={
                bus_in: 1,
                bus_heat: chp.efficiency,
                bus_el: chp.efficiency * chp.power_to_heat_ratio,
            },
        )
        es.add(converter)
        flows[(bus_in, converter)] = (bus_grids[bus_in], chp)
        flows[(converter, bus_heat)] = (chp, bus_grids[bus_heat])
        flows[(converter, bus_el)] = (chp, bus_grids[bus_el])

    for pv in scenario.solar_set.select_related("profile", "area"):
        # generates electricity: installed peak power (kW) * radiation / 1000 W/m² (STC)
        bus_out = bus_or_report_err(Grid.CarrierChoices.ELECTRICITY, pv)
        values_ok = values_or_err(pv, "power_kw")
        if pv.profile is None:
            report(pv, ModelErrorType.MISSING_PROFILE, profile=None)
            continue
        if not values_ok or bus_out is None:
            continue
        try:
            fix = (
                util_resample(pv.profile.timeseries, pv.profile.timestep, start, end, time_step)
                / 1000
            )
        except (TypeError, ValueError) as e:
            report(pv, ModelErrorType.MISSING_PROFILE, profile=pv.profile, reason=str(e))
            continue

        source = solph.components.Source(
            label=pv.internal_id,
            outputs={
                bus_out: solph.Flow(
                    nominal_capacity=pv.power_kw,
                    fix=fix,
                )
            },
        )
        es.add(source)
        flows[(source, bus_out)] = (pv, bus_grids[bus_out])

    for storage in scenario.storage_set.select_related("area"):
        values_ok = values_or_err(
            storage, "carrier", "capacity_installed", "efficiency_store", "efficiency_load"
        )
        if not values_ok:
            continue
        bus = bus_or_report_err(storage.carrier, storage)
        if bus is None:
            continue
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

    return es, flows, errors


def solve_energysystem(es):
    try:
        model = solph.Model(es)
        model.solve()
        return model
    except Exception as e:
        logger.error(e)
    return None


def store_results(scenario, model, flows, started_at):
    # save result in DB
    results = solph.Results(model)
    result, _ = Result.objects.update_or_create(  # only one result per scenario (1:1)
        scenario=scenario,
        defaults={
            "started_at": started_at,
            "finished_at": timezone.now(),
        },
    )
    # delete old result data
    result.resultdata_set.all().delete()
    result_data = list()

    # time
    result_data.append(
        ResultData(
            result=result,
            from_node=None,
            to_node=None,
            attribute="timeindex",
            value=[t.timestamp() for t in model.es.timeindex],
        )
    )

    # invest
    result_data.append(
        ResultData(
            result=result,
            from_node=None,
            to_node=None,
            attribute="costs",
            value=[float(results["objective"])],
        )
    )

    # flows (might not exist for empty scenario)
    for bus, flow in results.get("flow", dict()).items():
        try:
            from_node, to_node = flows[bus]
        except KeyError:
            # skip some link flows between grids
            continue
        from_node = from_node.internal_id if from_node is not None else None
        to_node = to_node.internal_id if to_node is not None else None
        result_data.append(
            ResultData(
                result=result,
                from_node=from_node,
                to_node=to_node,
                attribute="flow",
                value=flow.to_list(),
            )
        )
        logger.debug(f"{from_node} -> {to_node}: {sum(flow)}")

    # storage
    for storage in scenario.storage_set.all():
        result_data.append(
            ResultData(
                result=result,
                from_node=storage.internal_id,
                to_node=None,
                attribute="storage",
                value=results["storage_content"][storage.internal_id].to_list(),
            )
        )

    ResultData.objects.bulk_create(result_data)


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
