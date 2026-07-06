import logging
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.db import models
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.validators import MaxValueValidator
from django.core.validators import MinValueValidator
from django.db.models.functions import Now
from django.db.models.signals import post_delete
from django.db.models.signals import post_save
from django.db.transaction import atomic
from django.dispatch import Signal
from django.dispatch import receiver
from django.forms import ModelForm
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger("django_ports")


# Each set of scenario is bundled via its project
class Project(models.Model):
    id = models.BigAutoField(primary_key=True, blank=True)
    internal_id = models.UUIDField(
        db_index=True, unique=True, null=False, blank=True, default=uuid.uuid4
    )
    name = models.TextField(blank=False, null=True)
    description = models.TextField(blank=True, null=True)
    # Set to now() on the database side
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Related name + tells django not to create a reverse relation for user, e.g. user.scenario_set
    manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, default=None, null=True, related_name="+"
    )

    def group_name(self) -> str:
        return f"group_project_{self.id}"

    @property
    def users(self) -> "dict[str, models.QuerySet[User]]":
        if not hasattr(self, "_users_cache"):
            from django.contrib.contenttypes.models import ContentType
            from guardian.utils import get_group_obj_perms_model

            GroupObjectPermission = get_group_obj_perms_model()
            perms = (
                GroupObjectPermission.objects.filter(
                    content_type=ContentType.objects.get_for_model(self.__class__),
                    object_pk=self.pk,
                )
                .select_related("permission")
                .prefetch_related("group__user_set")
            )
            self._users_cache = {
                perm.permission.codename: perm.group.user_set.all() for perm in perms
            }
        return self._users_cache

    @atomic()
    def safe_delete(self):
        """Safely delete the project by safely deleting all scenarios referencing it.
        This is needed because of the nature of DeletedItems which are created during
        deletion
        """
        scenarios = Scenario.objects.filter(project=self)
        for s in scenarios:
            s.safe_delete()
        self.delete()


# Each set of scenario items is bundled via its scenario. The scenario has a simple BigInteger Id
class Scenario(models.Model):
    # Project which bundles Scenarios
    # on_delete is null since DeletedItems dont allow cascading delete on Scenario
    # instead safe_delete has to be used on the scenarios
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, default=None, null=True)
    id = models.BigAutoField(primary_key=True, blank=True)
    internal_id = models.UUIDField(
        db_index=True, unique=True, null=False, blank=True, default=uuid.uuid4
    )
    name = models.TextField(blank=False, null=True)
    description = models.TextField(blank=True, null=True)
    # Set to now() on the database side
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Aggregator of all scenario items. Used to track changes
    items_updated_at = models.DateTimeField(db_default=Now(), editable=False)
    # Related name + tells django not to create a reverse relation for user, e.g. user.scenario_set
    manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, default=None, null=True, related_name="+"
    )

    # Area of the scenario / Port region
    geom = models.PolygonField(null=True, blank=True)

    # Class variable which keeps track of scenarios which should be deleted
    # This disables DeletedItem creation which is slow for large queries
    _being_deleted: ClassVar[set[int]] = set()

    class Meta:
        permissions = (
            ("view", "view scenario"),
            ("details", "details scenario"),
            ("delete", "delete scenario"),
            ("change", "change scenario"),
        )

    def group_name(self):
        return f"Scenario_{self.id}_group"

    @staticmethod
    @contextmanager
    def true_delete(scenario_ids: Iterable[int]):
        ids = set(scenario_ids)
        Scenario._being_deleted.update(ids)
        try:
            yield
        finally:
            Scenario._being_deleted.difference_update(ids)

    @atomic()
    def safe_delete(self):
        """Disable DeletedItem creation during deletion"""
        with self.true_delete([self.id]):
            self.delete()

    def prepare_deepcopy(self):
        self.internal_id = uuid.uuid4()

    def changed_event(self):
        return f"{self._meta.model_name}-{self.internal_id}-changed"


class ItemTemplate(models.Model):
    """Abstract class for item templates"""

    id = models.BigAutoField(primary_key=True, auto_created=True, editable=False)
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

    class Meta:
        abstract = True  # Important: makes this a base, not a table

    def save(self, *args, **kwargs):
        if self.pk is None and self.updated_user is None:
            self.updated_user = self.manager
        return super().save(*args, **kwargs)


class ScenarioItem(ItemTemplate):
    """All items which have a scenario as reference inherit some common functionality"""

    # Scenario specific id, which stays the same over scenarios
    internal_id = models.UUIDField(db_index=True, null=False, default=uuid.uuid4)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, db_index=True)

    scenarioitem_post_delete = Signal()
    scenarioitem_post_save = Signal()

    # the item was authorized. It can be shown in the frontend
    has_authorization = False

    class Meta:
        abstract = True  # Important: makes this a base, not a table
        constraints = [
            models.UniqueConstraint(
                fields=["internal_id", "scenario"],
                name="%(class)s_unique_internal_id_per_scenario",
            )
        ]
        ordering = ["scenario", "id"]  # Optional: share common Meta options

        permissions = (
            ("view", "view item with limited attributes"),
            ("details", "read item with all attributes"),
            ("delete", "delete item"),
            ("change", "change item"),
        )

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

    def changed_callback(self):
        """
        On change, create a callback event in the frontend.
        Elements in the client listen for this event and can show a notification.
        Send updated_at time with event, so any item can compare its current state time.
        """
        date_str = self.updated_at.isoformat()
        return mark_safe(
            f"document.dispatchEvent(new CustomEvent( '{self.changed_event()}', "
            f"{{detail:{{updated_at:'{date_str}'}}}}));"
        )

    def deleted_event(self):
        return f"{self._meta.model_name}-{self.internal_id}-deleted"

    def layer_name(self):
        return f"{self._meta.model_name}"

    def list_icon(self) -> str:
        """The cotton template used as icon for this model inside lists"""
        return "icon.circle_full"

    def icon(self) -> str:
        """The cotton template used as icon for this model"""
        return "icon.circle_full"

    @classmethod
    def create_new(cls, scenario: Scenario, manager: User, **kwargs):
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
    # No DeletedItem creation in cases where the scenario is marked for deletion
    if instance.scenario_id in Scenario._being_deleted:
        return
    deleted_item = DeletedItem.from_scenario_item(instance)
    deleted_item.save()


@receiver(ScenarioItem.scenarioitem_post_save)
def update_scenario_post_save(sender, instance, **kwargs):
    """Update the scenario if a ScenarioItem was created"""
    # TODO: add project?
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

    def layer_name(self):
        return f"{self.area_type}-{self._meta.model_name}"

    class Meta:
        permissions = (
            ("details", "View area details"),
            ("delete", "delete area"),
            ("change", "change area"),
        )

    def list_icon(self) -> str:
        """The cotton template used as icon for this model inside lists"""
        if self.area_type == Area.AreaTypeChoices.BUILDING:
            return "icon.building-list"
        else:
            return "icon.area-list"

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
    def create_new(cls, scenario: Scenario, manager: User, **kwargs):
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
            manager=manager,
            **extra_args,
        )
        return instance


# FIXME: A load template is not part of an area. What permission state should it have?
class LoadTemplate(ScenarioItem):
    """Template for timeseries, mostly power series"""

    timeseries = models.JSONField()
    spec_load = models.FloatField(  # some specific characteristic, calculated for timeseries
        default=None
    )

    @classmethod
    def values_from_csv(cls, file: InMemoryUploadedFile) -> list:
        values = []
        for line in file:
            row = line.decode().strip()
            vals = row.split(",")
            try:
                value = float(vals[-1])
                values.append(value)
            except ValueError:
                pass
        return values

    def get_hourly_average(self) -> float:
        if not hasattr(self, "hourlyAvg"):
            self.annotateAverages()
        return self.hourlyAvg

    def get_daily_average(self) -> float:
        if not hasattr(self, "dailyAvg"):
            self.annotateAverages()
        return self.dailyAvg

    def get_yearly_average(self) -> float:
        if not hasattr(self, "yearlyAvg"):
            self.annotateAverages()
        return self.yearlyAvg

    def annotateAverages(self) -> "LoadTemplate":
        timestep_min = self.timeseries.get("timestep_minutes", 15)
        values = self.timeseries.get("values", [0])
        total_time_min = len(values) * timestep_min
        sum_values = sum(values)
        hourlyAverage = sum_values / (total_time_min / 60)
        dailyAverage = sum_values / (total_time_min / (60 * 24))
        yearlyAverage = sum_values / (total_time_min / (365 * 24 * 60))
        self.hourlyAvg = hourlyAverage
        self.dailyAvg = dailyAverage
        self.yearlyAvg = yearlyAverage
        return self


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
        cls,
        scenario: Scenario,
        manager: User,
        area_internal_ids: list[uuid.UUID | str] = None,
        **kwargs,
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
                    manager=manager,
                    **extra_args,
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


class ElectricComponentTemplate(ItemTemplate):
    """Abstract template for electric components, defines shared characteristics"""

    power_kw = models.FloatField(verbose_name=_("Leistung"), default=None, null=True, blank=True)
    efficiency = models.FloatField(verbose_name=_("Effizienz"), default=1.0)
    power_installed = models.FloatField(
        verbose_name=_("Installierte Leistung"), default=None, null=True, blank=True
    )  # kWh
    power_min = models.FloatField(
        verbose_name=_("Minimale Leistung"), default=None, null=True, blank=True
    )  # kWh
    power_max = models.FloatField(
        verbose_name=_("Maximale Leistung"), default=None, null=True, blank=True
    )  # kWh
    capex = models.FloatField(verbose_name=_("CAPEX"), default=None, null=True, blank=True)  # €
    opex = models.FloatField(verbose_name=_("OPEX"), default=None, null=True, blank=True)  # €/a

    class Meta:
        abstract = True  # abstract table

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


class ElectricComponent(ScenarioItem, ElectricComponentTemplate):
    """Abstract electric component"""

    area = models.ForeignKey(Area, verbose_name=_("Fläche"), on_delete=models.CASCADE)

    class Meta(ScenarioItem.Meta):
        abstract = True  # abstract table
        # retain unique internal_id/scenario_id constraint from ScenarioItem Meta

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

    @classmethod
    def create_new(
        cls, scenario: Scenario, manager: User, area_internal_ids: list[str | uuid.UUID], **kwargs
    ):
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
                    manager=manager,
                    **extra_args,
                )
            )
        return components


class AbstractGenerator(models.Model):
    """Transforms fuel into electricity"""

    class CarrierChoices(models.TextChoices):
        DIESEL = "diesel", "Diesel"
        OIL = "oil", "Öl"

    carrier = models.CharField(verbose_name=_("Energieträger"), choices=CarrierChoices)

    def carrier_verbose(self):
        if not self.carrier:
            return "Keine Angabe"
        return self.get_carrier_display()

    def list_icon(self) -> str:
        """The cotton template used as icon for this model inside lists"""
        return "icon.generator"

    class Meta:
        abstract = True


class GeneratorTemplate(ElectricComponentTemplate, AbstractGenerator):
    pass


class Generator(ElectricComponent, AbstractGenerator):
    pass


class AbstractHeating(models.Model):
    """Transforms energy source to heat"""

    class CarrierChoices(models.TextChoices):
        DIESEL = "diesel", "Diesel"
        OIL = "oil", "Öl"
        ELECTRICITY = "electricity", "Strom"

    carrier = models.CharField(verbose_name=_("Energieträger"), choices=CarrierChoices)

    def carrier_verbose(self):
        if not self.carrier:
            return "Keine Angabe"
        return self.get_carrier_display()

    def list_icon(self) -> str:
        """The cotton template used as icon for this model inside lists"""
        return "icon.heating"

    class Meta:
        abstract = True


class HeatingTemplate(ElectricComponentTemplate, AbstractHeating):
    pass


class Heating(ElectricComponent, AbstractHeating):
    pass


class AbstractCHP(models.Model):
    """Combined heat and power (Blockheizkraftwerk)"""

    class CarrierChoices(models.TextChoices):
        DIESEL = "diesel", "Diesel"
        OIL = "oil", "Öl"
        GAS = "gas", "Gas"

    carrier = models.CharField(verbose_name=_("Energieträger"), choices=CarrierChoices)
    efficiency_thermal = models.FloatField(verbose_name=_("Thermische Effizienz"), default=1.0)

    @classmethod
    def get_default_args(cls) -> dict:
        return {"carrier": cls.CarrierChoices.GAS}

    def carrier_verbose(self):
        if not self.carrier:
            return "Keine Angabe"
        return self.get_carrier_display()

    def list_icon(self) -> str:
        """The cotton template used as icon for this model inside lists"""
        return "icon.chp"

    class Meta:
        abstract = True


class CHPTemplate(ElectricComponentTemplate, AbstractCHP):
    pass


class CHP(ElectricComponent, AbstractCHP):
    pass


class AbstractFuelCell(models.Model):
    """Transform H2 into electricity"""

    # carrier is always hydrogen
    efficiency_thermal = models.FloatField(default=1.0)

    def list_icon(self) -> str:
        """The cotton template used as icon for this model inside lists"""
        return "icon.fuelcell"

    class Meta:
        abstract = True


class FuelCellTemplate(ElectricComponentTemplate, AbstractFuelCell):
    pass


class FuelCell(ElectricComponent, AbstractFuelCell):
    pass


class AbstractElectrolyzer(models.Model):
    """Transform electricity into H2"""

    # carrier is always electricity
    efficiency_thermal = models.FloatField(verbose_name=_("Thermische Effizienz"), default=1.0)

    def list_icon(self) -> str:
        """The cotton template used as icon for this model inside lists"""
        return "icon.electrolyzer"

    class Meta:
        abstract = True


class ElectrolyzerTemplate(ElectricComponentTemplate, AbstractElectrolyzer):
    pass


class Electrolyzer(ElectricComponent, AbstractElectrolyzer):
    pass


class AbstractHeatpump(models.Model):
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

    def list_icon(self) -> str:
        """The cotton template used as icon for this model inside lists"""
        return "icon.heat_pump"

    class Meta:
        abstract = True


class HeatpumpTemplate(ElectricComponentTemplate, AbstractHeatpump):
    pass


class Heatpump(ElectricComponent, AbstractHeatpump):
    pass


class AbstractSolar(models.Model):
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

    def list_icon(self) -> str:
        """The cotton template used as icon for this model inside lists"""
        return "icon.solar"

    class Meta:
        abstract = True


class SolarTemplate(ElectricComponentTemplate, AbstractSolar):
    pass


class Solar(ElectricComponent, AbstractSolar):
    pass


class AbstractStorage(models.Model):
    """Generic energy storage"""

    class CarrierChoices(models.TextChoices):
        ELECTRICITY = "electricity", "Strom"  # battery
        HEAT = "heat", "Wärme"
        H2 = "h2", "H2"

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

    class Meta:
        abstract = True


class StorageTemplate(ItemTemplate, AbstractStorage):
    pass


class Storage(ScenarioItem, AbstractStorage):  # order important (Meta)
    area = models.ForeignKey(Area, on_delete=models.CASCADE)


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
