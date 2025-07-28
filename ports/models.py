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
from django.dispatch import receiver

logger = logging.getLogger("django_ports")


# TODO: Discuss maybe gurantee proper id generation by overwriting bulk_create method?
def generate_id(scenario_id: uuid.UUID, id: int) -> uuid.UUID:
    """Generate an UUID4 from a given scenario_id and the internal integer id"""
    return uuid.uuid5(scenario_id, str(id))


# Create your models here.
class Scenario(models.Model):
    # Use a uuid as primary key. Scenarios can be created anywhere, without access to db and checking duplicate of id
    # e.g. a scenario specific id can be used to create the next url in the frontend.
    # this also means does not have to keep track of the last/max id
    # composite primary keys would be nice, but are not fully supported currently,
    # especially in regards to ContentType
    # https://docs.djangoproject.com/en/5.2/ref/contrib/contenttypes/
    # https://docs.djangoproject.com/en/5.2/topics/composite-primary-key/
    # and ForeignKeys
    # An easier approach is using uuid5 on all child elements.
    # our scenario id gives us our namespace
    # the internal id gives us the name
    # together we generate a uuid5, unique to each table
    # the uuid therefore simply reflects the scenario -id and internal id
    # to copy it we only have to exchange each uuid5 by generating(new_scenario_id, id)
    # changing ids or scenarios ids is not enforced right now but forbidden
    # if needed it could be enforced on the db level via custom migration
    # this way we can apply all relation fields on all objects,  and also use ContentTypes
    id = models.UUIDField(primary_key=True, auto_created=True, default=uuid.uuid4)
    name = models.TextField(blank=False, null=True)
    # Set to now() on the database side
    created_at = models.DateTimeField(auto_now_add=True, db_default=Now())
    # Related name + tells django not to create a reverse relation for user, e.g. user.scenario_set
    manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, default=None, null=True, related_name="+"
    )


class ScenarioItem(models.Model):
    """All items which have a scenario as reference inherit some common functionality"""

    id = models.UUIDField(primary_key=True, auto_created=False)
    # Scenario specific id, which stays the same over scenarios
    internal_id = models.IntegerField()
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, db_index=True)

    class Meta:
        abstract = True  # Important: makes this a base, not a table
        constraints = [
            models.UniqueConstraint(
                fields=["internal_id", "scenario"],
                name="%(class)s_unique_internal_id_per_scenario",
            )
        ]
        ordering = ["scenario", "internal_id"]  # Optional: share common Meta options

    def save(self, *args, **kwargs):
        generated_id = generate_id(self.scenario.id, self.internal_id)
        if not self.id:
            self.id = generated_id
        else:
            assert self.id == generated_id
        super().save(*args, **kwargs)


# Can an Area serve multiple purposes? (yes)
# Can an Area serve the same, e.g. solar, multiple times? (yes)
class Area(ScenarioItem):
    geom = models.PolygonField()


class Solar(ScenarioItem):
    area = models.ForeignKey(
        Area,
        on_delete=models.CASCADE,
    )


# --------------------------------------------------------------------------------
class Settings(ScenarioItem):
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

    id = models.UUIDField(primary_key=True, auto_created=False)
    # Scenario specific id, which stays the same over scenarios
    internal_id = models.IntegerField()
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, db_index=True)

    name = models.TextField(blank=False, null=True)
    file = models.FileField(upload_to=settings.UPLOAD_PATH)
    # This lets us attach a task to any object with a uuid
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField(null=False)
    # on_delete=CASCADE is the behaviour of GenericForeignKey.
    # Changing that is possible via signals
    content_object = GenericForeignKey("content_type", "object_id")

    @receiver(models.signals.pre_delete, sender=Scenario)
    def auto_delete_results_on_delete(sender, instance, **kwargs):
        """Delete the scenario results folder if the scenario is deleted from the database

        :param sender: Model which sends signal
        :param instance: instance of a model which gets deleted
        :param kwargs: other arguments
        :return:
        """
        if instance.task_id is not None:
            try:
                shutil.rmtree(Path(settings.UPLOAD_PATH) / str(instance.task_id))
            except FileNotFoundError:
                # The Folder does not exist. That is not a problem
                logger.debug(f"File {instance} does not exists and could not be deleted ")
