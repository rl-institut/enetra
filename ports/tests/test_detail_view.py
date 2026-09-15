"""
Tests for DetailsView — GET, POST, DELETE, and CREATE for
single and multi-instance modes across Area, Load, and Generator.
"""

from uuid import uuid4

from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.gis.geos import GEOSGeometry
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from guardian.shortcuts import assign_perm
from guardian.shortcuts import remove_perm

from ports.models import Area
from ports.models import Generator
from ports.models import Load
from ports.models import Project
from ports.models import Scenario
from ports.models import Timeseries


class DetailsViewBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Runs once per test class (not per test method); DB writes in individual
        # tests are rolled back automatically. However, cls.* objects are shared
        # Python references — an in-memory mutation (e.g. self.load.name = "x")
        # without a matching DB save is NOT rolled back and leaks to later tests
        # in alphabetical order. Always call refresh_from_db() before relying on
        # a shared object's fields, or work on a local copy instead.
        cls.user = User.objects.create_user("testuser", password="pass", is_superuser=True)

        cls.project = Project.objects.create(name="Test Projekt")
        cls.scenario = Scenario.objects.create(name="Test Scenario", project=cls.project)
        Group.objects.get_or_create(name=cls.project.group_name())

        cls.area = Area.objects.create(
            scenario=cls.scenario,
            name="Area 1",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
            geom=GEOSGeometry("SRID=4326;POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"),
        )
        cls.area2 = Area.objects.create(
            scenario=cls.scenario,
            name="Area 2",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
            geom=GEOSGeometry("SRID=4326;POLYGON((2 2, 3 2, 3 3, 2 3, 2 2))"),
        )

        cls.load_template = Timeseries.objects.create(
            scenario=cls.scenario,
            name="Template",
            manager=cls.user,
            timeseries={},
            spec_load=0.0,
        )
        cls.load = Load.objects.create(
            scenario=cls.scenario,
            name="Load 1",
            area=cls.area,
            template=cls.load_template,
            factor=1.0,
        )
        cls.load2 = Load.objects.create(
            scenario=cls.scenario,
            name="Load 2",
            area=cls.area,
            template=cls.load_template,
            factor=1.0,
        )

        cls.generator = Generator.objects.create(
            scenario=cls.scenario,
            name="Generator 1",
            area=cls.area,
            carrier=Generator.CarrierChoices.DIESEL,
        )
        cls.generator2 = Generator.objects.create(
            scenario=cls.scenario,
            name="Generator 2",
            area=cls.area,
            carrier=Generator.CarrierChoices.DIESEL,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def details_url(self, model_name):
        return reverse(
            "ports:details",
            kwargs={
                "scenario_internal_id": self.scenario.internal_id,
                "model": model_name,
            },
        )

    def details_create_url(self, model_name):
        return reverse(
            "ports:details_create",
            kwargs={
                "scenario_internal_id": self.scenario.internal_id,
                "model": model_name,
            },
        )


class DetailsViewGetTest(DetailsViewBase):
    def test_get_load_single(self):
        url = self.details_url("load")
        response = self.client.get(url, {"internal_ids": str(self.load.internal_id)})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_load_detail.html",
        )

    def test_get_area_single(self):
        url = self.details_url("area")
        response = self.client.get(url, {"internal_ids": str(self.area.internal_id)})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_main.html",
        )

    def test_get_generator_single(self):
        url = self.details_url("generator")
        response = self.client.get(url, {"internal_ids": str(self.generator.internal_id)})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_component.html",
        )

    def test_get_no_selection(self):
        url = self.details_url("load")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Reswap"], "none")

    def test_get_area_multi(self):
        url = self.details_url("area")
        internal_ids = f"{self.area.internal_id},{self.area2.internal_id}"
        response = self.client.get(url, {"internal_ids": internal_ids})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_main_multi.html",
        )

    def test_get_generator_multi(self):
        url = self.details_url("generator")
        internal_ids = f"{self.generator.internal_id},{self.generator2.internal_id}"
        response = self.client.get(url, {"internal_ids": internal_ids})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_component_multi.html",
        )

    def test_get_deleted_instance(self):
        temp_load = Load.objects.create(
            scenario=self.scenario,
            name="Temp Load",
            area=self.area,
            template=self.load_template,
            factor=1.0,
        )
        deleted_internal_id = temp_load.internal_id
        temp_load.delete()

        url = self.details_url("load")
        response = self.client.get(url, {"internal_ids": str(deleted_internal_id)})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_deleted.html",
        )

    def test_get_invalid_instance(self):
        url = self.details_url("load")
        # generates warning in log.
        # WARNING 2026-05-12 11:59:08,496 Not Found: /details/{uuid}/load/
        response = self.client.get(url, {"internal_ids": str(uuid4())})
        self.assertEqual(response.status_code, 404)


class DetailsViewPostTest(DetailsViewBase):
    def test_post_load_single_valid(self):
        url = self.details_url("load")
        data = {
            "internal_id": str(self.load.internal_id),
            "name": "Updated Load",
            "factor": "2.0",
            "template": self.load_template.internal_id,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        assert response.context.get("success")
        assert not response.context.get("errors")
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_load_detail.html",
        )
        self.load.refresh_from_db()
        self.assertEqual(self.load.name, "Updated Load")
        self.assertAlmostEqual(self.load.factor, 2.0)

    def test_post_area_single_valid(self):
        url = self.details_url("area")
        data = {
            "internal_id": str(self.area.internal_id),
            "name": "Updated Area",
            "usage": Area.BuildingUsageChoices.OFFICE,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        assert response.context.get("success")
        assert not response.context.get("errors")
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_main.html",
        )
        self.area.refresh_from_db()
        self.assertEqual(self.area.name, "Updated Area")

    def test_post_generator_single_valid(self):
        url = self.details_url("generator")
        data = {
            "internal_ids": str(self.generator.internal_id),
            "internal_id": str(self.generator.internal_id),
            "name": "Updated Generator",
            "carrier": Generator.CarrierChoices.OIL,
            "efficiency": "0.9",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        assert response.context.get("success")
        assert not response.context.get("errors")
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_component.html",
        )
        self.generator.refresh_from_db()
        self.assertEqual(self.generator.name, "Updated Generator")
        self.assertEqual(self.generator.carrier, Generator.CarrierChoices.OIL)

    def test_post_generator_single_invalid(self):
        url = self.details_url("generator")
        original_name = self.generator.name
        data = {
            "internal_ids": str(self.generator.internal_id),
            "internal_id": str(self.generator.internal_id),
            "carrier": Generator.CarrierChoices.DIESEL,
            "efficiency": "not-a-float",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        assert response.context.get("errors")
        assert not response.context.get("success")
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_component.html",
        )
        self.generator.refresh_from_db()
        self.assertEqual(self.generator.name, original_name)

    def test_post_load_single_invalid(self):
        url = self.details_url("load")
        original_name = self.load.name
        data = {
            "internal_ids": str(self.load.internal_id),
            "internal_id": str(self.load.internal_id),
            "template": self.load_template.internal_id,
            "factor": "not-a-float",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        assert response.context.get("errors")
        assert not response.context.get("success")
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_load_detail.html",
        )
        self.load.refresh_from_db()
        self.assertEqual(self.load.name, original_name)

    def test_post_load_multi(self):
        url = self.details_url("load")
        internal_ids = f"{self.load.internal_id},{self.load2.internal_id}"
        data = {
            "internal_ids": internal_ids,
            "description": "Bulk updated",
            "factor": "3.5",
            "template": self.load_template.internal_id,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        assert response.context, response.context
        assert response.context.get("success")
        assert not response.context.get("errors")
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_load_detail_multi.html",
        )
        self.load.refresh_from_db()
        self.load2.refresh_from_db()
        self.assertEqual(self.load.description, "Bulk updated")
        self.assertEqual(self.load2.description, "Bulk updated")
        self.assertAlmostEqual(self.load.factor, 3.5)
        self.assertAlmostEqual(self.load2.factor, 3.5)

    def test_post_area_multi(self):
        url = self.details_url("area")
        internal_ids = f"{self.area.internal_id},{self.area2.internal_id}"
        data = {
            "internal_ids": internal_ids,
            "description": "Bulk area update",
            "usage": Area.BuildingUsageChoices.STORAGE,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        assert response.context.get("success")
        assert not response.context.get("errors")
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_main_multi.html",
        )
        self.area.refresh_from_db()
        self.area2.refresh_from_db()
        self.assertEqual(self.area.description, "Bulk area update")
        self.assertEqual(self.area2.description, "Bulk area update")

    def test_post_generator_multi(self):
        url = self.details_url("generator")
        internal_ids = f"{self.generator.internal_id},{self.generator2.internal_id}"
        data = {
            "internal_ids": internal_ids,
            "description": "Bulk generator update",
            "carrier": Generator.CarrierChoices.DIESEL,
            "efficiency": "0.85",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        assert response.context.get("success")
        assert not response.context.get("errors")
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_component_multi.html",
        )
        self.generator.refresh_from_db()
        self.generator2.refresh_from_db()
        self.assertEqual(self.generator.description, "Bulk generator update")
        self.assertEqual(self.generator2.description, "Bulk generator update")


class DetailsViewDeleteTest(DetailsViewBase):
    def test_delete_load(self):
        load_to_delete = Load.objects.create(
            scenario=self.scenario,
            name="Delete Me",
            area=self.area,
            template=self.load_template,
            factor=1.0,
        )
        internal_id = load_to_delete.internal_id
        url = self.details_url("load")
        response = self.client.delete(f"{url}?internal_ids={internal_id}")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "ports/partials/update_delete_create_scenario_item.html",
        )
        self.assertFalse(Load.objects.filter(internal_id=internal_id).exists())

    def test_delete_generator(self):
        generator_to_delete = Generator.objects.create(
            scenario=self.scenario,
            name="Delete Me",
            area=self.area,
            carrier=Generator.CarrierChoices.DIESEL,
        )
        internal_id = generator_to_delete.internal_id
        url = self.details_url("generator")
        response = self.client.delete(f"{url}?internal_ids={internal_id}")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "ports/partials/update_delete_create_scenario_item.html",
        )
        self.assertFalse(Generator.objects.filter(internal_id=internal_id).exists())


class DetailsViewCreateTest(DetailsViewBase):
    def test_create_area(self):
        url = self.details_create_url("area")
        initial_count = Area.objects.filter(scenario=self.scenario).count()
        response = self.client.post(url, {"area_type": Area.AreaTypeChoices.BUILDING})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["created"])
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_main.html",
        )
        self.assertEqual(
            Area.objects.filter(scenario=self.scenario).count(),
            initial_count + 1,
        )

    def test_create_load_single_area(self):
        url = self.details_create_url("load")
        initial_count = Load.objects.filter(scenario=self.scenario).count()
        response = self.client.post(url, {"area_internal_ids": str(self.area.internal_id)})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["created"])
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_load_detail.html",
        )
        self.assertEqual(
            Load.objects.filter(scenario=self.scenario).count(),
            initial_count + 1,
        )

    def test_create_generator_single_area(self):
        url = self.details_create_url("generator")
        initial_count = Generator.objects.filter(scenario=self.scenario).count()
        response = self.client.post(url, {"area_internal_ids": str(self.area.internal_id)})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["created"])
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_component.html",
        )
        self.assertEqual(
            Generator.objects.filter(scenario=self.scenario).count(),
            initial_count + 1,
        )

    def test_create_load_multi_area(self):
        url = self.details_create_url("load")
        initial_count = Load.objects.filter(scenario=self.scenario).count()
        area_internal_ids = f"{self.area.internal_id},{self.area2.internal_id}"
        response = self.client.post(
            url,
            {
                "area_internal_ids": area_internal_ids,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["created"])
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_load_detail_multi.html",
        )
        self.assertEqual(
            Load.objects.filter(scenario=self.scenario).count(),
            initial_count + 2,
        )


class DetailsViewPermissionsTest(TestCase):
    """
    Tests that area-level Guardian permissions gate access to area details.

    Design:
    - user_a is the area/scenario manager → always has access
    - user_b is a project group member → access depends on Guardian object perms
    - Sentinel strings in component names are searched in raw response content
      to detect data leakage without relying on response.context

    Scenario-level "view" permission is granted to the scenario group in
    setUpTestData so that the area-level check is the deciding factor.
    Per-test assign_perm/remove_perm calls are rolled back after each test.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user_a = User.objects.create_user("perm_user_a", password="pass")
        cls.user_b = User.objects.create_user("perm_user_b", password="pass")
        cls.project = Project.objects.create(name="Test Projekt")
        cls.scenario = Scenario.objects.create(
            name="Perm Test Scenario", manager=cls.user_a, project=cls.project
        )
        cls.project_group, _ = Group.objects.get_or_create(name=cls.project.group_name())

        # Detailviews are gated by project access. Therefore users need to be added to project group
        cls.project_group.user_set.add(cls.user_a)
        cls.project_group.user_set.add(cls.user_b)
        # Grant project-level view so the area check is the gating decision
        assign_perm("view", cls.project_group, cls.project)

        cls.area = Area.objects.create(
            scenario=cls.scenario,
            name="SENTINEL_AREA_XYZ_9f3a",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
            geom=GEOSGeometry("SRID=4326;POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"),
            manager=cls.user_a,
        )
        cls.load_template = Timeseries.objects.create(
            scenario=cls.scenario,
            name="Template",
            manager=cls.user_a,
            timeseries={},
            spec_load=0.0,
        )
        cls.load_template_b = Timeseries.objects.create(
            scenario=cls.scenario,
            manager=cls.user_b,
            name="Template",
            timeseries={},
            spec_load=0.0,
        )
        cls.load = Load.objects.create(
            scenario=cls.scenario,
            name="SENTINEL_LOAD_XYZ_9f3a",
            area=cls.area,
            template=cls.load_template,
            factor=1.0,
            manager=cls.user_a,
        )
        cls.generator = Generator.objects.create(
            scenario=cls.scenario,
            name="SENTINEL_GEN_XYZ_9f3a",
            area=cls.area,
            carrier=Generator.CarrierChoices.DIESEL,
            manager=cls.user_a,
        )

    def area_detail_url(self):
        return reverse(
            "ports:details",
            kwargs={
                "scenario_internal_id": self.scenario.internal_id,
                "model": "area",
            },
        )

    def tool_base_url(self):
        return reverse(
            "ports:home",
            kwargs={
                "scenario_internal_id": self.scenario.internal_id,
            },
        )

    def test_area_detail_returns_not_allowed_for_non_owner(self):
        self.client.force_login(self.user_b)
        response = self.client.get(
            self.area_detail_url(), {"internal_ids": str(self.area.internal_id)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"not allowed", response.content.lower())

    def test_private_area_components_not_in_detail_response(self):
        self.client.force_login(self.user_b)
        response = self.client.get(
            self.area_detail_url(), {"internal_ids": str(self.area.internal_id)}
        )
        self.assertNotIn(b"SENTINEL_LOAD_XYZ_9f3a", response.content)
        self.assertNotIn(b"SENTINEL_GEN_XYZ_9f3a", response.content)

    def test_owner_can_access_own_area_details(self):
        self.client.force_login(self.user_a)
        response = self.client.get(
            self.area_detail_url(), {"internal_ids": str(self.area.internal_id)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"not allowed", response.content.lower())
        self.assertIn(b"SENTINEL_AREA_XYZ_9f3a", response.content)
        self.assertIn(b"SENTINEL_LOAD_XYZ_9f3a", response.content)
        self.assertIn(b"SENTINEL_GEN_XYZ_9f3a", response.content)

    def test_area_detail_no_longer_blocked_after_made_public(self):
        assign_perm("details", self.project_group, self.area)
        self.client.force_login(self.user_b)
        response = self.client.get(
            self.area_detail_url(), {"internal_ids": str(self.area.internal_id)}
        )
        self.assertNotIn(b"not allowed", response.content.lower())

    def test_public_area_components_visible_in_detail_response(self):
        assign_perm("details", self.project_group, self.area)
        self.client.force_login(self.user_b)
        response = self.client.get(
            self.area_detail_url(), {"internal_ids": str(self.area.internal_id)}
        )
        self.assertIn(b"SENTINEL_LOAD_XYZ_9f3a", response.content)
        self.assertIn(b"SENTINEL_GEN_XYZ_9f3a", response.content)

    def test_revoking_public_blocks_detail_access_again(self):
        assign_perm("details", self.project_group, self.area)
        remove_perm("details", self.project_group, self.area)
        self.client.force_login(self.user_b)
        response = self.client.get(
            self.area_detail_url(), {"internal_ids": str(self.area.internal_id)}
        )
        self.assertIn(b"not allowed", response.content.lower())

    def test_unauthenticated_user_cannot_access_area_details(self):
        response = self.client.get(
            self.area_detail_url(), {"internal_ids": str(self.area.internal_id)}
        )
        self.assertNotIn(b"SENTINEL_LOAD_XYZ_9f3a", response.content)
        self.assertNotIn(b"SENTINEL_GEN_XYZ_9f3a", response.content)

    def test_post_load_blocked_for_non_owner(self):
        self.client.force_login(self.user_b)
        url = reverse(
            "ports:details",
            kwargs={
                "scenario_internal_id": self.scenario.internal_id,
                "model": "load",
            },
        )
        response = self.client.post(
            url,
            {
                "internal_id": str(self.load.internal_id),
                "name": "TAMPERED_BY_USER_B",
                "factor": "9.9",
                "template": self.load_template.internal_id,
            },
        )
        self.assertIn(b"not allowed", response.content.lower())
        self.load.refresh_from_db()
        self.assertNotEqual(self.load.name, "TAMPERED_BY_USER_B")

    def test_post_load_description_allowed_for_owner(self):
        self.client.force_login(self.user_a)
        url = reverse(
            "ports:details",
            kwargs={
                "scenario_internal_id": self.scenario.internal_id,
                "model": "load",
            },
        )
        response = self.client.post(
            url,
            {
                "internal_id": str(self.load.internal_id),
                "name": self.load.name,
                "description": "Owner set description",
                "factor": "1.0",
                "template": self.load_template.internal_id,
            },
        )
        self.assertNotIn(b"not allowed", response.content.lower())
        self.load.refresh_from_db()
        self.assertEqual(self.load.description, "Owner set description")

    def test_multi_post_load_blocked_for_non_owner(self):
        self.client.force_login(self.user_b)
        url = reverse(
            "ports:details",
            kwargs={
                "scenario_internal_id": self.scenario.internal_id,
                "model": "load",
            },
        )
        response = self.client.post(
            url,
            {
                "internal_ids": str(self.load.internal_id),
                "description": "TAMPERED_MULTI_BY_USER_B",
                "factor": "9.9",
                "template": self.load_template.internal_id,
            },
        )
        self.assertIn(b"not allowed", response.content.lower())
        self.load.refresh_from_db()
        self.assertNotEqual(self.load.description, "TAMPERED_MULTI_BY_USER_B")


@override_settings(DEBUG=False)
class EnetraToolViewPermissionsTest(TestCase):
    """
    Tests that the basic scenario view (ports:enetra_tool, tool_base.html)
    is only reachable by users associated with the scenario's project.

    DEBUG is forced to False so the test exercises the production auth path
    (HttpResponseForbidden), rather than the DEBUG-only diagnostic responses
    in ports.views.enetra_tool.
    """

    @classmethod
    def setUpTestData(cls):
        cls.member_user = User.objects.create_user("tool_member", password="pass")
        cls.outsider_user = User.objects.create_user("tool_outsider", password="pass")
        cls.project = Project.objects.create(name="Tool Test Projekt")
        cls.scenario = Scenario.objects.create(name="Tool Test Scenario", project=cls.project)
        cls.project_group, _ = Group.objects.get_or_create(name=cls.project.group_name())
        cls.project_group.user_set.add(cls.member_user)
        assign_perm("details", cls.project_group, cls.project)
        # outsider_user is intentionally never added to project_group

    def tool_url(self):
        return reverse(
            "ports:enetra_tool",
            kwargs={"scenario_internal_id": self.scenario.internal_id},
        )

    def test_project_member_can_access_scenario_view(self):
        self.client.force_login(self.member_user)
        response = self.client.get(self.tool_url())
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ports/tool_base.html")

    def test_user_outside_project_cannot_access_scenario_view(self):
        self.client.force_login(self.outsider_user)
        response = self.client.get(self.tool_url())
        self.assertEqual(response.status_code, 403)
