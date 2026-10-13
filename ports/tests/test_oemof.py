from django.test import TestCase
from django.utils import timezone

from ports import create_placeholder_scenario
from ports import models
from ports import tasks


class TestTasks(TestCase):
    def test_empty_scenario(self):
        project = models.Project.objects.create(name="empty project")
        scenario = models.Scenario.objects.create(name="empty scenario", project=project)
        tasks.oemof_task(scenario_id=scenario.id)
        project.safe_delete()

    def test_placeholder_scenario(self):
        # test the whole oemof chain for the basic placeholder scenario
        scenario = create_placeholder_scenario.create_scenario()
        # pre-task: result does not exist
        self.assertFalse(models.Result.objects.filter(scenario=scenario).exists())
        # run task
        tasks.oemof_task(scenario_id=scenario.id, days=1, ts=60)
        # post-task: result exists
        self.assertTrue(models.Result.objects.filter(scenario=scenario).exists())
        # result has data
        self.assertTrue(scenario.result.resultdata_set.count() > 0)
        # timeindex: 24 hours (inclusive at both ends)
        time = scenario.result.resultdata_set.get(attribute="timeindex").value
        self.assertEqual(len(time), 25)
        scenario.project.safe_delete()

    def test_db_to_energysystem(self):
        # test model creation for placeholder scenario
        scenario = create_placeholder_scenario.create_scenario()
        tasks.db_to_energysystem(scenario=scenario, started_at=timezone.now())
        scenario.project.safe_delete()

    def test_store_results(self):
        # has already been part of oemof chain
        pass
