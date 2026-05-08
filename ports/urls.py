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
        "details_create/<uuid:scenario_internal_id>/<str:model>/",
        views.DetailsView.as_view(created=True),
        name="details_create",
    ),
    path(
        "changes_count/<uuid:scenario_internal_id>/",
        views.changes_count,
        name="changes_count",
    ),
    path(
        "patch_area/<uuid:scenario_internal_id>/",
        views.patch_area,
        name="patch_area",
    ),
    path(
        "changes/<uuid:scenario_internal_id>/",
        views.changes,
        name="changes",
    ),
    path(
        "details/<uuid:scenario_internal_id>/<str:model>/",
        views.DetailsView.as_view(created=False),
        name="details",
    ),
]
