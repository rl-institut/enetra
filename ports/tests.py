import json
from uuid import uuid4

from django.forms.models import model_to_dict
from django.test import TestCase

from ports.import_export import ScenarioJSONImporterExporter
from ports.import_export import visit_all_scenario_queries
from ports.models import Area  # noqa
from ports.models import Scenario  # noqa
from ports.models import Solar  # noqa

# Create your tests here.


def create_minimal_scenario() -> Scenario:
    scenario = Scenario.objects.create(name="Test Scenario")
    area = Area.objects.create(name="Test Area", scenario=scenario)
    Solar.objects.create(name="Test Scenario", area=area, scenario=scenario)
    Solar.objects.create(name="Solar Scenario", area=area, scenario=scenario)
    return scenario


class ImportExport(TestCase):
    def test_import(self):
        # TODO: Add many to many relationship to check that importer is working
        scenario = Scenario.objects.create(name="Test Scenario")
        area = Area.objects.create(name="Test Area", scenario=scenario)
        solar1 = Solar.objects.create(name="Solar Test1", area=area, scenario=scenario)
        Solar.objects.create(name="Solar Test2", area=area, scenario=scenario)
        exporter = ScenarioJSONImporterExporter()
        visit_all_scenario_queries(exporter, scenario)
        json_data = exporter.renderJSON()
        scenario_data = json.loads(json_data)
        exported_scenario = scenario_data[Scenario.__qualname__][0]  # noqa

        importer = ScenarioJSONImporterExporter()
        importer.loads(json_bytes=json_data)

        importer.generate_instances()
        for scenario in importer.object_data["Scenario"]:
            if Scenario.objects.filter(internal_id=scenario.internal_id).exists():
                scenario.internal_id = uuid4()

        # bulk create instances and the db generated ids to appropriately set the foreign keys
        importer.bulk_create_and_adjust_foreign_keys()

        importer.create_many_to_many()
        imported_scenario = Scenario.objects.get(id=importer.object_data["Scenario"][0].id)
        # Note this does not check all fields, in particular read only fields like created_at
        assert model_to_dict(scenario) == model_to_dict(imported_scenario)

        imported_solar1 = Solar.objects.get(id=importer.object_data["Solar"][0].id)
        # imported objects reference different objects so they are not equal anymore.
        # check for values instead
        assert solar1.name == imported_solar1.name
        assert solar1.area.name == imported_solar1.area.name
        # Assert they dont reference the same object
        assert solar1.area.id != imported_solar1.area.id
