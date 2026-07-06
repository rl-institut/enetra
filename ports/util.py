import json
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from uuid import uuid4

from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.gdal import DataSource
from django.core.files.uploadedfile import TemporaryUploadedFile
from django.db.models import QuerySet
from django.db.transaction import atomic
from guardian.shortcuts import assign_perm
from guardian.utils import get_group_obj_perms_model
from shapely import STRtree
from shapely import wkt

from .db_deepcopy import deepcopy
from .models import Area
from .models import Project
from .models import Scenario


@contextmanager
def _str_as_datasource(data: str):
    """Write a GeoJSON dict to a temp file and yield a GDAL DataSource.
    Keeps the file alive for the lifetime of the DataSource.
    """
    with tempfile.NamedTemporaryFile(suffix=".geojson", mode="w") as tmp:
        tmp.write(data)
        tmp.flush()
        yield DataSource(tmp.name)


def process_datasources_to_scenarios(ds_regions: DataSource, ds_buildings: DataSource, user: User):
    """Create Scenarios and Areas from GDAL DataSource objects.

    If buildings are not found within a Scenario region but have an "inland_port"
    attribute, an inland port Scenario with that name will be generated.
    """

    def featureValue(feature, field):
        """Depending on datasource a feature field can be a
        django.contrib.gis.gdal.field.OFTReal object
        or the plain value.
        For the first we have to explicity ask for the value
        """
        if field not in feature.fields:
            return None
        try:
            return feature.get(field).value
        except AttributeError:
            return feature.get(field)

    scenarios = []
    for feature in ds_regions[0]:
        if feature.geom.geom_name == "MULTIPOLYGON":
            geom = feature.geom[0].geos
        elif feature.geom.geom_name == "POLYGON":
            geom = feature.geom.geos
        else:
            raise NotImplementedError("Unknown geometry type: " + feature.geom.geom_name)
        scenarios.append(Scenario(name=featureValue(feature, "port_name"), geom=geom, manager=user))
        print(featureValue(feature, "port_name"))

    with atomic():
        scenarios = Scenario.objects.bulk_create(scenarios)
        geoms = [wkt.loads(s.geom.wkt) for s in scenarios]
        tree = STRtree(geoms)
        areas = []
        new_scenarios = {}
        counts = defaultdict(int)
        count_not_found = 0
        print("Allocating Buildings into found regions. This may take a while.")
        for feature in ds_buildings[0]:
            if feature.geom.geom_name == "MULTIPOLYGON":
                geom = feature.geom[0].geos
            elif feature.geom.geom_name == "POLYGON":
                geom = feature.geom.geos
            else:
                raise NotImplementedError("Unknown geometry type: " + feature.geom.geom_name)
            centroid = wkt.loads(feature.geom.centroid.wkt)
            found_scenario = None
            for candidate in tree.query(centroid, predicate="intersects"):
                if geoms[candidate].contains(centroid):
                    found_scenario = scenarios[candidate]
                    break
            if found_scenario is None:
                name = featureValue(feature, "inland_port")
                if not name:
                    count_not_found += 1
                    continue
                if name not in new_scenarios:
                    new_scenarios[name] = Scenario.objects.create(name=name, manager=user)
                found_scenario = new_scenarios[name]
            counts[found_scenario] += 1
            areas.append(
                Area(
                    name=f"Automatische Gebäudefläche {counts[found_scenario]}",
                    area_type=Area.AreaTypeChoices.BUILDING,
                    manager=user,
                    scenario=found_scenario,
                    geom=geom,
                )
            )
        areas = Area.objects.bulk_create(areas)

    if count_not_found:
        print(
            f"{count_not_found} Buidings could not be added to a port region "
            "and did not have a inland_port feature themselves"
        )
    return scenarios + list(new_scenarios.values()), areas


def scenarios_and_areas_from_geojson(regions_geojson: dict, buildings_geojson: dict, user):
    """Create Scenarios and Areas from GeoJSON dicts."""
    with (
        _str_as_datasource(json.dumps(regions_geojson)) as ds_regions,
        _str_as_datasource(json.dumps(buildings_geojson)) as ds_buildings,
    ):
        return process_datasources_to_scenarios(ds_regions, ds_buildings, user)


def scenarios_and_areas_from_file(region_file, buildings_file, user):
    """Create Scenarios and Areas from file-like objects containing GeoJSON."""
    if isinstance(region_file, TemporaryUploadedFile):
        region_data = region_file.read().decode()
    else:
        with open(region_file) as f:
            region_data = f.read()
    if isinstance(buildings_file, TemporaryUploadedFile):
        buildings_data = buildings_file.read().decode()
    else:
        with open(buildings_file) as f:
            buildings_data = f.read()
    with (
        _str_as_datasource(region_data) as ds_regions,
        _str_as_datasource(buildings_data) as ds_buildings,
    ):
        return process_datasources_to_scenarios(ds_regions, ds_buildings, user)


def duplicate_project(project: Project):
    # deepcopy may reassign the instance pk in memory, so resolve the old group first
    old_group = Group.objects.filter(name=project.group_name()).first()
    new_project, _ = deepcopy(project, exclude_models={User}, max_depth=2)

    # Authorization is not directly linked through foreign keys but through foreign_objects
    # Therefor the group is not deepcopied. Maybe make the group part of the object?
    # This would break down if multiple groups per project exist
    group = Group.objects.create(name=new_project.group_name())
    assign_perm("view", group, new_project)
    assign_perm("details", group, new_project)
    group.user_set.set(old_group.user_set.all())

    return new_project


def duplicate_scenario(scenario: Scenario, user: User, suffix=" (Dupliziert)"):
    # Scenario internal_id must be unique. by changing the in memory internal_id
    # the deepcopy does not create a collision
    from guardian.models import GroupObjectPermission

    scenario.internal_id = uuid4()
    new_scenario, _ = deepcopy(scenario, exclude_models={User, Project}, max_depth=1)
    new_scenario.name += suffix
    new_scenario.manager = user
    new_scenario.save()
    # Managers are properly copied but permissions are not since they are not referenced through foreign field. For now only Project Group Permissions are allowed
    old_areas = Area.objects.filter(scenario=scenario)
    new_areas = Area.objects.filter(scenario=new_scenario)
    assert len(old_areas) == len(new_areas)
    old_d = {x.id: x for x in old_areas}
    new_d = {x.internal_id: x for x in new_areas}

    group = Group.objects.get(name=scenario.project.group_name())
    content_type = ContentType.objects.get_for_model(Area)
    ids = [a.id for a in old_areas]
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

    return new_scenario


def prefetch_projects_users(projects: QuerySet[Project]) -> None:
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
    for perm in group_perms:
        project = id_to_project[int(perm.object_pk)]
        if not hasattr(project, "_users_cache"):
            project._users_cache = {}
        project._users_cache[perm.permission.codename] = perm.group.user_set.all()
    for p in id_to_project.values():
        if not hasattr(p, "_users_cache"):
            p._users_cache = {}


def get_user_projects(user: User):
    # TODO: Add all scenarios with permission for the user not just managed
    if user.is_superuser:
        return Project.objects.all().prefetch_related("scenario_set")
    return Project.objects.filter(manager=user).prefetch_related("scenario_set")


def get_template_scenarios(user: User):
    # TODO: template user, e.g. add data as user TEMPLATE or smth?
    template_user = User.objects.filter(is_superuser=True).get(username="data")
    if user.is_superuser:
        return Scenario.objects.all()
    return Scenario.objects.filter(manager__in=[user, template_user])
