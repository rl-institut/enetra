from typing import Any

import django.forms as forms
from django.contrib.auth.models import User
from django.contrib.gis.forms import PolygonField
from django.contrib.gis.geos import GEOSGeometry
from django.db.models import ForeignKey
from django.db.models import Q
from django.db.models import QuerySet
from django.forms import CharField
from django.forms import ValidationError
from django.forms import modelform_factory
from django.utils import timezone

from ports.models import update_scenario_post_save

from .authorization import has_authorization
from .models import UNITS
from .models import Area
from .models import ChangedItem
from .models import ElectricComponent
from .models import Grid
from .models import ItemTemplate
from .models import Load
from .models import Project
from .models import Scenario
from .models import ScenarioItem
from .models import Timeseries
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


def TemplateFormFactory(ItemModel: type[ItemTemplate]):
    # TODO:
    # FIXME:: Add authorization, e.g. pass User and only allow queries on permissed elements
    exclude = ["manager", "updated_user"]
    fk_fields = [f.name for f in ItemModel._meta.get_fields() if isinstance(f, ForeignKey)]
    if issubclass(ItemModel, ItemTemplate):
        # exclude all foreign key fields.
        # Templates should be atomic so they can be easily applied without side effects
        # manager
        exclude = set(exclude + fk_fields)
        BaseForm = modelform_factory(
            ItemModel,
            exclude=exclude,
            widgets={
                "internal_id": forms.HiddenInput(),
                "name": forms.TextInput(),
                "description": forms.Textarea(attrs={"rows": 2, "cols": 15}),
            },
        )

    else:
        raise NotImplementedError()

    # All fields are optional for a Template except name
    # and internal_id
    for field_name, field in BaseForm.base_fields.items():
        if field_name not in ("name", "internal_id"):
            field.required = False

    # Add units
    for field_name, field in BaseForm.base_fields.items():
        field.unit = UNITS.get(field_name, "")
    return BaseForm


def get_timeseries_for_user(user: User, scenario: Scenario, instance: Load | None = None):
    # TODO: should the timeseries stay bound to the scenario OR
    # be a TimeseriesTemplate without being bound to the scenario instead?
    # When a LoadTemplate is used in a different scenario it needs to be handled!
    # E.g. by copying timeseries to new scenario
    timeseries = Timeseries.objects.filter(manager=user, scenario=scenario)

    # add the current value
    current = getattr(instance, "timeseries", None)
    if instance and current:
        timeseries = Timeseries.objects.filter(
            Q(manager=user, scenario=scenario) | Q(id=current.id)
        )
    return timeseries


def get_loads_for_grid(user: User, scenario: Scenario, instance: "Grid | None" = None):
    loads = Load.objects.filter(manager=user, scenario=scenario)

    current = getattr(instance, "timeseries", None)
    if instance and current:
        loads = Load.objects.filter(Q(manager=user, scenario=scenario) | Q(id=current.id))
    return loads


def ScenarioItemFormFactory(
    ItemModel: type[ScenarioItem],
    scenario: Scenario,
    user: User,
    multi: bool = False,
    instance: ScenarioItem | None = None,
    **kwargs,
):
    # TODO:
    # FIXME:: Add authorization, e.g. pass User and only allow queries on permissed elements
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
            field_classes=field_classes,
            widgets={
                "internal_id": forms.HiddenInput(),
                # "geom": GeoJSONWidget(),
                "name": forms.Textarea(attrs={"rows": 1, "cols": 15}),
                # "description": forms.Textarea(attrs={"rows": 2, "cols": 15}),
            },
        )
        BaseForm.base_fields["is_public"] = forms.BooleanField(
            required=False,
            widget=forms.CheckboxInput(),
            label="Für andere im Projekt sichtbar machen",
        )

        # dynamic creation of grid selects
        # get gridtypes. Each Area can be connected to one grid of each type
        for gtype in Grid.CarrierChoices:
            BaseForm.base_fields["grid_" + gtype] = InternalIDModelChoiceField(
                queryset=Grid.objects.filter(
                    scenario=scenario,
                    carrier=gtype,
                ),
                label=gtype.label + "-Netz",
                required=False,
                empty_label="Kein Netz dieses Typs",
            )

    elif ItemModel == Load or issubclass(ItemModel, ElectricComponent):
        exclude = exclude + ["area"]
        field_classes = {}
        for fk_f in filter(lambda x: x not in exclude, fk_fields):
            field_classes[fk_f] = InternalIDModelChoiceField
        BaseForm = modelform_factory(
            ItemModel,
            exclude=exclude,
            field_classes=field_classes,
            widgets={
                "internal_id": forms.HiddenInput(),
                "name": forms.TextInput(),
                "description": forms.Textarea(attrs={"rows": 2, "cols": 15}),
            },
        )

        if ItemModel == Load:
            BaseForm.base_fields["timeseries"].queryset = get_timeseries_for_user(
                user, scenario, instance
            )
    elif ItemModel == Grid:
        fields = ["internal_id", "name", "feed_in", "power_max", "timeseries"]
        if not instance:
            # only allow a carrier when there is no instance yet
            fields.append("carrier")

        BaseForm = modelform_factory(
            Grid,
            fields=fields,
            field_classes={"timeseries": InternalIDModelChoiceField},
            widgets={
                "name": forms.TextInput(),
                "internal_id": forms.HiddenInput(),
            },
        )
        BaseForm.base_fields["timeseries"].queryset = get_loads_for_grid(user, scenario, instance)
    else:
        raise NotImplementedError()

    # Add units
    for field_name, field in BaseForm.base_fields.items():
        field.unit = UNITS.get(field_name, "")

    if not multi:
        return BaseForm

    class BulkForm(BaseForm):
        internal_ids = UUIDMultipleChoiceField(
            queryset=ItemModel.objects.filter(scenario=scenario),
            widget=forms.HiddenInput(),
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
                    if "grid" in key:
                        grids.append(data.pop(key))
                for grid in grids:
                    assert grid.scenario_id == qs.first().scenario_id
                    # Delete old connection between grid and areas
                    Grid.areas.through.objects.filter(
                        area__in=qs, grid__scenario=qs.first().scenario, grid__carrier=grid.carrier
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

    # Add units
    for field_name, field in BulkForm.base_fields.items():
        field.unit = UNITS.get(field_name, "")

    if ItemModel == Load:
        BulkForm.base_fields["timeseries"].queryset = get_timeseries_for_user(
            user, scenario, instance
        )

    elif ItemModel == Area:
        # dynamic creation of grid selects
        # get gridtypes. Each Area can be connected to one grid of each type
        for gtype in Grid.CarrierChoices:
            BulkForm.base_fields["grid_" + gtype] = InternalIDModelChoiceField(
                queryset=Grid.objects.filter(
                    scenario=scenario,
                    carrier=gtype,
                ),
                label=gtype.label + "-Netz",
                required=False,
                empty_label="Keine Anpassung vornehmen",
            )

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
        Scenario.objects, to_field_name="internal_id", required=False
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
