import django.forms as forms
from django.contrib.auth.models import User
from django.contrib.gis.forms import PolygonField
from django.contrib.gis.geos import GEOSGeometry
from django.db.models import QuerySet
from django.forms import CharField
from django.forms import ValidationError
from django.forms import modelform_factory

from ports.models import Area
from ports.models import ChangedItem
from ports.models import ElectricComponent
from ports.models import Load
from ports.models import Project
from ports.models import Scenario
from ports.models import ScenarioItem
from ports.util import duplicate_scenario


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


def ScenarioItemFormFactory(ItemModel: type[ScenarioItem], multi: bool = False, **kwargs):
    # TODO:
    # FIXME:: Add authorization, e.g. pass User and only allow queries on permissed elements
    exclude = ["manager", "scenario"]
    if ItemModel == Area:
        BaseForm = modelform_factory(
            ItemModel,
            exclude=exclude + ["area_type", "geom"],
            field_classes={"geom": GeoJSONPolygonField},
            widgets={
                "internal_id": forms.HiddenInput(),
                # "geom": GeoJSONWidget(),
                "name": forms.Textarea(attrs={"rows": 1, "cols": 15}),
                "description": forms.Textarea(attrs={"rows": 2, "cols": 15}),
            },
        )

    elif ItemModel == Load or ElectricComponent in ItemModel.mro():
        BaseForm = modelform_factory(
            ItemModel,
            exclude=exclude + ["area"],
            widgets={
                "internal_id": forms.HiddenInput(),
                "name": forms.TextInput(),
                "description": forms.Textarea(attrs={"rows": 2, "cols": 15}),
            },
        )
    else:
        raise NotImplementedError()

    if not multi:
        return BaseForm

    class BulkForm(BaseForm):
        internal_ids = UUIDMultipleChoiceField(
            queryset=ItemModel.objects.filter(scenario=kwargs["scenario"]),
            # widget=MultipleHiddenInput
        )

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields.pop("internal_id")
            self.fields.pop("name")
            # For multi forms no fields are required.
            # this allows partial overwriting.
            # e.g. efficiency would normally be required
            # 2 components with differing efficiency could not be saved,
            # without overwriting the efficiency with a common value
            for field in self.fields.values():
                field.required = False

        def save(self):
            qs = self.cleaned_data["internal_ids"]
            data = {
                k: v
                for k, v in self.cleaned_data.items()
                if k in self.fields and k != "internal_ids" and v not in [None, ""]
            }
            qs.update(**data)
            changed_items = []
            for instance in qs.all():
                changed_item = ChangedItem.from_scenario_item(instance)
                changed_items.append(changed_item)
            ChangedItem.objects.bulk_create(changed_items)
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

    def save(self, commit: bool = True):
        assert self.base_scenario.manager == self.user
        new_scenario = duplicate_scenario(self.base_scenario, self.user)
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
