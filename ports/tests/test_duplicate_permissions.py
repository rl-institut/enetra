"""
Tests that duplicating a Scenario or Project also carries over the
group-based Guardian permissions, i.e. a group member keeps access
to the duplicated scenario/project.

Setup mirrors how create_project (ports/views.py) wires permissions:
- A Group named project.group_name() is created
- "view" and "details" object permissions on the project are assigned to the group
- The member user is added to the group

Duplication runs through the real API endpoints (ports:api_duplicate),
logged in as the project manager.
"""

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from guardian.shortcuts import assign_perm

from ports.models import Area
from ports.models import Project
from ports.models import Scenario
from ports.models import has_authorization


class DuplicatePermissionsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user("dup_manager", password="pass")
        cls.member = User.objects.create_user("dup_member", password="pass")
        # Not part of the project group, must not gain access through duplication
        cls.outsider = User.objects.create_user("dup_outsider", password="pass")

        cls.project = Project.objects.create(name="Duplicate Perm Project", manager=cls.manager)
        cls.scenario = Scenario.objects.create(
            name="Duplicate Perm Scenario", project=cls.project, manager=cls.manager
        )

        cls.group = Group.objects.create(name=cls.project.group_name())
        assign_perm("view", cls.group, cls.project)
        assign_perm("details", cls.group, cls.project)
        cls.member.groups.add(cls.group)

        # One public area (group has 'details') and one restricted to the manager
        cls.public_area = Area.objects.create(
            scenario=cls.scenario,
            name="Public Area",
            area_type=Area.AreaTypeChoices.BUILDING,
            manager=cls.manager,
        )
        cls.private_area = Area.objects.create(
            scenario=cls.scenario,
            name="Private Area",
            area_type=Area.AreaTypeChoices.BUILDING,
            manager=cls.manager,
        )
        assign_perm("details", cls.group, cls.public_area)

    def setUp(self):
        self.client.force_login(self.manager)

    @staticmethod
    def fresh_member():
        """Fetch the member anew so no cached Guardian permissions are used"""
        return User.objects.get(username="dup_member")

    def duplicate_url(self, model_name, internal_id):
        return reverse(
            "ports:api_duplicate",
            kwargs={"model": model_name, "internal_id": internal_id},
        )

    def test_member_has_access_to_original_project(self):
        """Sanity check: the group setup grants the member access to the original"""
        member = self.fresh_member()
        self.assertTrue(has_authorization(self.project, member, "view"))
        self.assertTrue(has_authorization(self.project, member, "details"))

    def test_duplicated_scenario_keeps_member_access(self):
        response = self.client.post(self.duplicate_url("scenario", self.scenario.internal_id))
        self.assertEqual(response.status_code, 201, response.content)
        new_scenario = Scenario.objects.get(internal_id=response.json()["internal_id"])
        self.assertNotEqual(new_scenario.pk, self.scenario.pk)

        # Scenario access is gated through its project (see ApiView.dispatch),
        # so the duplicate must stay reachable for the project group member
        self.assertIsNotNone(new_scenario.project)
        member = self.fresh_member()
        self.assertTrue(has_authorization(new_scenario.project, member, "details"))

    def test_non_member_cannot_access_duplicated_scenario(self):
        response = self.client.post(self.duplicate_url("scenario", self.scenario.internal_id))
        self.assertEqual(response.status_code, 201, response.content)
        new_scenario = Scenario.objects.get(internal_id=response.json()["internal_id"])

        outsider = User.objects.get(username="dup_outsider")
        self.assertFalse(has_authorization(new_scenario.project, outsider, "view"))
        self.assertFalse(has_authorization(new_scenario.project, outsider, "details"))

    def test_duplicated_scenario_carries_over_area_permissions(self):
        """The public/restricted split of the areas must survive duplication"""
        response = self.client.post(self.duplicate_url("scenario", self.scenario.internal_id))
        self.assertEqual(response.status_code, 201, response.content)
        new_scenario = Scenario.objects.get(internal_id=response.json()["internal_id"])

        # ScenarioItem.internal_id is stable across scenario copies
        new_public = Area.objects.get(
            scenario=new_scenario, internal_id=self.public_area.internal_id
        )
        new_private = Area.objects.get(
            scenario=new_scenario, internal_id=self.private_area.internal_id
        )
        self.assertNotEqual(new_public.pk, self.public_area.pk)
        self.assertNotEqual(new_private.pk, self.private_area.pk)

        member = self.fresh_member()
        self.assertTrue(
            has_authorization(new_public, member, "details"),
            "Group member lost access to the public area after duplication",
        )
        self.assertFalse(
            has_authorization(new_private, member, "details"),
            "Group member must not gain access to the restricted area through duplication",
        )
        # The restricted area stays accessible for its manager
        self.assertTrue(has_authorization(new_private, self.manager, "details"))

    def test_duplicated_project_has_group(self):
        """The duplicate must get its own project group holding the same users"""
        response = self.client.post(self.duplicate_url("project", self.project.internal_id))
        self.assertEqual(response.status_code, 201, response.content)
        new_project = Project.objects.get(internal_id=response.json()["internal_id"])
        self.assertNotEqual(new_project.pk, self.project.pk)

        new_group = Group.objects.filter(name=new_project.group_name()).first()
        self.assertIsNotNone(new_group, "No group was created for the duplicated project")
        self.assertIn(
            self.member,
            new_group.user_set.all(),
            "Group member was not carried over to the duplicated project's group",
        )

    def test_duplicated_project_keeps_member_access(self):
        response = self.client.post(self.duplicate_url("project", self.project.internal_id))
        self.assertEqual(response.status_code, 201, response.content)
        new_project = Project.objects.get(internal_id=response.json()["internal_id"])

        member = self.fresh_member()
        self.assertTrue(
            has_authorization(new_project, member, "view"),
            "Member lost 'view' access on the duplicated project",
        )
        self.assertTrue(
            has_authorization(new_project, member, "details"),
            "Member lost 'details' access on the duplicated project",
        )


class CreateProjectFromTemplatePermissionsTest(TestCase):
    """A private area must stay private when its scenario is used as a template
    for a new project.

    user_a owns a private area in a shared scenario. user_b creates a new
    project with that scenario as base. user_b must not gain access to the
    copied private area, and user_a must not lose it.
    """

    @classmethod
    def setUpTestData(cls):
        # get_template_scenarios requires a superuser named "data";
        # its scenarios are the templates offered to every user
        cls.data_user, _ = User.objects.get_or_create(
            defaults={"password": "pass", "is_superuser": True},
            username=settings.DATA_USER,
        )
        cls.user_a = User.objects.create_user("tmpl_user_a", password="pass")
        cls.user_b = User.objects.create_user("tmpl_user_b", password="pass")

        cls.project = Project.objects.create(name="Template Project", manager=cls.data_user)
        cls.scenario = Scenario.objects.create(
            name="Template Scenario", project=cls.project, manager=cls.data_user
        )
        cls.group = Group.objects.create(name=cls.project.group_name())
        assign_perm("view", cls.group, cls.project)
        assign_perm("details", cls.group, cls.project)
        cls.group.user_set.set([cls.user_a, cls.user_b])

        # user_a's private area: no group permission assigned
        cls.private_area = Area.objects.create(
            scenario=cls.scenario,
            name="Private Area of A",
            area_type=Area.AreaTypeChoices.BUILDING,
            manager=cls.user_a,
        )
        # generic template area managed by the "data" user
        cls.data_area = Area.objects.create(
            scenario=cls.scenario,
            name="Generic Data Area",
            area_type=Area.AreaTypeChoices.BUILDING,
            manager=cls.data_user,
        )

    def create_project_from_template(self, name):
        self.client.force_login(self.user_b)
        response = self.client.post(
            reverse("ports:create_project"),
            {
                "name": name,
                "description": "",
                "template_scenario_internal_id": str(self.scenario.internal_id),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["success"], response.context["form"].errors)
        new_project = Project.objects.get(name=name)
        new_scenario = Scenario.objects.get(project=new_project)
        return new_project, new_scenario

    def test_private_area_stays_private_for_project_creator(self):
        _, new_scenario = self.create_project_from_template("B Project Private Check")
        copied_private = Area.objects.get(
            scenario=new_scenario, internal_id=self.private_area.internal_id
        )
        self.assertNotEqual(copied_private.pk, self.private_area.pk)

        user_b = User.objects.get(username="tmpl_user_b")
        self.assertEqual(
            copied_private.manager,
            self.user_a,
            "The private area's manager must not be overwritten by the project creator",
        )
        self.assertFalse(
            has_authorization(copied_private, user_b, "details"),
            "Project creator must not gain access to another user's private area",
        )
        # The original owner keeps access to the copy
        self.assertTrue(has_authorization(copied_private, self.user_a, "details"))

    def test_generic_data_area_is_taken_over_by_project_creator(self):
        _, new_scenario = self.create_project_from_template("B Project Data Area Check")
        copied_data_area = Area.objects.get(
            scenario=new_scenario, internal_id=self.data_area.internal_id
        )

        user_b = User.objects.get(username="tmpl_user_b")
        self.assertEqual(copied_data_area.manager, user_b)
        self.assertTrue(has_authorization(copied_data_area, user_b, "details"))
