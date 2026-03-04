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
        "<uuid:scenario_internal_id>/<str:model>/crud/",
        views.CrudView.as_view(),
        name="crud_model",
    ),
    path(
        "<uuid:scenario_uuid>/<str:first_load_str>/<str:last_update_str>/get_updates/",
        views.get_updates,
        name="get_updates",
    ),
]
