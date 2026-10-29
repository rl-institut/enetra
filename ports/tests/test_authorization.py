"""
Unit tests for ports.authorization.has_authorization() across the ScenarioItem subtypes that
scope access via an Area relation: Load/Generator/etc. use a singular `area` ForeignKey, Grid
uses a plural `areas` ManyToManyField.
"""

from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.gis.geos import GEOSGeometry
from django.test import TestCase
from guardian.shortcuts import assign_perm

from ports.authorization import has_authorization
from ports.models import Area
from ports.models import Grid
from ports.models import Load
from ports.models import Project
from ports.models import Scenario
from ports.models import Timeseries


class HasAuthorizationAreaScopedItemsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", password="pass")
        cls.colleague = User.objects.create_user("colleague", password="pass")

        cls.project = Project.objects.create(name="Test Projekt")
        cls.scenario = Scenario.objects.create(name="Test Scenario", project=cls.project)
        group, _ = Group.objects.get_or_create(name=cls.project.group_name())
        group.user_set.add(cls.owner)
        group.user_set.add(cls.colleague)

        cls.area = Area.objects.create(
            scenario=cls.scenario,
            name="Area 1",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
            geom=GEOSGeometry("SRID=4326;POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"),
            manager=cls.owner,
        )
        assign_perm("details", group, cls.project)
        # The colleague isn't the area's manager, but is explicitly granted "details" on it —
        # this is what should make them authorized for any item scoped to that area.
        assign_perm("details", group, cls.area)

    def test_colleague_with_area_permission_is_authorized_for_a_load_in_that_area(self):
        load = Load.objects.create(
            scenario=self.scenario,
            name="Load 1",
            area=self.area,
            manager=self.owner,
            factor=1.0,
            timeseries=Timeseries.objects.create(
                scenario=self.scenario,
                name="ts",
                manager=self.owner,
                timeseries={"values": [1], "timestep_minutes": 15},
                spec_load=0,
            ),
        )
        self.assertTrue(has_authorization(load, self.colleague, "details"))

    def test_colleague_with_area_permission_is_authorized_for_a_grid_in_that_area(self):
        # grid is only guarded by project "details"
        grid = Grid.objects.create(
            scenario=self.scenario,
            name="Grid 1",
            carrier=Grid.CarrierChoices.ELECTRICITY,
            manager=self.owner,
        )
        grid.areas.add(self.area)

        self.assertTrue(has_authorization(grid, self.colleague, "details"))
