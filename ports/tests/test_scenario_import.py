from django.contrib.auth.models import User
from django.test import TestCase

from ports.util import scenarios_and_areas_from_geojson

EMPTY_FC = {"type": "FeatureCollection", "features": []}


def _polygon(ring, properties=None):
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": properties or {},
    }


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


class TestScenarioImport(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="testuser", password="pw")

    def test_basic_import(self):
        """Scenarios and Areas are created from matching region and building data."""
        regions = _fc(
            _polygon([[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]], {"port_name": "Test Port"})
        )
        buildings = _fc(_polygon([[4, 4], [4, 6], [6, 6], [6, 4], [4, 4]]))

        scenarios, areas = scenarios_and_areas_from_geojson(regions, buildings, self.user)

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].name, "Test Port")
        self.assertEqual(len(areas), 1)
        self.assertEqual(areas[0].scenario_id, scenarios[0].pk)

    def test_missing_regions(self):
        """Buildings with no matching region and no inland_port attribute are silently skipped."""
        buildings = _fc(_polygon([[4, 4], [4, 6], [6, 6], [6, 4], [4, 4]]))

        scenarios, areas = scenarios_and_areas_from_geojson(EMPTY_FC, buildings, self.user)

        self.assertEqual(scenarios, [])
        self.assertEqual(list(areas), [])

    def test_scenario_from_buildings(self):
        """A building with inland_port creates a new Scenario and links the Area to it.
        Multiple buildings sharing the same inland_port name reuse the same Scenario.
        """
        buildings = _fc(
            _polygon([[4, 4], [4, 6], [6, 6], [6, 4], [4, 4]], {"inland_port": "Inland Port A"}),
            _polygon([[7, 7], [7, 9], [9, 9], [9, 7], [7, 7]], {"inland_port": "Inland Port A"}),
        )

        scenarios, areas = scenarios_and_areas_from_geojson(EMPTY_FC, buildings, self.user)

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].name, "Inland Port A")
        self.assertEqual(len(areas), 2)
        self.assertEqual(areas[0].scenario_id, scenarios[0].pk)
        self.assertEqual(areas[1].scenario_id, scenarios[0].pk)

    def test_building_into_region(self):
        """A building is assigned to the Scenario whose region contains its centroid,
        not to any other region that shares the same import.
        """
        regions = _fc(
            _polygon([[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]], {"port_name": "Port A"}),
            _polygon([[20, 20], [20, 30], [30, 30], [30, 20], [20, 20]], {"port_name": "Port B"}),
        )
        buildings = _fc(
            _polygon([[4, 4], [4, 6], [6, 6], [6, 4], [4, 4]])  # centroid (5,5) — inside Port A
        )

        scenarios, areas = scenarios_and_areas_from_geojson(regions, buildings, self.user)

        self.assertEqual(len(scenarios), 2)
        self.assertEqual(len(areas), 1)
        port_a = next(s for s in scenarios if s.name == "Port A")
        self.assertEqual(areas[0].scenario_id, port_a.pk)
