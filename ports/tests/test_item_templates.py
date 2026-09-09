"""
Tests for ItemTemplate ("Vorlagen") CRUD via ObjectTemplatesView, and for applying
a template onto ScenarioItem forms (DetailsView) via the `?template=` query param.

Covers:
- A template can be created when at least "name" is given.
- A template can be accessed (GET), posted (POST) and deleted (DELETE).
- A template can be applied to an implementation (e.g. Generator) via `?template=`.
- Only templates owned by the requesting user can be applied or accessed.
- Templates from other users cannot be accessed.
- Applying a template overwrites all form fields except internal_id, id and manager,
  for both single and multi component selections.
- Created templates show up in the "Vorlagen" list rendered by the komponenten_tab.

"""

from uuid import uuid4

from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.gis.geos import GEOSGeometry
from django.test import Client
from django.test import TestCase
from django.urls import reverse

from ports.models import Area
from ports.models import Generator
from ports.models import GeneratorTemplate
from ports.models import Project
from ports.models import Scenario
from ports.models import StorageTemplate


class ItemTemplateTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("templateuser", password="pass", is_superuser=True)
        cls.other_user = User.objects.create_user("otheruser", password="pass", is_superuser=True)

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

        cls.generator_template = GeneratorTemplate.objects.create(
            manager=cls.user,
            name="My Generator Template",
            carrier=GeneratorTemplate.CarrierChoices.OIL,
            power_kw=42.0,
            efficiency=0.75,
        )
        cls.generator_template_other = GeneratorTemplate.objects.create(
            manager=cls.other_user,
            name="Other Users Generator Template",
            carrier=GeneratorTemplate.CarrierChoices.OIL,
            power_kw=99.0,
            efficiency=0.5,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def templates_url(self, model_name, scenario=None):
        kwargs = {"model": model_name}
        if scenario is not None:
            kwargs["scenario_internal_id"] = scenario.internal_id
        return reverse("ports:templates", kwargs=kwargs)

    def template_create_url(self, model_name, scenario=None):
        kwargs = {"model": model_name}
        if scenario is not None:
            kwargs["scenario_internal_id"] = scenario.internal_id
        return reverse("ports:template_create", kwargs=kwargs)

    def details_url(self, model_name):
        return reverse(
            "ports:details",
            kwargs={
                "scenario_internal_id": self.scenario.internal_id,
                "model": model_name,
            },
        )


class TemplateCreateTest(ItemTemplateTestBase):
    def test_create_generator_template_without_scenario(self):
        initial_count = GeneratorTemplate.objects.filter(manager=self.user).count()
        url = self.template_create_url("generatortemplate")
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_component_template.html",
        )
        self.assertEqual(
            GeneratorTemplate.objects.filter(manager=self.user).count(),
            initial_count + 1,
        )
        created = response.context["instance"]
        # Name is auto-assigned by the view; it is never blank.
        self.assertTrue(created.name)
        self.assertEqual(created.manager, self.user)

    def test_create_generator_template_with_scenario(self):
        url = self.template_create_url("generatortemplate", scenario=self.scenario)
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        created = response.context["instance"]
        self.assertTrue(created.name)

    def test_create_generator_template_with_scenario_no_htmx(self):
        url = self.template_create_url("generatortemplate", scenario=self.scenario)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        created = response.context["instance"]
        self.assertTrue(created.name)

    def test_create_storage_template(self):
        initial_count = StorageTemplate.objects.filter(manager=self.user).count()
        url = self.template_create_url("storagetemplate")
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            StorageTemplate.objects.filter(manager=self.user).count(),
            initial_count + 1,
        )

    def test_created_template_appears_in_item_templates_context(self):
        url = self.template_create_url("generatortemplate")
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        created = response.context["instance"]
        names = [t.name for t in response.context["item_templates"]]
        self.assertIn(created.name, names)

    def test_create_unauthenticated_is_denied(self):
        self.client.logout()
        url = self.template_create_url("generatortemplate")
        response = self.client.post(url)
        # login_required redirects to the login page
        self.assertEqual(response.status_code, 302)


class TemplateFormNameRequiredTest(TestCase):
    """
    Direct unit tests of TemplateFormFactory: name is the only required field.
    """

    def setUp(self):
        self.user = User.objects.create_user("formuser", password="pass")

    def test_name_is_required(self):
        from ports.forms import TemplateFormFactory

        Form = TemplateFormFactory(GeneratorTemplate)
        form = Form(data={"internal_id": str(uuid4()), "name": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_only_name_is_sufficient(self):
        from ports.forms import TemplateFormFactory

        Form = TemplateFormFactory(GeneratorTemplate)
        form = Form(data={"internal_id": str(uuid4()), "name": "Only a name"})
        self.assertTrue(form.is_valid(), form.errors)


class TemplateAccessTest(ItemTemplateTestBase):
    def test_manager_can_get_own_template(self):
        url = self.templates_url("generatortemplate")
        response = self.client.get(
            url,
            {"internal_id": str(self.generator_template.internal_id)},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_component_template.html",
        )
        self.assertEqual(response.context["instance"], self.generator_template)

    def test_other_users_template_cannot_be_accessed(self):
        client = Client(raise_request_exception=False)
        client.force_login(self.user)
        url = self.templates_url("generatortemplate")
        response = client.get(url, {"internal_id": str(self.generator_template_other.internal_id)})
        self.assertEqual(response.status_code, 404)

    def test_unknown_template_raises_does_not_exist(self):
        response = self.client.get(
            self.templates_url("generatortemplate"), {"internal_id": str(uuid4())}
        )
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_cannot_access_template(self):
        self.client.logout()
        url = self.templates_url("generatortemplate")
        response = self.client.get(url, {"internal_id": str(self.generator_template.internal_id)})
        self.assertEqual(response.status_code, 302)


class TemplatePostTest(ItemTemplateTestBase):
    def test_post_updates_own_template(self):
        url = self.templates_url("generatortemplate")
        data = {
            "internal_id": str(self.generator_template.internal_id),
            "name": "Updated Template Name",
            "power_kw": "55.5",
            "efficiency": "0.9",
            "carrier": GeneratorTemplate.CarrierChoices.DIESEL,
        }
        response = self.client.post(url, data, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("success"))
        self.generator_template.refresh_from_db()
        self.assertEqual(self.generator_template.name, "Updated Template Name")
        self.assertAlmostEqual(self.generator_template.power_kw, 55.5)
        self.assertAlmostEqual(self.generator_template.efficiency, 0.9)

    def test_post_blank_name_is_rejected(self):
        original_name = self.generator_template.name
        url = self.templates_url("generatortemplate")
        data = {
            "internal_id": str(self.generator_template.internal_id),
            "name": "",
            "power_kw": "10",
        }
        response = self.client.post(url, data, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertIn("errors", response.context)
        self.generator_template.refresh_from_db()
        self.assertEqual(self.generator_template.name, original_name)

    def test_manager_field_cannot_be_overwritten_via_post(self):
        url = self.templates_url("generatortemplate")
        data = {
            "internal_id": str(self.generator_template.internal_id),
            "name": "Still mine",
            "manager": str(self.other_user.pk),
        }
        response = self.client.post(url, data, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.generator_template.refresh_from_db()
        self.assertEqual(self.generator_template.manager, self.user)

    def test_posting_to_other_users_template_raises_does_not_exist(self):
        """See NOTE in TemplateAccessTest.test_other_users_template_cannot_be_accessed."""
        response = self.client.post(
            self.templates_url("generatortemplate"),
            {
                "internal_id": str(self.generator_template_other.internal_id),
                "name": "TAMPERED",
            },
        )
        response = self.assertEqual(response.status_code, 404)
        self.generator_template_other.refresh_from_db()
        self.assertNotEqual(self.generator_template_other.name, "TAMPERED")


class TemplateDeleteTest(ItemTemplateTestBase):
    def test_manager_can_delete_own_template(self):
        template = GeneratorTemplate.objects.create(manager=self.user, name="Delete Me")
        url = self.templates_url("generatortemplate")
        response = self.client.delete(
            f"{url}?internal_id={template.internal_id}", HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            GeneratorTemplate.objects.filter(internal_id=template.internal_id).exists()
        )

    def test_delete_of_other_users_template_raises_does_not_exist(self):
        """See NOTE in TemplateAccessTest.test_other_users_template_cannot_be_accessed."""
        response = self.client.delete(
            f"{self.templates_url('generatortemplate')}"
            f"?internal_id={self.generator_template_other.internal_id}"
        )
        response = self.assertEqual(response.status_code, 404)
        self.assertTrue(
            GeneratorTemplate.objects.filter(
                internal_id=self.generator_template_other.internal_id
            ).exists()
        )


class TemplateApplyToComponentTest(ItemTemplateTestBase):
    """
    Applying a template via `?template=<internal_id>` on DetailsView.get/multi_get.
    """

    def test_single_component_get_prefills_from_own_template(self):
        url = self.details_url("generator")
        response = self.client.get(
            url,
            {
                "internal_ids": str(self.generator.internal_id),
                "template": str(self.generator_template.internal_id),
            },
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial.get("power_kw"), self.generator_template.power_kw)
        self.assertEqual(form.initial.get("efficiency"), self.generator_template.efficiency)
        self.assertEqual(form.initial.get("carrier"), self.generator_template.carrier)

    def test_single_component_template_never_overwrites_internal_id_or_manager(self):
        url = self.details_url("generator")
        response = self.client.get(
            url,
            {
                "internal_ids": str(self.generator.internal_id),
                "template": str(self.generator_template.internal_id),
            },
        )
        form = response.context["form"]
        # internal_id in the (unbound) initial dict comes from the instance itself
        # (added by ModelForm.__init__ via model_to_dict), never from the template.
        self.assertEqual(form.initial.get("internal_id"), self.generator.internal_id)
        self.assertNotEqual(form.initial.get("internal_id"), self.generator_template.internal_id)
        # manager/id are not form fields at all - they can never be set this way.
        self.assertNotIn("manager", form.fields)
        self.assertNotIn("id", form.fields)
        # The instance bound to the form is still the original generator.
        self.assertEqual(form.instance.pk, self.generator.pk)
        self.assertEqual(form.instance.internal_id, self.generator.internal_id)

    def test_multi_component_get_prefills_from_own_template(self):
        url = self.details_url("generator")
        internal_ids = f"{self.generator.internal_id},{self.generator2.internal_id}"
        response = self.client.get(
            url,
            {
                "internal_ids": internal_ids,
                "template": str(self.generator_template.internal_id),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "ports/partials/detail_sidebar/detail_sidebar_component_multi.html",
        )
        form = response.context["form"]
        # multi_get binds the merged+template data directly as form data.
        self.assertEqual(form.data.get("power_kw"), self.generator_template.power_kw)
        self.assertEqual(form.data.get("efficiency"), self.generator_template.efficiency)
        self.assertEqual(form.data.get("carrier"), self.generator_template.carrier)

    def test_multi_component_template_data_excludes_internal_id_and_manager(self):
        url = self.details_url("generator")
        internal_ids = f"{self.generator.internal_id},{self.generator2.internal_id}"
        response = self.client.get(
            url,
            {
                "internal_ids": internal_ids,
                "template": str(self.generator_template.internal_id),
            },
        )
        form = response.context["form"]
        self.assertNotEqual(form.data.get("internal_id"), str(self.generator_template.internal_id))
        # "manager" is not a field of this form, so even though model_to_dict()
        # put a raw "manager" key into the merged data dict, the form never uses it.
        self.assertNotIn("manager", form.fields)
        self.assertIsNone(form.data.get("manager"))

    def test_other_users_template_is_not_applied_single(self):
        """Only templates owned by the requesting user may be used via ?template=."""
        url = self.details_url("generator")
        response = self.client.get(
            url,
            {
                "internal_ids": str(self.generator.internal_id),
                "template": str(self.generator_template_other.internal_id),
            },
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        # None of the foreign template's values leaked into the initial data -
        # the generator's own (untouched) values are shown instead.
        self.assertEqual(form.initial.get("power_kw"), self.generator.power_kw)
        self.assertEqual(form.initial.get("efficiency"), self.generator.efficiency)
        self.assertNotEqual(form.initial.get("power_kw"), self.generator_template_other.power_kw)
        self.assertNotEqual(
            form.initial.get("efficiency"), self.generator_template_other.efficiency
        )

    def test_other_users_template_is_not_applied_multi(self):
        url = self.details_url("generator")
        internal_ids = f"{self.generator.internal_id},{self.generator2.internal_id}"
        response = self.client.get(
            url,
            {
                "internal_ids": internal_ids,
                "template": str(self.generator_template_other.internal_id),
            },
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertNotEqual(form.data.get("power_kw"), self.generator_template_other.power_kw)

    def test_unknown_template_query_param_is_ignored(self):
        url = self.details_url("generator")
        response = self.client.get(
            url,
            {
                "internal_ids": str(self.generator.internal_id),
                "template": str(uuid4()),
            },
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial.get("power_kw"), self.generator.power_kw)


class TemplateInKomponentenTabListTest(ItemTemplateTestBase):
    def user_template_list_url(self):
        return reverse("ports:user_template_list")

    def test_created_template_is_listed_for_owner(self):
        response = self.client.get(self.user_template_list_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.generator_template.name)
        self.assertContains(response, str(self.generator_template.internal_id))

    def test_other_users_template_is_not_listed(self):
        response = self.client.get(self.user_template_list_url())
        self.assertNotContains(response, self.generator_template_other.name)
        self.assertNotContains(response, str(self.generator_template_other.internal_id))

    def test_new_template_shows_up_as_clickable_list_item_after_creation(self):
        url = self.template_create_url("generatortemplate")
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        created = response.context["instance"]
        # The create response embeds the refreshed #user-template-list partial
        # (detail_sidebar_component_template.html includes it when request.method == "POST").
        self.assertContains(response, created.name)
        self.assertContains(response, f"button-list-item-{created.internal_id}")

    def test_deleted_template_no_longer_listed(self):
        template = GeneratorTemplate.objects.create(manager=self.user, name="Temp For Delete")
        url = self.templates_url("generatortemplate")
        self.client.delete(f"{url}?internal_id={template.internal_id}", HTTP_HX_REQUEST="true")
        response = self.client.get(self.user_template_list_url())
        self.assertNotContains(response, "Temp For Delete")

    def test_unauthenticated_cannot_list_templates(self):
        self.client.logout()
        response = self.client.get(self.user_template_list_url())
        # user is redirect to login
        response = self.assertEqual(response.status_code, 302)
