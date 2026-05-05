import json
import logging
import traceback
from collections.abc import Iterable
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import Literal
from uuid import UUID
from uuid import uuid4

import numpy as np
from django.apps.registry import apps
from django.contrib.auth.models import User
from django.db.models import Value
from django.forms import ModelForm
from django.forms import model_to_dict
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponseBadRequest
from django.http import HttpResponseForbidden
from django.http.response import HttpResponse
from django.shortcuts import aget_object_or_404  # noqa
from django.shortcuts import get_object_or_404  # noqa
from django.shortcuts import render  # noqa
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.generic import View
from django_oemof import models as oemof_models
from django_oemof import simulation

from ports import models
from ports.create_placeholder_scenario import create_scenario
from ports.forms import AreaItemFormFactory
from ports.forms import ScenarioItemFormFactory

from .models import Area
from .models import ChangedItem
from .models import DeletedItem
from .models import ElectricComponent
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


def changes_count(request, scenario_internal_id: UUID):
    """Get the count of changes as partial update

    Piggybacks the request to update the page with new content (from other users)
    """
    scenario: Scenario = get_object_or_404(Scenario, internal_id=scenario_internal_id)
    if not get_authentification(scenario, request.user, "read"):
        HttpResponseForbidden("No access")
    context = {}
    last_update = request.GET.get("updated_at")
    updated_at = scenario.updated_at
    context["updated_at"] = updated_at
    context["changed"] = False
    if last_update:
        last_update = datetime.fromisoformat(last_update)
        if scenario.updated_at > last_update:
            context["changed"] = True
            created_items = []
            changed_items = []
            deleted_items = []
            port_models = apps.get_app_config("ports").get_models()
            for Model in port_models:
                if Model in [Scenario, ChangedItem]:
                    continue
                if Model in [DeletedItem]:
                    filter = {
                        "created_at__gt": last_update,
                        "created_at__lte": updated_at,
                    }
                    items = list(DeletedItem.objects.filter(scenario=scenario).filter(**filter))
                    for i in items:
                        Model = i.content_type.model_class()
                        data = model_to_dict(i)
                        del data["content_type"]
                        cleaned_data = {}
                        for key, value in data.items():
                            cleaned_data[Model._meta.get_field(key).attname] = value
                        model_item = Model(**cleaned_data)
                        deleted_items.append(model_item)
                    continue
                filter = {"created_at__gt": last_update, "created_at__lte": updated_at}
                created_items.extend(list(Model.objects.filter(scenario=scenario).filter(**filter)))
                filter = {
                    "created_at__lte": last_update,
                    "updated_at__gt": last_update,
                    "updated_at__lte": updated_at,
                }
                changed_items.extend(list(Model.objects.filter(scenario=scenario).filter(**filter)))

            context["created_items"] = created_items
            context["changed_items"] = changed_items
            context["deleted_items"] = deleted_items

    # Reuse the calculated changes
    count = request.GET.get("all_changes_count", None)
    if not count or context["changed"]:
        count = 0
        port_models = apps.get_app_config("ports").get_models()
        # Create a mapping for all scenario items
        for Model in port_models:
            if Model in [Scenario]:
                continue
            count += Model.objects.filter(scenario=scenario).count()

    context["all_changes_count"] = count
    context["scenario"] = scenario
    return render(request, "ports/partials/changes_count.html", context)


def changes(request, scenario_internal_id: UUID):
    """View for changelog

    Different filter options are supported for timespans and user
    """
    scenario: Scenario = get_object_or_404(Scenario, internal_id=scenario_internal_id)
    if not get_authentification(scenario, request.user, "read"):
        HttpResponseForbidden("No access")
    days = int(request.GET.get("days", "90"))
    otherchanges = request.GET.get("otherchanges", "false").lower() == "true"
    query_time = timezone.now().astimezone() - timedelta(days=days)

    filter = {"scenario": scenario, "created_at__gte": query_time}
    changed_items_query = ChangedItem.objects.filter(**filter)
    if request.user.is_authenticated:
        user_filter = filter | {"manager": request.user}
        other_exclude = {"manager": request.user}
        user_changes_count = changed_items_query.filter(manager=request.user).count()
        other_changes_count = changed_items_query.exclude(manager=request.user).count()
    else:
        user_filter = filter | {"manager__isnull": True}
        other_exclude = {"manager__isnull": True}

    user_changes_count = 0
    other_changes_count = 0
    port_models = apps.get_app_config("ports").get_models()
    # Create a mapping for all scenario items
    for Model in port_models:
        if Model in [Scenario]:
            continue
        other_changes_count += Model.objects.filter(**filter).exclude(**other_exclude).count()
        user_changes_count += Model.objects.filter(**user_filter).count()

    exclude = {}
    if otherchanges:
        exclude = other_exclude
        filter = filter
    elif request.user.is_authenticated:
        filter = user_filter
        exclude = {}

    original_items_dict = dict()
    port_models = apps.get_app_config("ports").get_models()
    # Create a mapping for all scenario items
    for Model in port_models:
        if Model in [DeletedItem, ChangedItem, Scenario]:
            continue
        original_items = Model.objects.filter(scenario=scenario)
        original_items_dict[Model] = {x.internal_id: x for x in original_items}

    item_original_item = []
    # NOTE: get_models is an iterator and has to be reset
    port_models = apps.get_app_config("ports").get_models()
    for Model in port_models:
        if Model == Scenario:
            continue
        if Model == DeletedItem:
            for item in Model.objects.filter(**filter).exclude(**exclude):
                item_original_item.append(
                    {
                        "status": "deleted",
                        "time": item.created_at,
                        "item": item,
                        "original_item": None,
                    }
                )
        elif Model == ChangedItem:
            for item in Model.objects.filter(**filter).exclude(**exclude):
                # Original item might have been deleted
                item_original_item.append(
                    {
                        "status": "changed",
                        "time": item.created_at,
                        "item": item,
                        "original_item": original_items_dict[item.content_type.model_class()].get(
                            item.internal_id
                        ),
                    }
                )
        else:
            for item in Model.objects.filter(**filter).exclude(**exclude):
                item_original_item.append(
                    {
                        "status": "created",
                        "time": item.created_at,
                        "item": item,
                        "original_item": item,
                    }
                )
    item_original_item = sorted(item_original_item, key=lambda x: x["time"], reverse=True)

    context = {}
    context["item_original_items"] = item_original_item
    context["scenario"] = scenario
    context["all_changes_count"] = len(item_original_item)
    context["user_changes_count"] = user_changes_count
    context["other_changes_count"] = other_changes_count

    return render(request, "ports/partials/detail_sidebar/detail_sidebar_changes.html", context)


def get_home_context():
    data = {}
    scenario = Scenario.objects.last()
    data["scenario"] = scenario
    data["scenarios"] = Scenario.objects.all()
    data["building_areas"] = Area.objects.filter(
        scenario=scenario, area_type=Area.AreaTypeChoices.BUILDING
    )
    data["open_areas"] = Area.objects.filter(scenario=scenario, area_type=Area.AreaTypeChoices.OPEN)

    electric_components = dict()
    for m in apps.get_models():
        if issubclass(m, ElectricComponent):
            # create queries for all electriccomponenent models like
            # key is model_name + "s" ,e.g. solars, heatings, generators
            key = f"{m._meta.model_name}s"
            query = m.objects.filter(scenario=scenario)
            data[key] = query
            electric_components[key] = query

    # put the queries in a dict to, so we can directly iterate over them
    data["electric_components"] = electric_components
    data["Area"] = Area
    area_forms = []
    for a in data["building_areas"]:
        area_forms.append(AreaItemFormFactory()(instance=a))
    for a in data["open_areas"]:
        area_forms.append(AreaItemFormFactory()(instance=a))
    data["area_forms"] = area_forms

    return data


def home(request):
    if request.GET.get("new"):
        # NOTE: during development call /?new=true
        # to create a new placeholder scenario
        s = create_scenario()
        s.name = request.GET.get("new")
        s.save(update_fields=["name"])

    context = get_home_context()
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


def get_pre(instance_or_uuid: "ScenarioItem | UUID"):
    # When cruding single instances, there is no need for a prefix
    # The map widget gets inserted by id, so this is needed
    if isinstance(instance_or_uuid, ScenarioItem):
        instance_or_uuid = instance_or_uuid.internal_id
    return str(instance_or_uuid)[:5]


class DetailsView(View):
    """View which handles detail request for instances

    Handles get and post for single instances but also for batched instanced.
    """

    template = ""
    created = False
    multi = False
    scenario: models.Scenario | None = None
    Model: type[models.ScenarioItem] | None = None
    instance: models.ScenarioItem = None
    instances: Iterable[ScenarioItem] = []
    data: dict = {}

    def get_basic_context(self, request, *args, **kwargs) -> dict:
        context = {
            "scenario": self.scenario,
            "Model": self.Model,
            "model_name": self.Model._meta.model_name,
            "internal_id": self.internal_id,
            "internal_ids": ",".join(self.internal_ids),
            "instance": self.instance,
            "instances": self.instances,
            "updateOob": "true",
            "electric_models": [
                m._meta.model_name for m in apps.get_models() if issubclass(m, ElectricComponent)
            ],
        }

        return context

    def setup_view(self, request, *args, **kwargs) -> None:
        self.scenario = Scenario.objects.get(internal_id=kwargs["scenario_internal_id"])
        self.Model = apps.get_model("ports", kwargs["model"])
        assert ScenarioItem in self.Model.mro()
        self.data = request.GET
        if request.method == "POST":
            self.data = request.POST
        internal_ids = self.data.get("internal_ids", "").split(",")
        # These instances should be shown or posted.
        self.internal_ids = [] if internal_ids[0] == "" else internal_ids
        self.internal_id = self.data.get("internal_id")

        # NOTE: created is set through the url resolver
        self.multi = len(self.internal_ids) > 1
        if self.created:
            pass
        elif self.multi:
            self.instances = self.Model.objects.filter(
                scenario=self.scenario, internal_id__in=self.internal_ids
            )
        elif not self.internal_ids and not self.internal_id:
            # Empty selection and nothing created
            return
        else:
            # Multi select with a single item selected behaves the same as single select
            self.internal_id = self.internal_id or self.internal_ids[0]
            self.internal_ids = []
            # NOTE: Can be None if the instance was deleted
            self.instance = self.Model.objects.filter(
                scenario=self.scenario, internal_id=self.internal_id
            ).first()
        self.Form = ScenarioItemFormFactory(self.Model, multi=self.multi, scenario=self.scenario)
        self.template = self.get_template()

    def get_template(self) -> str:
        suffix = ""
        if self.multi:
            suffix = "_multi"
        if self.Model == Area:
            template = f"ports/partials/detail_sidebar/detail_sidebar_main{suffix}.html"
        elif self.Model == Load:
            template = f"ports/partials/detail_sidebar/detail_sidebar_load_detail{suffix}.html"
        elif ElectricComponent in self.Model.mro():
            template = f"ports/partials/detail_sidebar/detail_sidebar_component{suffix}.html"
        else:
            raise NotImplementedError(f"{self.Model} is not implemented")
        return template

    def dispatch(self, request, *args, **kwargs):
        # FIXME
        # TODO: Add authorization
        # Instantiate the class with its fixed attributes
        self.setup_view(request, *args, **kwargs)

        if not self.internal_ids and not self.internal_id and not self.created:
            # No item selected. Don't swap but hide the detail-sidebar. Ignore this if self.created
            response = HttpResponse()
            response["HX-Reswap"] = "none"
            response["HX-Trigger"] = "hide-detail-sidebar"
            return response
        self.context = self.get_basic_context(request, *args, **kwargs)
        if self.created:
            if self.Model == Area:
                self.context["area_type"] = self.data.get("area_type")
            return self.create(request, *args, **kwargs)
        elif self.multi:
            if request.method == "POST":
                return self.multi_post(request, *args, **kwargs)
            if request.method == "GET":
                return self.multi_get(request, *args, **kwargs)
        if self.instance is None:
            return self.get_deleted(request, *args, **kwargs)

        return super().dispatch(request, *args, **kwargs)

    def get_area_context(self) -> dict:
        # pass queryset to frontend to create links to load forms
        context = {}
        context["loads"] = Load.objects.filter(area=self.instance)
        models = [m for m in apps.get_models() if issubclass(m, ElectricComponent)]
        qs = list()
        for model in models:
            q = model.objects.filter(area=self.instance)
            qs.extend(list(q))
        context["energy_components"] = qs
        return context

    def get_deleted(self, request, *args, **kwargs):
        deleted_item = DeletedItem.objects.filter(
            scenario=self.scenario, internal_id=self.internal_id
        ).first()
        if deleted_item:
            return render(
                self.request,
                "ports/partials/detail_sidebar/detail_deleted.html",
                self.context,
            )
        raise Http404("This instance does not exist")

    def get(self, request, *args, **kwargs):
        if self.Model not in [Area, Load] and ElectricComponent not in self.Model.mro():
            raise Http404("This model does not exist or is not implemented yet")
        self.Form = self.Model.adjust_Form(self.Form, instance=self.instance)
        self.context["form"] = self.Form(instance=self.instance)
        if self.Model == Area:
            self.context |= self.get_area_context()
        elif self.Model == Load or ElectricComponent in self.Model.mro():
            return render(self.request, self.template, self.context)
        else:
            raise NotImplementedError("No template defined for this Model")
        return render(self.request, self.template, self.context)

    def create(self, request, *args, **kwargs):
        if self.instance:
            return HttpResponseBadRequest(
                b"The creation of an object is not possible with an instance"
            )
        # Create a new item and pass it back in the default state
        self.Form(data={"internal_id": uuid4()})
        # do NOT pass the request.POST directly which could lead to unauthorized injections
        # NOTE: POST.get(k) handles unpacking of values e.g. value=="foo" instead of ["foo"]
        data = {k: request.POST.get(k) for k in request.POST}
        if self.Model == Area:
            new_instance = Area.create_new(self.scenario, **data)
            new_instance.save()
            self.context["instance"] = new_instance
            # Created areas are selected immediately
            self.context["createItemCallback"] = "this.click()"
        elif self.Model == Load or ElectricComponent in self.Model.mro():
            # Handle single creation as well as creation from batch view
            area_internal_ids = self.request.POST.get("area_internal_ids").split(",")
            data["area_internal_ids"] = area_internal_ids
            new_items = self.Model.create_new(self.scenario, **data)
            self.multi = len(area_internal_ids) > 1
            self.Form = ScenarioItemFormFactory(
                self.Model, multi=self.multi, scenario=self.scenario
            )
            self.instances = self.Model.objects.bulk_create(new_items)
            if not self.multi:
                self.instance = self.instances[0]
                self.instances = []
                self.context["instance"] = self.instance
                self.context["form"] = self.Model.adjust_Form(self.Form, instance=self.instance)(
                    instance=self.instance
                )

            else:
                # Template choice earlier works for direct instance access.
                # For creation this is decided here, since self.multi might have been overwritten
                self.template = self.get_template()
                self.context["instances"] = self.instances
                self.context["instance"] = None
                self.context["internal_ids"] = ",".join(
                    [str(x.internal_id) for x in self.instances]
                )
                self.context["form"] = self.Model.adjust_Form(
                    self.Form, instance=self.instances[0]
                )(instance=self.instance)
                self.context["area_internal_ids"] = ",".join(area_internal_ids)
        else:
            raise NotImplementedError(f"Implement the creation of this Model{self.Model.__name__}")

        self.context["created"] = True
        return render(self.request, self.template, self.context)

    def delete(self, request, *args, **kwargs):
        form = self.Form(data=request.GET)
        form.is_valid()
        self.instance.delete()
        self.context["status"] = "deleted"
        return render(
            self.request,
            "ports/partials/update_delete_create_scenario_item.html",
            self.context,
        )

    def multi_get(self, request, *args, **kwargs):
        if self.instance:
            return HttpResponseBadRequest(
                b"The fetching of multiple objects is not possible with a single instance"
            )
        self.Form = self.Model.adjust_Form(self.Form, instance=self.instances[0])
        if self.Model == Area or ElectricComponent in self.Model.mro():
            merged_data = model_to_dict(self.instances[0])
            for x in self.instances:
                data = model_to_dict(x)
                for key, value in data.items():
                    if merged_data.get(key) != value and key in merged_data:
                        del merged_data[key]

            form = self.Form(data=merged_data)
            self.context["form"] = form
            return render(self.request, self.template, self.context)

        raise NotImplementedError(f"Multi Get not implemented for {self.Model.__name__}")

    def multi_post(self, request, *args, **kwargs):
        if self.Model not in [Area, Load] and ElectricComponent not in self.Model.mro():
            raise NotImplementedError("This model is not implemented for multi posting yet")
        if self.instance:
            return HttpResponseBadRequest(
                b"The patching of multiple objects is not possible with an instance"
            )
        self.Form = self.Model.adjust_Form(self.Form, instance=self.instances[0])
        try:
            form = self.Form(data=request.POST)
            self.context["form"] = form
            if form.is_valid():
                self.context["instances"] = form.save()
                self.context["success"] = "Erfolgreich gespeichert"
            else:
                self.context["errors"] = ["An error occured", form.errors]
        except Exception:
            self.context["errors"] = ["An unexpected error occured"]
            traceback.print_exc()

        if self.Model == Load or ElectricComponent in self.Model.mro():
            # Multi post request for Load needs references to areas
            self.context["area_internal_ids"] = ",".join(
                str(y)
                for y in (
                    Area.objects.filter(
                        id__in=[x.area_id for x in self.context["instances"]]
                    ).values_list("internal_id", flat=True)
                )
            )
        self.context |= get_home_context()
        self.context["update"] = True

        return render(self.request, self.template, self.context)

    def post(self, request, *args, **kwargs):
        if self.Model not in [Area, Load] and ElectricComponent not in self.Model.mro():
            raise NotImplementedError("This model is not implemented for posting yet")
        if not self.instance:
            return HttpResponseBadRequest(b"The patching of an object needs an instance")
        try:
            self.Form = self.Model.adjust_Form(self.Form, instance=self.instance)
            form = self.Form(data=request.POST, instance=self.instance)
            self.context["form"] = form
            if form.is_valid():
                self.context["item"] = form.save()
                self.context["success"] = "Erfolgreich gespeichert"
            else:
                self.context["errors"] = ["An error occured", form.errors]
        except Exception:
            logger.error(traceback.format_exc())
            self.context["errors"] = ["An unexpected error occured"]

        self.context |= get_home_context()
        self.context["update"] = True
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
