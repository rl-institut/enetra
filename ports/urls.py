from django.urls import path

from . import views  # noqa

app_name = "ports"

urlpatterns = [
    path(
        "simulate/",
        views.testview,
        name="test",
    ),
    path(
        "",
        views.home,
        name="home",
    ),
    path(
        "leaflet/",
        views.leaflet,
        name="leaflet",
    ),
    # path(
    #     "leaflet/",
    #     views.leaflet,
    #     name="leaflet",
    # ),
    path(
        "<uuid:scenario_internal_id>/<str:model>/crud/",
        views.CrudView.as_view(),
        name="home",
    ),
    path(
        "<uuid:scenario_uuid>/<str:first_load_str>/<str:last_update_str>/get_updates/",
        views.get_updates,
        name="get_updates",
    ),
]
