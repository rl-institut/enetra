"""
Tests for the account-settings page (core.views.account_settings) and account
deletion (core.views.delete_account).
"""

from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse

from core.models import Invite
from ports.models import Area
from ports.models import Project
from ports.models import Scenario


class AccountSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            "settings_user", email="settings_user@example.com", password="old-pass-123"
        )

    def setUp(self):
        self.client.force_login(User.objects.get(pk=self.user.pk))

    def settings_url(self):
        return reverse("core:einstellungen")

    def change_account(self, **overrides):
        data = {
            "email": "settings_user@example.com",
            "first_name": "First",
            "last_name": "Last",
            "current_password": "old-pass-123",
        }
        data.update(overrides)
        return self.client.post(self.settings_url(), data)

    def test_get_prefills_account_and_password_forms(self):
        self.user.first_name = "First"
        self.user.last_name = "Last"
        self.user.save()
        response = self.client.get(self.settings_url())
        account_form = response.context["account_form"]
        self.assertEqual(account_form.instance, self.user)
        self.assertEqual(account_form.initial["email"], self.user.email)
        self.assertEqual(account_form.initial["first_name"], "First")
        self.assertEqual(account_form.initial["last_name"], "Last")
        self.assertEqual(response.context["password_change_form"].user, self.user)

    def test_name_and_email_change_without_password_fails(self):
        response = self.change_account(
            email="new_email@example.com", first_name="New", current_password=""
        )
        self.assertFalse(response.context["account_form"].is_valid())
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "settings_user@example.com")
        self.assertEqual(self.user.first_name, "")

    def test_name_and_email_change_with_password_works(self):
        response = self.change_account(email="new_email@example.com", first_name="New")
        self.assertTrue(response.context["account_form"].is_valid())
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new_email@example.com")
        self.assertEqual(self.user.first_name, "New")

    def test_username_is_synced_when_email_changes(self):
        self.change_account(email="synced_email@example.com")
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "synced_email@example.com")

    def test_email_cant_be_changed_to_existing_email(self):
        # username is always kept equal to email.lower() by signup/account forms
        User.objects.create_user("taken@example.com", email="taken@example.com", password="pass")
        response = self.change_account(email="taken@example.com")
        self.assertFalse(response.context["account_form"].is_valid())
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "settings_user@example.com")

    def test_password_change_without_correct_password_fails(self):
        response = self.client.post(
            self.settings_url(),
            {
                "old_password": "wrong-password",
                "new_password1": "new-pass-456",
                "new_password2": "new-pass-456",
            },
        )
        self.assertFalse(response.context["password_change_form"].is_valid())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-pass-123"))

    def test_password_change_with_correct_password_works(self):
        response = self.client.post(
            self.settings_url(),
            {
                "old_password": "old-pass-123",
                "new_password1": "new-pass-456",
                "new_password2": "new-pass-456",
            },
        )
        self.assertTrue(response.context["password_change_form"].is_valid())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("new-pass-456"))


class AccountDeletionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "delete_me", email="delete_me@example.com", password="pass"
        )
        self.other_user = User.objects.create_user(
            "other_user", email="other_user@example.com", password="pass"
        )

        self.own_project = Project.objects.create(name="Own Project", manager=self.user)
        self.own_scenario = Scenario.objects.create(
            name="Own Scenario", project=self.own_project, manager=self.user
        )
        self.own_area = Area.objects.create(
            scenario=self.own_scenario,
            name="Own Area",
            area_type=Area.AreaTypeChoices.BUILDING,
            manager=self.user,
        )

        self.other_project = Project.objects.create(name="Other Project", manager=self.other_user)
        self.other_scenario = Scenario.objects.create(
            name="Other Scenario", project=self.other_project, manager=self.other_user
        )
        # Item managed by the other user, but last touched by the user being deleted:
        # must survive deletion, only its updated_user reference should be nullified.
        self.other_area_touched_by_user = Area.objects.create(
            scenario=self.other_scenario,
            name="Other Area",
            area_type=Area.AreaTypeChoices.BUILDING,
            manager=self.other_user,
            updated_user=self.user,
        )

        self.group = Group.objects.create(name="delete-account-invite-group")
        self.invite = Invite.objects.create(
            token="delete-account-token",
            group=self.group,
            created_by=self.user,
            payload={"email": "someone@example.com", "role": "editor"},
        )

    def test_account_deletion_removes_user_and_owned_content(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("core:delete_user"))
        self.assertEqual(response.status_code, 302)

        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Project.objects.filter(pk=self.own_project.pk).exists())
        self.assertFalse(Scenario.objects.filter(pk=self.own_scenario.pk).exists())
        self.assertFalse(Area.objects.filter(pk=self.own_area.pk).exists())

    def test_account_deletion_cascades_invites_created_by_user(self):
        self.client.force_login(self.user)
        self.client.post(reverse("core:delete_user"))
        self.assertFalse(Invite.objects.filter(pk=self.invite.pk).exists())

    def test_account_deletion_nullifies_references_on_content_of_other_users(self):
        self.client.force_login(self.user)
        self.client.post(reverse("core:delete_user"))

        self.other_area_touched_by_user.refresh_from_db()
        self.assertIsNone(self.other_area_touched_by_user.updated_user)
        self.assertEqual(self.other_area_touched_by_user.manager, self.other_user)

    def test_user_cant_delete_someone_else(self):
        self.client.force_login(self.other_user)
        response = self.client.post(reverse("core:delete_user"))
        self.assertEqual(response.status_code, 302)

        self.assertFalse(User.objects.filter(pk=self.other_user.pk).exists())
        # The other user's own deletion request must not touch the first user's data
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertTrue(Project.objects.filter(pk=self.own_project.pk).exists())
        self.assertTrue(Scenario.objects.filter(pk=self.own_scenario.pk).exists())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ForgotPasswordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "forgot_pw_user", email="forgot_pw_user@example.com", password="pass"
        )

    def test_forgot_password_form_sends_email(self):
        response = self.client.post(reverse("core:forgot_password"), {"email": self.user.email})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.user.email, mail.outbox[0].to)
