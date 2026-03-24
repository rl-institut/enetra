import json
import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from typing import Literal
from uuid import uuid4

import numpy as np
from django.apps.registry import apps
from django.contrib.auth.models import User
from django.db.models import Value
from django.forms import ModelForm
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

from ports.create_placeholder_scenario import create_scenario
from ports.forms import ScenarioItemFormFactory

from .models import Area
from .models import ElectricComponent
from .models import Generator
from .models import Heating
from .models import Load
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


def test(request):
    context = {}
    return render(request, "ports/test.html", context)


def home(request):
    if request.GET.get("new"):
        # NOTE: during development call /?new=true
        # to create a new placeholder scenario
        s = create_scenario()
        s.name = request.GET.get("new")
        s.save(update_fields=["name"])

    context = {}
    scenario = Scenario.objects.last()
    context["scenario"] = scenario
    context["scenarios"] = Scenario.objects.all()
    context["building_areas"] = Area.objects.filter(
        scenario=scenario, area_type=Area.AreaTypeChoices.BUILDING
    )
    context["open_areas"] = Area.objects.filter(
        scenario=scenario, area_type=Area.AreaTypeChoices.OPEN
    )
    context["solars"] = Solar.objects.filter(scenario=scenario)
    context["generators"] = Generator.objects.filter(scenario=scenario)
    context["heaters"] = Heating.objects.filter(scenario=scenario)
    context["Area"] = Area
    return render(request, "ports/tool_base.html", context)


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


class DetailsView(FormView):
    template = "ports/partials/create_form.html"

    def dispatch(self, request, *args, **kwargs):
        # TODO: Add authorization
        self.scenario = Scenario.objects.get(internal_id=kwargs["scenario_internal_id"])
        model = kwargs["model"]
        self.Model = apps.get_model("ports", model)
        self.Form = ScenarioItemFormFactory(self.Model)
        if self.Model == Area:
            self.template = "ports/partials/detail_sidebar/detail_sidebar_main.html"
        self.instance = self.Model.objects.filter(
            scenario=self.scenario, internal_id=kwargs.get("internal_id")
        ).first()
        assert ScenarioItem in self.Model.mro()
        self.context: dict[str, Any] = {"scenario": self.scenario}
        self.context["item"] = self.instance
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        if not self.instance:
            raise Http404("This instance does not exist")
        if self.Model not in [Solar, Area, Generator]:
            raise Http404("This model does not exist or is not implemented yet")
        _id = uuid4()
        form = self.Form(initial={"internal_id": _id}, prefix=get_pre(_id))
        # form autogenerates instance with random uuid. we have to stop this diverging
        form.instance.internal_id = _id
        self.context["form"] = form
        if self.Model == Area:
            self.Form.base_fields["usage"].required = True
            if self.instance.area_type == Area.AreaTypeChoices.BUILDING:
                self.Form.base_fields["usage"].choices = Area.BuildingUsageChoices
            else:
                self.Form.base_fields["usage"].choices = Area.OpenUsageChoices
            self.context["form"] = self.Form(instance=self.instance)
            # pass queryset to frontend to create links to load forms
            self.context["loads"] = Load.objects.filter(area=self.instance)
            models = [m for m in apps.get_models() if issubclass(m, ElectricComponent)]
            qs = list()
            for model in models:
                q = model.objects.filter(area=self.instance)
                qs.extend(list(q))
            self.context["energy_components"] = qs
        else:
            raise NotImplementedError("No template defined for this Model")
        return render(self.request, self.template, self.context)

    def delete(self, request, *args, **kwargs):
        # NOTE: data is send as hx-include, so not part of POST
        form = self.Form(data=request.GET)
        form.is_valid()
        self.instance.delete()
        return render(
            self.request,
            "ports/partials/detail_sidebar/detail_deleted.html",
            self.context,
        )

    def post(self, request, *args, **kwargs):
        if self.Model not in [Area]:
            raise NotImplementedError("This model is not implemented for posting yet")
        if not self.instance:
            # Create a new item and pass it back in the default state
            form = self.Form(data={"internal_id": uuid4()})
            # do NOT pass the request.POST directly which could lead to unauthorized injections
            extra_args = {}
            if self.Model == Area:
                # TODO: Refactor into model method so this function stays clean
                allowed_attributes = ["area_type"]
                for att in allowed_attributes:
                    extra_args[att] = request.POST.get(att)
            count = self.Model.objects.filter(
                scenario=self.scenario, area_type=extra_args["area_type"]
            ).count()

            self.instance = self.Model.objects.create(
                scenario=self.scenario,
                name=f"Neues Fläche {count + 1}",
                **extra_args,
                # TODO: manager=request.user
            )

            self.context["form"] = Area.adjust_Form(self.Form, instance=self.instance)(
                instance=self.instance
            )

            self.context["item"] = self.instance
            self.context["created"] = True

            return render(self.request, self.template, self.context)
        try:
            self.Form = Area.adjust_Form(self.Form, instance=self.instance)
            form = self.Form(data=request.POST, instance=self.instance)
            self.context["form"] = form
            if form.is_valid():
                self.context["item"] = form.save()
                self.context["success"] = "Erfolgreich gespeichert"
            else:
                self.context["errors"] = ["An error occured", form.errors]
        except Exception:
            self.context["errors"] = ["An unexpected error occured"]
        return render(self.request, self.template, self.context)


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
