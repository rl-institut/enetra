import django.forms as forms
from django.contrib.gis.forms import PolygonField
from django.contrib.gis.geos import GEOSGeometry
from django.db.models import ForeignKey
from django.db.models import QuerySet
from django.forms import CharField
from django.forms import ValidationError
from django.forms import modelform_factory

from ports.models import Area
from ports.models import ChangedItem
from ports.models import ElectricComponent
from ports.models import Load
from ports.models import LoadTemplate
from ports.models import ScenarioItem


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


def ScenarioItemFormFactory(ItemModel: type[ScenarioItem], multi: bool = False, **kwargs):
    # TODO:
    # FIXME:: Add authorization, e.g. pass User and only allow queries on permissed elements
    exclude = ["manager", "scenario", "updated_user"]
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
                "description": forms.Textarea(attrs={"rows": 2, "cols": 15}),
            },
        )
        BaseForm.base_fields["is_public"] = forms.BooleanField(
            required=False,
            widget=forms.CheckboxInput(),
            label="Für andere im Projekt sichtbar machen",
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


class InternalIDModelChoiceField(forms.ModelChoiceField):
    def __init__(self, queryset, **kwargs):
        kwargs.setdefault("to_field_name", "internal_id")
        super().__init__(queryset, **kwargs)


ALLOWED_UPLOAD_SUFFIXES = ["csv"]


class LoadTemplateUploadForm(forms.Form):
    allowed_suffixes = ALLOWED_UPLOAD_SUFFIXES
    # Set both required to false, so the fields to not mess with the outer load form
    # otherwise we would need form injection for the inputs

    template_file = forms.FileField(
        label="Vorlagedatei",
        widget=forms.FileInput(
            attrs={
                "accept": "." + ",".join(ALLOWED_UPLOAD_SUFFIXES),
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

        suffix = file.name.split(".")[-1].lower()
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise ValidationError(
                "Nicht unterstützter Dateityp. Erlaubt sind: " + ", ".join(ALLOWED_UPLOAD_SUFFIXES)
            )
        values = LoadTemplate.values_from_csv(file=file)
        if not values:
            raise ValidationError("Keine numerischen Werte gefunden")
        self._parsed_values = values
        return file

    def save(self, scenario, load, user):
        values = self._parsed_values
        timestep_minutes = self.cleaned_data["timestep_minutes"]
        file = self.cleaned_data["template_file"]
        template_load = LoadTemplate(
            timeseries={"values": values, "timestep_minutes": timestep_minutes},
            spec_load=sum(values) / len(values),
        )
        template_load.scenario = scenario
        template_load.name = file.name
        template_load.manager = user
        template_load.save()
        load.template = template_load
        load.save()
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
