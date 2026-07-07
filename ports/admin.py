from typing import Any

from django.contrib import admin
from django.http import HttpResponse
from django.urls import path
from django.views.generic import TemplateView
from unfold.admin import ModelAdmin
from unfold.views import UnfoldModelAdminViewMixin

from .forms import ScenarioAreasForm
from .models import ElectricComponent
from .models import Scenario
from .models import ScenarioItem
from .util import scenarios_and_areas_from_file

# Register your models here.
# class ScenarioAdmin(ModelAdmin, GuardedModelAdmin):
#     pass
#
#
# admin.site.register(Scenario, ScenarioAdmin)


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


methods = {
    "get_queryset": get_queryset,
    "has_change_permission": has_change_permission,
}
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
            new_scenarios, areas = scenarios_and_areas_from_file(
                region_file, buildings_file, request.user
            )
            return HttpResponse(
                f"Success: created {len(areas)} Areas and {len(new_scenarios)} Scenarios"
            )
        return self.get(request=request)


@admin.register(Scenario)
class CustomAdmin(ModelAdmin):
    def get_urls(self):
        # IMPORTANT: model_admin is required
        port_regions_view = self.admin_site.admin_view(
            PortRegionsUploadView.as_view(model_admin=self)
        )

        return super().get_urls() + [
            path("scenario_from_geojson", port_regions_view, name="scenario_from_geojson"),
        ]
