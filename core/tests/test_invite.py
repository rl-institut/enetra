"""
Tests for the project-invite flow (core.models.Invite, core.views.handle_invite,
core.views.user_rights_view, ports.views.remove_project_user).

Setup mirrors how create_project (ports/views.py) wires permissions: a Group
named project.group_name() holds "view"/"details" object permissions on the
project, and members are added to that group.
"""

import datetime

from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from guardian.shortcuts import assign_perm

from core.models import Invite
from ports.models import Area
from ports.models import Project
from ports.models import Scenario


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InviteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user(
            "invite_manager", email="manager@example.com", password="pass"
        )
        cls.project = Project.objects.create(name="Invite Project", manager=cls.manager)
        cls.group = Group.objects.create(name=cls.project.group_name())
        assign_perm("view", cls.group, cls.project)
        assign_perm("details", cls.group, cls.project)

    def user_rechte_url(self):
        return reverse("core:user_rechte", kwargs={"project_internal_id": self.project.internal_id})

    def invite_url(self, token):
        return reverse("core:invite") + f"?project_token={token}"

    def create_invite(self, email="invitee@example.com", role="editor"):
        self.client.force_login(self.manager)
        response = self.client.post(self.user_rechte_url(), {"email": email, "role": role})
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        return Invite.objects.get(payload__email=email)

    def test_invite_token_is_created(self):
        invite = self.create_invite()
        self.assertTrue(invite.token)
        self.assertEqual(invite.group, self.group)
        self.assertEqual(invite.created_by, self.manager)
        self.assertEqual(invite.uses, 0)
        self.assertEqual(invite.max_uses, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(invite.token, mail.outbox[0].body)

    def test_successful_token_usage_adds_invited_user_to_group(self):
        invite = self.create_invite(email="invitee@example.com")
        invitee = User.objects.create_user("invitee", email="invitee@example.com", password="pass")
        self.client.force_login(invitee)
        response = self.client.get(self.invite_url(invite.token))
        self.assertEqual(response.status_code, 302)
        invite.refresh_from_db()
        self.assertEqual(invite.uses, 1)
        self.assertIn(invitee, self.group.user_set.all())

    def test_failed_token_usage_for_user_with_different_email(self):
        invite = self.create_invite(email="invitee@example.com")
        User.objects.create_user("invitee", email="invitee@example.com", password="pass")
        other_user = User.objects.create_user(
            "someone_else", email="someone_else@example.com", password="pass"
        )
        self.client.force_login(other_user)
        response = self.client.get(self.invite_url(invite.token))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"You are not the user the invite is for", response.content)
        invite.refresh_from_db()
        self.assertEqual(invite.uses, 0)
        self.assertNotIn(other_user, self.group.user_set.all())

    def test_deletion_of_access_by_project_manager(self):
        invite = self.create_invite(email="invitee@example.com")
        invitee = User.objects.create_user("invitee", email="invitee@example.com", password="pass")
        self.client.force_login(invitee)
        self.client.get(self.invite_url(invite.token))
        self.assertIn(invitee, self.group.user_set.all())

        self.client.force_login(self.manager)
        response = self.client.post(
            reverse(
                "ports:api_remove_project_user",
                kwargs={
                    "project_internal_id": self.project.internal_id,
                    "email": invitee.email,
                },
            )
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn(invitee, self.group.user_set.all())

    def test_removal_by_project_manager_deletes_users_content_in_project(self):
        invite = self.create_invite(email="invitee@example.com")
        invitee = User.objects.create_user("invitee", email="invitee@example.com", password="pass")
        self.client.force_login(invitee)
        self.client.get(self.invite_url(invite.token))
        self.assertIn(invitee, self.group.user_set.all())

        scenario = Scenario.objects.create(
            name="Shared Scenario", project=self.project, manager=self.manager
        )
        own_area = Area.objects.create(
            scenario=scenario,
            name="Invitee Area",
            area_type=Area.AreaTypeChoices.BUILDING,
            manager=invitee,
        )
        # Content managed by someone else must survive the removal.
        managers_area = Area.objects.create(
            scenario=scenario,
            name="Manager Area",
            area_type=Area.AreaTypeChoices.BUILDING,
            manager=self.manager,
        )

        self.client.force_login(self.manager)
        response = self.client.post(
            reverse(
                "ports:api_remove_project_user",
                kwargs={
                    "project_internal_id": self.project.internal_id,
                    "email": invitee.email,
                },
            )
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(Area.objects.filter(pk=own_area.pk).exists())
        self.assertTrue(Area.objects.filter(pk=managers_area.pk).exists())
        self.assertNotIn(invitee, self.group.user_set.all())

    def test_failed_reuse_of_token(self):
        invite = self.create_invite(email="invitee@example.com")
        invitee = User.objects.create_user("invitee", email="invitee@example.com", password="pass")
        self.client.force_login(invitee)
        first = self.client.get(self.invite_url(invite.token))
        self.assertEqual(first.status_code, 302)

        second = self.client.get(self.invite_url(invite.token))
        self.assertEqual(second.status_code, 400)
        invite.refresh_from_db()
        self.assertEqual(invite.uses, 1)

    def test_message_for_already_logged_in_user_who_is_not_the_token_user(self):
        invite = self.create_invite(email="invitee@example.com")
        User.objects.create_user("invitee", email="invitee@example.com", password="pass")
        logged_in_user = User.objects.create_user(
            "already_logged_in", email="already_logged_in@example.com", password="pass"
        )
        self.client.force_login(logged_in_user)
        response = self.client.get(self.invite_url(invite.token))
        self.assertEqual(response.content, b"You are not the user the invite is for")

    def test_expired_token_usage(self):
        invite = self.create_invite(email="invitee@example.com")
        invite.expires_at = timezone.now() - datetime.timedelta(days=1)
        invite.save()
        invitee = User.objects.create_user("invitee", email="invitee@example.com", password="pass")
        self.client.force_login(invitee)
        response = self.client.get(self.invite_url(invite.token))
        self.assertEqual(response.status_code, 400)
        invite.refresh_from_db()
        self.assertEqual(invite.uses, 0)
        self.assertNotIn(invitee, self.group.user_set.all())

    def test_nonexistent_token_returns_404(self):
        response = self.client.get(self.invite_url("does-not-exist"))
        self.assertEqual(response.status_code, 404)

    def test_invite_for_new_user_redirects_to_signup(self):
        invite = self.create_invite(email="brand_new@example.com")
        response = self.client.get(self.invite_url(invite.token))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:signup"), response.url)
        self.assertIn(f"project_token={invite.token}", response.url)
        invite.refresh_from_db()
        self.assertEqual(invite.uses, 0)

    def test_invited_user_cant_invite_other_users(self):
        member = User.objects.create_user(
            "editor_member", email="editor_member@example.com", password="pass"
        )
        self.group.user_set.add(member)
        self.client.force_login(member)
        invites_before = Invite.objects.count()
        response = self.client.post(
            self.user_rechte_url(), {"email": "new_invitee@example.com", "role": "editor"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Nur der Projektmanager kann Nutzer hinzufügen",
            response.context["form"].non_field_errors(),
        )
        self.assertEqual(Invite.objects.count(), invites_before)

    def test_invited_user_cant_delete_other_users(self):
        member = User.objects.create_user(
            "editor_member", email="editor_member@example.com", password="pass"
        )
        self.group.user_set.add(member)
        other = User.objects.create_user(
            "other_member", email="other_member@example.com", password="pass"
        )
        self.group.user_set.add(other)

        self.client.force_login(member)
        response = self.client.post(
            reverse(
                "ports:api_remove_project_user",
                kwargs={
                    "project_internal_id": self.project.internal_id,
                    "email": other.email,
                },
            )
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn(other, self.group.user_set.all())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
# Force production branch of ports.views.enetra_tool, which enforces permissions
# with redirects/403s instead of DEBUG-only inline messages.
@override_settings(DEBUG=False)
class InviteAcceptedAccessTests(TestCase):
    """A non-invited user must not be able to view a project's pages; an invited
    user gains access only after actually accepting the invite (core:invite)."""

    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user(
            "access_manager", email="access_manager@example.com", password="pass"
        )
        cls.project = Project.objects.create(name="Access Project", manager=cls.manager)
        cls.scenario = Scenario.objects.create(
            name="Access Scenario", project=cls.project, manager=cls.manager
        )
        cls.group = Group.objects.create(name=cls.project.group_name())
        assign_perm("view", cls.group, cls.project)
        assign_perm("details", cls.group, cls.project)

    def user_rechte_url(self):
        return reverse("core:user_rechte", kwargs={"project_internal_id": self.project.internal_id})

    def project_overview_url(self):
        return reverse(
            "core:project_overview", kwargs={"project_internal_id": self.project.internal_id}
        )

    def scenario_tool_url(self):
        return reverse(
            "ports:enetra_tool", kwargs={"scenario_internal_id": self.scenario.internal_id}
        )

    def invite_url(self, token):
        return reverse("core:invite") + f"?project_token={token}"

    def create_invite(self, email):
        self.client.force_login(self.manager)
        response = self.client.post(self.user_rechte_url(), {"email": email, "role": "editor"})
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        return Invite.objects.get(payload__email=email)

    def test_non_invited_user_cant_access_project_and_scenario_pages(self):
        outsider = User.objects.create_user(
            "outsider", email="outsider@example.com", password="pass"
        )
        self.client.force_login(outsider)

        response = self.client.get(self.project_overview_url())
        self.assertEqual(response.status_code, 403)

        response = self.client.get(self.scenario_tool_url())
        self.assertEqual(response.status_code, 403)

    def test_invited_user_who_accepted_can_access_project_and_scenario_pages(self):
        invite = self.create_invite(email="accepted_invitee@example.com")
        invitee = User.objects.create_user(
            "accepted_invitee", email="accepted_invitee@example.com", password="pass"
        )

        self.client.force_login(invitee)
        # Not accepted yet -> still no access
        response = self.client.get(self.project_overview_url())
        self.assertEqual(response.status_code, 403)
        response = self.client.get(self.scenario_tool_url())
        self.assertEqual(response.status_code, 403)

        accept_response = self.client.get(self.invite_url(invite.token))
        self.assertEqual(accept_response.status_code, 302)

        response = self.client.get(self.project_overview_url())
        self.assertEqual(response.status_code, 200)

        response = self.client.get(self.scenario_tool_url())
        self.assertEqual(response.status_code, 200)
