import csv
import datetime
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from collections.abc import Iterable

import pandas as pd
from django.apps.registry import apps
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.db import models
from django.contrib.gis.geos import Polygon
from django.db.models import Q
from guardian.shortcuts import assign_perm
from guardian.utils import get_group_obj_perms_model

from .db_deepcopy import deepcopy
from .models import Area
from .models import Project
from .models import Result
from .models import Scenario
from .models import ScenarioItem
from .models import SolarRadiation

logger = logging.getLogger(__name__)


def process_file_to_solar_radiation(file):
    """Import timeseries data to SolarRadiation"""

    with open(file) as f:
        # find the columns with time and power values
        reader = csv.DictReader(f)
        time_col = None
        val_col = None
        for col in reader.fieldnames:
            if "time" in col.lower():
                time_col = col
            elif "power" in col.lower():
                val_col = col
        if not (time_col and val_col):
            raise ValueError("No column found for 'time' or 'power'")

        # reading the file changed the stream position.
        # reset it so we can start from the top
        f.seek(0)
        values = []
        times = []
        for row in csv.DictReader(f):
            try:
                values.append(float(row[val_col]))
                times.append(row[time_col])
            except ValueError as e:
                raise ValueError(
                    f"{file} has a value of {row[val_col]} which is not numeric"
                ) from e

        # let pandas handle the datetimes since local german times need handling of
        # spring and autumn timezone changes.
        timestamps = pd.to_datetime(values)
        timestamps = timestamps.tz_localize(
            "Europe/Berlin",
            ambiguous="infer",
            nonexistent="raise",
        )
        timestamps = timestamps.tz_convert("UTC")
        timestep = timestamps[1] - timestamps[0]
        start_time = timestamps[0]
        for t2, t1 in zip(timestamps[1:], timestamps[:-1], strict=False):
            # sometimes there are microseconds changes which should not lead to a warning
            if (t2 - t1) - timestep > datetime.timedelta(milliseconds=1):
                logger.warning(
                    f"Timesteps are not consistent in file {file} at {t1}\n({t2 - t1 - timestep})"
                )
                break
        SolarRadiation.all_objects.create(
            name=file.stem,
            timeseries=values,
            timestep=timestep.total_seconds() / 60,
            timeseries_start=start_time,
        )

    pass


def process_geojson_dict_to_scenarios(regions: dict, buildings: dict, user: User):
    """Import Geojson to scenarios
    Buidlings are matched into regions via port_name on a region and inland_port on the building.
    Assumed EPSG:4326

    """

    def rings_from_feature(feature):
        """Extract the polygon rings from the feature
        For a multipolygon only the first polygon is used
        """
        geometry_type = feature["geometry"].get("type", "").lower()
        if geometry_type == "multipolygon":
            # rings of first polygon
            rings = feature["geometry"]["coordinates"][0]
        elif geometry_type == "polygon":
            rings = feature["geometry"]["coordinates"]
        else:
            raise NotImplementedError("Unknown geometry type: " + geometry_type)
        return rings

    scenario_lut = {}
    for feature in regions["features"]:
        if not feature.get("geometry"):
            continue

        rings = rings_from_feature(feature)
        # Split ring and holes
        geom = Polygon(rings[0], *rings[1:], srid=4326)
        port_name = feature.get("properties", {}).get("port_name")
        if port_name is None:
            continue
        if port_name in scenario_lut:
            logger.warning("%s was processed already and is skipped.", port_name)
            continue
        logger.info(port_name)
        scenario = Scenario(name=port_name, geom=geom, manager=user)
        scenario_lut[port_name] = scenario

    counts = defaultdict(int)
    areas = []
    missing_scenarios = []
    for feature in buildings["features"]:
        if not feature.get("geometry"):
            continue
        rings = rings_from_feature(feature)
        # Split ring and holes
        geom = Polygon(rings[0], *rings[1:], srid=4326)
        port_name = feature.get("properties", {}).get("inland_port")
        if port_name is None:
            continue
        try:
            found_scenario = scenario_lut[port_name]
        except KeyError:
            found_scenario = Scenario(name=port_name, manager=user)
            scenario_lut[port_name] = found_scenario
            missing_scenarios.append(found_scenario)
        areas.append(
            Area(
                name=f"Automatische Gebäudefläche {counts[port_name]}",
                area_type=Area.AreaTypeChoices.BUILDING,
                manager=user,
                scenario=found_scenario,
                geom=geom,
            )
        )
        counts[port_name] += 1
    if missing_scenarios:
        logger.warn(
            "Some buildings had inland_port values not found in the regions file. "
            "%s missing scenarios were created.",
            len(missing_scenarios),
        )
    Scenario.objects.bulk_create(scenario_lut.values())
    Area.objects.bulk_create(areas)
    return list(scenario_lut.values()), areas


def duplicate_project(project: Project):
    # deepcopy may reassign the instance pk in memory, so resolve the old group first
    old_group = Group.objects.filter(name=project.group_name()).first()
    new_project, _ = deepcopy(project, exclude_models={User, Result, SolarRadiation}, max_depth=2)

    # Authorization is not directly linked through foreign keys but through foreign_objects
    # Therefore the group is not deepcopied. Maybe make the group part of the object?
    # This would break down if multiple groups per project exist
    group = Group.objects.create(name=new_project.group_name())
    assign_perm("view", group, new_project)
    assign_perm("details", group, new_project)
    group.user_set.set(old_group.user_set.all())

    return new_project


def transfer_group_permission(scenario, new_scenario):
    """Add all area permissions of the scenario to the new_scenario.
    The group stays the same, since it is expected to be a scenario copy inside the same project.
    """
    old_areas = Area.objects.filter(scenario=scenario)
    new_areas = Area.objects.filter(scenario=new_scenario)
    if not len(old_areas) == len(new_areas):
        raise Exception("Transfering permissions failed due to uneven count of Areas")
    old_d = {x.id: x for x in old_areas}
    new_d = {x.internal_id: x for x in new_areas}

    group = Group.objects.get(name=scenario.project.group_name())
    content_type = ContentType.objects.get_for_model(Area)
    ids = [a.id for a in old_areas]
    GroupObjectPermission = get_group_obj_perms_model()
    group_perms = GroupObjectPermission.objects.filter(
        group=group, content_type=content_type, object_pk__in=ids
    )
    new_group_perms = []
    for gp in group_perms:
        gp.id = None
        # Lookup the original instance of the group permission. Use the interal_id
        # to lookup the copied id, since all copies share the same internal_id
        gp.object_pk = new_d[old_d[int(gp.object_pk)].internal_id].id
        new_group_perms.append(gp)
    GroupObjectPermission.objects.bulk_create(new_group_perms)


def duplicate_scenario(scenario: Scenario, user: User, suffix=" (Dupliziert)"):
    """Duplicate scenario without transferring group permissions.
    Group permissions should not be transferred in cases of scenario duplication for a new project
    The previous group should not be authorized to view a scenario or its items from a different project
    """
    new_scenario, _ = deepcopy(
        scenario, exclude_models={User, Project, Result, SolarRadiation}, max_depth=1
    )
    new_scenario.name += suffix
    new_scenario.manager = user
    new_scenario.save()
    return new_scenario


def duplicate_scenario_with_permissions(scenario: Scenario, user: User, suffix=" (Dupliziert)"):
    new_scenario = duplicate_scenario(scenario, user, suffix)
    # Managers have already been copied, now transfer Project group permissions.
    transfer_group_permission(scenario, new_scenario)
    return new_scenario


def prefetch_projects_users(projects: Iterable[Project]) -> None:
    """Annotate projects with dictionary of user with some level of permission through a group
    Projects can access the users through project.users[permission_code]
    """
    id_to_project = {p.pk: p for p in projects}
    GroupObjectPermission = get_group_obj_perms_model()
    project_ct = ContentType.objects.get_for_model(Project)
    group_perms = (
        GroupObjectPermission.objects.filter(content_type=project_ct, object_pk__in=id_to_project)
        .select_related("permission")
        .prefetch_related("group__user_set")
    )
    for p in projects:
        p._users_cache = {}
    for perm in group_perms:
        project = id_to_project[int(perm.object_pk)]
        project._users_cache[perm.permission.codename] = perm.group.user_set.all()


def get_user_projects(user: User):
    if user.is_staff:
        return Project.objects.all().prefetch_related("scenario_set")
    # All projects a group of the user has the "view" object permission on
    GroupObjectPermission = get_group_obj_perms_model()
    project_ct = ContentType.objects.get_for_model(Project)
    project_ids = GroupObjectPermission.objects.filter(
        content_type=project_ct,
        permission__codename="view",
        group__in=user.groups.all(),
    ).values_list("object_pk", flat=True)
    # object_pk is a CharField on guardian's generic permission model
    project_ids = [int(pk) for pk in project_ids]
    return Project.objects.filter(Q(manager=user) | Q(id__in=project_ids)).prefetch_related(
        "scenario_set"
    )


def get_template_scenarios(user: User):
    if user.is_superuser:
        return Scenario.objects.all()
    template_user = User.objects.get(username=settings.DATA_USER)
    return Scenario.objects.filter(manager__in=[user, template_user])


def get_template_model(model):
    template_model_name = model.__qualname__ + "Template"
    return apps.get_app_config("ports").get_model(template_model_name)


_cache = {}


def throttle(func, cooldown, unique_id) -> None:
    """Call func if the cooldown since the last throttled call with the same unique_id passed.

    Is supposed to be used sparingly, with no dynamic unique_id generation, since memory is never
    cleaned
    """
    last_call = _cache.get(unique_id)
    now = time.time()
    if last_call is None or now - last_call > cooldown:
        func()
        _cache[unique_id] = time.time()


def get_related_items(
    instances: list[models.Model],
    excluded_models: None | set = None,
    field_filter: Callable | None = None,
    max_depth=None,
    _depth=0,
    _gathered: None | set = None,
    _prev_field=None,
) -> list[models.Model]:
    """Get all related items.

    Instance with a type inside excluded models are not returned.
    Related instances via field where the callable field_filter(field) returns True are ignored.
    Search depth/ steps of relations can be limited to max_depth.
    Queries are fastest when starting from a Parent Node

    Iterate each model. Query related instances for all new objects in a second loop.
    This breadth first approach is faster than going dfs since outer queries are more efficient.

    Forward ForeignKey lookups are skipped when the remote_field was used to get the object.
    e.g. First Query on the scenario instance which has a (auto_created and not concrete) field called "area"
    Area.objects.filter(scenario_id__in=[1])
    --> returns Area 1,2,3
    then later these areas are searched with their (not auto_created and concrete) field "scenario"
    since those are the same fields the second lookup
    (Scenario.objects.filter(area_id__in=[1,2,3]))
    can be skipped

    The other way around this should not happen, e.g.
    get_related_items([Area.objects.get(id=1)])
    returns Scenario with id=1, fetching the related objects on Scenario should fetch the Siblings of Area with id=1
    e.g.
    Area with id in (1,2,3)
    In this case the field which was used to find the scenario was the field scenario on Area which is a not auto_created (we defined it explicitly) and concrete (its a real db column)

    This function with some custom filters can be used to determine which instances are delete via Cascade
    see: get_items_to_be_deleted

    Optimization to join fields with same related model is not used.
    """
    excluded_models = excluded_models or set()
    field_filter = field_filter or (lambda x: False)
    if not instances or (max_depth is not None and _depth >= max_depth):
        return []

    if _depth == 0:
        itype = type(instances[0])
        for i in instances[1:]:
            assert isinstance(i, itype), "Instances must have the same type"
    gathered: set = _gathered or set(instances)
    new = []
    for field in instances[0]._meta.get_fields():
        if not field.is_relation or not field.remote_field:
            continue
        if field.related_model in excluded_models:
            continue
        if field_filter(field) or (
            _prev_field
            and _prev_field.auto_created
            and not _prev_field.concrete
            and field == _prev_field.remote_field
        ):
            continue
        f = {f"{field.remote_field.name}__in": {x.pk for x in instances}}
        # Only load the shared ScenarioItem columns, skip model specific ones (geom, efficiency, ...)
        if issubclass(field.related_model, ScenarioItem):
            only_fields = [sf.name for sf in ScenarioItem._meta.concrete_fields]
        else:
            only_fields = ["pk"]
        found_objs = field.related_model._base_manager.filter(**f).only(*only_fields).distinct()
        new_objs = set(found_objs).difference(gathered)
        new.append((new_objs, field))
        gathered.update(new_objs)
    for new_objs, prev_field in new:
        get_related_items(
            list(new_objs),
            excluded_models,
            field_filter,
            max_depth=max_depth,
            _depth=_depth + 1,
            _gathered=gathered,
            _prev_field=prev_field,
        )
    return list(gathered)


def get_items_to_be_deleted(instances: list[models.Model], *args, **kwargs) -> list[models.Model]:
    custom_filter = kwargs.pop("field_filter", lambda x: False)

    def filter_no_cascade(field):
        on_delete = getattr(field, "on_delete", None)
        if on_delete != models.CASCADE:
            return True
        return False or custom_filter(field)

    return get_related_items(instances, *args, **kwargs, field_filter=filter_no_cascade)
