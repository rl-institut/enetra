import logging
from collections import defaultdict

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.geos import Polygon
from django.db.models import Q
from django.db.models import QuerySet
from guardian.shortcuts import assign_perm
from guardian.utils import get_group_obj_perms_model

from .db_deepcopy import deepcopy
from .models import Area
from .models import Project
from .models import Scenario

logger = logging.getLogger(__name__)


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
    new_project, _ = deepcopy(project, exclude_models={User}, max_depth=2)

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
    new_scenario, _ = deepcopy(scenario, exclude_models={User, Project}, max_depth=1)
    new_scenario.name += suffix
    new_scenario.manager = user
    new_scenario.save()
    return new_scenario


def duplicate_scenario_with_permissions(scenario: Scenario, user: User, suffix=" (Dupliziert)"):
    new_scenario = duplicate_scenario(scenario, user, suffix)
    # Managers have already been copied, now transfer Project group permissions.
    transfer_group_permission(scenario, new_scenario)
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
