"""Helper script to create a minimal Scenario.
Can be used to help when implementing the frontend.
Can be used for testing.
"""

from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.gis.geos import Polygon
from guardian.shortcuts import assign_perm

from .models import Area
from .models import Generator
from .models import Grid
from .models import Heating
from .models import Load
from .models import LoadTemplate
from .models import Project
from .models import Scenario
from .models import Solar


def create_scenario() -> Scenario:
    """Deletes previous scenario with 'special' uid and creates new one"""
    user1, _ = User.objects.get_or_create(username="fo@fo")
    user2, _ = User.objects.get_or_create(username="ba@ba")
    # Test user without special access
    user3, _ = User.objects.get_or_create(username="ca@ca")
    user1.set_password("123")
    user1.is_active = True
    user2.set_password("123")
    user2.is_active = True
    user1.save()
    user2.save()

    _uid = "00000000-0000-4000-0000-000000000000"
    s = Scenario.objects.filter(internal_id=_uid).first()
    if s:
        s.safe_delete()

    new_project = Project.objects.create(name="TestProjekt", manager=user1)
    s = Scenario.objects.create(
        name="TestScenario", internal_id=_uid, manager=user1, project=new_project
    )
    # Group might exist already, since developer might switch back and force between migrations states
    old_group = Group.objects.filter(name=new_project.group_name()).first()
    if old_group:
        # Delete the group so no permissions leak from another project
        old_group.delete()
    group = Group.objects.create(name=new_project.group_name())
    user1.groups.add(group)
    user2.groups.add(group)
    # Group is allowed to view generic and details of Project
    assign_perm("view", group, new_project)
    assign_perm("details", group, new_project)

    area1 = Area.objects.create(
        scenario=s,
        name="Building Area 1",
        geom=Polygon(
            (
                [13.333282, 52.53887],
                [13.333282, 52.53887],
                [13.335385, 52.538648],
                [13.335385, 52.538648],
                [13.3356, 52.539144],
                [13.3356, 52.539144],
                [13.334506, 52.539445],
                [13.334506, 52.539445],
                [13.334033, 52.539327],
                [13.333712, 52.539262],
                [13.333712, 52.539262],
                [13.333454, 52.539144],
                [13.333454, 52.539144],
                [13.333282, 52.53887],
            )
        ),
        area_type=Area.AreaTypeChoices.BUILDING,
        manager=user1,
    )

    area2 = Area.objects.create(
        scenario=s,
        name="Silo",
        geom=Polygon(
            (
                [13.332338, 52.53825],
                [13.332338, 52.53825],
                [13.332467, 52.538192],
                [13.332467, 52.538192],
                [13.332564, 52.538296],
                [13.332564, 52.538296],
                [13.332564, 52.538413],
                [13.332564, 52.538413],
                [13.332403, 52.538492],
                [13.332403, 52.538492],
                [13.332263, 52.538433],
                [13.332263, 52.538433],
                [13.332253, 52.538342],
                [13.332253, 52.538342],
                [13.332338, 52.53825],
            )
        ),
        area_type=Area.AreaTypeChoices.BUILDING,
        manager=user1,
    )
    area3 = Area.objects.create(
        scenario=s,
        name="Building Area 2",
        area_type=Area.AreaTypeChoices.BUILDING,
        manager=user2,
    )
    Area.objects.create(
        scenario=s,
        name="Open Area 1",
        area_type=Area.AreaTypeChoices.OPEN,
        usage=Area.OpenUsageChoices.GREEN,
        manager=user2,
    )

    Area.objects.create(
        scenario=s,
        name="Open Area 2",
        area_type=Area.AreaTypeChoices.OPEN,
        manager=user1,
    )

    user1_areas = Area.objects.filter(manager=user1)
    user2_areas = Area.objects.filter(manager=user2)
    assign_perm("details", user1, user1_areas)
    assign_perm("details", user2, user2_areas)

    template1 = LoadTemplate.objects.create(
        scenario=s,
        name="Constant load 1 (by fo)",
        manager=user1,
        timeseries={"timestep_minutes": 15, "values": [1, 4, 99, 99]},
        spec_load=1,
    )
    template1 = LoadTemplate.objects.create(
        scenario=s,
        name="Constant load 1 (by ba)",
        manager=user2,
        timeseries={"timestep_minutes": 5, "values": [1, 2, 4]},
        spec_load=1,
    )
    load1 = Load.objects.create(scenario=s, name="Some Load 1999", area=area1, template=template1)
    grid = Grid.objects.create(
        scenario=s,
        name="My Grid 1",
        carrier=Grid.CarrierChoices.OIL,
        timeseries=load1,
    )
    grid.areas.add(area1)

    Generator.objects.create(
        scenario=s,
        name="My Generator 1",
        area=area1,
        power_kw=20,
        carrier=Generator.CarrierChoices.DIESEL,
        manager=area1.manager,
    )

    Generator.objects.create(
        scenario=s,
        name="My Generator 2",
        area=area2,
        power_kw=20,
        carrier=Generator.CarrierChoices.DIESEL,
        manager=area2.manager,
    )
    Heating.objects.create(
        scenario=s,
        name="My Heating 1",
        area=area2,
        power_kw=20,
        carrier=Heating.CarrierChoices.ELECTRICITY,
        manager=area1.manager,
    )
    Solar.objects.create(
        scenario=s, name="My Solar 1", area=area3, power_kw=20, manager=area3.manager
    )
    Solar.objects.create(
        scenario=s, name="My Solar 2", area=area2, power_kw=20, manager=area2.manager
    )
    Solar.objects.create(
        scenario=s, name="My Solar 3", area=area2, power_kw=20, manager=area2.manager
    )
    Solar.objects.create(
        scenario=s, name="My Solar 4", area=area2, power_kw=20, manager=area2.manager
    )

    return s
