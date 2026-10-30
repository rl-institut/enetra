"""
Tests for ports.forms.get_shared_grids: the grids every given area is connected to,
keyed by grid_<carrier>. Used for the initial grid values of single and bulk area forms.
"""

from django.contrib.auth.models import User
from django.contrib.gis.geos import GEOSGeometry
from django.test import TestCase

from ports.constants import GRID_FIELD_PREFIX
from ports.forms import get_shared_grids
from ports.models import Area
from ports.models import DuplicateGridCarrierError
from ports.models import Grid
from ports.models import Project
from ports.models import Scenario

ELECTRICITY_KEY = GRID_FIELD_PREFIX + Grid.CarrierChoices.ELECTRICITY
HEAT_KEY = GRID_FIELD_PREFIX + Grid.CarrierChoices.HEAT


class GetSharedGridsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("testuser", password="pass")
        cls.project = Project.objects.create(name="Test Projekt")
        cls.scenario = Scenario.objects.create(name="Test Scenario", project=cls.project)

        cls.area1 = cls.create_area("Area 1", "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        cls.area2 = cls.create_area("Area 2", "POLYGON((2 2, 3 2, 3 3, 2 3, 2 2))")
        cls.area3 = cls.create_area("Area 3", "POLYGON((4 4, 5 4, 5 5, 4 5, 4 4))")

        cls.electricity_grid = cls.create_grid("Electricity Grid", Grid.CarrierChoices.ELECTRICITY)
        cls.other_electricity_grid = cls.create_grid(
            "Second Electricity Grid", Grid.CarrierChoices.ELECTRICITY
        )
        cls.heat_grid = cls.create_grid("Heat Grid", Grid.CarrierChoices.HEAT)

    @classmethod
    def create_area(cls, name, polygon):
        return Area.objects.create(
            scenario=cls.scenario,
            name=name,
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
            geom=GEOSGeometry("SRID=4326;" + polygon),
        )

    @classmethod
    def create_grid(cls, name, carrier):
        return Grid.objects.create(
            scenario=cls.scenario, name=name, carrier=carrier, manager=cls.user
        )

    def areas(self, *areas):
        return Area.objects.filter(pk__in=[area.pk for area in areas])

    # single area

    def test_single_area_without_grids(self):
        self.assertEqual(get_shared_grids(self.areas(self.area1)), {})

    def test_single_area_with_one_grid(self):
        self.electricity_grid.areas.add(self.area1)

        self.assertEqual(
            get_shared_grids(self.areas(self.area1)), {ELECTRICITY_KEY: self.electricity_grid}
        )

    def test_single_area_with_grids_of_different_carriers(self):
        self.electricity_grid.areas.add(self.area1)
        self.heat_grid.areas.add(self.area1)

        self.assertEqual(
            get_shared_grids(self.areas(self.area1)),
            {ELECTRICITY_KEY: self.electricity_grid, HEAT_KEY: self.heat_grid},
        )

    def test_values_are_grid_instances(self):
        # the bulk form is bound with this data and resolves grids by internal_id,
        # so a plain pk would not be a valid choice
        self.electricity_grid.areas.add(self.area1)

        result = get_shared_grids(self.areas(self.area1))

        self.assertIsInstance(result[ELECTRICITY_KEY], Grid)

    # multiple areas

    def test_no_areas(self):
        self.assertEqual(get_shared_grids(Area.objects.none()), {})

    def test_all_areas_share_grid(self):
        self.electricity_grid.areas.add(self.area1, self.area2, self.area3)

        self.assertEqual(
            get_shared_grids(self.areas(self.area1, self.area2, self.area3)),
            {ELECTRICITY_KEY: self.electricity_grid},
        )

    def test_grid_connected_to_some_areas_is_not_shared(self):
        # area2 has no electricity grid at all
        self.electricity_grid.areas.add(self.area1)

        self.assertEqual(get_shared_grids(self.areas(self.area1, self.area2)), {})

    def test_grid_connected_to_all_but_one_area_is_not_shared(self):
        self.electricity_grid.areas.add(self.area1, self.area2)

        self.assertEqual(get_shared_grids(self.areas(self.area1, self.area2, self.area3)), {})

    def test_areas_with_different_grids_of_same_carrier(self):
        self.electricity_grid.areas.add(self.area1)
        self.other_electricity_grid.areas.add(self.area2)

        self.assertEqual(get_shared_grids(self.areas(self.area1, self.area2)), {})

    def test_only_shared_carriers_are_returned(self):
        # electricity is shared, heat only by area1
        self.electricity_grid.areas.add(self.area1, self.area2)
        self.heat_grid.areas.add(self.area1)

        self.assertEqual(
            get_shared_grids(self.areas(self.area1, self.area2)),
            {ELECTRICITY_KEY: self.electricity_grid},
        )

    def test_connections_of_other_areas_are_ignored(self):
        # area3 is not part of the selection and must not influence the result
        self.electricity_grid.areas.add(self.area1, self.area2)
        self.other_electricity_grid.areas.add(self.area3)

        self.assertEqual(
            get_shared_grids(self.areas(self.area1, self.area2)),
            {ELECTRICITY_KEY: self.electricity_grid},
        )

    # invalid data

    def test_area_with_multiple_grids_of_same_carrier_raises(self):
        # the m2m_changed signal prohibits this, but a direct bulk_create on the
        # through model bypasses it (see prohibit_multi_grid_of_same_carrier_per_area)
        Grid.areas.through.objects.bulk_create(
            [
                Grid.areas.through(area_id=self.area1.id, grid_id=self.electricity_grid.id),
                Grid.areas.through(area_id=self.area1.id, grid_id=self.other_electricity_grid.id),
            ]
        )

        with self.assertRaises(DuplicateGridCarrierError):
            get_shared_grids(self.areas(self.area1))

    # queries

    def test_number_of_queries(self):
        self.electricity_grid.areas.add(self.area1, self.area2)
        self.heat_grid.areas.add(self.area1, self.area2)
        areas = self.areas(self.area1, self.area2)

        # area ids + links joined with their grids, independent of the number of carriers
        with self.assertNumQueries(2):
            get_shared_grids(areas)
