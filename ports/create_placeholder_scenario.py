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
    s = Scenario.objects.create(name="TestScenario", uuid="00000000-0000-4000-0000-000000000000")
    area1 = Area.objects.create(
        scenario=s,
        name="Building Area 1",
        geom=Polygon(((52, 13), (52, 13.5), (52.5, 13.2))),
    )
    area2 = Area.objects.create(
        scenario=s,
        name="Open Area 2",
        geom=Polygon(((51, 13), (51, 13.5), (51.5, 13.2))),
    )
    template1 = LoadTemplate.objects.create(
        scenario=s,
        name="Constant load 1",
        timeseries={"time": [datetime.today], "value": [1]},
    )
    load1 = Load.objects.create(scenario=s, name="Some Load 1999", area=area1, template=template1)
    Grid.objects.create(
        scenario=s,
        name="My Grid 1",
        carrier=Grid.CarrierChoices.OIL,
        timeseries=load1,
        area=area1,
    )
    Generator.objects.create(
        scenario=s,
        name="My Generator 1",
        area=area2,
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

    return s
