import json
import tempfile
from collections import defaultdict
from contextlib import contextmanager

from django.contrib.auth.models import User
from django.contrib.gis.gdal import DataSource
from django.core.files.uploadedfile import TemporaryUploadedFile
from django.db.transaction import atomic
from shapely import STRtree
from shapely import wkt

from .models import Area
from .models import Scenario


@contextmanager
def _str_as_datasource(data: str):
    """Write a GeoJSON dict to a temp file and yield a GDAL DataSource.
    Keeps the file alive for the lifetime of the DataSource.
    """
    with tempfile.NamedTemporaryFile(suffix=".geojson", mode="w") as tmp:
        tmp.write(data)
        tmp.flush()
        yield DataSource(tmp.name)


def process_datasources_to_scenarios(ds_regions: DataSource, ds_buildings: DataSource, user: User):
    """Create Scenarios and Areas from GDAL DataSource objects.

    If buildings are not found within a Scenario region but have an "inland_port"
    attribute, an inland port Scenario with that name will be generated.
    """

    def featureValue(feature, field):
        """Depending on datasource a feature field can be a
        django.contrib.gis.gdal.field.OFTReal object
        or the plain value.
        For the first we have to explicitly ask for the value
        """
        if field not in feature.fields:
            return None
        try:
            return feature.get(field).value
        except AttributeError:
            return feature.get(field)

    scenarios = []
    for feature in ds_regions[0]:
        if feature.geom.geom_name == "MULTIPOLYGON":
            geom = feature.geom[0].geos
        elif feature.geom.geom_name == "POLYGON":
            geom = feature.geom.geos
        else:
            raise NotImplementedError("Unknown geometry type: " + feature.geom.geom_name)
        scenarios.append(Scenario(name=featureValue(feature, "port_name"), geom=geom, manager=user))
        print(featureValue(feature, "port_name"))

    with atomic():
        scenarios = Scenario.objects.bulk_create(scenarios)
        geoms = [wkt.loads(s.geom.wkt) for s in scenarios]
        tree = STRtree(geoms)
        areas = []
        new_scenarios = {}
        counts = defaultdict(int)
        count_not_found = 0
        print("Allocating Buildings into found regions. This may take a while.")
        for feature in ds_buildings[0]:
            if feature.geom.geom_name == "MULTIPOLYGON":
                geom = feature.geom[0].geos
            elif feature.geom.geom_name == "POLYGON":
                geom = feature.geom.geos
            else:
                raise NotImplementedError("Unknown geometry type: " + feature.geom.geom_name)
            centroid = wkt.loads(feature.geom.centroid.wkt)
            found_scenario = None
            for candidate in tree.query(centroid, predicate="intersects"):
                if geoms[candidate].contains(centroid):
                    found_scenario = scenarios[candidate]
                    break
            if found_scenario is None:
                name = featureValue(feature, "inland_port")
                if not name:
                    count_not_found += 1
                    continue
                if name not in new_scenarios:
                    new_scenarios[name] = Scenario.objects.create(name=name, manager=user)
                found_scenario = new_scenarios[name]
            counts[found_scenario] += 1
            areas.append(
                Area(
                    name=f"Automatische Gebäudefläche {counts[found_scenario]}",
                    area_type=Area.AreaTypeChoices.BUILDING,
                    manager=user,
                    scenario=found_scenario,
                    geom=geom,
                )
            )
        areas = Area.objects.bulk_create(areas)

    if count_not_found:
        print(
            f"{count_not_found} Buidings could not be added to a port region "
            "and did not have a inland_port feature themselves"
        )
    return scenarios + list(new_scenarios.values()), areas


def scenarios_and_areas_from_geojson(regions_geojson: dict, buildings_geojson: dict, user):
    """Create Scenarios and Areas from GeoJSON dicts."""
    with (
        _str_as_datasource(json.dumps(regions_geojson)) as ds_regions,
        _str_as_datasource(json.dumps(buildings_geojson)) as ds_buildings,
    ):
        return process_datasources_to_scenarios(ds_regions, ds_buildings, user)


def scenarios_and_areas_from_file(region_file, buildings_file, user):
    """Create Scenarios and Areas from file-like objects containing GeoJSON."""
    if isinstance(region_file, TemporaryUploadedFile):
        region_data = region_file.read().decode()
    else:
        with open(region_file) as f:
            region_data = f.read()
    if isinstance(buildings_file, TemporaryUploadedFile):
        buildings_data = buildings_file.read().decode()
    else:
        with open(buildings_file) as f:
            buildings_data = f.read()
    with (
        _str_as_datasource(region_data) as ds_regions,
        _str_as_datasource(buildings_data) as ds_buildings,
    ):
        return process_datasources_to_scenarios(ds_regions, ds_buildings, user)
