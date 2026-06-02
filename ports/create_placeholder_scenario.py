"""Helper script to create a minimal Scenario.
Can be used to help when implementing the frontend.
Can be used for testing.
"""

from datetime import datetime

from django.contrib.gis.geos import Polygon

from .models import Area
from .models import Generator
from .models import Grid
from .models import Heating
from .models import Load
from .models import LoadTemplate
from .models import Scenario
from .models import Solar


def create_scenario() -> Scenario:
    """Deletes previous scenario with 'special' uid and creates new one"""
    _uid = "00000000-0000-4000-0000-000000000000"
    s = Scenario.objects.filter(internal_id=_uid).first()
    if s:
        s.safe_delete()
    s = Scenario.objects.create(name="TestScenario", internal_id=_uid)
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
    )
    area1 = Area.objects.create(
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
    )
    area2 = Area.objects.create(
        scenario=s,
        name="Building Area 2",
        area_type=Area.AreaTypeChoices.BUILDING,
    )
    Area.objects.create(
        scenario=s,
        name="Open Area 1",
        area_type=Area.AreaTypeChoices.OPEN,
        usage=Area.OpenUsageChoices.GREEN,
    )

    Area.objects.create(
        scenario=s,
        name="Open Area 2",
        area_type=Area.AreaTypeChoices.OPEN,
    )

    template1 = LoadTemplate.objects.create(
        scenario=s,
        name="Constant load 1",
        timeseries={"time": [datetime.today().isoformat()], "value": [1]},
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
    )

    Generator.objects.create(
        scenario=s,
        name="My Generator 2",
        area=area2,
        power_kw=20,
        carrier=Generator.CarrierChoices.DIESEL,
    )
    Heating.objects.create(
        scenario=s,
        name="My Heating 1",
        area=area2,
        power_kw=20,
        carrier=Heating.CarrierChoices.ELECTRICITY,
    )
    Solar.objects.create(scenario=s, name="My Solar 1", area=area2, power_kw=20)
    Solar.objects.create(scenario=s, name="My Solar 2", area=area2, power_kw=20)
    Solar.objects.create(scenario=s, name="My Solar 3", area=area2, power_kw=20)
    Solar.objects.create(scenario=s, name="My Solar 4", area=area2, power_kw=20)
    print(f"Created new scenario {s}")

    return s
