"""
Tests for the Timeseries rename/upload views (`timeseries_rename`, `timeseries_upload`)
and the ApiView delete-in-use guard for Timeseries.
"""

import re
from uuid import uuid4

from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.gis.geos import GEOSGeometry
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from guardian.shortcuts import assign_perm

from ports.models import Area
from ports.models import Load
from ports.models import Project
from ports.models import Scenario
from ports.models import Timeseries


def csv_file(name="data.csv", values=(1, 2, 3)):
    content = "\n".join(str(v) for v in values).encode()
    return SimpleUploadedFile(name, content, content_type="text/csv")


class TimeseriesRenameViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user("manager", password="pass", is_superuser=True)
        cls.project = Project.objects.create(name="Test Projekt")
        cls.scenario = Scenario.objects.create(name="Test Scenario", project=cls.project)
        Group.objects.get_or_create(name=cls.project.group_name())

        cls.timeseries = Timeseries.objects.create(
            scenario=cls.scenario,
            name="Original name",
            manager=cls.manager,
            timeseries={"values": [1, 2, 3], "timestep_minutes": 15},
            spec_load=1.0,
        )

    def rename_url(self, internal_id=None):
        return reverse(
            "ports:timeseries_rename",
            kwargs={
                "scenario_internal_id": self.scenario.internal_id,
                "internal_id": internal_id or self.timeseries.internal_id,
            },
        )

    def test_get_renders_unbound_form_with_current_name(self):
        self.client.force_login(self.manager)
        response = self.client.get(self.rename_url())
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ports/partials/timeseries_rename_modal.html")
        self.assertContains(response, "Original name")
        self.assertContains(response, "Speichern")
        self.assertNotContains(response, "Gespeichert")

    def test_post_valid_renames_and_includes_updated_list_item(self):
        self.client.force_login(self.manager)
        response = self.client.post(self.rename_url(), {"name": "New name"})
        self.assertEqual(response.status_code, 200)
        self.timeseries.refresh_from_db()
        self.assertEqual(self.timeseries.name, "New name")
        self.assertContains(response, "New name")
        self.assertContains(response, "Gespeichert")

    def test_post_empty_name_is_rejected(self):
        self.client.force_login(self.manager)
        response = self.client.post(self.rename_url(), {"name": ""})
        self.assertEqual(response.status_code, 200)
        self.timeseries.refresh_from_db()
        self.assertEqual(self.timeseries.name, "Original name")
        self.assertContains(response, "Fehlgeschlagen")

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self.rename_url())
        self.assertEqual(response.status_code, 302)

    def test_user_without_project_permission_is_forbidden(self):
        other = User.objects.create_user("stranger", password="pass")
        self.client.force_login(other)
        response = self.client.post(self.rename_url(), {"name": "Hijacked"})
        self.assertEqual(response.status_code, 403)
        self.timeseries.refresh_from_db()
        self.assertEqual(self.timeseries.name, "Original name")

    def test_non_manager_with_project_permission_is_forbidden_without_crashing(self):
        other = User.objects.create_user("colleague", password="pass")
        group = Group.objects.get(name=self.project.group_name())
        group.user_set.add(other)
        assign_perm("details", group, self.project)

        self.client.force_login(other)
        response = self.client.post(self.rename_url(), {"name": "Renamed by colleague"})
        self.assertEqual(response.status_code, 403)
        self.timeseries.refresh_from_db()
        self.assertEqual(self.timeseries.name, "Original name")


class TimeseriesUploadViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user("manager", password="pass", is_superuser=True)
        cls.project = Project.objects.create(name="Test Projekt")
        cls.scenario = Scenario.objects.create(name="Test Scenario", project=cls.project)
        Group.objects.get_or_create(name=cls.project.group_name())

        cls.original_timeseries = Timeseries.objects.create(
            scenario=cls.scenario,
            name="Original timeseries",
            manager=cls.manager,
            timeseries={"values": [1], "timestep_minutes": 15},
            spec_load=1.0,
        )
        cls.area = Area.objects.create(
            scenario=cls.scenario,
            name="Area 1",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
            geom=GEOSGeometry("SRID=4326;POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"),
        )
        cls.load = Load.objects.create(
            scenario=cls.scenario,
            name="Load 1",
            area=cls.area,
            timeseries=cls.original_timeseries,
            factor=1.0,
        )
        cls.area2 = Area.objects.create(
            scenario=cls.scenario,
            name="Area 2",
            manager=cls.manager,
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
            geom=GEOSGeometry("SRID=4326;POLYGON((1 1, 2 1, 2 2, 1 2, 1 1))"),
        )
        cls.load2 = Load.objects.create(
            scenario=cls.scenario,
            name="Load 2",
            area=cls.area2,
            manager=cls.manager,
            timeseries=cls.original_timeseries,
            factor=1.0,
        )

    def upload_to_load_url(self):
        return reverse(
            "ports:timeseries_upload",
            kwargs={"scenario_internal_id": self.scenario.internal_id, "from_model": "load"},
        )

    def upload_standalone_url(self):
        return reverse(
            "ports:timeseries_upload",
            kwargs={"scenario_internal_id": self.scenario.internal_id},
        )

    def test_upload_attached_to_single_load_updates_load_timeseries(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            self.upload_to_load_url(),
            {
                "internal_id": str(self.load.internal_id),
                "timestep_minutes": "5",
                "template_file": csv_file("profile.csv", values=(1, 2, 3)),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.load.refresh_from_db()
        self.assertNotEqual(self.load.timeseries_id, self.original_timeseries.id)
        self.assertEqual(self.load.timeseries.name, "profile.csv")
        self.assertEqual(self.load.timeseries.timeseries["values"], [1.0, 2.0, 3.0])
        self.assertEqual(response["HX-Trigger"], f"refresh-template-{self.load.internal_id}")

    def test_upload_without_load_creates_standalone_timeseries(self):
        self.client.force_login(self.manager)
        before_count = Timeseries.objects.filter(scenario=self.scenario).count()
        response = self.client.post(
            self.upload_standalone_url(),
            {
                "timestep_minutes": "15",
                "template_file": csv_file("standalone.csv", values=(4, 5, 6)),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Timeseries.objects.filter(scenario=self.scenario).count(), before_count + 1
        )
        self.load.refresh_from_db()
        self.assertEqual(self.load.timeseries_id, self.original_timeseries.id)
        self.assertNotIn("HX-Trigger", response)

    def test_upload_invalid_file_type_is_rejected(self):
        self.client.force_login(self.manager)
        before_count = Timeseries.objects.filter(scenario=self.scenario).count()
        response = self.client.post(
            self.upload_standalone_url(),
            {
                "timestep_minutes": "15",
                "template_file": SimpleUploadedFile(
                    "notes.txt", b"1,2,3", content_type="text/plain"
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dateityp")
        self.assertEqual(Timeseries.objects.filter(scenario=self.scenario).count(), before_count)

    def test_missing_internal_id_for_load_upload_returns_400(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            self.upload_to_load_url(),
            {"timestep_minutes": "15", "template_file": csv_file()},
        )
        self.assertEqual(response.status_code, 400)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.manager)
        response = self.client.get(self.upload_standalone_url())
        self.assertEqual(response.status_code, 405)

    def test_upload_attached_to_multiple_loads_updates_all_loads(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            self.upload_to_load_url(),
            {
                "internal_ids": f"{self.load.internal_id},{self.load2.internal_id}",
                "timestep_minutes": "15",
                "template_file": csv_file("multi.csv", values=(7, 8, 9)),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.load.refresh_from_db()
        self.load2.refresh_from_db()
        self.assertNotEqual(self.load.timeseries_id, self.original_timeseries.id)
        self.assertEqual(self.load.timeseries_id, self.load2.timeseries_id)
        self.assertEqual(self.load.timeseries.timeseries["values"], [7.0, 8.0, 9.0])
        self.assertEqual(self.load.updated_user, self.manager)
        self.assertEqual(self.load2.updated_user, self.manager)
        self.assertEqual(response["HX-Trigger"], f"refresh-template-{self.load.internal_id}")

    def test_upload_multiple_loads_with_unknown_id_returns_404(self):
        self.client.force_login(self.manager)
        before_count = Timeseries.objects.filter(scenario=self.scenario).count()
        response = self.client.post(
            self.upload_to_load_url(),
            {
                "internal_ids": f"{self.load.internal_id},{uuid4()}",
                "timestep_minutes": "15",
                "template_file": csv_file(),
            },
        )
        self.assertEqual(response.status_code, 404)
        self.load.refresh_from_db()
        self.assertEqual(self.load.timeseries_id, self.original_timeseries.id)
        self.assertEqual(Timeseries.objects.filter(scenario=self.scenario).count(), before_count)

    def test_upload_multiple_loads_without_permission_for_one_is_forbidden(self):
        # colleague manages load 1 but has no permission on area 2 (managed by self.manager)
        colleague = User.objects.create_user("colleague", password="pass")
        group = Group.objects.get(name=self.project.group_name())
        group.user_set.add(colleague)
        assign_perm("details", group, self.project)
        Load.objects.filter(pk=self.load.pk).update(manager=colleague)

        self.client.force_login(colleague)
        before_count = Timeseries.objects.filter(scenario=self.scenario).count()
        response = self.client.post(
            self.upload_to_load_url(),
            {
                "internal_ids": f"{self.load.internal_id},{self.load2.internal_id}",
                "timestep_minutes": "15",
                "template_file": csv_file(),
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["HX-Retarget"], "#templateError")
        self.load.refresh_from_db()
        self.load2.refresh_from_db()
        self.assertEqual(self.load.timeseries_id, self.original_timeseries.id)
        self.assertEqual(self.load2.timeseries_id, self.original_timeseries.id)
        self.assertEqual(Timeseries.objects.filter(scenario=self.scenario).count(), before_count)

    def test_upload_after_creating_loads_for_multiple_areas_applies_to_all_new_loads(self):
        """
        Selecting multiple areas and adding a load creates one load per area. The rendered
        multi load form must carry all new load ids, so the upload applies to every new load.
        """
        self.client.force_login(self.manager)
        create_response = self.client.post(
            reverse(
                "ports:details_create",
                kwargs={"scenario_internal_id": self.scenario.internal_id, "model": "load"},
            ),
            {"area_internal_ids": f"{self.area.internal_id},{self.area2.internal_id}"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(create_response.status_code, 200)
        new_loads = Load.objects.filter(scenario=self.scenario).exclude(
            pk__in=[self.load.pk, self.load2.pk]
        )
        self.assertEqual(new_loads.count(), 2)

        # The upload button posts the enclosing form, so use the rendered hidden internal_ids
        match = re.search(
            r'<input[^>]*name="internal_ids"[^>]*value="([^"]*)"',
            create_response.content.decode(),
        )
        self.assertIsNotNone(match, "multi load form does not render the internal_ids field")
        self.assertEqual(
            set(match.group(1).split(",")), {str(load.internal_id) for load in new_loads}
        )

        response = self.client.post(
            self.upload_to_load_url(),
            {
                "internal_ids": match.group(1),
                "timestep_minutes": "15",
                "template_file": csv_file("new_loads.csv", values=(3, 2, 1)),
            },
        )
        self.assertEqual(response.status_code, 200)
        timeseries_ids = set(new_loads.values_list("timeseries_id", flat=True))
        self.assertEqual(len(timeseries_ids), 1)
        timeseries = Timeseries.objects.get(pk=timeseries_ids.pop())
        self.assertEqual(timeseries.name, "new_loads.csv")
        # existing loads in the selected areas stay untouched
        self.load.refresh_from_db()
        self.load2.refresh_from_db()
        self.assertEqual(self.load.timeseries_id, self.original_timeseries.id)
        self.assertEqual(self.load2.timeseries_id, self.original_timeseries.id)


class ApiViewTimeseriesDeleteTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user("manager", password="pass", is_superuser=True)
        cls.project = Project.objects.create(name="Test Projekt")
        cls.scenario = Scenario.objects.create(name="Test Scenario", project=cls.project)
        Group.objects.get_or_create(name=cls.project.group_name())

        cls.used_timeseries = Timeseries.objects.create(
            scenario=cls.scenario,
            name="In use",
            manager=cls.manager,
            timeseries={"values": [1], "timestep_minutes": 15},
            spec_load=1.0,
        )
        cls.unused_timeseries = Timeseries.objects.create(
            scenario=cls.scenario,
            name="Unused",
            manager=cls.manager,
            timeseries={"values": [1], "timestep_minutes": 15},
            spec_load=1.0,
        )
        cls.area = Area.objects.create(
            scenario=cls.scenario,
            name="Area 1",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
            geom=GEOSGeometry("SRID=4326;POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"),
        )
        cls.load = Load.objects.create(
            scenario=cls.scenario,
            name="Load 1",
            area=cls.area,
            timeseries=cls.used_timeseries,
            factor=1.0,
        )

    def delete_url(self, timeseries):
        return reverse(
            "ports:api",
            kwargs={
                "scenario_internal_id": self.scenario.internal_id,
                "model": "timeseries",
                "internal_id": timeseries.internal_id,
            },
        )

    def test_delete_is_blocked_while_referenced_by_a_load(self):
        self.client.force_login(self.manager)
        response = self.client.delete(self.delete_url(self.used_timeseries))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
        self.assertTrue(Timeseries.objects.filter(pk=self.used_timeseries.pk).exists())

    def test_delete_succeeds_when_unused(self):
        self.client.force_login(self.manager)
        response = self.client.delete(self.delete_url(self.unused_timeseries))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertFalse(Timeseries.objects.filter(pk=self.unused_timeseries.pk).exists())

    def test_user_without_project_permission_is_forbidden(self):
        other = User.objects.create_user("stranger", password="pass")
        self.client.force_login(other)
        response = self.client.delete(self.delete_url(self.unused_timeseries))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Timeseries.objects.filter(pk=self.unused_timeseries.pk).exists())

    def test_non_manager_with_project_permission_is_forbidden_without_crashing(self):
        # Regression test: ApiView._check_permission() calls has_authorization() directly on
        # the Timeseries instance for every non-Project/Scenario model, which used to crash
        # for any non-manager (Timeseries has no Area relation to walk). It must now return
        # False — denying access — instead of raising, even for a user with project-level
        # permission, since Timeseries aren't scoped to an Area for per-item authorization yet.
        other = User.objects.create_user("colleague", password="pass")
        group = Group.objects.get(name=self.project.group_name())
        group.user_set.add(other)
        assign_perm("details", group, self.project)

        self.client.force_login(other)
        response = self.client.delete(self.delete_url(self.unused_timeseries))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Timeseries.objects.filter(pk=self.unused_timeseries.pk).exists())
