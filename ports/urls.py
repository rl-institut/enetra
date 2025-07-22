from django.urls import path

from . import views  # noqa

app_name = "ports"

urlpatterns = [
    path(
        "simulate/",
        views.testview,
        name="home",
    )
]
