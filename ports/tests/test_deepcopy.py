"""
Tests deepcopying
"""

from django.test import TestCase

from ports.deepcopy import Deepcopy
from ports.models import Area
from ports.models import Generator
from ports.models import Load
from ports.models import LoadTemplate
from ports.models import Project
from ports.models import Scenario


class DuplicatePermissionsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(name="Test Projekt")
        cls.scenario = Scenario.objects.create(name="Test Scenario", project=cls.project)
        cls.area = Area.objects.create(
            scenario=cls.scenario,
            name="Area 1",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
        )
        cls.area2 = Area.objects.create(
            scenario=cls.scenario,
            name="Area 2",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
        )

        cls.load_template = LoadTemplate.objects.create(
            scenario=cls.scenario,
            name="Template",
            timeseries={},
            spec_load=0.0,
        )
        cls.load = Load.objects.create(
            scenario=cls.scenario,
            name="Load 1",
            area=cls.area,
            template=cls.load_template,
            factor=1.0,
        )
        cls.load2 = Load.objects.create(
            scenario=cls.scenario,
            name="Load 2",
            area=cls.area,
            template=cls.load_template,
            factor=1.0,
        )

        cls.generator = Generator.objects.create(
            scenario=cls.scenario,
            name="Generator 1",
            area=cls.area,
            carrier=Generator.CarrierChoices.DIESEL,
        )
        cls.generator2 = Generator.objects.create(
            scenario=cls.scenario,
            name="Generator 2",
            area=cls.area,
            carrier=Generator.CarrierChoices.DIESEL,
        )

    def test_deepcopy(self):
        model_hierarchy = [Project, Scenario, Area, LoadTemplate, Load, Generator]
        dc = Deepcopy(model_hierarchy)
        new_project = dc.deepcopy(self.project)
        assert new_project.id != self.project.id
        assert new_project.scenario_set.count() == self.scenario_set.count()
