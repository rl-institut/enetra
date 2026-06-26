import json
import logging
import traceback
from collections.abc import Iterable
from datetime import datetime
from datetime import timedelta
from typing import Literal
from uuid import UUID
from uuid import uuid4

import numpy as np
from django.apps.registry import apps
from django.contrib.auth.models import User
from django.forms import model_to_dict
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponseBadRequest
from django.http import HttpResponseForbidden
from django.http import JsonResponse
from django.http.response import HttpResponse
from django.shortcuts import aget_object_or_404  # noqa
from django.shortcuts import get_object_or_404  # noqa
from django.shortcuts import redirect  # noqa
from django.shortcuts import render  # noqa
from django.urls import reverse
from django.utils import timezone
from django.views.generic import View
from django_oemof import models as oemof_models
from django_oemof import simulation

from ports import models
from ports.create_placeholder_scenario import create_scenario as create_placeholder_scenario
from ports.forms import AreaItemFormFactory
from ports.forms import CreateProjectForm
from ports.forms import CreateScenarioForm
from ports.forms import ScenarioItemFormFactory
from ports.util import duplicate_scenario
from ports.util import get_template_scenarios

from .models import Area
from .models import ChangedItem
from .models import DeletedItem
from .models import ElectricComponent
from .models import Load
from .models import Project
from .models import Scenario
from .models import ScenarioItem
from .util import duplicate_project

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
    scenario = Scenario.objects.last()
    all_areas = list(Area.objects.filter(scenario=scenario))
    building_areas = [a for a in all_areas if a.area_type == Area.AreaTypeChoices.BUILDING]
    open_areas = [a for a in all_areas if a.area_type == Area.AreaTypeChoices.OPEN]
    area_forms = []
    for a in open_areas + building_areas:
        area_forms.append(AreaItemFormFactory()(instance=a))
    context["area_forms"] = area_forms
    return render(request, "ports/map_test.html", context)


def patch_area(request, scenario_internal_id: UUID):
    internal_id = request.POST.get("internal_id")
    area = Area.objects.get(scenario__internal_id=scenario_internal_id, internal_id=internal_id)
    form = AreaItemFormFactory()(instance=area, data=request.POST)
    try:
        if form.is_valid():
            form.save()
            return HttpResponse(b"success")
    except ValueError:
        pass
    return HttpResponse(b"failed")


def changes_count(request, scenario_internal_id: UUID):
    """Get the count of changes as partial update

    Piggybacks the request to update the page with new content (from other users)
    """
    scenario: Scenario = get_object_or_404(Scenario, internal_id=scenario_internal_id)
    if not get_authentification(scenario, request.user, "read"):
        return HttpResponseForbidden("No access")
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
                if not issubclass(Model, models.ScenarioItem):
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
            if issubclass(Model, models.ScenarioItem):
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
        return HttpResponseForbidden("No access")
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
        # only count scenario items
        if issubclass(Model, models.ScenarioItem):
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
        if not issubclass(Model, models.ScenarioItem):
            continue
        original_items = Model.objects.filter(scenario=scenario)
        original_items_dict[Model] = {x.internal_id: x for x in original_items}

    item_original_item = []
    # NOTE: get_models is an iterator and has to be reset
    port_models = apps.get_app_config("ports").get_models()
    for Model in port_models:
        if Model == Scenario:
            continue
        if not issubclass(Model, models.ScenarioItem):
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


def get_home_context(scenario: Scenario | None = None):
    data = {}
    scenario = scenario or Scenario.objects.select_related("project").last()
    data["scenario"] = scenario
    data["project"] = scenario.project
    data["create_scenario_form"] = CreateScenarioForm(base_scenario=scenario)
    data["scenarios"] = Scenario.objects.filter(project=scenario.project)

    electric_components = dict()
    import time

    t = time.time()
    for m in apps.get_models():
        if issubclass(m, ElectricComponent):
            # create queries for all electriccomponenent models like
            # key is model_name + "s" ,e.g. solars, heatings, generators
            key = f"{m._meta.model_name}s"
            query = m.objects.filter(scenario=scenario)
            data[key] = query
            electric_components[key] = list(query)

    print("get electric in ", time.time() - t)
    # put the queries in a dict to, so we can directly iterate over them
    data["electric_components"] = electric_components
    data["Area"] = Area

    t = time.time()
    # important:  prefetch all related models to avoid n+1 queries
    all_areas = list(Area.objects.filter(scenario=scenario).prefetch_related("generator_set"))

    print("get areas in ", time.time() - t)
    building_areas = list()
    open_areas = list()
    for area in all_areas:
        if area.area_type == Area.AreaTypeChoices.BUILDING:
            building_areas.append(area)
        if area.area_type == Area.AreaTypeChoices.OPEN:
            open_areas.append(area)
    data["building_areas"] = building_areas
    data["open_areas"] = open_areas
    area_forms = []
    for a in open_areas + building_areas:
        area_forms.append(AreaItemFormFactory()(instance=a))
    data["area_forms"] = area_forms
    return data


def home(request):
    scenario = None
    if request.GET.get("new"):
        # NOTE: during development call /?new=true
        # to create a new placeholder scenario
        s = create_placeholder_scenario()
        s.name = request.GET.get("new")
        s.save(update_fields=["name"])
    if sid := request.GET.get("internal_id"):
        scenario = Scenario.objects.get(internal_id=sid)
    import time

    t = time.time()
    context = get_home_context(scenario=scenario)
    print("context in ", time.time() - t)

    t = time.time()
    resp = render(request, "ports/tool_base.html", context)
    print("render in ", time.time() - t)

    return resp


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
            "internal_id": str(self.internal_id),
            "internal_ids": ",".join(self.internal_ids),
            "instance": self.instance,
            "instances": self.instances,
            "electric_models": [
                m._meta.model_name for m in apps.get_models() if issubclass(m, ElectricComponent)
            ],
        }
        for model in [m for m in apps.get_models() if issubclass(m, ScenarioItem)]:
            context[model._meta.object_name] = model
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
        response = render(self.request, self.template, self.context)
        response["HX-Trigger"] = "map-redraw"
        return response

    def create(self, request, *args, **kwargs):
        if self.instance:
            return HttpResponseBadRequest(
                b"The creation of an object is not possible with an instance"
            )
        # Create a new item and pass it back in the default state
        self.Form(data={"internal_id": uuid4()})
        # do NOT pass the request.POST directly into a query
        # which could lead to unauthorized injections
        # ScenarioItem.create_new sanitizes input for allowed attributes
        data = request.POST.dict()
        if self.Model == Area:
            new_instance = Area.create_new(self.scenario, **data)
            new_instance.save()
            self.context["instance"] = new_instance
            # Created areas are selected immediately
            self.context["createItemCallback"] = "this.click()"
            self.context["geom_form"] = AreaItemFormFactory()(instance=new_instance)
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
                self.context["internal_id"] = str(self.instance.internal_id)
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

        self.context |= get_home_context(scenario=self.scenario)
        self.context["created"] = True
        response = render(self.request, self.template, self.context)
        response["HX-Trigger"] = "map-redraw"
        return response

    def delete(self, request, *args, **kwargs):
        form = self.Form(data=request.GET)
        form.is_valid()
        self.instance.delete()
        self.context["status"] = "deleted"
        response = render(
            self.request,
            "ports/partials/update_delete_create_scenario_item.html",
            self.context,
        )
        response["HX-Trigger"] = "map-redraw"
        return response

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
        self.context |= get_home_context(self.scenario)
        self.context["update"] = True

        response = render(self.request, self.template, self.context)
        response["HX-Trigger"] = "map-redraw"
        return response

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

        self.context |= get_home_context(self.scenario)
        self.context["update"] = True
        response = render(self.request, self.template, self.context)
        response["HX-Trigger"] = "map-redraw"
        return response


def create_project(request):
    if not request.user.is_authenticated:
        return HttpResponse("Not allowed")
    context = {}
    context["id"] = "project-create-modal"
    if request.method == "POST":
        form = CreateProjectForm(
            data=request.POST, template_queryset=get_template_scenarios(request.user)
        )
        success = False
        if form.is_valid():
            new_project = form.save(commit=True)
            new_project.manager = request.user
            scenario = form.cleaned_data["template_scenario_internal_id"]
            if scenario:
                new_scenario = duplicate_scenario(scenario, request.user)
            else:
                new_scenario = Scenario(manager=request.user, name="Basis-Szenario")
            new_scenario.project = new_project
            new_scenario.save()
            success = True
        context["form"] = form
        context["success"] = success
    return render(request, "core/partials/create_project.html", context)


def create_scenario(request, scenario_internal_id: UUID):
    """Create copy based on other scenario"""
    if not request.user.is_authenticated:
        return HttpResponse("Not allowed")
    scenario = get_object_or_404(Scenario, internal_id=scenario_internal_id)
    if request.user != scenario.manager and not request.user.is_superuser:
        return HttpResponse("Not allowed")
    context = {"id": "scenario-create-modal", "project": scenario.project}
    if request.method == "POST":
        form = CreateScenarioForm(data=request.POST, base_scenario=scenario, user=request.user)
        success = False
        if form.is_valid():
            new_scenario = form.save()
            redirect_url = reverse("ports:home", query={"internal_id": new_scenario.internal_id})
            context["redirect_url"] = redirect_url
            print(redirect_url)
            success = True
        context["form"] = form
        context["success"] = success

    return render(request, "ports/partials/create_scenario.html", context)


class ApiView(View):
    """Handle delete and duplicate for Project and Scenario, returning JSON responses."""

    action = None
    ALLOWED_MODELS = (Project, Scenario)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse(
                {"status": "failure", "message": "Authentication required"}, status=401
            )
        try:
            self.Model = apps.get_model("ports", kwargs["model"])
        except LookupError:
            return JsonResponse(
                {"status": "error", "message": f"Unknown model: {kwargs['model']}"}, status=400
            )
        if self.Model not in self.ALLOWED_MODELS:
            return JsonResponse({"status": "error", "message": "Model not supported"}, status=400)
        self.instance = get_object_or_404(self.Model, internal_id=kwargs["internal_id"])
        if self.action == "duplicate":
            return self.duplicate(request, *args, **kwargs)
        return super().dispatch(request, *args, **kwargs)

    def _check_permission(self, request):
        if request.user != self.instance.manager and not request.user.is_superuser:
            return JsonResponse({"status": "failure", "message": "Not allowed"}, status=403)
        return None

    def delete(self, request, *args, **kwargs):
        denied = self._check_permission(request)
        if denied:
            return denied
        self.instance.safe_delete()
        return JsonResponse({"status": "success", "message": "Deleted"}, status=200)

    def duplicate(self, request, *args, **kwargs):
        try:
            denied = self._check_permission(request)
            if denied:
                return denied
            if self.Model == Project:
                self.instance.internal_id = uuid4()
                self.instance.name += " (Dupliziert)"
                new_instance = duplicate_project(self.instance)
            else:
                new_instance = duplicate_scenario(self.instance, request.user)
            return JsonResponse(
                {
                    "status": "success",
                    "message": f"{new_instance.name} created",
                    "internal_id": str(new_instance.internal_id),
                },
                status=201,
            )
        except:  # noqa
            return JsonResponse({"status": "failure", "message": "Duplicating failed"}, status=400)


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
