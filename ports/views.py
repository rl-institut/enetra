import json
import logging
import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from typing import Literal
from uuid import uuid4

import numpy as np
from django import forms
from django.apps.registry import apps
from django.contrib.auth.models import User
from django.contrib.gis.forms import PolygonField
from django.contrib.gis.geos import GEOSGeometry
from django.db.models import Value
from django.forms import ModelForm
from django.forms import modelform_factory
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponseForbidden
from django.http.response import HttpResponse
from django.shortcuts import get_object_or_404  # noqa
from django.shortcuts import render  # noqa
from django.template.loader import render_to_string
from django.views.generic import FormView
from django_oemof import models as oemof_models
from django_oemof import simulation

from .models import Area
from .models import Scenario
from .models import ScenarioItem
from .models import Solar

logger = logging.getLogger("django-ports")


def get_authentification(
    object: Scenario | ScenarioItem,
    user: User,
    crud: Literal["create", "c", "read", "r", "update", "u", "delete", "d"],
) -> bool:
    # TODO: Implement
    if False:  # noqa
        return False
    return True


def home(request):
    context = {}
    return render(request, "ports/partials/tool_base.html", context)


def get_updates(request, scenario_uuid: str, first_load_str: str, last_update_str: str):
    scenario: Scenario = get_object_or_404(Scenario, internal_id=scenario_uuid)
    focused_form = request.GET.get("focusedForm", None)
    if not get_authentification(scenario, request.user, "read"):
        return HttpResponseForbidden("Not Allowed")
    first_load = datetime.fromisoformat(first_load_str)
    context = {"scenario": scenario, "first_load": first_load_str}
    if scenario.items_updated_at <= first_load:
        return render(request, "ports/partials/changelog.html", context)
    else:
        changed_items: list[ScenarioItem] = []
        created_items: list[ScenarioItem] = []
        port_models = apps.get_models("ports")
        scenario_item_models = [model for model in port_models if issubclass(model, ScenarioItem)]
        for model in scenario_item_models:
            objs = model.objects.filter(scenario=scenario, updated_at__gte=first_load).annotate(
                changed=Value(True)
            )
            changed_items = changed_items + list(objs)
            objs = model.objects.filter(scenario=scenario, created_at__gt=first_load).annotate(
                changed=Value(False)
            )
            created_items = created_items + list(objs)
        changed_or_created_items = created_items + changed_items
        changed_or_created_items.sort(key=lambda x: x.updated_at if x.changed else x.created_at)
        context |= {"changed_or_created_items": changed_or_created_items}
        change_log = render_to_string("ports/partials/changelog.html", context, request)

    last_update = datetime.fromisoformat(last_update_str)
    if scenario.items_updated_at <= last_update:
        return HttpResponse(change_log)

    changed, created = get_update_items(scenario, last_update, scenario_item_models)
    oob_updates = render_oob_updates(created, changed, focused_form)
    return HttpResponse(change_log + oob_updates)


def get_update_items(scenario, last_update, scenario_item_models):
    changed_items: list[ScenarioItem] = []
    created_items: list[ScenarioItem] = []
    for model in scenario_item_models:
        objs = model.objects.filter(
            scenario=scenario,
            updated_at__gte=last_update,
            created_at__lte=last_update,
        )
        changed_items = changed_items + list(objs)
        objs = model.objects.filter(scenario=scenario, created_at__gt=last_update)
        created_items = created_items + list(objs)
    return changed_items, created_items


def render_oob_updates(
    created_items: Iterable[ScenarioItem],
    update_items: Iterable[ScenarioItem],
    focused_form: str | None = None,
) -> str:
    """Render ScenarioItems via oob to inject updates into a response"""
    context = {}
    for key, items in [
        ("created_forms", created_items),
        ("updated_forms", update_items),
    ]:
        forms: list[ModelForm[ScenarioItem]] = []
        for item in items:
            Form = ScenarioItemFormFactory(item._meta.model)
            prefix = get_pre(item)
            form = Form(instance=item, prefix=prefix)
            if focused_form and focused_form == f"form_{prefix}":
                context |= {"focused_form_changed": focused_form}
                continue
            forms.append(form)
        context |= {key: forms}
    oob_changed_items = render_to_string("ports/partials/oob_form_swap.html", context)
    return oob_changed_items


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


def leaflet(request):
    s, _ = Scenario.objects.get_or_create(name="Test Scenario")
    a, _ = Area.objects.get_or_create(name="Test Area", scenario=s)
    a, _ = Area.objects.get_or_create(name="Test Area2", scenario=s)
    context: dict[str, Any] = {"scenario": s}

    Form = ScenarioItemFormFactory(Area)
    context["areas"] = [
        Form(instance=s, prefix=get_pre(s)) for s in Area.objects.filter(scenario=s)
    ]
    return render(request, "ports/leaflet.html", context)


def dynamic_forms(request):
    s, _ = Scenario.objects.get_or_create(name="Test Scenario")
    a, _ = Area.objects.get_or_create(name="Test Area", scenario=s)
    a, _ = Area.objects.get_or_create(name="Test Area2", scenario=s)
    context: dict[str, Any] = {"scenario": s}
    Form = ScenarioItemFormFactory(Solar)
    context["solars"] = [
        Form(instance=s, prefix=get_pre(s)) for s in Solar.objects.filter(scenario=s)
    ]

    Form = ScenarioItemFormFactory(Area)
    context["areas"] = [
        Form(instance=s, prefix=get_pre(s)) for s in Area.objects.filter(scenario=s)
    ]
    return render(request, "ports/dynamic_forms.html", context)


def get_pre(instance_or_uuid: "ScenarioItem | uuid4"):
    # When cruding single instances, there is no need for a prefix
    # The map widget gets inserted by id, so this is needed
    if isinstance(instance_or_uuid, ScenarioItem):
        instance_or_uuid = instance_or_uuid.internal_id
    return str(instance_or_uuid)[:5]


def ScenarioItemFormFactory(ItemModel: type[ScenarioItem]):
    exclude = ["manager", "scenario"]
    if ItemModel == Area:
        return modelform_factory(
            ItemModel,
            exclude=exclude,
            field_classes={"geom": GeoJSONPolygonField},
            widgets={
                "internal_id": forms.TextInput(),
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


class CrudView(FormView):
    template = "ports/partials/create_form.html"

    def dispatch(self, request, *args, **kwargs):
        # TODO: Add authorization
        time.sleep(0.1)
        self.scenario = Scenario.objects.get(internal_id=kwargs["scenario_internal_id"])
        model = kwargs["model"]
        self.Model = apps.get_model("ports", model)
        self.Form = ScenarioItemFormFactory(self.Model)
        assert ScenarioItem in self.Model.mro()
        self.context = {"scenario": self.scenario}
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        time.sleep(0.1)
        if self.Model == Solar or self.Model == Area:
            prefix = request.GET.get("prefix")
            if prefix:
                # Usually this item already exists if it is requested with a prefix
                form = self.Form(data=request.GET, prefix=prefix)
                form.is_valid()
                _id = form.cleaned_data.get("internal_id")
                # If the item was deleted, we clean up by removing the form
                # Posting the form with the same prefix would create issues since
                # the deleteditem already has the same id
                if _id and not self.Model.objects.filter(internal_id=_id).exists():
                    response = HttpResponse(b"This element was deleted")
                    response["HX-Retarget"] = f".form-container-{_id}"
                    response["HX-Reswap"] = "outerHTML"
                    return response
            else:
                _id = uuid4()
                form = self.Form(initial={"internal_id": _id}, prefix=get_pre(_id))
                # form autogenerates instance with random uuid. we have to stop this diverging
                form.instance.internal_id = _id
            self.context["form"] = form
            if self.Model == Solar:
                template = "ports/dynamic_forms.html#crud-solar-htmx-partial"
            elif self.Model == Area:
                template = "ports/dynamic_forms.html#crud-area-htmx-partial"
            else:
                raise NotImplementedError()
            return render(self.request, template, self.context)
        raise Http404("This model does not exist")

    def delete(self, request, *args, **kwargs):
        time.sleep(0.1)
        # NOTE: data is send as hx-include, so not part of POST
        prefix = request.GET["prefix"]
        form = self.Form(data=request.GET, prefix=prefix)
        form.is_valid()
        out = self.Model.objects.filter(
            scenario=self.scenario, internal_id=form.cleaned_data["internal_id"]
        ).delete()
        # TODO: Style a response which shows deletion
        return HttpResponse(out)

    def post(self, request, *args, **kwargs):
        time.sleep(0.1)
        prefix = request.POST["prefix"]
        if self.Model == Solar or self.Model == Area:
            form = self.Form(data=request.POST, prefix=prefix)
            try:
                if form.is_valid():
                    instance = self.Model.objects.filter(
                        scenario=self.scenario,
                        internal_id=form.cleaned_data["internal_id"],
                    ).first()
                    if instance:
                        form = self.Form(data=request.POST, instance=instance, prefix=prefix)
                        form.save()
                    else:
                        # Patch in data which was not part of the form but is part of the model
                        obj = form.save(commit=False)
                        obj.scenario = self.scenario
                        obj.save()
                    self.context["success"] = "Erfolgreich gespeichert"
                else:
                    self.context["errors"] = ["An error occured"]
            except Exception:
                self.context["errors"] = ["An error occured"]
            self.context["form"] = form
            return render(self.request, self.template, self.context)
        raise Http404("This model does not exist")


# Create your views here.
def testview(request: HttpRequest):
    # Example with some hooks
    logger.info(request.GET.get("scenario"))

    OEMOF_DATAPACKAGE = request.GET.get("scenario") if request.GET.get("scenario") else "dispatch"
    # working scenarios
    # dispatch
    # invest
    # emission_constraint

    # Hook functions must be defined beforehand
    # ph = hooks.Hook(OEMOF_DATAPACKAGE, test_parameter_hook)
    # esh = hooks.Hook(OEMOF_DATAPACKAGE, test_es_hook)
    # mh = hooks.Hook(OEMOF_DATAPACKAGE, test_model_hook)
    #
    # hooks.register_hook(hook_type=hooks.HookType.PARAMETER, hook=ph)
    # hooks.register_hook(hook_type=hooks.HookType.ENERGYSYSTEM, hook=esh)
    # hooks.register_hook(hook_type=hooks.HookType.MODEL, hook=mh)
    #
    parameters = {}
    oemof_models.Simulation.objects.filter(scenario=OEMOF_DATAPACKAGE).delete()
    simulation_id = simulation.simulate_scenario(
        scenario=OEMOF_DATAPACKAGE, parameters=parameters, lp_file="lastCBCModel.lp"
    )
    logger.info("Simulation ID:", simulation_id)

    # Restore oemof results from DB

    sim = oemof_models.Simulation.objects.get(id=simulation_id)
    inputs, outputs = sim.dataset.restore_results()
    data = {
        "result": {
            "inputs": serialize_string_default(inputs),
            "outputs": serialize_string_default(outputs),
        }
    }
    return HttpResponse(json.dumps(data), content_type="application/json")


def serialize_string_default(
    data,
):
    if isinstance(data, dict):
        output = dict()
        for key, value in data.items():
            if not isinstance(key, str | int | float | bool | None):
                key = str(key)
            output[key] = serialize_string_default(value)
        return output
    elif data.__class__.__name__ == "Series":
        return [x if not isnan(x) else "NaN" for x in data]
    elif data == float("inf") or data == float("-inf"):
        return "infinity"
    elif isnan(data):
        return "NaN"
    else:
        return data


def isnan(val):
    try:
        return np.isnan(val)
    except TypeError:
        return False
