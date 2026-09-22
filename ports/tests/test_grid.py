"""
Tests for Grid functionality: creation via the api_create endpoint (from a
single area), and attaching a grid to areas via the DetailsView post/multi_post
paths (single area vs multi area selection).
"""

from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.gis.geos import GEOSGeometry
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from guardian.shortcuts import assign_perm

from ports.models import Area
from ports.models import DuplicateGridCarrierError
from ports.models import Grid
from ports.models import Project
from ports.models import Scenario


class GridTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # See ports/tests/test_detail_view.py for the shared-object gotcha:
        # always refresh_from_db() before asserting on cls.* objects after a POST.
        cls.user = User.objects.create_user("testuser", password="pass", is_superuser=True)

        cls.project = Project.objects.create(name="Test Projekt")
        cls.scenario = Scenario.objects.create(name="Test Scenario", project=cls.project)
        group, _ = Group.objects.get_or_create(name=cls.project.group_name())
        cls.user.groups.add(group)

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

    def setUp(self):
        self.client.force_login(self.user)

    def api_create_url(self, model_name="grid"):
        return reverse(
            "ports:api_create",
            kwargs={"scenario_internal_id": self.scenario.internal_id, "model": model_name},
        )

    def api_remove_carrier_url(self, model_name="grid"):
        return reverse(
            "ports:api_remove_carrier",
            kwargs={"scenario_internal_id": self.scenario.internal_id, "model": model_name},
        )

    def details_url(self, model_name="area"):
        return reverse(
            "ports:details",
            kwargs={"scenario_internal_id": self.scenario.internal_id, "model": model_name},
        )


class GridCreateFromAreaTest(GridTestBase):
    """Creating a Grid from a single Area via ports:api_create."""

    def test_create_grid_from_single_area(self):
        url = self.api_create_url()
        data = {
            "name": "Grid 1",
            "carrier": Grid.CarrierChoices.ELECTRICITY,
            "feed_in": "on",
        }
        response = self.client.post(url, data, QUERY_STRING=f"area={self.area.internal_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("success"))

        grid = Grid.objects.get(scenario=self.scenario, name="Grid 1")
        self.assertEqual(grid.carrier, Grid.CarrierChoices.ELECTRICITY)
        self.assertTrue(grid.feed_in)
        self.assertIn(self.area, grid.areas.all())
        self.assertEqual(response.get("HX-Trigger"), f"refresh-{self.area.internal_id}")

    def test_create_grid_without_area(self):
        url = self.api_create_url()
        data = {
            "name": "Orphan Grid",
            "carrier": Grid.CarrierChoices.GAS,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("success"))

    def test_create_grid_invalid_form(self):
        url = self.api_create_url()
        data = {
            "name": "",
            "carrier": "not-a-real-carrier",
        }
        response = self.client.post(url, data, QUERY_STRING=f"area={self.area.internal_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload.get("success"))
        self.assertFalse(Grid.objects.filter(scenario=self.scenario).exists())

    def test_create_grid_without_permission_is_rejected(self):
        other_user = User.objects.create_user("otheruser", password="pass")
        self.client.force_login(other_user)
        url = self.api_create_url()
        data = {
            "name": "Grid by non-manager",
            "carrier": Grid.CarrierChoices.HEAT,
        }
        response = self.client.post(url, data, QUERY_STRING=f"area={self.area.internal_id}")
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertFalse(payload.get("success"))
        self.assertFalse(
            Grid.objects.filter(scenario=self.scenario, name="Grid by non-manager").exists()
        )


class GridPostFromAreaTest(GridTestBase):
    """Posting grid_<carrier> selections from the Area detail form, single vs multi."""

    def setUp(self):
        super().setUp()
        self.grid = Grid.objects.create(
            scenario=self.scenario,
            name="Electricity Grid",
            carrier=Grid.CarrierChoices.ELECTRICITY,
            manager=self.user,
        )
        self.other_grid = Grid.objects.create(
            scenario=self.scenario,
            name="Second Electricity Grid",
            carrier=Grid.CarrierChoices.ELECTRICITY,
            manager=self.user,
        )

    def test_post_area_single_selects_grid(self):
        url = self.details_url("area")
        data = {
            "internal_id": str(self.area.internal_id),
            "name": self.area.name,
            "usage": Area.BuildingUsageChoices.OFFICE,
            "grid_electricity": str(self.grid.internal_id),
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("success"))
        self.assertFalse(response.context.get("errors"))

        self.assertTrue(
            Grid.objects.filter(internal_id=self.grid.internal_id, areas=self.area).exists()
        )
        self.assertFalse(
            Grid.objects.filter(internal_id=self.other_grid.internal_id, areas=self.area).exists()
        )

    def test_post_area_single_reassigns_grid(self):
        self.grid.areas.add(self.area)
        url = self.details_url("area")
        data = {
            "internal_id": str(self.area.internal_id),
            "name": self.area.name,
            "usage": Area.BuildingUsageChoices.OFFICE,
            "grid_electricity": str(self.other_grid.internal_id),
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("success"))

        self.assertFalse(
            Grid.objects.filter(internal_id=self.grid.internal_id, areas=self.area).exists()
        )
        self.assertTrue(
            Grid.objects.filter(internal_id=self.other_grid.internal_id, areas=self.area).exists()
        )

    def test_post_area_multi_selects_grid_for_all_areas(self):
        url = self.details_url("area")
        internal_ids = f"{self.area.internal_id},{self.area2.internal_id}"
        data = {
            "internal_ids": internal_ids,
            "grid_electricity": str(self.grid.internal_id),
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("success"))
        self.assertFalse(response.context.get("errors"))

        self.assertTrue(
            Grid.objects.filter(internal_id=self.grid.internal_id, areas=self.area).exists()
        )
        self.assertTrue(
            Grid.objects.filter(internal_id=self.grid.internal_id, areas=self.area2).exists()
        )

    def test_post_area_multi_overwrites_existing_per_area_grid(self):
        # Bulk save deletes existing (area, carrier) links for all selected
        # areas before adding the new grid, even if one area already had a
        # different grid of that carrier assigned.
        self.other_grid.areas.add(self.area)
        url = self.details_url("area")
        internal_ids = f"{self.area.internal_id},{self.area2.internal_id}"
        data = {
            "internal_ids": internal_ids,
            "grid_electricity": str(self.grid.internal_id),
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("success"))

        self.assertFalse(
            Grid.objects.filter(internal_id=self.other_grid.internal_id, areas=self.area).exists()
        )
        self.assertTrue(
            Grid.objects.filter(internal_id=self.grid.internal_id, areas=self.area).exists()
        )
        self.assertTrue(
            Grid.objects.filter(internal_id=self.grid.internal_id, areas=self.area2).exists()
        )


class GridGetInitialSelectionTest(GridTestBase):
    """GET on the Area details form should return the correct initial grid selection."""

    def setUp(self):
        super().setUp()
        self.grid = Grid.objects.create(
            scenario=self.scenario,
            name="Electricity Grid",
            carrier=Grid.CarrierChoices.ELECTRICITY,
            manager=self.user,
        )
        self.other_grid = Grid.objects.create(
            scenario=self.scenario,
            name="Second Electricity Grid",
            carrier=Grid.CarrierChoices.ELECTRICITY,
            manager=self.user,
        )

    def test_get_area_single_returns_selected_grid(self):
        self.grid.areas.add(self.area)
        url = self.details_url("area")
        response = self.client.get(url, {"internal_ids": str(self.area.internal_id)})
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial.get("grid_electricity"), self.grid)

    def test_get_area_single_two_grids_same_carrier(self):
        self.grid.areas.add(self.area)
        assert self.grid.carrier == self.other_grid.carrier
        with self.assertRaises(DuplicateGridCarrierError), transaction.atomic():
            self.other_grid.areas.add(self.area)
        url = self.details_url("area")
        response = self.client.get(url, {"internal_ids": str(self.area.internal_id)})
        self.assertEqual(response.status_code, 200)

    def test_get_area_multi_common_grid_returned(self):
        self.grid.areas.add(self.area, self.area2)
        url = self.details_url("area")
        internal_ids = f"{self.area.internal_id},{self.area2.internal_id}"
        response = self.client.get(url, {"internal_ids": internal_ids})
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.data.get("grid_electricity"), self.grid)

    def test_get_area_multi_mixed_grid_not_returned(self):
        # Areas have different grids of the same carrier -> mixed value,
        # field should be left unset (no single common selection).
        self.grid.areas.add(self.area)
        self.other_grid.areas.add(self.area2)
        url = self.details_url("area")
        internal_ids = f"{self.area.internal_id},{self.area2.internal_id}"
        response = self.client.get(url, {"internal_ids": internal_ids})
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertNotIn("grid_electricity", form.data)


class GridRemoveCarrierTest(GridTestBase):
    """Removing all areas' link to a grid of a given carrier via ports:api_remove_carrier."""

    def setUp(self):
        super().setUp()
        self.grid = Grid.objects.create(
            scenario=self.scenario,
            name="Electricity Grid",
            carrier=Grid.CarrierChoices.ELECTRICITY,
            manager=self.user,
        )
        self.grid.areas.add(self.area, self.area2)

    def test_remove_carrier_from_multiple_areas(self):
        url = self.api_remove_carrier_url()
        data = {
            "area_internal_ids": f"{self.area.internal_id},{self.area2.internal_id}",
            "carrier": Grid.CarrierChoices.ELECTRICITY,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("success"))
        self.assertEqual(response.get("HX-Trigger"), f"refresh-{self.area.internal_id}")

        self.assertFalse(
            Grid.objects.filter(internal_id=self.grid.internal_id, areas=self.area).exists()
        )
        self.assertFalse(
            Grid.objects.filter(internal_id=self.grid.internal_id, areas=self.area2).exists()
        )

    def test_remove_carrier_from_single_area_keeps_other_area(self):
        url = self.api_remove_carrier_url()
        data = {
            "area_internal_ids": str(self.area.internal_id),
            "carrier": Grid.CarrierChoices.ELECTRICITY,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("success"))

        self.assertFalse(
            Grid.objects.filter(internal_id=self.grid.internal_id, areas=self.area).exists()
        )
        self.assertTrue(
            Grid.objects.filter(internal_id=self.grid.internal_id, areas=self.area2).exists()
        )

    def test_remove_carrier_invalid_carrier(self):
        url = self.api_remove_carrier_url()
        data = {
            "area_internal_ids": str(self.area.internal_id),
            "carrier": "not-a-real-carrier",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json().get("success"))
        self.assertTrue(
            Grid.objects.filter(internal_id=self.grid.internal_id, areas=self.area).exists()
        )

    def test_remove_carrier_without_permission_is_rejected(self):
        other_user = User.objects.create_user("otheruser", password="pass")
        self.client.force_login(other_user)
        url = self.api_remove_carrier_url()
        data = {
            "area_internal_ids": str(self.area.internal_id),
            "carrier": Grid.CarrierChoices.ELECTRICITY,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            Grid.objects.filter(internal_id=self.grid.internal_id, areas=self.area).exists()
        )


class GridAreaAuthorizationTest(TestCase):
    """
    ApiView.create (grid creation) and ApiView.remove_carrier are gated by
    scenario-level "details" authorization, but the areas they mutate are
    supplied directly in the request body (?area=... / area_internal_ids).
    A user with scenario access but no area-level "details" permission for
    a specific area must not be able to sneak that area's internal_id into
    either request and have it mutated anyway.
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("grid_perm_owner", password="pass")
        cls.outsider = User.objects.create_user("grid_perm_outsider", password="pass")
        cls.project = Project.objects.create(name="Perm Test Projekt")
        cls.scenario = Scenario.objects.create(
            name="Perm Test Scenario", manager=cls.owner, project=cls.project
        )
        cls.project_group, _ = Group.objects.get_or_create(name=cls.project.group_name())
        cls.project_group.user_set.add(cls.owner)
        cls.project_group.user_set.add(cls.outsider)
        # Grant scenario-level "details" so ApiView.dispatch's scenario check
        # passes for the outsider; area-level "details" is deliberately
        # withheld, which is the permission actually being tested here.
        assign_perm("details", cls.project_group, cls.scenario)

        cls.area = Area.objects.create(
            scenario=cls.scenario,
            name="Owner Area",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
            geom=GEOSGeometry("SRID=4326;POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"),
            manager=cls.owner,
        )
        cls.grid = Grid.objects.create(
            scenario=cls.scenario,
            name="Owner Grid",
            carrier=Grid.CarrierChoices.ELECTRICITY,
            manager=cls.owner,
        )
        cls.grid.areas.add(cls.area)

    def api_create_url(self):
        return reverse(
            "ports:api_create",
            kwargs={"scenario_internal_id": self.scenario.internal_id, "model": "grid"},
        )

    def api_remove_carrier_url(self):
        return reverse(
            "ports:api_remove_carrier",
            kwargs={"scenario_internal_id": self.scenario.internal_id, "model": "grid"},
        )

    def test_create_grid_rejects_area_without_area_permission(self):
        self.client.force_login(self.outsider)
        url = self.api_create_url()
        data = {
            "name": "Sneaked-in Grid",
            "carrier": Grid.CarrierChoices.GAS,
        }
        response = self.client.post(url, data, QUERY_STRING=f"area={self.area.internal_id}")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            Grid.objects.filter(scenario=self.scenario, name="Sneaked-in Grid").exists()
        )

    def test_remove_carrier_rejects_area_without_area_permission(self):
        self.client.force_login(self.outsider)
        url = self.api_remove_carrier_url()
        data = {
            "area_internal_ids": str(self.area.internal_id),
            "carrier": Grid.CarrierChoices.ELECTRICITY,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            Grid.objects.filter(internal_id=self.grid.internal_id, areas=self.area).exists()
        )
