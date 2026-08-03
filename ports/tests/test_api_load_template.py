"""
Tests for the LoadTemplate JSON API endpoint — only the template's manager may access it.
"""

from uuid import uuid4

from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from ports.models import LoadTemplate
from ports.models import Scenario


class ApiLoadTemplateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user("manager", password="pass")
        cls.other_user = User.objects.create_user("other", password="pass")

        cls.scenario = Scenario.objects.create(name="Test Scenario")
        Group.objects.get_or_create(name=cls.scenario.group_name())

        cls.load_template = LoadTemplate.objects.create(
            scenario=cls.scenario,
            name="Template",
            manager=cls.manager,
            timeseries={"timestep_minutes": 15, "values": [1.0, 2.0, 3.0]},
            spec_load=0.5,
        )

    def api_url(self, scenario_internal_id=None, internal_id=None):
        return reverse(
            "ports:api_load_template",
            kwargs={
                "scenario_internal_id": scenario_internal_id or self.scenario.internal_id,
                "internal_id": internal_id or self.load_template.internal_id,
            },
        )

    def test_manager_gets_json(self):
        self.client.force_login(self.manager)
        response = self.client.get(self.api_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        data = response.json()
        self.assertEqual(data["internal_id"], str(self.load_template.internal_id))
        self.assertEqual(data["scenario_internal_id"], str(self.scenario.internal_id))
        self.assertEqual(data["name"], "Template")
        self.assertEqual(data["timeseries"], {"timestep_minutes": 15, "values": [1.0, 2.0, 3.0]})
        self.assertEqual(data["spec_load"], 0.5)

    def test_non_manager_is_forbidden(self):
        self.client.force_login(self.other_user)
        response = self.client.get(self.api_url())
        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_unauthorized(self):
        response = self.client.get(self.api_url())
        self.assertEqual(response.status_code, 401)

    def test_unknown_template_returns_404(self):
        self.client.force_login(self.manager)
        response = self.client.get(self.api_url(internal_id=uuid4()))
        self.assertEqual(response.status_code, 404)

    def test_unknown_scenario_returns_404(self):
        self.client.force_login(self.manager)
        response = self.client.get(self.api_url(scenario_internal_id=uuid4()))
        self.assertEqual(response.status_code, 404)
