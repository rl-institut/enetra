import logging
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.db import models
from django.db.models.functions import Now
from django.db.models.signals import post_delete
from django.db.models.signals import post_save
from django.dispatch import Signal
from django.dispatch import receiver

logger = logging.getLogger("django_ports")


# Create your models here.
# Each set of scenario items is bundled via its scenario. The scenario has a simple BigInteger Id
class Scenario(models.Model):
    id = models.BigAutoField(primary_key=True, blank=True)
    # Scenario specific id, which stays the same over scenarios
    internal_id = models.UUIDField(
        db_index=True, unique=True, null=False, blank=True, default=uuid.uuid4
    )
    name = models.TextField(blank=False, null=True)
    # Set to now() on the database side
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Aggregator of all scenario items. Used to track changes
    items_updated_at = models.DateTimeField(db_default=Now(), editable=False)
    # Related name + tells django not to create a reverse relation for user, e.g. user.scenario_set
    manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, default=None, null=True, related_name="+"
    )

    class Meta:
        permissions = (("foo", "Assign foo"),)


class ScenarioItem(models.Model):
    """All items which have a scenario as reference inherit some common functionality"""

    scenario_id: int
    id = models.BigAutoField(primary_key=True, auto_created=True, editable=False)
    # Scenario specific id, which stays the same over scenarios
    internal_id = models.UUIDField(db_index=True, null=False, default=uuid.uuid4)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, db_index=True)
    name = models.TextField(blank=True, null=True, max_length=200)
    description = models.TextField(blank=True, null=True)

    # Set to now() on the database side
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, default=None, null=True, related_name="+"
    )

    updated_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        default=None,
        null=True,
        related_name="+",
        editable=False,
    )
    scenarioitem_post_delete = Signal()
    scenarioitem_post_save = Signal()

    class Meta:
        abstract = True  # Important: makes this a base, not a table
        constraints = [
            models.UniqueConstraint(
                fields=["internal_id", "scenario"],
                name="%(class)s_unique_internal_id_per_scenario",
            )
        ]
        ordering = ["scenario", "id"]  # Optional: share common Meta options

    """
    The scenario contains different types of models, which should share some common functionality.
    For example the scenario should store information when the last update of a scenario item
    happened. This means explicitly updating the scenario on each change or let signals handle
    that. Since we dont want to connect each ModelSignal individually, we create merge scenarioitem
    signals e.g. scenarioitem_post_delete
    this way we can implement functions which only listen to this signal.
    The connection is done in the appconfig.ready() function automatically for all subclasses of
    ScenarioItem
    """

    @classmethod
    def _connect_signals(cls):
        post_delete.connect(cls._send_scenarioitem_post_delete, sender=cls)
        post_save.connect(cls._send_scenarioitem_post_save, sender=cls)

    def _send_scenarioitem_post_delete(sender, instance, **kwargs):
        ScenarioItem.scenarioitem_post_delete.send(sender=sender, instance=instance)

    def _send_scenarioitem_post_save(sender, instance, **kwargs):
        ScenarioItem.scenarioitem_post_save.send(sender=sender, instance=instance)

    def save(self, *args, **kwargs):
        if self.pk is None and self.updated_user is None:
            self.updated_user = self.manager
        super().save(*args, **kwargs)

    def model_name(self):
        return self._meta.model_name


@receiver(ScenarioItem.scenarioitem_post_delete)
def update_scenario_post_delete(sender: type[ScenarioItem], instance: ScenarioItem, **kwargs):
    """Create a DeletedItem"""
    # NOTE: Be sure to handle bouncing signals which can introduce infinite signal loops
    # Create a DeletedItem with all Scenario item values
    # We use post_delete to only create these items when the item is really deleted.
    # using pre_delete leads to errors if deletion fails
    if sender == DeletedItem:
        return
    deleted_item = DeletedItem(
        **{f.name: getattr(instance, f.name) for f in ScenarioItem._meta.fields if f.name != "id"}
    )
    content_type = ContentType.objects.get(
        app_label=sender._meta.app_label, model=sender._meta.model_name
    )
    deleted_item.content_type = content_type
    deleted_item.save()


@receiver(ScenarioItem.scenarioitem_post_save)
def update_scenario_post_save(sender, instance, **kwargs):
    """Update the scenario if a ScenarioItem was created"""
    scenario = instance.scenario
    scenario.items_updated_at = instance.updated_at
    scenario.save()


class DeletedItem(ScenarioItem):
    """Store basic information about deleted item.

    This gives explicit access to updates of items, which are not in the database anymore.
    Example:
    User Tom is shown an item Foo of id 123. User Jim deletes Foo 123.
    The scenario gets an updated with a new timestamp updated at. Toms site polls the scenario
    for changes.
    A change is detected. Searching for updates would not show that 123 is deleted. but searching
    DeletedItem.objects.filter(update_at__gt=last_update) shows a Foo item 123 was deleted.
    a signal can be passed to Toms frontend 'updateFoo123'. This refetches the instance and shows
    tom. "This item has been deleted".
    """

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    pass


# Can an Area serve multiple purposes? (yes)
# Can an Area serve the same usage, e.g. solar, multiple times? (yes)
class Area(ScenarioItem):
    geom = models.PolygonField(null=True, blank=False)


class Solar(ScenarioItem):
    area = models.ForeignKey(
        Area,
        on_delete=models.CASCADE,
    )

    def __str__(self):
        return f"Solaranlage {(self.id or '')}"


# --------------------------------------------------------------------------------
class Setting(ScenarioItem):
    settings = models.JSONField(default=dict)


class UploadedFile(ScenarioItem):
    """
    Model representing an uploaded file associated with a scenario.

    Attributes:
        scenario (Scenario): The scenario to which the file is associated. Foreign key to the Scenario model.
        file (FileField): The actual file field storing the uploaded file, with the specified upload path.

    Usage Example:
        To create a new UploadedFile instance and associate it with a scenario:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> uploaded_file_instance = UploadedFile(scenario=scenario_instance, file=my_file)
        >>> uploaded_file_instance.save()
    """

    name = models.TextField(blank=False, null=True)
    file = models.FileField(upload_to=settings.UPLOAD_PATH)
    # This lets us attach any object to the uploaded file
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField()
    # on_delete=CASCADE is the behaviour of GenericForeignKey.
    # Changing that is possible via signals
    content_object = GenericForeignKey("content_type", "object_id")

    @receiver(models.signals.post_delete, sender=Scenario)
    def auto_delete_results_on_delete(sender, instance, **kwargs):
        """Delete the scenario results folder if the scenario is deleted from the database

        :param sender: Model which sends signal
        :param instance: instance of a model which gets deleted
        :param kwargs: other arguments
        :return:
        """
        try:
            shutil.rmtree(Path(settings.UPLOAD_PATH) / str(instance.task_id))
        except FileNotFoundError:
            # The Folder does not exist. That is not a problem
            logger.debug(f"File {instance} does not exists and could not be deleted ")
