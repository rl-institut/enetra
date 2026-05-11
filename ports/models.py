import logging
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.db import models
from django.core.validators import MaxValueValidator
from django.core.validators import MinValueValidator
from django.db.models.functions import Now
from django.db.models.signals import post_delete
from django.db.models.signals import post_save
from django.db.transaction import atomic
from django.dispatch import Signal
from django.dispatch import receiver
from django.forms import ModelForm
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger("django_ports")


# Create your models here.
# Each set of scenario items is bundled via its scenario. The scenario has a simple BigInteger Id
class Scenario(models.Model):
    id = models.BigAutoField(primary_key=True, blank=True)
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

    @atomic()
    def safe_delete(self):
        """Delete Scenario by first deleting all references. When deleting the
        scenario in the usual way, django iterates over other models to delete
        them. this triggers post_delete which creates deletedItems. these
        deletedItems are not cleaned up by django. this is handled with this
        function. Maybe a better approach would be use a 'deleted' boolean flag
        per item or use custom delete functions on the models."""
        # iterate over all related objects
        for rel in self._meta.get_fields():
            if rel.one_to_many:  # reverse FK
                related_manager = getattr(self, rel.get_accessor_name())
                related_manager.all().delete()
        DeletedItem.objects.filter(scenario=self).delete()
        self.delete()

    def changed_event(self):
        return f"{self._meta.model_name}-{self.internal_id}-changed"


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
    def __init_subclass__(cls, **kwargs):
        """Connect nested classes to. _connect_signals is not fired for
        models based on ElectricComponent but __init_subclass__ is"""
        super().__init_subclass__(**kwargs)
        post_save.connect(cls._send_scenarioitem_post_save, sender=cls)
        post_delete.connect(cls._send_scenarioitem_post_delete, sender=cls)

    @classmethod
    def _connect_signals(cls):
        post_save.connect(cls._send_scenarioitem_post_save, sender=cls)
        post_delete.connect(cls._send_scenarioitem_post_delete, sender=cls)

    def _send_scenarioitem_post_delete(sender, instance, **kwargs):
        ScenarioItem.scenarioitem_post_delete.send(sender=sender, instance=instance)

    def _send_scenarioitem_post_save(sender, instance, **kwargs):
        ScenarioItem.scenarioitem_post_save.send(
            sender=sender, instance=instance, created=kwargs.get("created")
        )

    def save(self, *args, **kwargs):
        if self.pk is None and self.updated_user is None:
            self.updated_user = self.manager
        return super().save(*args, **kwargs)

    def model_name(self):
        return self._meta.model_name

    def model(self):
        return self._meta.model

    def verbose_name(self):
        return self._meta.verbose_name

    def __str__(self):
        return f"{self._meta.object_name}: {self.name if self.name is not None else self.id} ({self.scenario.name if self.scenario.name is not None else self.scenario_id})"

    def changed_event(self):
        return f"{self._meta.model_name}-{self.internal_id}-changed"

    def deleted_event(self):
        return f"{self._meta.model_name}-{self.internal_id}-deleted"

    def icon(self) -> str:
        """The cotton template used as icon for this model"""
        return "icon.circle_full"

    @classmethod
    def create_new(cls, scenario: Scenario, **kwargs):
        """Create a new instance of the object, with Model specific defaults and allowed user facing attributes"""
        raise NotImplementedError("Missing implementation of Model specific empty Instance")

    @classmethod
    def adjust_Form(
        cls, FormClass: type[ModelForm["ScenarioItem"]], instance: "ScenarioItem", **kwargs
    ) -> type[ModelForm]:
        return FormClass


@receiver(ScenarioItem.scenarioitem_post_delete)
def update_scenario_post_delete(sender: type[ScenarioItem], instance: ScenarioItem, **kwargs):
    """Create a DeletedItem"""
    # NOTE: Be sure to handle bouncing signals which can introduce infinite signal loops
    # Create a DeletedItem with all Scenario item values
    # We use post_delete to only create these items when the item is really deleted.
    # using pre_delete leads to errors if deletion fails
    if sender in [DeletedItem, ChangedItem]:
        return
    deleted_item = DeletedItem.from_scenario_item(instance)
    deleted_item.save()


@receiver(ScenarioItem.scenarioitem_post_save)
def update_scenario_post_save(sender, instance, **kwargs):
    """Update the scenario if a ScenarioItem was created"""
    scenario = instance.scenario
    scenario.items_updated_at = instance.updated_at
    scenario.save()


@receiver(ScenarioItem.scenarioitem_post_save)
def create_changeditem_post_save(sender, instance, **kwargs):
    """Create a ChangedItem"""
    # NOTE: Be sure to handle bouncing signals which can introduce infinite signal loops
    # Create a ChangedItem with all Scenarioitem values
    # We use post_save to only create these items when the item is really deleted.
    if sender in [DeletedItem, ChangedItem]:
        return
    # Item was created. Dont create a ChangedItem
    if kwargs.get("created"):
        return
    changed_item = ChangedItem.from_scenario_item(instance)
    changed_item.save()


class ChangedItem(ScenarioItem):
    """Store basic information about a changed item.

    This is an "easy" implementation, but its not very transparent to the developer.

    This gives explicit access to updates of items, which are not in the database anymore.
    This allows storing update information for more than the last update of an item
    """

    class Meta:
        # Explicit overwrite of ScenarioItem constraint. Multiple ChangedItems can share the same
        # internalId
        constraints = []
        ordering = ["scenario", "id"]  # Optional: share common Meta options

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)

    def __repr__(self):
        return f"Changed with id {self.id} of type {self.content_type} in scenario {self.scenario.id} with uuid {self.internal_id}"

    @classmethod
    def from_scenario_item(cls, item: ScenarioItem) -> "ChangedItem":
        changed_item = ChangedItem(
            **{f.name: getattr(item, f.name) for f in ScenarioItem._meta.fields if f.name != "id"}
        )
        content_type = ContentType.objects.get(
            app_label=item._meta.app_label, model=item._meta.model_name
        )
        changed_item.content_type = content_type
        return changed_item


class DeletedItem(ScenarioItem):
    """Store basic information about deleted item.

    This is an "easy" implementation, but its not very transparent to the developer.
    A better approach might be deleting ScenarioItems via custom delete method. This would be more performant since it allows bulk creation, and the signal could be "turned" off for Scenario deletes. Each model would need an implementation of safe delete, since related models would need safe deletion as well, for proper cascading.

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

    def __repr__(self):
        return f"DeletedItem with id {self.id} of type {self.content_type} in scenario {self.scenario.id} with uuid {self.internal_id}"

    @classmethod
    def from_scenario_item(cls, item: ScenarioItem) -> "DeletedItem":
        deleted_item = DeletedItem(
            **{f.name: getattr(item, f.name) for f in ScenarioItem._meta.fields if f.name != "id"}
        )
        content_type = ContentType.objects.get(
            app_label=item._meta.app_label, model=item._meta.model_name
        )
        deleted_item.content_type = content_type
        return deleted_item


# Can an Area serve multiple purposes? (yes)
# Can an Area serve the same usage, e.g. solar, multiple times? (yes)
class Area(ScenarioItem):
    class AreaTypeChoices(models.TextChoices):
        BUILDING = "building", "Gebäudefläche"
        OPEN = "open", "Freifläche"

    class OpenUsageChoices(models.TextChoices):
        PV = "pv", "Photovolatik"
        PARKING = "parking", "Parkfläche"
        GREEN = "green", "Grünfläche"

    class BuildingUsageChoices(models.TextChoices):
        OFFICE = "office", "Büro"
        STORAGE = "storage", "Lager"

    geom = models.PolygonField(null=True, blank=False)
    area_type = models.CharField(choices=AreaTypeChoices, null=True)
    usage = models.CharField(
        choices=OpenUsageChoices.choices + BuildingUsageChoices.choices,
        null=True,
        blank=True,
        default=None,
    )

    @classmethod
    def adjust_Form(
        cls, FormClass: type[ModelForm[ScenarioItem]], instance: "Area", **kwargs
    ) -> type[ModelForm]:
        FormClass.base_fields["usage"].required = True
        area_type = (instance and instance.area_type) or kwargs.get("area_type")
        if not area_type:
            raise MissingFormValueException(
                "The area form needs an area_type to show the correct choices. The area_type can be provided by the instance or as kwarg"
            )
        if area_type == Area.AreaTypeChoices.BUILDING:
            FormClass.base_fields["usage"].choices = Area.BuildingUsageChoices
        else:
            FormClass.base_fields["usage"].choices = Area.OpenUsageChoices
        return FormClass

    @classmethod
    def create_new(cls, scenario: Scenario, **kwargs):
        """Create a new instance of the object, with Model specific defaults and allowed user facing attributes"""
        # TODO: Refactor into model method so this function stays clean
        allowed_attributes = ["area_type"]
        extra_args = {}
        for att in allowed_attributes:
            extra_args[att] = kwargs.get(att)
        count = cls.objects.filter(scenario=scenario, area_type=extra_args["area_type"]).count()
        new_name = f"Neue Fläche {count + 1}"

        instance = cls(
            scenario=scenario,
            name=new_name,
            **extra_args,
            # TODO: manager=request.user
        )
        return instance


class LoadTemplate(ScenarioItem):
    """Template for timeseries, mostly power series"""

    timeseries = models.JSONField()
    spec_load = models.FloatField(  # some specific characteristic, calculated for timeseries
        default=None
    )


class Load(ScenarioItem):
    """Power timeseries, derived from LoadTemplate"""

    area = models.ForeignKey(Area, on_delete=models.CASCADE)
    template = models.ForeignKey(LoadTemplate, on_delete=models.CASCADE)
    factor = models.FloatField(default=1.0)  # scale template values

    @classmethod
    def adjust_Form(
        cls, FormClass: type[ModelForm["ScenarioItem"]], instance: "ScenarioItem", **kwargs
    ) -> type[ModelForm]:
        return FormClass

    @classmethod
    def create_new(
        cls, scenario: Scenario, area_internal_ids: list[uuid.UUID | str] = None, **kwargs
    ):
        if area_internal_ids is None:
            area_internal_ids = []
        areas = Area.objects.filter(scenario=scenario, internal_id__in=area_internal_ids)
        count = cls.objects.filter(scenario=scenario).count()
        template = LoadTemplate.objects.filter(scenario=scenario).first()

        if not template:
            template = LoadTemplate.objects.create(
                scenario=scenario, name="Empty template", timeseries=[], spec_load=0
            )
        loads = []
        for area in areas:
            count += 1
            new_name = f"Neue Last {count}"
            extra_args = {"template": template, "area": area}
            loads.append(
                Load(
                    scenario=scenario,
                    name=new_name,
                    **extra_args,
                    # TODO: manager=request.user
                )
            )
        return loads


class Grid(ScenarioItem):
    """Information about how areas are connected"""

    class CarrierChoices(models.TextChoices):
        ELECTRICITY = "electricity", "Strom"
        DIESEL = "diesel", "Diesel"
        OIL = "oil", "Öl"
        GAS = "gas", "Gas"
        HEAT = "heat", "Wärme"
        H2 = "h2", "H2"

    carrier = models.CharField(choices=CarrierChoices)
    feed_in = models.BooleanField(default=False)  # does this grid support feed-in?
    connected_to = models.ForeignKey(
        "Grid", on_delete=models.SET_NULL, default=None, null=True, blank=True
    )
    timeseries = models.ForeignKey(
        "Load", on_delete=models.SET_NULL, default=None, null=True, blank=True
    )
    areas = models.ManyToManyField("Area")


class ElectricComponent(ScenarioItem):
    """Abstract electric component, defines shared characteristics"""

    area = models.ForeignKey(Area, verbose_name=_("Fläche"), on_delete=models.CASCADE)
    power_kw = models.FloatField(_("Leistung"), default=None, null=True, blank=True)
    efficiency = models.FloatField(_("Effizienz"), default=1.0)
    power_installed = models.FloatField(
        _("Installierte Leistung"), default=None, null=True, blank=True
    )  # kWh
    power_min = models.FloatField(
        _("Minimale Leistung"), default=None, null=True, blank=True
    )  # kWh
    power_max = models.FloatField(
        _("Maximale Leistung"), default=None, null=True, blank=True
    )  # kWh
    capex = models.FloatField(_("CAPEX"), default=None, null=True, blank=True)  # €
    opex = models.FloatField(_("OPEX"), default=None, null=True, blank=True)  # €/a

    class Meta:
        abstract = True  # abstract table

    def custom_fields(self):
        generic_field_names = {f.name for f in ElectricComponent._meta.get_fields()}
        for parent in ElectricComponent.__mro__[1:]:
            if hasattr(parent, "_meta"):
                generic_field_names |= {f.name for f in parent._meta.get_fields()}
        return [f.name for f in self._meta.get_fields() if f.name not in generic_field_names]

    def area_verbose(self):
        if not self.area:
            return "Keine Angabe"
        return f"{self.area} m^2"

    def power_kw_verbose(self):
        if not self.power_kw:
            return "Keine Angabe"
        return f"{self.power_kw} kW"

    def efficiency_verbose(self):
        if not self.efficiency:
            return "Keine Angabe"
        return f"{(self.efficiency * 100):.2f} %"

    def power_installed_verbose(self):
        if not self.power_installed:
            return "Keine Angabe"
        return f"{self.power_installed} kW"

    def power_min_verbose(self):
        if not self.power_min:
            return "Keine Angabe"
        return f"{self.power_min} kW"

    def power_max_verbose(self):
        if not self.power_max:
            return "Keine Angabe"
        return f"{self.power_max} kW"

    def capex_verbose(self):
        if not self.capex:
            return "Keine Angabe"
        return f"{self.capex} €"

    def opex_verbose(self):
        if not self.opex:
            return "Keine Angabe"
        return f"{self.opex} €/a"

    @classmethod
    def get_default_args(cls) -> dict:
        return {}

    @classmethod
    def create_new(cls, scenario: Scenario, area_internal_ids: list[str | uuid.UUID], **kwargs):
        """Create a new instance of the object, with Model specific defaults and allowed user facing attributes"""
        # TODO: Refactor into model method so this function stays clean
        areas = Area.objects.filter(scenario=scenario, internal_id__in=area_internal_ids)
        count = cls.objects.filter(scenario=scenario).count()
        components = []
        for area in areas:
            count += 1
            new_name = f"Neue Komponente {count}"
            extra_args = {"area": area}
            extra_args |= cls.get_default_args()
            components.append(
                cls(
                    scenario=scenario,
                    name=new_name,
                    **extra_args,
                    # TODO: manager=request.user
                )
            )
        return components


class Generator(ElectricComponent):
    """Transforms fuel into electricity"""

    class CarrierChoices(models.TextChoices):
        DIESEL = "diesel", "Diesel"
        OIL = "oil", "Öl"

    carrier = models.CharField(_("Energieträger"), choices=CarrierChoices)

    def carrier_verbose(self):
        if not self.carrier:
            return "Keine Angabe"
        return self.get_carrier_display()


class Heating(ElectricComponent):
    """Transforms energy source to heat"""

    class CarrierChoices(models.TextChoices):
        DIESEL = "diesel", "Diesel"
        OIL = "oil", "Öl"
        ELECTRICITY = "electricity", "Strom"

    carrier = models.CharField(_("Energieträger"), choices=CarrierChoices)

    def carrier_verbose(self):
        if not self.carrier:
            return "Keine Angabe"
        return self.get_carrier_display()


class CHP(ElectricComponent):
    """Combined heat and power (Blockheizkraftwerk)"""

    class CarrierChoices(models.TextChoices):
        DIESEL = "diesel", "Diesel"
        OIL = "oil", "Öl"
        GAS = "gas", "Gas"

    carrier = models.CharField(_("Energieträger"), choices=CarrierChoices)
    efficiency_thermal = models.FloatField(_("Thermische Effizienz"), default=1.0)

    @classmethod
    def get_default_args(cls) -> dict:
        return {"carrier": cls.CarrierChoices.GAS}

    def carrier_verbose(self):
        if not self.carrier:
            return "Keine Angabe"
        return self.get_carrier_display()


class FuelCell(ElectricComponent):
    """Transform H2 into electricity"""

    # carrier is always hydrogen
    efficiency_thermal = models.FloatField(default=1.0)


class Electrolyzer(ElectricComponent):
    """Transform electricity into H2"""

    # carrier is always electricity
    efficiency_thermal = models.FloatField(_("Thermische Effizienz"), default=1.0)


class Heatpump(ElectricComponent):
    """Transform electricity into heat"""

    # carrier is always electricity
    class HeatChoices(models.TextChoices):
        AIR = "air", "Luft"
        WATER = "water", "Wasser"
        WASTE_AIR = "waste_air", "Abwärme"

    class ModeChoices(models.IntegerChoices):
        MONOVALENT = 1
        BIVALENT = 2

    heatsource = models.CharField(choices=HeatChoices)
    mode = models.IntegerField(choices=ModeChoices)

    @classmethod
    def get_default_args(cls) -> dict:
        return {"heatsource": cls.HeatChoices.WATER, "mode": cls.ModeChoices.MONOVALENT}


class Solar(ElectricComponent):
    """Photovoltaics"""

    profile = models.ForeignKey(
        Load, on_delete=models.SET_NULL, default=None, null=True, blank=True
    )
    azimut = models.FloatField(
        default=None,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(360)],
    )
    angle = models.FloatField(
        default=None,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(90)],
    )
    spec_power = models.FloatField(default=None, null=True, blank=True)  # kW/m^2
    surface_area_installed = models.FloatField(default=None, null=True, blank=True)  # m^2
    surface_area_min = models.FloatField(default=None, null=True, blank=True)  # m^2
    surface_area_max = models.FloatField(default=None, null=True, blank=True)  # m^2


class Storage(ScenarioItem):
    """Generic energy storage"""

    class CarrierChoices(models.TextChoices):
        ELECTRICITY = "electricity", "Strom"  # battery
        HEAT = "heat", "Wärme"
        H2 = "h2", "H2"

    area = models.ForeignKey(Area, on_delete=models.CASCADE)
    carrier = models.CharField(choices=CarrierChoices)
    efficiency_load = models.FloatField(default=1.0)
    efficiency_store = models.FloatField(default=1.0)
    # capacity: unit depends on carrier. kWh for electricity and heat, liters for H2
    capacity_installed = models.FloatField(default=None, null=True, blank=True)
    capacity_min = models.FloatField(default=None, null=True, blank=True)
    capacity_max = models.FloatField(default=None, null=True, blank=True)
    capex = models.FloatField(default=None, null=True, blank=True)  # €/kWh, €/l

    def carrier_verbose(self):
        if not self.carrier:
            return "Keine Angabe"
        return self.get_carrier_display()


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


@receiver(models.signals.pre_delete, sender=UploadedFile)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    if instance.file:
        path = Path(instance.file.path)
        if path.exists():
            path.unlink()


# Custom Exceptions
class MissingFormValueException(Exception):
    pass
