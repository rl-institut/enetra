import django.forms as forms
from django.contrib.gis.forms import PolygonField
from django.contrib.gis.geos import GEOSGeometry
from django.forms import modelform_factory

from ports.models import Area
from ports.models import ScenarioItem


def ScenarioItemFormFactory(ItemModel: type[ScenarioItem]):
    exclude = ["manager", "scenario"]
    if ItemModel == Area:
        return modelform_factory(
            ItemModel,
            exclude=exclude + ["area_type", "geom"],
            field_classes={"geom": GeoJSONPolygonField},
            widgets={
                "internal_id": forms.HiddenInput(),
                "geom": GeoJSONWidget(),
                "name": forms.Textarea(attrs={"rows": 1, "cols": 15}),
                "description": forms.Textarea(attrs={"rows": 2, "cols": 15}),
            },
        )
    return modelform_factory(
        ItemModel,
        exclude=exclude,
        widgets={
            "internal_id": forms.HiddenInput(),
            "name": forms.Textarea(attrs={"rows": 1, "cols": 15}),
            "description": forms.Textarea(attrs={"rows": 2, "cols": 15}),
        },
    )


# TODO: Move to forms
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
