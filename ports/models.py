import datetime
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from typing import ClassVar

import pandas as pd
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.db import models
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.mail import mail_admins
from django.core.validators import MaxValueValidator
from django.core.validators import MinValueValidator
from django.db import transaction
from django.db.models import F
from django.db.models import Value
from django.db.models.functions import Greatest
from django.db.models.functions import Now
from django.db.models.signals import post_save
from django.dispatch import Signal
from django.dispatch import receiver
from django.shortcuts import render  # noqa
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.formats import number_format
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

# Unit suffix shown next to a field's value, both on forms (ports/forms.py) and on
# the {{ instance|display:"field" }} template filter (ports/templatetags/display.py).
UNITS = {
    "power_kw": "kW",
    "efficiency": "[-]",
    "efficiency_thermal": "[-]",
    "power_min": "kW",
    "power_max": "kW",
    "capex": "€",
    "opex": "€/a",
    "azimut": "°",
    "angle": "°",
    "spec_power": "W/m²",
    "surface_area_installed": "m²",
    "surface_area_min": "m²",
    "surface_area_max": "m²",
    "co2_price": "€/t",
    "electricity_price": "€/kWh",
    "heat_price": "€/kWh",
    "oil_price": "€/l",
    "diesel_price": "€/l",
    "gasoline_price": "€/l",
    "hydrogen_price": "€/kg",
    "ammonia_price": "€/kg",
}


# Each set of scenario is bundled via its project
class Project(models.Model):
    id = models.BigAutoField(primary_key=True, blank=True)
    internal_id = models.UUIDField(
        db_index=True, unique=True, null=False, blank=True, default=uuid.uuid4
    )
    name = models.TextField(blank=False, null=True, help_text=_("Name des Projekts."))
    description = models.TextField(
        blank=True, null=True, help_text=_("Optionale Beschreibung des Projekts.")
    )
    # Set to now() on the database side
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Related name + tells django not to create a reverse relation for user, e.g. user.scenario_set
    # To find all projects a user has access to use util.get_user_projects, which takes group permissions into account
    manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, default=None, null=True, related_name="+"
    )

    class Meta:
        permissions = (
            ("view", "view project"),
            ("details", "details project"),
            ("delete", "delete project"),
            ("change", "change project"),
        )

    def group_name(self) -> str:
        return f"group_project_{self.id}"

    @property
    def users(self) -> "dict[str, models.QuerySet[User]]":
        """Get all users with some permission for the project as dictionary
        with key of the permission.codename"""
        if not hasattr(self, "_users_cache"):
            from .util import prefetch_projects_users

            prefetch_projects_users([self])
        return self._users_cache


class Scenario(models.Model):
    # Instance which bundles ScenarioItems
    # Bundled with other scenarios in a common _Project_.

    project = models.ForeignKey(Project, on_delete=models.CASCADE, default=None, null=True)
    id = models.BigAutoField(primary_key=True, blank=True)
    internal_id = models.UUIDField(
        db_index=True, unique=True, null=False, blank=True, default=uuid.uuid4
    )
    name = models.TextField(blank=False, null=True, help_text=_("Name des Szenarios."))
    description = models.TextField(
        blank=True, null=True, help_text=_("Optionale Beschreibung des Szenarios.")
    )
    # Set to now() on the database side
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Aggregator of all scenario items. Used to track changes
    items_updated_at = models.DateTimeField(db_default=Now(), editable=False)
    # Related name + tells django not to create a reverse relation for user, e.g. user.scenario_set
    manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, default=None, null=True, related_name="+"
    )

    # oemof solver task
    task_id = models.UUIDField(default=None, null=True, blank=True)

    # Area of the scenario / Port region
    geom = models.PolygonField(
        default=None,
        null=True,
        blank=True,
        help_text=_("Geografische Ausdehnung des Szenarios."),
        srid=4326,
        geography=True,
    )

    class Meta:
        permissions = (
            ("view", "view scenario"),
            ("details", "details scenario"),
            ("delete", "delete scenario"),
            ("change", "change scenario"),
        )

    def prepare_deepcopy(self):
        """Adjust the instance so it can be deepcopied
        Since the internal_id of each scenario has to be unique, its overwritten with a new one.
        """
        self.internal_id = uuid.uuid4()

    def changed_event(self):
        return f"{self._meta.model_name}-{self.internal_id}-changed"

    def is_baseline_scenario(self):
        # the first scenario in a project is by definition the baseline scenario
        return self.project.scenario_set.order_by("id").first() == self


class ItemTemplate(models.Model):
    """Abstract class for item templates"""

    id = models.BigAutoField(primary_key=True, auto_created=True, editable=False)
    name = models.TextField(verbose_name=_("Name"), blank=False, null=True, max_length=200)
    description = models.TextField(
        blank=True, null=True, help_text=_("Optionale Beschreibung der Vorlage.")
    )

    # Templates also contain an internal_id for access.
    # This allows consistent lookup via uuid between ScenarioItems and ItemTemplates
    # For Scenario Items this is overridden
    internal_id = models.UUIDField(db_index=True, unique=True, null=False, default=uuid.uuid4)

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

    # Per-field display formatters, keyed by field name, used by the {{ instance|display:"field" }}
    # template filter (ports/templatetags/display.py). Subclasses override/extend this to
    # customize how a field's value is shown, e.g. unit conversion (efficiency as a
    # fraction -> "42.00 %"). Fields without an entry fall back to UNITS.get(field_name)
    # as a suffix.
    display_formatters: ClassVar[dict[str, Callable[[Any], str]]] = {}

    def display_value(self, field_name: str) -> str:
        """Format field_name's current value for display, e.g. in a template via
        {% load display %}{{ instance|display:"power_kw" }}.
        Checks display_formatters first (e.g. unit conversion), then falls back to
        a UNITS suffix, then the plain value. None/"" renders as "Keine Angabe".
        """
        value = getattr(self, field_name)
        if value is None or value == "":
            return "Keine Angabe"
        formatter = self.display_formatters.get(field_name)
        if formatter:
            return formatter(value)
        unit = UNITS.get(field_name)
        return f"{number_format(value, 2)} {unit}" if unit else str(value)

    def save(self, *args, **kwargs):
        if self.pk is None and self.updated_user is None:
            self.updated_user = self.manager

        return super().save(*args, **kwargs)

    @classmethod
    def model_name(cls):
        """Get the _meta.model_name"""
        return cls._meta.model_name

    @classmethod
    def model(cls):
        """Get the _meta.model"""
        return cls._meta.model

    @classmethod
    def verbose_name(cls):
        """Get the _meta.verbose_name in case the subclass does not implement a proper verbose name"""
        return cls._meta.verbose_name

    def __str__(self):
        return f"{self._meta.object_name}: {self.name if self.name is not None else self.id}"

    # Icon shown in lists
    list_icon = "icon.circle_full"
    # Generic Icon shown elsewhere, e.g. map
    icon = "icon.circle_full"

    @property
    def icon_selected(self) -> str:
        """Symbol id of the filled icon variant in ports/static/ports/icons/sprite.svg
        This is used for more efficient rendering on large loops
        """
        list_icon = self.list_icon() if callable(self.list_icon) else self.list_icon
        return list_icon.removeprefix("icon.").replace("-", "_")

    @property
    def icon_unselected(self) -> str:
        """Symbol id of the outline icon variant in ports/static/ports/icons/sprite.svg
        This is used for more efficient rendering on large loops"""
        return f"{self.icon_selected}-outline"

    rud_url = "ports:templates"

    create_url = "ports:template_create"


class ScenarioItem(ItemTemplate):
    """All items which have a scenario as reference inherit some common functionality"""

    # Scenario specific id, which stays the same over scenarios
    internal_id = models.UUIDField(db_index=True, null=False, default=uuid.uuid4)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, db_index=True)

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

    @classmethod
    def _connect_signals(cls):
        post_save.connect(cls._send_scenarioitem_post_save, sender=cls)

    def _send_scenarioitem_post_save(sender, instance, **kwargs):
        ScenarioItem.scenarioitem_post_save.send(
            sender=sender, instance=instance, created=kwargs.get("created")
        )

    # Generic str should never lookup other db instance. this can lead to n fetches of
    # scenario in the /admin/ panel which is terribly slow. verbose names can be set explicitly for the frontend
    # self.scenario.name removed
    def __str__(self):
        return f"{self._meta.object_name}: {self.name if self.name is not None else self.id} ({self.scenario_id})"

    def changed_event(self):
        return f"{self._meta.model_name}-{self.internal_id}-changed"

    def changed_callback(self):
        """
        On change, create a callback event in the frontend.
        Elements in the client listen for this event and can show a notification.
        Send updated_at time with event, so any item can compare its current state time.
        """
        date_str = timezone.localtime(self.updated_at).isoformat()
        return mark_safe(
            f"document.dispatchEvent(new CustomEvent( '{self.changed_event()}', "
            f"{{detail:{{updated_at:'{date_str}'}}}}));"
        )

    def deleted_event(self):
        return f"{self._meta.model_name}-{self.internal_id}-deleted"

    @classmethod
    def layer_name(self):
        return f"{self._meta.model_name}"

    @classmethod
    def create_new(cls, scenario: Scenario, manager: User, **kwargs):
        """Create a new instance of the object, with Model specific defaults and allowed user facing attributes"""
        raise NotImplementedError("Missing implementation of Model specific empty Instance")

    rud_url = "ports:details"

    create_url = "ports:details_create"


@receiver(ScenarioItem.scenarioitem_post_save)
def update_scenario_post_save(sender, instance, **kwargs):
    """Update the scenario if a ScenarioItem was created
    Publish the change to redis, if available
    """
    scenario_id = instance.scenario_id
    if sender == ChangedItem:
        return

    def publish_change():
        try:
            import redis
            from redis.exceptions import RedisError

            r = redis.from_url(settings.REDIS_URL)
            r.publish(f"scenario:{scenario_id}", "changed")
        except RedisError as e:
            # should redis fail, the scenario is updated still

            # lazy import to avoid circular import
            from ports.util import throttle

            msg = repr(e)
            throttle(
                lambda: mail_admins("Redis Error for enetra", msg, fail_silently=True),
                24 * 60 * 60,
                "redis_failure_admin_mail",
            )

    if settings.REDIS_AVAILABLE:
        # Publish only after the surrounding transaction committed, otherwise clients refetch
        # before the change is visible. Runs immediately when no transaction is active.
        transaction.on_commit(publish_change)
    # GREATEST keeps the timestamps monotonic when concurrent saves commit out of order
    new_ts = Value(instance.updated_at, output_field=models.DateTimeField())
    Scenario.objects.filter(pk=scenario_id).update(
        items_updated_at=Greatest(F("items_updated_at"), new_ts),
        updated_at=Greatest(F("updated_at"), new_ts),
    )


@receiver(ScenarioItem.scenarioitem_post_save)
def create_changeditem_post_save(sender, instance, **kwargs):
    """Create a ChangedItem"""
    # NOTE: Be sure to handle bouncing signals which can introduce infinite signal loops
    # Create a ChangedItem with all Scenarioitem values
    # We use post_save to only create these items when the item is really created
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
        content_type = ContentType.objects.get_for_model(item)
        changed_item.content_type = content_type
        return changed_item


class DeletedItem(ScenarioItem):
    """Store basic information about deleted item.
    These items are explicitly created on delete

    This keeps normal deletes fast during development and on the admin panel

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
        return f"DeletedItem with id {self.id} of type {self.content_type} in scenario {self.scenario_id} with uuid {self.internal_id}"

    @classmethod
    def from_scenario_item(cls, item: ScenarioItem) -> "DeletedItem":
        deleted_item = DeletedItem(
            **{f.name: getattr(item, f.name) for f in ScenarioItem._meta.fields if f.name != "id"}
        )
        # use get_for_model to use cache
        content_type = ContentType.objects.get_for_model(item)
        deleted_item.content_type = content_type
        return deleted_item


# Can an Area serve multiple purposes? (yes)
# Can an Area serve the same usage, e.g. solar, multiple times? (yes)
class Area(ScenarioItem):
    class AreaTypeChoices(models.TextChoices):
        BUILDING = "building", "Gebäudefläche"
        OPEN = "open", "Freifläche"

    class OpenUsageChoices(models.TextChoices):
        PV = "pv", "Photovoltaik"
        PARKING = "parking", "Parkfläche"
        GREEN = "green", "Grünfläche"

    class BuildingUsageChoices(models.TextChoices):
        OFFICE = "office", "Büro"
        STORAGE = "storage", "Lager"

    geom = models.PolygonField(
        null=True, blank=False, help_text=_("Geometrie der Fläche."), geography=True, srid=4326
    )
    area_type = models.CharField(choices=AreaTypeChoices, null=True, help_text=_("Art der Fläche."))
    usage = models.CharField(
        choices=OpenUsageChoices.choices + BuildingUsageChoices.choices,
        null=True,
        blank=True,
        default=None,
        help_text=_("Nutzung der Fläche."),
    )

    def layer_name(self):
        return f"{self.area_type}-{self._meta.model_name}"

    class Meta:
        # When overriding Meta with anything except Abstract True,
        # Meta options from the BaseClass(ScenarioItem) get lost
        constraints = [
            models.UniqueConstraint(
                fields=["internal_id", "scenario"],
                name="%(class)s_unique_internal_id_per_scenario",
            )
        ]
        ordering = ["scenario", "id"]  # Optional: share common Meta options
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
    def create_new(cls, scenario: Scenario, manager: User, **kwargs):
        """Create a new instance of the object, with Model specific defaults and allowed user facing attributes"""
        if manager is None or not manager.is_authenticated:
            raise ImproperlyConfigured("create_new can only be called with an authenticated User")
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

    @classmethod
    def electric_component_accessor_names(cls):
        accessor_list = []
        for field in cls._meta.get_fields():
            if field.is_relation and issubclass(field.related_model, ElectricComponent):
                accessor_list.append(field.accessor_name)
        return accessor_list

    def get_all_electric_components(self):
        accessor_names = self.electric_component_accessor_names()
        electric_component_managers = []
        for name in accessor_names:
            related_set = getattr(self, name)
            electric_component_managers.append(related_set.all())
        return electric_component_managers


# FIXME: A load template is not part of an area. What permission state should it have?
class Timeseries(ScenarioItem):
    """Template for timeseries"""

    timeseries = models.JSONField(help_text=_("Zeitreihendaten."))
    spec_load = models.FloatField(  # some specific characteristic, calculated for timeseries
        default=None, help_text=_("Spezifische Last der Zeitreihe.")
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

    def annotateAverages(self) -> "Timeseries":
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


class EnergyCost(ScenarioItem):
    """Energy carrier prices

    units are defined in UNITS
    """

    co2_price = models.FloatField(
        verbose_name=_("CO₂-Preis"), default=80, help_text=_("CO₂-Preis.")
    )
    electricity_price = models.FloatField(
        verbose_name=_("Strom-Preis"), default=0.25, help_text=_("Strompreis.")
    )
    heat_price = models.FloatField(
        verbose_name=_("Wärme-Preis"), default=0.12, help_text=_("Wärmepreis.")
    )
    oil_price = models.FloatField(verbose_name=_("Öl-Preis"), default=1.60, help_text=_("Ölpreis."))
    diesel_price = models.FloatField(
        verbose_name=_("Diesel-Preis"), default=1.65, help_text=_("Dieselpreis.")
    )
    gasoline_price = models.FloatField(
        verbose_name=_("Benzin-Preis"), default=1.80, help_text=_("Benzinpreis.")
    )
    hydrogen_price = models.FloatField(
        verbose_name=_("H₂-Preis"), default=7, help_text=_("Preis für Wasserstoff.")
    )
    ammonia_price = models.FloatField(
        verbose_name=_("NH₃-Preis"), default=0.70, help_text=_("Preis für Ammoniak.")
    )

    def render_form(self):
        from .forms import EnergyCostForm

        form = EnergyCostForm(instance=self)
        scenario = self.scenario
        context = {"scenario": scenario, "form": form}

        return render_to_string("ports/partials/energiekosten.html", context)

    class Meta(ScenarioItem.Meta):
        # retains the internal_id/scenario unique-together constraint from ScenarioItem.Meta,
        # and additionally enforces a single EnergyCost row per Scenario.
        constraints = [
            *ScenarioItem.Meta.constraints,
            models.UniqueConstraint(fields=["scenario"], name="unique_energycost_per_scenario"),
        ]

    @classmethod
    def get_or_create_for_scenario(cls, scenario: Scenario) -> tuple["EnergyCost", bool]:
        # helpful since EnergyCost is a singleton per scenario right now
        instance, created = cls.objects.get_or_create(
            scenario=scenario,
            defaults={
                "name": "Energiekosten",
                "manager_id": scenario.manager_id,
                "updated_user_id": scenario.manager_id,
            },
        )
        return instance, created


class SolarRadiation(models.Model):
    """Global items to define Solar Radiation

    These can be referenced by Solar.
    They are public and server specified.
    No need for authorization, scoping to scenarios or internal_ids
    """

    is_active = models.BooleanField(
        default=True, help_text=_("Gibt an ob dieses Profil Nutzern zur Auswahl steht")
    )
    name = models.TextField(
        blank=False,
        null=True,
        help_text=_("Name der Zeitreihe"),
    )
    description = models.TextField(
        blank=True, null=True, help_text=_("Optionale Beschreibung der Zeitreihe.")
    )
    timeseries = models.JSONField("Zeitreihe", help_text=_("Zeitreihendaten in W/m²"))
    timestep = models.FloatField(verbose_name="Zeitschritt in Minuten")
    timeseries_start = models.DateTimeField(verbose_name="Startzeitpunkt der Zeitreihe")

    class ActiveSolarRadiationManager(models.Manager):
        def get_queryset(self):
            return super().get_queryset().filter(is_active=True)

    objects = ActiveSolarRadiationManager()
    all_objects = models.Manager()

    def __str__(self):
        return self.name or (f"{self.id} SolarRadiation")


class Load(ScenarioItem):
    """Power timeseries, derived from a Timeseries"""

    class CarrierChoices(models.TextChoices):
        ELECTRICITY = "electricity", "Strom"
        DIESEL = "diesel", "Diesel"
        OIL = "oil", "Öl"
        GAS = "gas", "Gas"
        HEAT = "heat", "Wärme"
        H2 = "h2", "H2"

    area = models.ForeignKey(
        Area,
        on_delete=models.CASCADE,
        help_text=_("Fläche, der diese Last zugeordnet ist."),
    )
    carrier = models.CharField(
        verbose_name=_("Energieträger"),
        choices=CarrierChoices,
        help_text=_("Energieträger der Last."),
        blank=False,
        default=CarrierChoices.ELECTRICITY,
    )
    timeseries = models.ForeignKey(
        Timeseries,
        on_delete=models.CASCADE,
        help_text=_("Zeitreihenvorlage dieser Last."),
    )
    # We could allow negative factors, but maybe its confusing
    factor = models.FloatField(
        default=1.0,
        validators=[MinValueValidator(0)],
        help_text=_(
            "Skalierungsfaktor für die Werte der Zeitreihenvorlage. Muss größer oder gleich 0 sein."
        ),
    )  # scale template values

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
        if manager is None or not manager.is_authenticated:
            raise ImproperlyConfigured("create_new can only be called with an authenticated User")

        areas = Area.objects.filter(scenario=scenario, internal_id__in=area_internal_ids)
        count = cls.objects.filter(scenario=scenario).count()
        timeseries = Timeseries.objects.filter(scenario=scenario, manager=manager).first()

        if not timeseries:
            timeseries = Timeseries.objects.create(
                scenario=scenario,
                name="Konstante Zeitreihe",
                manager=manager,
                timeseries={"values": [1, 1], "timestep_minutes": 15},
                spec_load=0,
            )
        loads = []
        for area in areas:
            count += 1
            new_name = f"Neue Last {count}"
            extra_args = {"timeseries": timeseries, "area": area}
            loads.append(
                Load(
                    scenario=scenario,
                    name=new_name,
                    manager=manager,
                    **extra_args,
                )
            )
        return loads

    def resample(self, start: datetime.datetime, end: datetime.datetime, time_step: int):
        timestep_orig = self.timeseries.timeseries.get("timestep_minutes", 15)
        values = self.timeseries.timeseries["values"]
        time_index = pd.date_range(start, end, freq=f"{timestep_orig}Min")
        if len(values) < len(time_index):
            # not enough values: extend series with empty values, fill later
            values += [None] * (len(time_index) - len(values))
        elif len(values) > len(time_index):
            # too many values: shorten series
            values = values[: len(time_index)]
        series = pd.Series(values, index=time_index) * self.factor
        series.ffill(inplace=True)  # fill up missing values: retain last value
        if timestep_orig != time_step:
            # resample if needed (adjust timestep)
            series = series.resample(f"{time_step}Min").mean()
        return series


class Grid(ScenarioItem):
    """Information about how areas are connected"""

    class CarrierChoices(models.TextChoices):
        ELECTRICITY = "electricity", "Strom"
        DIESEL = "diesel", "Diesel"
        OIL = "oil", "Öl"
        GAS = "gas", "Gas"
        HEAT = "heat", "Wärme"
        H2 = "h2", "H2"

    carrier = models.CharField(
        choices=CarrierChoices,
        help_text=_("Energieträger des Netzes."),
        blank=False,
        default=CarrierChoices.ELECTRICITY,
    )
    feed_in = models.BooleanField(
        default=False, help_text=_("Gibt an, ob dieses Netz Einspeisung unterstützt.")
    )  # does this grid support feed-in?
    connected_to = models.ForeignKey(
        "Grid",
        on_delete=models.SET_NULL,
        default=None,
        null=True,
        blank=True,
        help_text=_("Netz, mit dem dieses Netz verbunden ist."),
    )
    load = models.ForeignKey(
        "Load",
        on_delete=models.SET_NULL,
        default=None,
        null=True,
        blank=True,
        help_text=_("Lastzeitreihe dieses Netzes."),
    )
    areas = models.ManyToManyField(
        "Area", help_text=_("Flächen, die an dieses Netz angeschlossen sind.")
    )

    display_formatters: ClassVar[dict[str, Callable[[Any], str]]] = {
        "load": lambda v: v.name,
    }

    power_max = models.FloatField(
        verbose_name=_("Kapazität"),
        default=None,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_(
            "Maximale Leistung des Netzanschlusspunkts in kW. Muss größer oder gleich 0 sein."
        ),
    )


class ElectricComponentTemplate(ItemTemplate):
    """Abstract template for electric components, defines shared characteristics"""

    power_kw = models.FloatField(
        verbose_name=_("Leistung"),
        default=None,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_("Nennleistung der Komponente in kW. Muss größer oder gleich 0 sein."),
    )
    efficiency = models.FloatField(
        verbose_name=_("Effizienz"),
        default=1.0,
        blank=False,
        null=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text=_("Wirkungsgrad der Komponente. Muss ein Wert zwischen 0 und 1 sein."),
    )
    power_min = models.FloatField(
        verbose_name=_("Minimale Leistung"),
        default=None,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_("Minimale Leistung der Komponente in kW. Muss größer oder gleich 0 sein."),
    )  # kWh
    power_max = models.FloatField(
        verbose_name=_("Maximale Leistung"),
        default=None,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_("Maximale Leistung der Komponente in kW. Muss größer oder gleich 0 sein."),
    )  # kWh
    capex = models.FloatField(
        verbose_name=_("CAPEX"),
        default=None,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_("Investitionskosten der Komponente in €. Muss größer oder gleich 0 sein."),
    )  # €
    opex = models.FloatField(
        verbose_name=_("OPEX"),
        default=None,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_(
            "Jährliche Betriebskosten der Komponente in €/a. Muss größer oder gleich 0 sein."
        ),
    )  # €/a

    class Meta:
        abstract = True  # abstract table

    # power_kw, power_min, power_max, capex, opex all use the
    # UNITS-based default from ItemTemplate.display_value()
    # efficiency needs the fraction -> percentage conversion, so it gets an explicit formatter.
    display_formatters: ClassVar[dict[str, Callable[[Any], str]]] = {
        "efficiency": lambda v: number_format(v * 100, 2) + " %",
    }

    def clean(self):
        # parent clean for default field validation
        super().clean()
        # check that power_max is not lower than power_min (if both are set)
        if (
            self.power_min is not None
            and self.power_max is not None
            and self.power_min > self.power_max
        ):
            raise ValidationError(
                {
                    "power_max": _(
                        "Minimale Leistung muss kleiner oder gleich der maximalen Leistung sein"
                    )
                }
            )

    def custom_fields(self):
        generic_field_names = {f.name for f in ElectricComponentTemplate._meta.get_fields()}
        for parent in ElectricComponentTemplate.__mro__[1:]:
            if hasattr(parent, "_meta"):
                generic_field_names |= {f.name for f in parent._meta.get_fields()}
        return [f.name for f in self._meta.get_fields() if f.name not in generic_field_names]

    @classmethod
    def get_default_args(cls) -> dict:
        return {}


class ElectricComponent(ScenarioItem, ElectricComponentTemplate):
    """Abstract electric component"""

    area = models.ForeignKey(
        Area,
        verbose_name=_("Fläche"),
        on_delete=models.CASCADE,
        help_text=_("Fläche, der diese Komponente zugeordnet ist."),
    )

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
        cls,
        scenario: Scenario,
        manager: User,
        area_internal_ids: list[str | uuid.UUID],
        **kwargs,
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

    list_icon = "icon.generator"
    icon = "icon.generator"

    class CarrierChoices(models.TextChoices):
        DIESEL = "diesel", "Diesel"
        OIL = "oil", "Öl"

    carrier = models.CharField(
        verbose_name=_("Energieträger"),
        choices=CarrierChoices,
        help_text=_("Energieträger, der vom Generator genutzt wird."),
    )

    def carrier_verbose(self):
        if not self.carrier:
            return "Keine Angabe"
        return self.get_carrier_display()

    class Meta:
        abstract = True


class GeneratorTemplate(AbstractGenerator, ElectricComponentTemplate):
    class Meta(ElectricComponentTemplate.Meta):
        verbose_name = _("Generator Vorlage")


class Generator(AbstractGenerator, ElectricComponent):
    template_model_name = GeneratorTemplate.model_name()

    class Meta(ElectricComponent.Meta):
        verbose_name = _("Generator")


class AbstractHeating(models.Model):
    """Transforms energy source to heat"""

    list_icon = "icon.heating"
    icon = "icon.heating"

    class CarrierChoices(models.TextChoices):
        DIESEL = "diesel", "Diesel"
        OIL = "oil", "Öl"
        ELECTRICITY = "electricity", "Strom"

    carrier = models.CharField(
        verbose_name=_("Energieträger"),
        choices=CarrierChoices,
        help_text=_("Energieträger, der von der Heizung genutzt wird."),
    )

    def carrier_verbose(self):
        if not self.carrier:
            return "Keine Angabe"
        return self.get_carrier_display()

    class Meta:
        abstract = True


class HeatingTemplate(AbstractHeating, ElectricComponentTemplate):
    class Meta(ElectricComponentTemplate.Meta):
        verbose_name = _("Heizung Vorlage")


class Heating(AbstractHeating, ElectricComponent):
    template_model_name = HeatingTemplate.model_name()

    class Meta(ElectricComponent.Meta):
        verbose_name = _("Heizung")


class AbstractCHP(models.Model):
    """Combined heat and power (Blockheizkraftwerk)"""

    list_icon = "icon.chp"
    icon = "icon.chp"

    class CarrierChoices(models.TextChoices):
        DIESEL = "diesel", "Diesel"
        OIL = "oil", "Öl"
        GAS = "gas", "Gas"

    # override of electric components efficiency
    efficiency = models.FloatField(
        verbose_name=_("Thermische Effizienz"),
        default=1.0,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text=_("Thermischer Wirkungsgrad. Muss ein Wert zwischen 0 und 1 sein."),
    )

    carrier = models.CharField(
        verbose_name=_("Energieträger"),
        choices=CarrierChoices,
        help_text=_("Energieträger, der von der Kraft-Wärme-Kopplung genutzt wird."),
    )

    power_to_heat_ratio = models.FloatField(
        verbose_name=_("Stromkennzahl"),
        default=1.0,
        # https://de.wikipedia.org/wiki/Stromkennzahl for combustion engine up to 1.2
        validators=[MinValueValidator(0), MaxValueValidator(2)],
        help_text=_("Verhältnis aus elektrischer Energie zu nutzbarer Wärmeenergie."),
    )

    @classmethod
    def get_default_args(cls) -> dict:
        return {"carrier": cls.CarrierChoices.GAS}

    def carrier_verbose(self):
        if not self.carrier:
            return "Keine Angabe"
        return self.get_carrier_display()

    class Meta:
        abstract = True


class CHPTemplate(AbstractCHP, ElectricComponentTemplate):
    class Meta(ElectricComponentTemplate.Meta):
        verbose_name = _("Kraft-Wärme-Kopplung Vorlage")


class CHP(AbstractCHP, ElectricComponent):
    template_model_name = CHPTemplate.model_name()

    class Meta(ElectricComponent.Meta):
        verbose_name = _("Kraft-Wärme-Kopplung")


class AbstractFuelCell(models.Model):
    """Transform H2 into electricity"""

    list_icon = "icon.fuelcell"
    icon = "icon.fuelcell"

    # override of electric components efficiency which would be used otherwise
    efficiency = models.FloatField(
        verbose_name=_("Thermische Effizienz"),
        default=1.0,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text=_("Thermischer Wirkungsgrad. Muss ein Wert zwischen 0 und 1 sein."),
    )

    power_to_heat_ratio = models.FloatField(
        verbose_name=_("Stromkennzahl"),
        default=1.0,
        # https://de.wikipedia.org/wiki/Stromkennzahl for fuel cell up to 3
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        help_text=_("Verhältnis aus elektrischer Energie zu nutzbarer Wärmeenergie."),
    )

    class Meta:
        abstract = True


class FuelCellTemplate(AbstractFuelCell, ElectricComponentTemplate):
    class Meta(ElectricComponentTemplate.Meta):
        verbose_name = _("Brennstoffzelle Vorlage")


class FuelCell(AbstractFuelCell, ElectricComponent):
    template_model_name = FuelCellTemplate.model_name()

    class Meta(ElectricComponent.Meta):
        verbose_name = _("Brennstoffzelle")


class AbstractElectrolyzer(models.Model):
    """Transform electricity into H2"""

    list_icon = "icon.electrolyzer"
    icon = "icon.electrolyzer"

    class Meta:
        abstract = True


class ElectrolyzerTemplate(
    AbstractElectrolyzer,
    ElectricComponentTemplate,
):
    class Meta(ElectricComponentTemplate.Meta):
        verbose_name = _("Elektrolyseur Vorlage")


class Electrolyzer(AbstractElectrolyzer, ElectricComponent):
    template_model_name = ElectrolyzerTemplate.model_name()

    class Meta(ElectricComponent.Meta):
        verbose_name = _("Elektrolyseur")


class AbstractHeatpump(models.Model):
    """Transform electricity into heat"""

    list_icon = "icon.heat_pump"
    icon = "icon.heat_pump"

    # carrier is always electricity
    class HeatChoices(models.TextChoices):
        AIR = "air", "Luft"
        WATER = "water", "Wasser"

    heatsource = models.CharField(
        choices=HeatChoices,
        blank=False,
        null=True,
        help_text=_("Wärmequelle der Wärmepumpe."),
    )

    efficiency = models.FloatField(  # override efficiency to allow values > 1
        verbose_name=_("Leistungszahl (COP)"),
        default=1.0,
        blank=False,
        null=True,
        validators=[MinValueValidator(0)],
        help_text=_("COP der Wärmepumpe. Muss ein Wert größer 0 sein."),
    )

    @classmethod
    def get_default_args(cls) -> dict:
        return {"heatsource": cls.HeatChoices.WATER}

    class Meta:
        abstract = True


class HeatpumpTemplate(
    AbstractHeatpump,
    ElectricComponentTemplate,
):
    class Meta(ElectricComponentTemplate.Meta):
        verbose_name = _("Wärmepumpe Vorlage")


class Heatpump(AbstractHeatpump, ElectricComponent):
    template_model_name = HeatpumpTemplate.model_name()

    class Meta(ElectricComponent.Meta):
        verbose_name = _("Wärmepumpe")


class AbstractSolar(models.Model):
    """Photovoltaics
    The profile is the 'Einstrahlungsprofil' for the plane of the pv module,
    e.g. 30˚ Incline towards south, with the locale sun radiation power.
    """

    list_icon = "icon.solar"
    icon = "icon.solar"

    profile = models.ForeignKey(
        "SolarRadiation",
        verbose_name="Einstrahlungsprofil",
        on_delete=models.SET_NULL,
        default=None,
        null=True,
        blank=True,
        help_text=_("Spezifisches Einstrahlungsprofil in W/m²"),
    )
    spec_power = models.FloatField(
        default=210,
        verbose_name="Spezifische Peak-Leistung",
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_(
            "Spezifische Peak-Leistung der Solaranlage in W/m². Muss größer oder gleich 0 sein. Die Eingabe aktualisiert die installierte Leistung automatisch, wenn eine Fläche mit installierter Photovoltaik angegeben ist."
        ),
    )  # W/m^2

    surface_area_installed = models.FloatField(
        default=None,
        verbose_name="Fläche mit installierter Photovoltaik",
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_(
            "Installierte Fläche der Solaranlage in m². Muss größer oder gleich 0 sein. Die Eingabe aktualisiert die installierte Leistung automatisch."
        ),
    )  # m^2

    surface_area_min = models.FloatField(
        default=None,
        verbose_name="Minimale Fläche mit Photovoltaik",
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_("Minimale Fläche der Solaranlage in m². Muss größer oder gleich 0 sein."),
    )  # m^2
    surface_area_max = models.FloatField(
        default=None,
        verbose_name="Maximale Fläche mit Photovoltaik",
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_("Maximale Fläche der Solaranlage in m². Muss größer oder gleich 0 sein."),
    )  # m^2

    class Meta:
        abstract = True


class SolarTemplate(AbstractSolar, ElectricComponentTemplate):
    class Meta(ElectricComponentTemplate.Meta):
        verbose_name = _("Solaranlage Vorlage")


class Solar(AbstractSolar, ElectricComponent):
    template_model_name = SolarTemplate.model_name()

    class Meta(ElectricComponent.Meta):
        verbose_name = _("Solaranlage")


class AbstractStorage(models.Model):
    """Generic energy storage"""

    class CarrierChoices(models.TextChoices):
        ELECTRICITY = "electricity", "Strom"  # battery
        HEAT = "heat", "Wärme"
        H2 = "h2", "H2"

    carrier = models.CharField(
        choices=CarrierChoices,
        verbose_name=_("Energieträger"),
        null=True,
        blank=False,
        help_text=_("Energieträger des Speichers."),
    )
    efficiency_load = models.FloatField(
        verbose_name=_("Lade-Effizienz"),
        default=1.0,
        null=True,
        blank=False,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text=_("Wirkungsgrad beim Beladen. Muss ein Wert zwischen 0 und 1 sein."),
    )
    efficiency_store = models.FloatField(
        verbose_name=_("Entlade-Effizienz"),
        default=1.0,
        null=True,
        blank=False,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text=_("Wirkungsgrad beim Entladen. Muss ein Wert zwischen 0 und 1 sein."),
    )
    # capacity: unit depends on carrier. kWh for electricity and heat, liters for H2
    capacity_installed = models.FloatField(
        verbose_name=_("Kapazität des Speichers"),
        default=None,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_(
            "Installierte Kapazität des Speichers (kWh oder Liter, abhängig vom Energieträger). Muss größer oder gleich 0 sein."
        ),
    )
    capacity_min = models.FloatField(
        verbose_name=_("Mindest-Kapazität des Speichers"),
        default=None,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_(
            "Minimale Kapazität des Speichers (kWh oder Liter, abhängig vom Energieträger). Muss größer oder gleich 0 sein. Dieser Wert wird nur bei Optimierungen genutzt."
        ),
    )
    capacity_max = models.FloatField(
        verbose_name=_("Maximal-Kapazität des Speichers"),
        default=None,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_(
            "Maximale Kapazität des Speichers (kWh oder Liter, abhängig vom Energieträger). Muss größer oder gleich 0 sein. Dieser Wert wird nur bei Optimierungen genutzt."
        ),
    )
    capex = models.FloatField(
        default=None,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_(
            "Investitionskosten des Speichers (€/kWh oder €/l). Muss größer oder gleich 0 sein."
        ),
    )  # €/kWh, €/l

    def carrier_verbose(self):
        if not self.carrier:
            return "Keine Angabe"
        return self.get_carrier_display()

    # For the same rendering process and look as Electric components
    def custom_fields(self):
        generic_field_names = {f.name for f in ElectricComponentTemplate._meta.get_fields()}
        for parent in ElectricComponentTemplate.__mro__[1:]:
            if hasattr(parent, "_meta"):
                generic_field_names |= {f.name for f in parent._meta.get_fields()}
        return [f.name for f in self._meta.get_fields() if f.name not in generic_field_names]

    class Meta:
        abstract = True


class StorageTemplate(AbstractStorage, ItemTemplate):
    class Meta(ElectricComponentTemplate.Meta):
        verbose_name = _("Speicher Vorlage")


class Storage(AbstractStorage, ScenarioItem):  # order important (Meta)
    area = models.ForeignKey(
        Area,
        on_delete=models.CASCADE,
        help_text=_("Fläche, der dieser Speicher zugeordnet ist."),
    )
    template_model_name = StorageTemplate.model_name()

    class Meta(ScenarioItem.Meta):
        verbose_name = _("Speicher")


# ------------------------------------------------------------------------------
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

    name = models.TextField(blank=False, null=True, help_text=_("Name der Datei."))
    file = models.FileField(upload_to=settings.UPLOAD_PATH, help_text=_("Hochgeladene Datei."))
    # This lets us attach any object to the uploaded file
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField()
    # on_delete=CASCADE is the behaviour of GenericForeignKey.
    # Changing that is possible via signals
    content_object = GenericForeignKey("content_type", "object_id")


@receiver(models.signals.m2m_changed, sender=Grid.areas.through)
def prohibit_multi_grid_of_same_carrier_per_area(sender, instance, **kwargs):
    # NOTE: This does not catch the direct bulk creation via through ,e.g. Grid.areas.through.bulk_create(..)

    # different type of m2m_changed signals exist.
    # prohibition is done before it happens
    if kwargs["action"] != "pre_add":
        return
    area_id_set = kwargs["pk_set"]
    # instance is a grid not a Grid.areas.through
    # does this through model already contain a row with this area and grid__carrier
    if sender.objects.filter(area_id__in=area_id_set, grid__carrier=instance.carrier).exists():
        raise DuplicateGridCarrierError("An area can only have one grid per carrier")


@receiver(models.signals.pre_delete, sender=UploadedFile)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    if instance.file:
        path = Path(instance.file.path)
        if path.exists():
            path.unlink()


# Results
class Result(models.Model):
    scenario = models.OneToOneField(Scenario, on_delete=models.CASCADE)
    started_at = models.DateTimeField(default=None)
    finished_at = models.DateTimeField(default=None)


class ResultData(models.Model):
    """Generalized oemof result

    from/to_node: associated ScenarioItem, by internal_id.
      May be None if not applicable (general scenario info, not part of scenario).
      For attribute=flow, at least one node is usually a grid.
    attribute:
      - timeindex: value contains UNIX-timestamps for every timestep
      - costs: value contains oemof objective cost
      - flow: value contains timeseries for a connection between nodes
      - storage: value contains timeseries for storage content of node

    """

    result = models.ForeignKey(Result, on_delete=models.CASCADE)
    from_node = models.UUIDField(default=None, null=True, blank=True)
    to_node = models.UUIDField(default=None, null=True, blank=True)
    attribute = models.CharField(max_length=255)
    value = ArrayField(base_field=models.FloatField())


# Custom Exceptions
class MissingFormValueException(Exception):
    pass


class DuplicateGridCarrierError(Exception):
    """Raised when an Area would be linked to more than one Grid of the same carrier."""
