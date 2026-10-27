from collections import defaultdict
from typing import Any

import django.forms as forms
from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.gis.forms import PolygonField
from django.contrib.gis.geos import GEOSGeometry
from django.db.models import ForeignKey
from django.db.models import Q
from django.db.models import QuerySet
from django.db.models import TextChoices
from django.forms import CharField
from django.forms import ValidationError
from django.forms import modelform_factory
from django.utils import timezone
from guardian.shortcuts import assign_perm
from guardian.shortcuts import get_perms
from guardian.shortcuts import remove_perm

from .authorization import has_authorization
from .constants import GRID_FIELD_PREFIX
from .models import UNITS
from .models import Area
from .models import ChangedItem
from .models import DuplicateGridCarrierError
from .models import ElectricComponent
from .models import EnergyCost
from .models import Grid
from .models import ItemTemplate
from .models import Load
from .models import MissingFormValueException
from .models import Project
from .models import Scenario
from .models import ScenarioItem
from .models import Solar
from .models import Timeseries
from .models import update_scenario_post_save
from .util import duplicate_scenario_with_permissions


class ScenarioChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.name


def AreaItemFormFactory():
    return modelform_factory(
        Area,
        fields=["internal_id", "geom"],
        field_classes={"geom": GeoJSONPolygonField},
        widgets={
            # "internal_id": forms.HiddenInput(),
            "internal_id": forms.TextInput(),
            "geom": GeoJSONWidget(),
        },
    )


class ScenarioAreasForm(forms.Form):
    geojson_ports_regions_file = forms.FileField(required=True)
    geojson_ports_buildings_file = forms.FileField(required=True)


def TemplateFormFactory(ItemModel: type[ItemTemplate], user: User | None = None):
    exclude = ["manager", "updated_user"]
    fk_fields = [f.name for f in ItemModel._meta.get_fields() if isinstance(f, ForeignKey)]
    if issubclass(ItemModel, ItemTemplate):
        # exclude all foreign key fields.
        # Templates should be atomic so they can be easily applied without side effects
        # manager
        exclude = set(exclude + fk_fields)
        # Templates are not bound to a scenario, so all relation querysets stay empty
        BaseForm = modelform_factory(
            ItemModel,
            form=ScopedModelForm,
            exclude=exclude,
            widgets={
                "internal_id": forms.HiddenInput(),
                "name": forms.TextInput(),
                "description": forms.Textarea(attrs={"rows": 2, "cols": 15}),
            },
        )

    else:
        raise NotImplementedError()

    # NOTE: if subclassing this Baseform this loop should be moved into a own baseforms __init__
    # or else the adjustments below would go missing

    # All fields are optional for a Template except name
    # and internal_id
    for field_name, field in BaseForm.base_fields.items():
        if field_name not in ("name", "internal_id"):
            field.required = False

    return BaseForm


def ScenarioItemFormFactory(
    ItemModel: type[ScenarioItem],
    scenario: Scenario,
    user: User,
    multi: bool = False,
    instance: ScenarioItem | None = None,
    area_type: str | None = None,
    **kwargs,
):
    exclude = ["manager", "scenario", "updated_user", "description"]
    fk_fields = [f.name for f in ItemModel._meta.get_fields() if isinstance(f, ForeignKey)]
    if ItemModel == Area:
        exclude = exclude + ["area_type", "geom"]
        field_classes = {"geom": GeoJSONPolygonField}
        for fk_f in filter(lambda x: x not in exclude, fk_fields):
            field_classes[fk_f] = InternalIDModelChoiceField
        BaseForm = modelform_factory(
            ItemModel,
            exclude=exclude,
            form=AreaForm,
            field_classes=field_classes,
            widgets={
                "internal_id": forms.HiddenInput(),
                # "geom": GeoJSONWidget(),
                "name": forms.Textarea(attrs={"rows": 1, "cols": 15}),
                # "description": forms.Textarea(attrs={"rows": 2, "cols": 15}),
            },
        )
        BaseForm.area_type = area_type or getattr(instance, "area_type", None)

    elif ItemModel == Load or issubclass(ItemModel, ElectricComponent):
        if ItemModel == Load:
            form = LoadForm
        elif ItemModel == Solar:
            form = SolarForm
        else:
            form = ScopedModelForm

        exclude = exclude + ["area"]
        field_classes = {}
        for fk_f in filter(lambda x: x not in exclude, fk_fields):
            field_classes[fk_f] = InternalIDModelChoiceField
        BaseForm = modelform_factory(
            ItemModel,
            exclude=exclude,
            form=form,
            field_classes=field_classes,
            widgets={
                "internal_id": forms.HiddenInput(),
                "name": forms.TextInput(),
                "description": forms.Textarea(attrs={"rows": 2, "cols": 15}),
            },
        )

    elif ItemModel == Grid:
        fields = ["internal_id", "name", "feed_in", "power_max", "load"]
        if not instance:
            # only allow a carrier when there is no instance yet
            fields.append("carrier")

        BaseForm = modelform_factory(
            Grid,
            fields=fields,
            form=GridForm,
            field_classes={"load": InternalIDModelChoiceField},
            widgets={
                "name": forms.TextInput(),
                "internal_id": forms.HiddenInput(),
            },
        )
    else:
        raise NotImplementedError()

    # bind the request context used by ScopedModelForm.scope_querysets
    BaseForm.scope_user = user
    BaseForm.scope_scenario = scenario

    if not multi:
        return BaseForm

    class BulkForm(BaseForm):
        internal_ids = UUIDMultipleChoiceField(
            queryset=ItemModel.objects.none(),
            widget=forms.HiddenInput(),
        )
        multi = True
        # only used by area forms
        grid_empty_label = "Keine Anpassung vornehmen"

        def scope_querysets(self) -> None:
            super().scope_querysets()
            self.fields["internal_ids"].queryset = ItemModel.objects.filter(
                scenario=self.scope_scenario
            )

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields.pop("internal_id")
            self.fields.pop("is_public", None)
            self.fields.pop("name")
            # For multi forms no fields are required.
            # this allows partial overwriting.
            # e.g. efficiency would normally be required
            # 2 components with differing efficiency could not be saved,
            # without overwriting the efficiency with a common value
            for field in self.fields.values():
                field.required = False

        def save(self):
            # UUIDMultipleChoiceField returns queryset
            qs = self.cleaned_data["internal_ids"]
            if not qs:
                return
            data = {
                k: v
                for k, v in self.cleaned_data.items()
                if k in self.fields and k != "internal_ids" and v not in [None, ""]
            }
            data["updated_at"] = timezone.now()
            if self.instance._meta.model == Area:
                grids = []
                for key in data.copy():
                    if key.startswith(GRID_FIELD_PREFIX):
                        grids.append(data.pop(key))
                scenario_id = qs.first().scenario_id
                for grid in grids:
                    assert grid.scenario_id == scenario_id
                    # Delete old connection between grid and areas
                    Grid.areas.through.objects.filter(
                        area__in=qs, grid__scenario_id=scenario_id, grid__carrier=grid.carrier
                    ).delete()
                    grid.areas.add(*qs)

            qs.update(**data)
            changed_items = []
            for instance in qs.all():
                changed_item = ChangedItem.from_scenario_item(instance)
                changed_items.append(changed_item)
            ChangedItem.objects.bulk_create(changed_items)

            # also trigger a scenario changed event through signals
            update_scenario_post_save(qs.model, qs.first())
            return qs

    return BulkForm


class GeoJSONPolygonField(PolygonField):
    def to_python(self, value):
        if not value:
            return None
        # Convert GeoJSON string to GEOSGeometry
        geom = GEOSGeometry(value, srid=4326)
        if not geom.valid:
            raise forms.ValidationError(geom.valid_reason)
        return super().to_python(str(geom))


class GeoJSONWidget(forms.Textarea):
    def format_value(self, value):
        if value is None:
            return ""
        if hasattr(value, "geojson"):
            return value.geojson
        return value


class InternalIDModelChoiceField(forms.ModelChoiceField):
    def __init__(self, queryset, **kwargs):
        # ForeignKey.formfield() always passes to_field_name="id".
        # Needs hard overwrite
        kwargs["to_field_name"] = "internal_id"
        super().__init__(queryset, **kwargs)

    def prepare_value(self, value):
        # ModelForm initial data holds the related object's pk,
        # while the choices are keyed by internal_id
        if isinstance(value, int):
            value = self.queryset.filter(pk=value).values_list("internal_id", flat=True).first()
        return super().prepare_value(value)


ALLOWED_UPLOAD_SUFFIXES = [".csv"]


class TimeseriesUploadForm(forms.Form):
    allowed_suffixes = ALLOWED_UPLOAD_SUFFIXES
    # Set both required to false, so the fields to not mess with the outer load form
    # otherwise we would need form injection for the inputs

    template_file = forms.FileField(
        label="Vorlagedatei",
        widget=forms.FileInput(
            attrs={
                "accept": ",".join(ALLOWED_UPLOAD_SUFFIXES),
                "title": "Datei auswählen",
            }
        ),
        required=False,
    )
    timestep_minutes = forms.FloatField(
        label="Zeitschritt (Minuten)",
        initial=15,
        min_value=1,
        required=False,
    )

    def clean_template_file(self):
        file = self.cleaned_data.get("template_file")
        if not file:
            raise ValidationError("Keine Datei ausgewählt")

        suffix = "." + file.name.split(".")[-1].lower()
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise ValidationError(
                "Nicht unterstützter Dateityp. Erlaubt sind: " + ", ".join(ALLOWED_UPLOAD_SUFFIXES)
            )
        values = Timeseries.values_from_csv(file=file)
        if not values:
            raise ValidationError("Keine numerischen Werte gefunden")
        self._parsed_values = values
        return file

    def save(self, scenario, user):
        values = self._parsed_values
        timestep_minutes = self.cleaned_data["timestep_minutes"]
        file = self.cleaned_data["template_file"]
        template_load = Timeseries.objects.create(
            timeseries={"values": values, "timestep_minutes": timestep_minutes},
            spec_load=sum(values) / len(values),
            scenario=scenario,
            name=file.name,
            manager=user,
        )

        return template_load


class UUIDMultipleChoiceField(CharField):
    def __init__(self, queryset, **kwargs) -> None:
        super().__init__(**kwargs)
        self.queryset = queryset

    def to_python(self, value):
        if not value:
            return self.queryset.none()
        ids = value.split(",")
        return self.queryset.filter(internal_id__in=ids)

    def validate(self, value) -> None:
        if value.count() == 0:
            raise ValidationError(self.error_messages["required"], code="required")

    def clean(self, value):
        value = self.to_python(value)
        if not isinstance(value, QuerySet):
            raise ValidationError(
                self.error_messages["invalid_list"],
                code="invalid_list",
            )
        # we run custom validators here
        self.validate(value)
        self.run_validators(value)
        if not isinstance(value, QuerySet):
            raise ValidationError(self.error_messages["required"], code="required")
        return value


class ScopedModelForm(forms.ModelForm):
    """ModelForm with safe defaults for relation fields

    All relation querysets are emptied on init, so no items can leak into the form.
    Subclasses explicitly allow items in scope_querysets(), which only runs
    when the form is bound to a user and scenario (see ScenarioItemFormFactory).
    """

    scope_user: User | None = None
    scope_scenario: Scenario | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            field.unit = UNITS.get(field_name, "")
            if isinstance(field, forms.ModelChoiceField | UUIDMultipleChoiceField):
                field.queryset = field.queryset.none()
        if self.scope_user is not None and self.scope_scenario is not None:
            self.scope_querysets()

    def scope_querysets(self) -> None:
        """Set the querysets of the relation fields the user may select from"""
        pass

    def own_or_current(self, field_name: str, model: type[ScenarioItem]) -> QuerySet:
        """Items of the user in the scenario, plus the currently selected one"""
        allowed = Q(manager=self.scope_user, scenario=self.scope_scenario)
        current_id = getattr(self.instance, field_name + "_id", None)
        if current_id:
            allowed |= Q(id=current_id)
        return model.objects.filter(allowed)


def get_timeseries_for_user(user: User, scenario: Scenario) -> QuerySet[Timeseries]:
    return Timeseries.objects.filter(manager=user, scenario=scenario)


class LoadForm(ScopedModelForm):
    def scope_querysets(self) -> None:
        # TODO: should the timeseries stay bound to the scenario OR
        # be a TimeseriesTemplate without being bound to the scenario instead?
        # When a LoadTemplate is used in a different scenario it needs to be handled!
        # E.g. by copying timeseries to new scenario
        self.fields["timeseries"].queryset = self.own_or_current("timeseries", Timeseries)


class SolarForm(ScopedModelForm):
    def scope_querysets(self) -> None:
        self.fields["profile"].queryset = self.own_or_current("profile", Load)


class GridForm(ScopedModelForm):
    def scope_querysets(self) -> None:
        self.fields["load"].queryset = self.own_or_current("load", Load)


def get_shared_grids(areas: QuerySet[Area]) -> dict[str, Grid]:
    """Get a dictionary of the grids shared by all areas with key of grid_{grid_type}"""
    initial = {}
    area_ids = set(areas.values_list("id", flat=True))
    if not area_ids:
        return {}
    # all links <grid_id <-> area_id>
    links = Grid.areas.through.objects.filter(area_id__in=area_ids).select_related("grid")
    carriers_per_area = defaultdict(set)
    shared_grids = defaultdict(set)
    # shared grid = all areas are linked to this grid
    for link in links:
        # enforce that each area has at most 1 grid per gridtype
        if link.grid.carrier in carriers_per_area[link.area_id]:
            raise DuplicateGridCarrierError("Area has multiple grids per carrier")
        carriers_per_area[link.area_id].add(link.grid.carrier)

        shared_grids[link.grid].add(link.area_id)

    for grid, linked_area_ids in shared_grids.items():
        if len(linked_area_ids) == len(area_ids):
            initial[GRID_FIELD_PREFIX + grid.carrier] = grid
    return initial


class AreaFormBase(ScopedModelForm):
    multi = False
    # fallback when the instance has no area_type, e.g. bulk forms (see ScenarioItemFormFactory)
    area_type: str | None = None
    is_public = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(),
        label="Für andere im Projekt sichtbar machen",
    )
    grid_empty_label = "Kein Netz dieses Typs"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for carrier in Grid.CarrierChoices:
            if field := self.fields.get(GRID_FIELD_PREFIX + carrier):
                field.empty_label = self.grid_empty_label

        area_type = self.instance.area_type or self.area_type
        if not area_type:
            raise MissingFormValueException(
                "The area form needs an area_type to show the correct usage choices. "
                "It can be provided by the instance or by ScenarioItemFormFactory"
            )
        if area_type == Area.AreaTypeChoices.BUILDING:
            usage_choices = Area.BuildingUsageChoices
        else:
            usage_choices = Area.OpenUsageChoices
        if self.multi:
            # dict choices.choices = key: choice_value, value: choice_label->cant cast to dict directly
            # textchoices expects choice_label:choice_value
            usage_choices = TextChoices(
                "MultiUsageChoices",
                {**{x[1]: x[0] for x in usage_choices.choices}, "Keine Veränderung": ""},
            )
        else:
            self.fields["usage"].required = True

        self.fields["usage"].choices = usage_choices

        # an unbound form of an existing area or areas shows its grids.
        # the single area form also sets the public initial data
        # Explicitly passed initial values (e.g. from templates) take precedence
        if (self.instance.pk or self.multi) and not self.is_bound:
            if not self.scope_scenario:
                raise Exception("ScopedModelForm needs a scenario and user injection on the type")
            # If Object permissions are queried multiple times consider using a
            # guardian.core.ObjectPermissionChecker
            if not self.multi:
                is_public = "details" in get_perms(self.get_project_group(), self.instance)
                self.initial.setdefault("is_public", is_public)
                areas = Area.objects.filter(scenario=self.scope_scenario, pk=self.instance.pk)
            else:
                internal_ids = [x for x in self.initial.get("internal_ids", "").split(",") if x]
                areas = Area.objects.filter(
                    scenario=self.scope_scenario, internal_id__in=internal_ids
                )
            for key, grid in get_shared_grids(areas).items():
                self.initial.setdefault(key, grid)

    def scope_querysets(self) -> None:
        for carrier in Grid.CarrierChoices:
            self.fields[GRID_FIELD_PREFIX + carrier].queryset = Grid.objects.filter(
                scenario=self.scope_scenario, carrier=carrier
            )

    def get_project_group(self) -> Group:
        return Group.objects.get(name=self.instance.scenario.project.group_name())

    def save(self, commit: bool = True) -> Area:
        instance = super().save(commit=commit)
        if commit:
            self.save_relations()
        return instance

    def save_relations(self) -> None:
        """Save is_public as project group permission and recreate the grid connections"""
        group = self.get_project_group()
        if self.cleaned_data.get("is_public"):
            assign_perm("details", group, self.instance)
        else:
            remove_perm("details", group, self.instance)

        # Delete previous assignment of area -> grid
        # All connections are recreated next based on the current selection
        Grid.areas.through.objects.filter(area=self.instance).delete()
        Grid.areas.through.objects.bulk_create(
            Grid.areas.through(area_id=self.instance.id, grid_id=grid.id)
            for key, grid in self.cleaned_data.items()
            if key.startswith(GRID_FIELD_PREFIX) and isinstance(grid, Grid)
        )


# Each Area can be connected to one grid of each carrier.
# The grid selects are declared fields, so they are inherited by the bulk form
AreaForm = type(
    "AreaForm",
    (AreaFormBase,),
    {
        GRID_FIELD_PREFIX + carrier: InternalIDModelChoiceField(
            queryset=Grid.objects.none(),
            label=carrier.label + "-Netz",
            required=False,
        )
        for carrier in Grid.CarrierChoices
    },
)


class ChangeProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(),
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class ChangeScenarioForm(forms.ModelForm):
    class Meta:
        model = Scenario
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(),
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class ChangeTimeseriesForm(forms.ModelForm):
    class Meta:
        model = Timeseries
        fields = ("name",)
        widgets = {
            "name": forms.TextInput(),
        }


class EnergyCostForm(forms.ModelForm):
    class Meta:
        model = EnergyCost
        fields = (
            "co2_price",
            "electricity_price",
            "heat_price",
            "oil_price",
            "diesel_price",
            "gasoline_price",
            "hydrogen_price",
            "ammonia_price",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            field.unit = UNITS.get(field_name, "")


class CreateScenarioForm(forms.ModelForm):
    base_scenario: Scenario | None = None
    user: User | None = None

    def __init__(self, *args, base_scenario: Scenario = None, user: User | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        assert base_scenario is not None
        self.base_scenario = base_scenario
        self.user = user

    class Meta:
        model = Scenario
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self) -> dict[str, Any]:
        if not has_authorization(self.base_scenario.project, self.user, "details"):
            raise ValidationError(
                self.error_messages["no_authorization"],
                code="no_authorization",
            )
        return super().clean()

    def save(self, commit: bool = True):
        new_scenario = duplicate_scenario_with_permissions(self.base_scenario, self.user)
        new_scenario.name = self.cleaned_data["name"]
        new_scenario.description = self.cleaned_data["description"]
        new_scenario.save()
        return new_scenario


class CreateProjectForm(forms.ModelForm):
    template_scenario_internal_id = ScenarioChoiceField(
        Scenario.objects.none(), to_field_name="internal_id", required=False
    )

    def __init__(self, *args, template_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if template_queryset is not None:
            self.fields["template_scenario_internal_id"].queryset = template_queryset

    class Meta:
        model = Project
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(),
            "description": forms.Textarea(attrs={"rows": 3}),
        }
