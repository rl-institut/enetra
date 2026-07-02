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
        "simulate_oemof/",
        views.testview,
        name="test_oemof",
    ),
    path(
        "progress/<uuid:scenario_internal_id>/<str:task_type>/",
        views.progress,
        name="progress",
    ),
    path(
        "simulate/<uuid:scenario_internal_id>/",
        views.simulate,
        name="simulate",
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
        "calculate_modal/<uuid:scenario_internal_id>/",
        views.calculate_modal,
        name="calculate_modal",
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
