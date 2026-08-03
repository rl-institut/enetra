from django.contrib.auth.models import User
from django.contrib.gis.geos import Polygon
from django.test import TestCase

from ports.util import process_geojson_dict_to_scenarios

EMPTY_FC = {"type": "FeatureCollection", "features": []}


def _polygon(ring, properties=None):
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": properties or {},
    }


def _multipolygon(rings, properties=None):
    return {
        "type": "Feature",
        "geometry": {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]},
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
        buildings = _fc(
            _polygon([[4, 4], [4, 6], [6, 6], [6, 4], [4, 4]], {"inland_port": "Test Port"})
        )

        scenarios, areas = process_geojson_dict_to_scenarios(regions, buildings, self.user)

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].name, "Test Port")
        self.assertEqual(len(areas), 1)
        self.assertEqual(areas[0].scenario_id, scenarios[0].pk)

    def test_missing_regions(self):
        """Buildings with no matching region and no inland_port attribute are silently skipped."""
        buildings = _fc(_polygon([[4, 4], [4, 6], [6, 6], [6, 4], [4, 4]]))

        scenarios, areas = process_geojson_dict_to_scenarios(EMPTY_FC, buildings, self.user)

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

        scenarios, areas = process_geojson_dict_to_scenarios(EMPTY_FC, buildings, self.user)
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].name, "Inland Port A")
        self.assertEqual(len(areas), 2)
        self.assertEqual(areas[0].scenario_id, scenarios[0].pk)
        self.assertEqual(areas[1].scenario_id, scenarios[0].pk)

    def test_multipolygon_uses_first_polygon(self):
        """MultiPolygon regions and buildings are reduced to their first polygon."""
        region_ring_1 = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]
        region_ring_2 = [[20, 20], [20, 30], [30, 30], [30, 20], [20, 20]]
        building_ring_1 = [[4, 4], [4, 6], [6, 6], [6, 4], [4, 4]]
        building_ring_2 = [[7, 7], [7, 9], [9, 9], [9, 7], [7, 7]]

        regions = _fc(_multipolygon([region_ring_1, region_ring_2], {"port_name": "Test Port"}))
        buildings = _fc(
            _multipolygon([building_ring_1, building_ring_2], {"inland_port": "Test Port"})
        )

        scenarios, areas = process_geojson_dict_to_scenarios(regions, buildings, self.user)

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].geom.coords, Polygon(region_ring_1).coords)
        self.assertEqual(len(areas), 1)
        self.assertEqual(areas[0].geom.coords, Polygon(building_ring_1).coords)
