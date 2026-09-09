from django.contrib.auth.views import LogoutView
from django.contrib.auth.views import PasswordResetView
from django.shortcuts import render
from django.urls import path
from django.urls import reverse_lazy

from . import forms
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
    path("register/", views.signup, name="signup"),
    path(
        "login/",
        views.LoginViewWithRemember.as_view(
            authentication_form=forms.AuthForm,
            template_name="core/registration/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path(
        "logout/",
        LogoutView.as_view(
            template_name="core/landing_page.html",
        ),
        name="logout",
    ),
    path(
        "registration_pending/",
        lambda x: render(x, template_name="core/registration/registration_pending.html"),
        name="registration_pending",
    ),
    path(
        "registration_success/",
        lambda x: render(x, template_name="core/registration/registration_success.html"),
        name="registration_success",
    ),
    path(
        "delete_user/",
        views.delete_account,
        name="delete_user",
    ),
    path(
        "projects/",
        views.projects_view,
        name="projects",
    ),
    path(
        "invite/",
        views.handle_invite,
        name="invite",
    ),
    path(
        "user_rechte/<uuid:project_internal_id>",
        views.user_rights_view,
        name="user_rechte",
    ),
    path(
        "einstellungen/",
        views.account_settings,
        name="einstellungen",
    ),
    path(
        "project_overview/<uuid:project_internal_id>/",
        views.project_overview_view,
        name="project_overview",
    ),
    path(
        "szenarienvergleich/",
        lambda x: render(x, template_name="core/szenarienvergleich.html"),
        name="szenarienvergleich",
    ),
    path(
        "ergebnisse/<uuid:scenario_internal_id>/",
        views.scenario_results,
        name="ergebnisse",
    ),
    # Trigger Email with reset link
    path(
        "login/forgot_password/",
        PasswordResetView.as_view(
            template_name="core/registration/forgot-password.html",
            success_url=reverse_lazy("core:forgot_password_success"),
        ),
        name="forgot_password",
    ),
    path(
        "login/forgot_password_success/",
        lambda x: render(x, template_name="core/registration/forgot-password-success.html"),
        name="forgot_password_success",
    ),
]


# urlpatterns = [
#     path(
#         "login/",
#         LoginView.as_view(
#             authentication_form=forms.AuthForm,
#             template_name="core/registration/login.html",
#         ),
#         name="login",
#     ),
#     path(
#         "logout/",
#         LogoutView.as_view(
#             template_name="core/registration/logged_out.html",
#         ),
#         name="logout",
#     ),
#     path(
#         "password_reset/",
#         PasswordResetView.as_view(form_class=forms.PWReset),
#         name="password_reset",
#     ),
#     path("password_change/", views.changePassword, name="password_change"),
#     path("register/", views.signup, name="signup"),
#     path("profile/", TemplateView.as_view(template_name="core/profile.html"), name="profile"),
#     path("help/", views.HelpView.as_view(), name="help"),
#     path("test_email/", views.test_email, name="test_email"),
#     path("impressum/", TemplateView.as_view(template_name="core/legal.html"), name="legal"),
#     path("datenschutz/", TemplateView.as_view(template_name="core/privacy.html"), name="privacy"),
#     path("", include("django.conf.urls.i18n")),  # includes /setlang/ template
#     path("", TemplateView.as_view(template_name="core/index.html"), name="home"),
# ]
