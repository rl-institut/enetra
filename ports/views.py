import json
import logging
import time
from uuid import uuid4

import numpy as np
from django import forms
from django.apps.registry import apps
from django.forms import modelform_factory
from django.http import Http404
from django.http import HttpRequest
from django.http.response import HttpResponse
from django.shortcuts import render  # noqa
from django.views.generic import FormView
from django_oemof import models
from django_oemof import simulation

from .models import Area
from .models import Scenario
from .models import ScenarioItem
from .models import Solar

logger = logging.getLogger("django-ports")


def home(request):
    s, _ = Scenario.objects.get_or_create(name="Test Scenario")
    a, _ = Area.objects.get_or_create(name="Test Area", scenario=s)
    context = {"scenario": s}
    return render(request, "ports/index.html", context)


class CrudView(FormView):
    template = "ports/partials/create_form.html"

    def dispatch(self, request, *args, **kwargs):
        # TODO: Add authorization
        time.sleep(0.1)
        self.scenario = Scenario.objects.get(internal_id=kwargs["scenario_internal_id"])
        model = kwargs["model"]
        self.Model = apps.get_model("ports", model)
        exclude = ["manager", "scenario"]
        self.Form = modelform_factory(
            self.Model,
            exclude=exclude,
            widgets={"internal_id": forms.HiddenInput()},
        )
        assert ScenarioItem in self.Model.mro()
        self.context = {"scenario": self.scenario, "model": model}
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        time.sleep(0.1)
        if self.Model == Solar:
            # TODO: Continue to pass internal id to frontend so every save overwrites an instance
            # and does not create a new on
            self.context["form"] = self.Form(initial={"internal_id": uuid4()})
            return render(self.request, self.template, self.context)
        raise Http404("This model does not exist")

    def delete(self, request, *args, **kwargs):
        time.sleep(0.1)
        # NOTE: data is send as hx-include, so not part of POST
        form = self.Form(data=request.GET)
        form.is_valid()
        out = self.Model.objects.filter(
            scenario=self.scenario, internal_id=form.cleaned_data["internal_id"]
        ).delete()
        # TODO: Style a response which shows deletion
        return HttpResponse(out)

    def post(self, request, *args, **kwargs):
        time.sleep(0.1)
        if self.Model == Solar:
            form = self.Form(data=request.POST)
            if form.is_valid():
                instance = self.Model.objects.filter(
                    scenario=self.scenario, internal_id=form.cleaned_data["internal_id"]
                ).first()
                if instance:
                    form = self.Form(data=request.POST, instance=instance)
                    form.save()
                else:
                    # Patch in data which was not part of the form but is part of the model
                    obj = form.save(commit=False)
                    obj.scenario = self.scenario
                    obj.save()
                self.context["success"] = "Erfolgreich gespeichert"
            else:
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
    models.Simulation.objects.filter(scenario=OEMOF_DATAPACKAGE).delete()
    simulation_id = simulation.simulate_scenario(
        scenario=OEMOF_DATAPACKAGE, parameters=parameters, lp_file="lastCBCModel.lp"
    )
    logger.info("Simulation ID:", simulation_id)

    # Restore oemof results from DB

    sim = models.Simulation.objects.get(id=simulation_id)
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
