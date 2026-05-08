"""
Tests for DetailsView — GET, POST, DELETE, and CREATE for
single and multi-instance modes across Area, Load, and Generator.
"""

from django.contrib.auth.models import User
from django.contrib.gis.geos import GEOSGeometry
from django.test import TestCase
from django.urls import reverse

from ports.models import Area
from ports.models import Generator
from ports.models import Load
from ports.models import LoadTemplate
from ports.models import Scenario


class DetailsViewBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Runs once per test class (not per test method); DB writes in individual
        # tests are rolled back automatically. However, cls.* objects are shared
        # Python references — an in-memory mutation (e.g. self.load.name = "x")
        # without a matching DB save is NOT rolled back and leaks to later tests
        # in alphabetical order. Always call refresh_from_db() before relying on
        # a shared object's fields, or work on a local copy instead.
        cls.user = User.objects.create_user("testuser", password="pass")
        cls.scenario = Scenario.objects.create(name="Test Scenario")

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

        cls.load_template = LoadTemplate.objects.create(
            scenario=cls.scenario,
            name="Template",
            timeseries=[],
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


class DetailsViewPostTest(DetailsViewBase):
    def test_post_load_single_valid(self):
        url = self.details_url("load")
        data = {
            "internal_id": str(self.load.internal_id),
            "name": "Updated Load",
            "factor": "2.0",
            "template": self.load_template.pk,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("success", response.context)
        self.assertNotIn("errors", response.context)
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
        self.assertIn("success", response.context)
        self.assertNotIn("errors", response.context)
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
        self.assertIn("success", response.context)
        self.assertNotIn("errors", response.context)
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
        self.assertIn("errors", response.context)
        self.assertNotIn("success", response.context)
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
            "template": self.load_template.pk,
            "factor": "not-a-float",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("errors", response.context)
        self.assertNotIn("success", response.context)
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
            "template": self.load_template.pk,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        assert response.context, response.context
        self.assertIn("success", response.context)
        self.assertNotIn("errors", response.context)
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
        self.assertIn("success", response.context)
        self.assertNotIn("errors", response.context)
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
        self.assertIn("success", response.context)
        self.assertNotIn("errors", response.context)
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
            "ports/partials/detail_sidebar/detail_deleted.html",
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
            "ports/partials/detail_sidebar/detail_deleted.html",
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
