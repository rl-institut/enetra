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
        geom=Polygon(((52, 13), (52, 13.5), (52.5, 13.2), (52, 13))),
        area_type=Area.AreaTypeChoices.BUILDING,
    )
    area1 = Area.objects.create(
        scenario=s,
        name="Building Area 2",
        geom=Polygon(((52, 13.2), (52, 13.7), (52.5, 13.4), (52, 13.2))),
        area_type=Area.AreaTypeChoices.BUILDING,
    )
    area2 = Area.objects.create(
        scenario=s,
        name="Building Area 2",
        geom=Polygon(((51, 13), (51, 13.5), (51.5, 13.2), (51, 13))),
        area_type=Area.AreaTypeChoices.BUILDING,
    )
    Area.objects.create(
        scenario=s,
        name="Open Area 1",
        geom=Polygon(((51, 13), (51, 13.5), (51.5, 13.2), (51, 13))),
        area_type=Area.AreaTypeChoices.OPEN,
        usage=Area.OpenUsageChoices.GREEN,
    )

    Area.objects.create(
        scenario=s,
        name="Open Area 2",
        geom=Polygon(((51, 13), (51, 13.5), (51.5, 13.2), (51, 13))),
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
