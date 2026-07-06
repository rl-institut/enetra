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
from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.db.models import ForeignKey
from django.db.models import ManyToManyField
from django.db.models import QuerySet
from django.forms import model_to_dict
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponseBadRequest
from django.http import HttpResponseForbidden
from django.http.response import HttpResponse
from django.shortcuts import aget_object_or_404  # noqa
from django.shortcuts import get_object_or_404  # noqa
from django.shortcuts import redirect  # noqa
from django.shortcuts import render  # noqa
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.generic import View
from django_oemof import models as oemof_models
from django_oemof import simulation
from guardian.shortcuts import assign_perm
from guardian.shortcuts import get_objects_for_user
from guardian.shortcuts import get_perms
from guardian.shortcuts import remove_perm

from ports import models
from ports.create_placeholder_scenario import create_scenario
from ports.forms import AreaItemFormFactory
from ports.forms import LoadTemplateUploadForm
from ports.forms import ScenarioItemFormFactory

from .models import Area
from .models import ChangedItem
from .models import DeletedItem
from .models import ElectricComponent
from .models import Load
from .models import LoadTemplate
from .models import Scenario
from .models import ScenarioItem

logger = logging.getLogger("django-ports")


def get_related_model_values(
    instances: QuerySet[ScenarioItem], model: type[ScenarioItem], field_name: str
):
    """Find the values of field_name on all related instances of type model for the given queryset

    Used to find all area_internal_ids for a given QuerySet of ScenarioItems, so the permission
    can be checked on those areas
    """
    all_fields = instances.model._meta.get_fields()
    fk_fields = [
        f for f in all_fields if isinstance(f, ForeignKey) and issubclass(f.related_model, model)
    ]
    m2m_fields = [
        f
        for f in all_fields
        if isinstance(f, ManyToManyField) and issubclass(f.related_model, model)
    ]
    if not fk_fields and not m2m_fields:
        return None

    if fk_fields:
        instances = instances.select_related(*[f.name for f in fk_fields])
    if m2m_fields:
        instances = instances.prefetch_related(*[f.name for f in m2m_fields])

    values = []
    for instance in instances:
        for field in fk_fields:
            related_instance = getattr(instance, field.name)
            if related_instance is not None:
                values.append(getattr(related_instance, field_name))
        for field in m2m_fields:
            values.extend(getattr(instance, field.name).values_list(field_name, flat=True))
    return values


def has_area_authorization_from_uuids(
    uuids: list[UUID | str],
    user: User,
    crud: Literal["view", "create", "details", "change", "delete"],
    scenario: Scenario,
    model: type[ScenarioItem],
):
    if model is Area:
        area_uuids = uuids
    else:
        instances = model.objects.filter(scenario=scenario, internal_id__in=uuids)
        area_uuids = get_related_model_values(instances, Area, "internal_id")
    areas = Area.objects.filter(scenario=scenario, internal_id__in=area_uuids).exclude(manager=user)
    non_managed_areas_internal_ids = areas.values_list("internal_id", flat=True)
    allowed_internal_ids = get_objects_for_user(user, crud, areas).values_list(
        "internal_id", flat=True
    )
    missing_permissions = len(set(non_managed_areas_internal_ids).difference(allowed_internal_ids))
    return missing_permissions == 0


def has_authorization(
    item: Scenario | ScenarioItem,
    user: User,
    crud: Literal["view", "create", "details", "change", "delete"],
) -> bool:
    """
    View: See an item with limited set of attributes
    details: See an item with all its attributes
    Change: Change an item
    Delete: Delete an item
    Create: Create an item
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user == item.manager:
        return True

    # For now short the authorization for ScenarioItems, since their authorization is
    # based only on areas for now
    if isinstance(item, ScenarioItem):
        return has_area_authorization_from_uuids(
            [item.internal_id], user, crud, item.scenario, item.model()
        )

    has_perm = False
    match crud:
        case "delete" | "details":
            has_perm = user.has_perm("details", item)
        case "view":
            has_perm = user.has_perm("view", item)
        case _:
            print("no matching crud found for " + crud)
    return has_perm


def debug_switch_user(request, username: str):
    from django.contrib.auth import login
    from django.contrib.auth import logout

    if username == "SUPERUSER":
        user = User.objects.filter(is_superuser=True).first()
    else:
        user = User.objects.filter(username=username).first()
    if user:
        logout(request)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return render(request, "ports/partials/user_debug_buttons.html", {})


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
    if not has_authorization(scenario, request.user, "details"):
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
                base_qs = Model.objects.filter(scenario=scenario)
                created_items.extend(list(base_qs.filter(**filter)))
                filter = {
                    "created_at__lte": last_update,
                    "updated_at__gt": last_update,
                    "updated_at__lte": updated_at,
                }
                changed_items.extend(list(base_qs.filter(**filter)))

            base_qs = Area.objects.filter(scenario=scenario)
            allowed_details_ids = set(
                get_objects_for_user(request.user, "details", base_qs).values_list("id", flat=True)
            )
            managed_area_ids = set(
                base_qs.filter(manager=request.user).values_list("id", flat=True)
            )
            allowed_details_ids_union = allowed_details_ids.union(managed_area_ids)
            for items in [created_items, changed_items]:
                for item in items:
                    if isinstance(item, Area):
                        area_id = item.id
                    else:
                        try:
                            area_id = item.area_id
                        except AttributeError:
                            # instances without area are not authorized as
                            # secure default
                            continue
                    if area_id in allowed_details_ids_union:
                        item.has_authorization = True

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
    if not has_authorization(scenario, request.user, "details"):
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


def get_home_context(user: User, scenario: Scenario):
    data = {}
    data["scenario"] = scenario
    data["scenarios"] = Scenario.objects.all()
    data["Area"] = Area

    base_qs = Area.objects.filter(scenario=scenario)
    allowed_details_ids = set(
        get_objects_for_user(user, "details", base_qs).values_list("id", flat=True)
    )
    managed_area_ids = set(base_qs.filter(manager=user).values_list("id", flat=True))
    allowed_details_ids_union = allowed_details_ids.union(managed_area_ids)

    electric_components = dict()
    for m in apps.get_models():
        if issubclass(m, ElectricComponent):
            # create queries for all electriccomponenent models like
            # key is model_name + "s" ,e.g. solars, heatings, generators
            key = f"{m._meta.model_name}s"
            items = list(m.objects.filter(scenario=scenario))
            for item in items:
                if item.area_id in allowed_details_ids_union:
                    item.has_authorization = True
            data[key] = items
            electric_components[key] = items

    # put the queries in a dict to, so we can directly iterate over them
    data["electric_components"] = electric_components

    # important:  prefetch all related models to avoid n+1 queries
    all_areas = list(base_qs.prefetch_related("generator_set"))
    building_areas = list()
    open_areas = list()
    for area in all_areas:
        if area.area_type == Area.AreaTypeChoices.BUILDING:
            building_areas.append(area)
        if area.area_type == Area.AreaTypeChoices.OPEN:
            open_areas.append(area)

    for areas in [building_areas, open_areas]:
        for area in areas:
            if area.id in allowed_details_ids_union:
                area.has_authorization = True

    data["building_areas"] = building_areas
    data["open_areas"] = open_areas
    area_forms = []
    for a in open_areas + building_areas:
        area_forms.append(AreaItemFormFactory()(instance=a))
    data["area_forms"] = area_forms
    return data


def home(request):
    if request.GET.get("new") or Scenario.objects.count() == 0:
        # NOTE: during development call /?new=true
        # to create a new placeholder scenario
        s = create_scenario()
        s.name = request.GET.get("new", "NewScenario")
        s.save(update_fields=["name"])
        return redirect(
            reverse(
                "ports:enetra_tool",
                kwargs={"scenario_internal_id": s.internal_id},
            )
        )
    s_internal_id = Scenario.objects.order_by("created_at").last().internal_id
    return redirect(
        reverse(
            "ports:enetra_tool",
            kwargs={"scenario_internal_id": s_internal_id},
        )
    )


def enetra_tool(request, scenario_internal_id: UUID):
    scenario = get_object_or_404(Scenario, internal_id=scenario_internal_id)
    debug_buttons = render_to_string("ports/partials/user_debug_buttons.html", {}, request)
    if not request.user.is_authenticated:
        # During development allow easy access to scenario_users
        return HttpResponse(
            "<div>To See this scenario you need to be logged in as a user of the scenario_group</div>"
            + debug_buttons
        )
    elif not has_authorization(scenario, request.user, "details"):
        return HttpResponse(
            f"Current user {request.user} has no details permission for the scenario"
            + debug_buttons
        )
    context = get_home_context(user=request.user, scenario=scenario)
    return render(request, "ports/tool_base.html", context)


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

    def details_render(
        self, request, template_name, context=None, content_type=None, status=None, using=None
    ):
        if request.headers.get("HX-Request"):
            return render(
                request=request,
                template_name=template_name,
                context=context,
                content_type=content_type,
                status=None,
                using=using,
            )
        # Details were requested directly, return full response
        else:
            content = render_to_string(
                request=request,
                template_name=template_name,
                context=context,
                using=using,
            )
            context = get_home_context(user=request.user, scenario=self.scenario)
            context["content"] = content
        return render(
            request,
            "ports/tool_base.html",
            context,
            content_type=content_type,
            status=status,
            using=using,
        )

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
        assert issubclass(self.Model, ScenarioItem)
        self.data = request.GET
        if request.method == "POST":
            self.data = request.POST
        internal_ids = self.data.get("internal_ids", "").split(",")
        # These instances should be shown or posted.
        self.internal_ids = [] if internal_ids[0] == "" else internal_ids
        self.internal_id = self.data.get("internal_id")
        # NOTE: created is set through the url resolver
        self.multi = len(self.internal_ids) > 1
        if self.multi and self.internal_id:
            assert self.internal_id in self.internal_ids
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
        elif issubclass(self.Model, ElectricComponent):
            template = f"ports/partials/detail_sidebar/detail_sidebar_component{suffix}.html"
        else:
            raise NotImplementedError(f"{self.Model} is not implemented")
        return template

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            response = HttpResponse("You need to be authenicated to view this")
            return response
        assert isinstance(request.user, User)
        # Instantiate the class with its fixed attributes
        self.setup_view(request, *args, **kwargs)
        if not has_authorization(self.scenario, request.user, "view"):
            response = HttpResponse("You are not allowed to VIEW this scenario")
            return response

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
            return self.details_render(
                self.request,
                "ports/partials/detail_sidebar/detail_deleted.html",
                self.context,
            )
        raise Http404("This instance does not exist")

    def get(self, request, *args, **kwargs):
        if not has_authorization(self.instance, request.user, "details"):
            return HttpResponse("You are not allowed to see details")

        initial = {}
        if self.Model not in [Area, Load] and not issubclass(self.Model, ElectricComponent):
            raise Http404("This model does not exist or is not implemented yet")
        self.Form = self.Model.adjust_Form(self.Form, instance=self.instance)
        if self.Model == Area:
            group = Group.objects.get(name=self.scenario.group_name())
            # If Object permissions are queried multiple times consider using a
            # guardian.core.ObjectPermissionChecker
            initial = {"is_public": "details" in get_perms(group, self.instance)}
            self.context |= self.get_area_context()
        elif self.Model == Load:
            # TODO: Are all templates available to every user?
            templates = list(LoadTemplate.objects.filter(scenario=self.scenario))
            self.context["templates"] = templates
        elif issubclass(self.Model, ElectricComponent):
            # nothing to do here
            # ElectricComponent does not reference other models and does not need
            # further data injected
            pass
        else:
            raise NotImplementedError("No template defined for this Model")
        self.context["form"] = self.Form(initial=initial, instance=self.instance)
        response = self.details_render(self.request, self.template, self.context)
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
            new_instance = Area.create_new(self.scenario, request.user, **data)
            new_instance.save()
            new_instance.has_authorization = True
            self.context["instance"] = new_instance

            # Created areas are selected immediately
            self.context["createItemCallback"] = "this.click()"
            self.context["geom_form"] = AreaItemFormFactory()(instance=new_instance)
        elif self.Model == Load or issubclass(self.Model, ElectricComponent):
            # Handle single creation as well as creation from batch view
            area_internal_ids = self.request.POST.get("area_internal_ids").split(",")
            data["area_internal_ids"] = area_internal_ids
            # Check permissions for creation for all areas that are not managed by the request user
            has_permission = has_area_authorization_from_uuids(
                area_internal_ids, request.user, "details", scenario=self.scenario, model=self.Model
            )
            if not has_permission:
                return HttpResponse(
                    "You dont have permission to create instances for all the selected areas"
                )

            new_items = self.Model.create_new(self.scenario, request.user, **data)
            self.multi = len(area_internal_ids) > 1
            self.Form = ScenarioItemFormFactory(
                self.Model, multi=self.multi, scenario=self.scenario
            )
            self.instances = self.Model.objects.bulk_create(new_items)
            if not self.multi:
                self.instance = self.instances[0]
                self.instances = []
                self.instance.has_authorization = True
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
                for item in self.instances:
                    item.has_authorization = True

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

        self.context |= get_home_context(request.user, self.scenario)
        self.context["created"] = True
        response = self.details_render(self.request, self.template, self.context)
        response["HX-Trigger"] = "map-redraw"
        return response

    def delete(self, request, *args, **kwargs):
        if not has_authorization(self.instance, request.user, "delete"):
            response = HttpResponse("You are not allowed to delete")
            response["HX-Reselect"] = "unset"
            response["HX-Reswap"] = "innerHTML"
            return response
        self.instance.delete()
        self.context["status"] = "deleted"
        response = self.details_render(
            self.request,
            "ports/partials/update_delete_create_scenario_item.html",
            self.context,
        )
        response["HX-Trigger"] = "map-redraw"
        response["HX-Trigger"] = "hide-detail-sidebar"

        return response

    def multi_get(self, request, *args, **kwargs):
        if self.instance:
            return HttpResponseBadRequest(
                b"The fetching of multiple objects is not possible with a single instance"
            )

        if not has_area_authorization_from_uuids(
            self.internal_ids, request.user, "details", self.scenario, self.Model
        ):
            response = HttpResponse("You are not allowed to VIEW all of these items")
            response["HX-Reselect"] = "unset"
            response["HX-Reswap"] = "innerHTML"
            return response
        self.Form = self.Model.adjust_Form(self.Form, instance=self.instances[0])

        if self.Model == Area or issubclass(self.Model, ElectricComponent):
            merged_data = model_to_dict(self.instances[0])
            for x in self.instances:
                data = model_to_dict(x)
                for key, value in data.items():
                    if merged_data.get(key) != value and key in merged_data:
                        del merged_data[key]

            form = self.Form(data=merged_data)
            self.context["form"] = form
            return self.details_render(self.request, self.template, self.context)

        raise NotImplementedError(f"Multi Get not implemented for {self.Model.__name__}")

    def multi_post(self, request, *args, **kwargs):
        if self.Model not in [Area, Load] and not issubclass(self.Model, ElectricComponent):
            raise NotImplementedError("This model is not implemented for multi posting yet")
        if self.instance:
            return HttpResponseBadRequest(
                b"The patching of multiple objects is not possible with an instance"
            )

        if not has_area_authorization_from_uuids(
            self.internal_ids, request.user, "details", self.scenario, self.Model
        ):
            response = HttpResponse("You are not allowed to change all of these items")
            response["HX-Reselect"] = "unset"
            response["HX-Reswap"] = "innerHTML"
            return response
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

        if self.Model == Load or issubclass(self.Model, ElectricComponent):
            # Multi post request for Load needs references to areas
            self.context["area_internal_ids"] = ",".join(
                str(y)
                for y in (
                    Area.objects.filter(
                        id__in=[x.area_id for x in self.context["instances"]]
                    ).values_list("internal_id", flat=True)
                )
            )
        self.context |= get_home_context(user=request.user, scenario=self.scenario)
        self.context["update"] = True

        response = self.details_render(self.request, self.template, self.context)
        response["HX-Trigger"] = "map-redraw"
        return response

    def post(self, request, *args, **kwargs):
        if self.Model not in [Area, Load] and not issubclass(self.Model, ElectricComponent):
            raise NotImplementedError("This model is not implemented for posting yet")
        if not self.instance:
            return HttpResponseBadRequest(b"The patching of an object needs an instance")
        if not has_authorization(self.instance, request.user, "details"):
            response = HttpResponse("You are not allowed to change this item")

            response["HX-Reselect"] = "unset"
            response["HX-Reswap"] = "innerHTML"
            return response
        try:
            self.Form = self.Model.adjust_Form(self.Form, instance=self.instance)
            form = self.Form(data=request.POST, instance=self.instance)
            self.context["form"] = form
            if self.Model == Area:
                self.context |= self.get_area_context()
            elif self.Model == Load:
                templates = list(LoadTemplate.objects.filter(scenario=self.scenario))
                self.context["templates"] = templates
                self.context["upload_form"] = LoadTemplateUploadForm()
            if form.is_valid():
                self.context["item"] = form.save()
                group = Group.objects.get(name=self.scenario.group_name())
                if self.Model == Area:
                    if form.cleaned_data.get("is_public"):
                        assign_perm("details", group, form.instance)
                    else:
                        remove_perm("details", group, form.instance)

                self.context["success"] = "Erfolgreich gespeichert"
            else:
                self.context["errors"] = ["An error occured", form.errors]
        except Exception:
            logger.error(traceback.format_exc())
            self.context["errors"] = ["An unexpected error occured"]

        self.context |= get_home_context(user=request.user, scenario=self.scenario)
        self.context["update"] = True
        response = self.details_render(self.request, self.template, self.context)
        response["HX-Trigger"] = "map-redraw"
        return response


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
