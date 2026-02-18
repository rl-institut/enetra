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
        "<uuid:scenario_internal_id>/<str:model>/crud/",
        views.CrudView.as_view(),
        name="home",
    ),
]
