from django.urls import path

from . import views  # noqa

app_name = "ports"

urlpatterns = [
    path(
        "",
        views.home,
        name="home",
    ),
    path(
        "<uuid:scenario_internal_id>/",
        views.enetra_tool,
        name="enetra_tool",
    ),
    path(
        "simulate/",
        views.testview,
        name="test",
    ),
    path(
        "template_upload_from_load/<uuid:scenario_internal_id>/<str:model>/",
        views.template_upload_from_load,
        name="template_upload_from_load",
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
    path(
        "<uuid:scenario_uuid>/<str:first_load_str>/<str:last_update_str>/get_updates/",
        views.get_updates,
        name="get_updates",
    ),
    path(
        "debug/switch-user/<str:username>/",
        views.debug_switch_user,
        name="debug_switch_user",
    ),
]
