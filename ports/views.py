import json

import numpy as np
from django.http.response import HttpResponse
from django.shortcuts import render  # noqa


# Create your views here.
def testview(request):
    # Example with some hooks
    from django_oemof import simulation

    OEMOF_DATAPACKAGE = "dispatch"

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
    simulation_id = simulation.simulate_scenario(
        scenario=OEMOF_DATAPACKAGE,
        parameters=parameters,
    )
    print("Simulation ID:", simulation_id)

    # Restore oemof results from DB
    from django_oemof import models

    sim = models.Simulation.objects.get(id=1)
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
