from django.shortcuts import render
from django.urls import path

from . import views  # noqa

app_name = "core"

urlpatterns = [
    path(
        "select_test/",
        lambda x: render(x, template_name="cotton/select/test.html"),
        name="select_test",
    ),
    path(
        "tabs_test/",
        lambda x: render(x, template_name="cotton/tabs/test.html"),
        name="tabs_test",
    ),
]
