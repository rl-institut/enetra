import json
import logging
from typing import Any

from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.urls import path
from django.views.generic import TemplateView
from guardian.admin import GuardedModelAdmin
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm
from unfold.forms import UserChangeForm
from unfold.forms import UserCreationForm
from unfold.views import UnfoldModelAdminViewMixin

from .forms import ScenarioAreasForm
from .models import ElectricComponent
from .models import Project
from .models import Scenario
from .models import ScenarioItem
from .util import process_geojson_dict_to_scenarios

logger = logging.getLogger(__name__)


# TODO: Implement more advances permissions using django-guardian
def get_queryset(self, request):
    qs = super(self.__class__, self).get_queryset(request)
    if request.user.is_superuser:
        return qs
    # Only show user's own articles for now
    return qs.filter(manager=request.user)


def has_change_permission(self, request, obj=None):
    if request.user.is_superuser:
        return True
    if obj and obj.manager != request.user:
        return False
    return super(self.__class__, self).has_change_permission(request, obj)


def has_delete_permission(self, request, obj=None):
    # Without this, `get_actions()` filters out "delete_selected" for every
    # non-superuser (no global delete_x permission is ever granted), which
    # empties `action_form` and hides the Unfold "Run" button entirely.
    # See https://github.com/unfoldadmin/django-unfold/issues/699
    if request.user.is_superuser:
        return True
    if obj and obj.manager != request.user:
        return False
    return super(self.__class__, self).has_delete_permission(request, obj)


@admin.register(Scenario)
class ScenarioAdmin(ModelAdmin, GuardedModelAdmin):
    def get_urls(self):
        # IMPORTANT: model_admin is required
        port_regions_view = self.admin_site.admin_view(
            PortRegionsUploadView.as_view(model_admin=self)
        )

        return super().get_urls() + [
            path("scenario_from_geojson", port_regions_view, name="scenario_from_geojson"),
        ]

    get_queryset = get_queryset
    has_change_permission = has_change_permission
    # has_delete_permission = has_delete_permission


methods = {
    "get_queryset": get_queryset,
    "has_change_permission": has_change_permission,
    "has_delete_permission": has_delete_permission,
    "search_fields": ("name",),
    "list_display": ("id", "name", "manager"),
}


admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    # Forms loaded from `unfold.forms`
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


DynamicClass = type("ProjectAdmin", (ModelAdmin,), methods)
admin.site.register(Project, DynamicClass)


methods["list_display"] = ("id", "name", "manager", "scenario_id")

# Add all scenario items to the admin panel
for subclass in ScenarioItem.__subclasses__():
    if subclass == ElectricComponent:
        continue  # abstract subclass
    DynamicClass = type(str(subclass) + "Admin", (ModelAdmin,), methods)
    admin.site.register(subclass, DynamicClass)
for subclass in ElectricComponent.__subclasses__():
    DynamicClass = type(str(subclass) + "Admin", (ModelAdmin,), methods)
    admin.site.register(subclass, DynamicClass)


class PortRegionsUploadView(UnfoldModelAdminViewMixin, TemplateView):
    title = "Create Scenarios from geojson"  # required: custom page header title
    permission_required = ()  # required: tuple of permissions
    template_name = "ports/partials/admin_geodata_upload.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["form"] = ScenarioAreasForm()
        return context

    def post(self, request):
        form = ScenarioAreasForm(request.POST, files=request.FILES)
        if form.is_valid():
            region_file = form.cleaned_data["geojson_ports_regions_file"]
            buildings_file = form.cleaned_data["geojson_ports_buildings_file"]
            regions = json.loads(region_file.read())
            buildings = json.loads(buildings_file.read())

            logging.info("Importing Scenarios based on %s and %s", region_file, buildings_file)
            new_scenarios, areas = process_geojson_dict_to_scenarios(
                regions, buildings, request.user
            )
            logging.info(
                "Importing Finished. %s scenarios and %s areas created",
                len(new_scenarios),
                len(areas),
            )
            return HttpResponse(
                f"Success: created {len(areas)} Areas and {len(new_scenarios)} Scenarios"
            )
        return self.get(request=request)
