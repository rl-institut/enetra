import datetime
import uuid

import pytz
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models.functions import Now


# Create your models here.
class Task(models.Model):
    class Type(models.TextChoices):
        RUN_SIMULATION = "Simulation"

    celery_task_id = models.UUIDField(db_index=True, null=True, blank=True, default=uuid.uuid4)
    # tasks may have a tree like structure
    parent_task = models.ForeignKey("Task", null=True, on_delete=models.CASCADE)
    type = models.CharField(max_length=100, choices=Type.choices)

    # This lets us attach a task to any object with a charfield
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField()
    # on_delete=CASCADE is the behaviour of GenericForeignKey.
    # Changing that is possible via signals
    content_object = GenericForeignKey("content_type", "object_id")


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
