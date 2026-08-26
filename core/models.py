import datetime

import pytz
from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db import transaction
from django.db.models.functions import Now
from django.utils import timezone


# Create your models here.
class Task(models.Model):
    class Type(models.TextChoices):
        RUN_SIMULATION = "Simulation"

    # tasks may have a tree like structure
    parent_task = models.ForeignKey("Task", null=True, on_delete=models.CASCADE)
    type = models.CharField(max_length=100, choices=Type.choices)

    # This lets us attach a task to any object with a charfield
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField()
    # on_delete=CASCADE is the behaviour of GenericForeignKey.
    # Changing that is possible via signals
    content_object = GenericForeignKey("content_type", "object_id")


# Maybe move to choices.py
class Role(models.TextChoices):
    MANAGER = "manager", "Ersteller"
    EDITOR = "editor", "Bearbeiter"
    OBSERVER = "observer", "Beobachter"


class Progress(models.Model):
    class Status(models.TextChoices):
        FINISHED = "Fertig"
        FAILED = "Fehlgeschlagen"
        RUNNING = "Gestartet"
        WAITING = "Wartet"

    task = models.ForeignKey(Task, null=False, on_delete=models.CASCADE)
    created = models.DateTimeField(null=False, auto_now_add=True, db_default=Now())
    status = models.CharField(choices=Status.choices)
    total_work = models.IntegerField(default=1, null=False)
    current_work = models.IntegerField(default=0, null=False)

    errors = models.JSONField(default=list, null=True)

    def estimate_duration(self) -> None | float:
        """Return the number of minutes estimated to finish based on
        linear extrapolation of the current and total work"""
        if self.current_work == 0:
            return None
        passed_duration_minutes = (
            datetime.datetime.now(pytz.UTC) - self.created
        ).total_seconds() / 60
        # Upper bound for estimation is first guess of duration when no progress was
        speed = self.current_work / passed_duration_minutes
        further_duration_minutes = (self.total_work - self.current_work) / speed
        return further_duration_minutes

    def get_progress(self) -> float:
        """Return a progress, which should be between 0 and 100."""
        try:
            return self.current_work / self.total_work * 100
        except ZeroDivisionError:
            return 0

    def set_success(self):
        self.status = Progress.Status.FINISHED
        self.current_work = self.total_work
        self.save()

    def set_failed(self):
        self.status = Progress.Status.FAILED
        self.save()

    def reset(self):
        self.status = Progress.Status.WAITING
        self.current_work = 0
        self.total_work = 1
        self.created = Now()
        self.errors = []
        self.save()


class InviteError(Exception):
    """Base class for errors when redeeming an invite token."""


class InviteEmailMismatchError(InviteError):
    """Raised when the redeeming user's email does not match the invite payload."""

    def __init__(self) -> None:
        super().__init__("Email from invite is not identical with this user")


class InviteExpiredError(InviteError):
    """Raised when an invite is redeemed after its `expires_at` timestamp."""

    def __init__(self) -> None:
        super().__init__("Token expired")


class InviteUsedUpError(InviteError):
    """Raised when an invite is redeemed more than `max_uses` times."""

    def __init__(self) -> None:
        super().__init__("Token is used up")


# Used to track invite status. this allows single use tokens for login or accepting of
# invites.
# Project User Management is supposed to show invite status. This is not possible
# via static tokenization of user and project
# since the user might not exist yet we store the email in the payload
class Invite(models.Model):
    token = models.CharField(max_length=64, unique=True)
    group = models.ForeignKey(Group, on_delete=models.CASCADE)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    expires_at = models.DateTimeField(null=True)
    max_uses = models.PositiveIntegerField(default=1)
    uses = models.PositiveIntegerField(default=0)
    payload = models.JSONField(default=dict, null=True)

    @classmethod
    @transaction.atomic
    def add_user_to_group_from_token(cls, token: str, user: User) -> None:
        """Add the user to the invite's group, raising an InviteError if the token
        is mismatched, expired, or already used up."""
        # Lock the row during the transaction so uses is properly checked and incremented
        invite = Invite.objects.select_for_update().get(token=token)
        if user.email != invite.payload["email"]:
            raise InviteEmailMismatchError
        if invite.expires_at and invite.expires_at < timezone.make_aware(datetime.datetime.now()):
            raise InviteExpiredError
        if invite.uses >= invite.max_uses:
            raise InviteUsedUpError
        invite.uses += 1
        # TODO: Different permission levels on project, e.g. Roles?
        invite.group.user_set.add(user)
        invite.save()
