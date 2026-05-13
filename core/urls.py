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
    path(
        "landing/",
        lambda x: render(x, template_name="core/landing_page.html"),
        name="landing_page",
    ),
    path(
        "login/",
        lambda x: render(x, template_name="core/login.html"),
        name="login",
    ),
    path(
        "register/",
        lambda x: render(x, template_name="core/register.html"),
        name="register",
    ),
    path(
        "reset_password/",
        lambda x: render(x, template_name="core/reset-password.html"),
        name="reset_password",
    ),
    path(
        "forgot_password/",
        lambda x: render(x, template_name="core/forgot-password.html"),
        name="forgot_password",
    ),
    path(
        "projects/",
        lambda x: render(x, template_name="core/projects.html"),
        name="projects",
    ),
    path(
        "user_rechte/",
        lambda x: render(x, template_name="core/user-rechte.html"),
        name="user_rechte",
    ),
    path(
        "ergebnisse/",
        lambda x: render(x, template_name="core/ergebnisse.html"),
        name="ergebnisse",
    ),
    path(
        "einstellungen/",
        lambda x: render(x, template_name="core/einstellungen.html"),
        name="einstellungen",
    ),
    path(
        "project_overview/",
        lambda x: render(x, template_name="core/project-overview.html"),
        name="project_overview",
    ),
    path(
        "szenarienvergleich/",
        lambda x: render(x, template_name="core/szenarienvergleich.html"),
        name="szenarienvergleich",
    ),
]
