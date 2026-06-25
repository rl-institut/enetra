from collections import defaultdict
from typing import Any

from django.contrib import admin
from django.db.transaction import atomic
from django.http import HttpResponse
from django.urls import path
from django.views.generic import TemplateView
from shapely import STRtree
from shapely import wkt
from unfold.admin import ModelAdmin
from unfold.views import UnfoldModelAdminViewMixin

from .forms import ScenarioAreasForm
from .models import Area
from .models import ElectricComponent
from .models import Scenario
from .models import ScenarioItem

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
            import tempfile

            from django.contrib.gis.gdal import DataSource

            region_file = form.cleaned_data["geojson_ports_regions_file"]
            buildings_file = form.cleaned_data["geojson_ports_buildings_file"]
            ds_regions = None
            ds_buildings = None
            with tempfile.NamedTemporaryFile(suffix=".geojson") as tmp:
                tmp.write(region_file.read())
                tmp.flush()
                ds_regions = DataSource(tmp.name)

            with tempfile.NamedTemporaryFile(suffix=".geojson") as tmp:
                tmp.write(buildings_file.read())
                tmp.flush()
                ds_buildings = DataSource(tmp.name)

            layer = ds_regions[0]
            scenarios = []
            for feature in layer:
                if feature.geom.geom_name == "MULTIPOLYGON":
                    geom = feature.geom[0].geos
                elif feature.geom.geom_name == "POLYGON":
                    geom = feature.geom.geos
                else:
                    raise NotImplementedError("Unkown file type " + feature.geom.geom_name)
                s = Scenario(
                    name=feature["port_name"],
                    geom=geom,
                    manager=request.user,
                )
                scenarios.append(s)
                print(feature["port_name"])
            with atomic():
                scenarios = Scenario.objects.bulk_create(scenarios)
                geoms = [wkt.loads(s.geom.wkt) for s in scenarios]
                tree = STRtree(geoms)
                layer = ds_buildings[0]
                areas = []
                new_scenarios = {}
                counts = defaultdict(int)
                for feature in layer:
                    if feature.geom.geom_name == "MULTIPOLYGON":
                        geom = feature.geom[0].geos
                    elif feature.geom.geom_name == "POLYGON":
                        geom = feature.geom.geos
                    else:
                        raise NotImplementedError("Unkown file type " + feature.geom.geom_name)
                    centroid = wkt.loads(feature.geom.centroid.wkt)
                    found_scenario = None
                    for candidate in tree.query(centroid, predicate="intersects"):
                        port_area = geoms[candidate]
                        if port_area.contains(centroid):
                            found_scenario = scenarios[candidate]
                            break
                    if found_scenario is None:
                        name = feature.get("inland_port")
                        if not name:
                            continue
                        found_scenario = new_scenarios.get(name)
                        if not found_scenario:
                            s = Scenario.objects.create(name=name, manager=request.user)
                            new_scenarios[name] = s
                            found_scenario = s
                    if not found_scenario:
                        print(feature, "\nnot found")
                        continue
                    print(".", end="")
                    num = counts[found_scenario] + 1
                    counts[found_scenario] += 1
                    area = Area(
                        name=f"Automatische Gebäudefläche {num}",
                        area_type=Area.AreaTypeChoices.BUILDING,
                        manager=request.user,
                        scenario=found_scenario,
                        geom=geom,
                    )
                    areas.append(area)
                areas = Area.objects.bulk_create(areas)
                return HttpResponse(
                    f"Success: created {len(areas)} Areas and {len(new_scenarios) + len(scenarios)} Scenarios"
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
