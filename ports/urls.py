from django.urls import path

from . import views  # noqa

app_name = "ports"

urlpatterns = [
    path(
        "map/",
        views.test,
        name="map",
    ),
    path(
        "",
        views.home,
        name="home",
    ),
    path(
        "simulate/",
        views.testview,
        name="test",
    ),
    path(
        "dynamic_forms_example/",
        views.dynamic_forms,
        name="dynamic_forms_example",
    ),
    path(
        "leaflet_example/",
        views.leaflet,
        name="leaflet",
    ),
    path(
        "details_create/<uuid:scenario_internal_id>/<str:model>/",
        views.DetailsView.as_view(),
        name="details_create",
    ),
    path(
        "details/<uuid:scenario_internal_id>/<str:model>/<uuid:internal_id>/",
        views.DetailsView.as_view(),
        name="details",
    ),
    path(
        "<uuid:scenario_uuid>/<str:first_load_str>/<str:last_update_str>/get_updates/",
        views.get_updates,
        name="get_updates",
    ),
]
