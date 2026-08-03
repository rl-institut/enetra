import logging
from collections import defaultdict

from django.contrib.auth.models import User
from django.contrib.gis.geos import Polygon

from .models import Area
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
    if missing_scenarios:
        logger.warn(
            "Some buildings had inland_port values not found in the regions file. "
            "%s missing scenarios were created.",
            len(missing_scenarios),
        )
    Scenario.objects.bulk_create(scenario_lut.values())
    Area.objects.bulk_create(areas)
    return list(scenario_lut.values()), areas
