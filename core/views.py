import functools
import secrets
from collections.abc import Callable
from datetime import datetime
from datetime import timedelta
from itertools import chain

from django.apps.registry import apps
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth import logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.core import mail
from django.core import signing
from django.db.models import F
from django.db.models import Q
from django.db.transaction import atomic
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render  # noqa
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from django.views.generic import TemplateView

from core.models import Invite
from core.models import InviteError
from core.models import Role
from ports.authorization import has_authorization
from ports.forms import CreateProjectForm
from ports.models import Project
from ports.models import Scenario
from ports.models import ScenarioItem
from ports.util import get_template_scenarios
from ports.util import get_user_projects
from ports.util import prefetch_projects_users

from .forms import AuthForm
from .forms import ChangeAccountDataForm
from .forms import InviteForm
from .forms import SignUpForm


class LoginViewWithRemember(LoginView):
    """If the form contains a checkbox with name='remember', this is used to set the session expiration"""

    def form_valid(self, form: AuthenticationForm) -> HttpResponse:
        """Log the user in, then set session expiry based on the 'remember' checkbox."""
        response = super().form_valid(form)

        if self.request.POST.get("remember"):
            self.request.session.set_expiry(60 * 60 * 24 * 30)  # 30 days
        else:
            self.request.session.set_expiry(0)  # browser session. Gone after browser closes
        return response


@login_required()
@require_http_methods(["POST"])
def delete_account(request: HttpRequest) -> HttpResponse:
    """
    Deletion of account deletes all content managed by the user
    This can lead cascading deletes of non user content
    """
    user = request.user
    # All scenarios insdie projects of the user and all scenarios managed by the user
    with atomic():
        scenarios = Scenario.objects.filter(Q(project__manager=user) | Q(manager=user))
        with Scenario.true_delete(scenarios.values_list("id", flat=True)):
            scenarios.delete()
        Project.objects.filter(manager=user).delete()
        port_models = apps.get_app_config("ports").get_models()
        for Model in port_models:
            if not issubclass(Model, ScenarioItem):
                continue
            Model.objects.filter(manager=user).delete()
        user.delete()
    logout(request)
    return redirect(reverse("core:landing_page"))


@login_required()
@require_http_methods(["GET", "POST"])
def account_settings(request: HttpRequest) -> HttpResponse:
    """Show and process the account-data and password-change forms on the settings page."""
    context = {}
    match request.method:
        case "GET":
            context["account_form"] = ChangeAccountDataForm(instance=request.user)
            context["password_change_form"] = PasswordChangeForm(user=request.user)
        case "POST":
            change_account_form = ChangeAccountDataForm(data=request.POST, instance=request.user)
            change_password_form = PasswordChangeForm(data=request.POST, user=request.user)
            context["account_form"] = change_account_form
            context["password_change_form"] = change_password_form
            if change_account_form.is_valid():
                change_account_form.save()
            if change_password_form.is_valid():
                change_password_form.save()
                update_session_auth_hash(request, request.user)

    return render(request, template_name="core/einstellungen.html", context=context)


def ensure_project_rights(func: Callable) -> Callable:
    """Decorate a view keyed by `project_internal_id` to require login and project
    view permission, and inject the resolved `project` as a kwarg."""

    @functools.wraps(func)
    def wrapped_function(*args, **kwargs) -> HttpResponse:
        request = args[0]
        project_internal_id = kwargs.get("project_internal_id")
        if not isinstance(request, HttpRequest) or project_internal_id is None:
            raise Exception(
                "ensure_project_rights Decorator works with views with project_internal_id as kwarg"
            )

        if not request.user.is_authenticated:
            # login may further redirect to the
            # LOGIN_REDIRECT_URL
            return redirect(reverse("core:login"))

        project = get_object_or_404(Project, internal_id=project_internal_id)

        if not has_authorization(project, request.user, "view"):
            return HttpResponseForbidden("Not allowed")

        return func(*args, **kwargs, project=project)

    return wrapped_function


@login_required()
def projects_view(request):
    context = {}
    template_scenarios = get_template_scenarios(request.user)
    projects = get_user_projects(request.user)
    context["projects"] = projects
    prefetch_projects_users(projects)
    context["create_project_form"] = CreateProjectForm(template_queryset=template_scenarios)
    return render(request, template_name="core/projects.html", context=context)


def handle_invite(request: HttpRequest) -> HttpResponse:
    """Redeem an invite token: log in and add the matching user to the invite's
    group, redirect to signup for a not-yet-registered email, or reject a
    mismatched logged-in user."""
    token = request.GET.get("project_token")
    invite = get_object_or_404(Invite, token=token)
    email = invite.payload["email"]
    user = User.objects.filter(email=email).first()
    if user:
        if request.user == user or not request.user.is_authenticated:
            try:
                Invite.add_user_to_group_from_token(token, user)
            except InviteError as error:
                return HttpResponse(str(error), status=400)

            # The user exists already. Since the token was only sent via email to the user
            # we can be sure the user should have access to the account
            login(request, user, "django.contrib.auth.backends.ModelBackend")
            project_internal_id = invite.payload.get("project_internal_id")
            if project_internal_id:
                return redirect(
                    reverse(
                        "core:project_overview", kwargs={"project_internal_id": project_internal_id}
                    )
                )
            return redirect(reverse("core:projects"))
        else:
            return HttpResponse("You are not the user the invite is for")

    return redirect(reverse("core:signup", query={"project_token": token}))


# Decorator which ensures view permission on the project and injects project as kwarg
@ensure_project_rights
def user_rights_view(
    request: HttpRequest, project_internal_id: str, project: Project
) -> HttpResponse:
    """Show a project's users grouped by role (manager/editor/observer), including
    pending invites, and handle sending a new invite via POST."""
    context = {}
    context["project"] = project
    prefetch_projects_users([project])
    group = Group.objects.get(name=project.group_name())
    users = dict()
    users[Role.MANAGER.value] = set([project.manager])
    # All users with details permission for the project.
    # Remove manager with highest permission
    users[Role.EDITOR.value] = set(project.users["details"]).difference(users[Role.MANAGER.value])
    # All users with view permission for the project.
    # Remove all users which also have higher permissions
    users[Role.OBSERVER.value] = (
        set(project.users["view"])
        .difference(users[Role.EDITOR.value])
        .difference(users[Role.MANAGER.value])
    )

    # annotate these user with active to show in table
    for role_users in users.values():
        for u in role_users:
            u.active = True

    # add invited people to list as pending
    invites = Invite.objects.filter(group=group, uses__lt=F("max_uses"))
    # invited people are not active
    # add pseudo id so user becomes hashable
    for invite in invites:
        # hacky solution. Negative ids will not collide with users from db
        # also the user is mapped directly to the invite
        user = User(email=invite.payload["email"], id=-invite.id)
        user.active = False
        users[invite.payload["role"]].add(user)

    delete_urls = {}
    if request.user.is_superuser or request.user == project.manager:
        for user in chain(users[Role.EDITOR.value], users[Role.OBSERVER.value]):
            if user.id < 0:
                # User was added to project via invite but did not accept yet -> delete invite
                signed_id = signing.dumps(str(-user.id), salt="invite_id")
                url = reverse(
                    "ports:api_remove_project_invite",
                    kwargs={"signed_invite_id": signed_id},
                )

            else:
                # User is part of the project. Delete user from group and remove all user managed content from project
                url = reverse(
                    "ports:api_remove_project_user",
                    kwargs={"project_internal_id": project_internal_id, "email": user.email},
                )
            delete_urls[user] = url
    context["delete_urls"] = delete_urls
    # Sort the users by lastname, email and activity
    for role, role_users in users.items():
        users[role] = list(sorted(role_users, key=lambda x: (x.last_name, x.email, x.active)))

    context["users"] = users
    if request.method == "GET":
        context["form"] = InviteForm()
    elif request.method == "POST":
        form = InviteForm(request.POST)
        context["form"] = form
        if request.user != project.manager and not request.user.is_superuser:
            form.add_error(None, "Nur der Projektmanager kann Nutzer hinzufügen")
        if not form.is_valid():
            return render(request, template_name="core/user-rechte.html", context=context)
        email = form.cleaned_data["email"]
        role = form.cleaned_data["role"]
        # TODO: Different Roles per project? Currently Role is not used
        token = secrets.token_urlsafe(32)
        invite = Invite.objects.create(
            token=token,
            group=group,
            created_by=request.user,
            expires_at=datetime.now() + timedelta(days=365),
            payload={
                "email": email,
                "role": role,
                "project_internal_id": str(project.internal_id),
            },
        )
        invite_url = request.build_absolute_uri(
            reverse("core:invite", query={"project_token": token})
        )
        mail.send_mail(
            subject="Einladung zu Projekt",
            message=f"Du wurdest zum Projekt {project.name} eingeladen. Klicke auf folgenden link um die Einladung anzunehmen {invite_url}",
            from_email=None,
            recipient_list=[email],
            fail_silently=False,
        )
        # clear form
        form = InviteForm()
        return redirect(
            reverse("core:user_rechte", kwargs={"project_internal_id": project_internal_id})
        )

    else:
        HttpResponseNotAllowed("Method not allowed")

    return render(request, template_name="core/user-rechte.html", context=context)


def scenario_results(request, scenario_internal_id):
    # TODO: guard results page against unwarranted access
    context = {}
    scenario = get_object_or_404(Scenario, internal_id=scenario_internal_id)
    context["scenario"] = scenario

    context["project"] = scenario.project
    return render(request, template_name="core/ergebnisse.html", context=context)


@login_required()
def project_overview_view(request, project_internal_id):
    project = get_object_or_404(Project, internal_id=project_internal_id)

    if not has_authorization(project, request.user, "view"):
        return HttpResponseForbidden("Not allowed")
    context = {}
    context["project"] = project
    response = render(request, template_name="core/project-overview.html", context=context)
    return response


# ******** User management ******** #
def signup(request):
    """
    Create new user model from form input.
    """
    if request.user.is_authenticated:
        # login may further redirect to the
        # LOGIN_REDIRECT_URL
        return redirect(reverse("core:login"))

    email = None
    project_token = None
    if request.GET.get("token") or request.GET.get("project_token"):
        # token may be from signup process or invite
        if token := request.GET.get("token"):
            try:
                email = signing.loads(token)
            except signing.BadSignature:
                return HttpResponse("Wrong signature", status=400)
        else:
            project_token = request.GET.get("project_token")
            invite = Invite.objects.get(token=project_token)
            email = invite.payload["email"]

    if request.method == "POST":
        # posted data: create new user instance
        form = SignUpForm(request.POST)
        if not form.is_valid():
            return render(request, "core/registration/signup.html", {"form": form})
        user = form.save()  # read necessary info from form
        user.refresh_from_db()
        user.username = user.email.lower()  # force lowercase for username
        # # user came here from invite: no further email needed
        user.is_active = False
        # email was provided via token or project_token
        if email:
            user.is_active = True
        user.save()
        if user.is_active:
            login(request, user, "django.contrib.auth.backends.ModelBackend")

            # The user was brought here via project invite. after registration he is forwarded to the project_overview of the invite
            if project_token:
                return redirect(
                    reverse(
                        "core:invite",
                        query={"project_token": project_token},
                    )
                )
            return redirect(reverse("core:landing_page"))
        else:
            token = signing.dumps(user.username)
            signup_url = request.build_absolute_uri(reverse("core:signup", query={"token": token}))
            user.email_user(
                subject=_("Enetra Registrierung"),
                message=render_to_string(
                    "core/registration/email_signup.txt",
                    {"host_url": settings.DJANGO_HOST_URL, "signup_url": signup_url},
                ),
                html_message=render_to_string(
                    "core/registration/email_signup.html",
                    {"host_url": settings.DJANGO_HOST_URL, "signup_url": signup_url},
                ),
                fail_silently=True,
            )
            return render(
                request, "core/registration/registration_pending.html", {"email": user.email}
            )

    elif email:
        try:
            user = User.objects.get(username=email.lower())
            # token from signup: activate user
            user.is_active = True
            user.save(update_fields=["is_active"])
            form = AuthForm(initial={"username": user.email})
            return render(request, "core/registration/registration_success.html", {"form": form})
        except User.DoesNotExist:
            # token from invite: present registration form, fill in email from token
            form = SignUpForm(initial={"email": email})
            return render(request, "core/registration/signup.html", {"form": form})
    else:
        # GET, no token: normal registration
        return render(request, "core/registration/signup.html", {"form": SignUpForm()})
    raise Http404()


@login_required()
def test_email(request):
    if request.user.is_staff:
        mail.send_mail(
            subject="TEST",
            message="Wenn du das lesen kannst, ist die Email angekommen.",
            from_email=None,
            recipient_list=[request.user.email],
            fail_silently=False,
        )
    return redirect(request.GET.get("path", "/"))


class HelpView(TemplateView):
    template_name = "core/help.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["max_file_size_mb"] = settings.MAX_FILE_SIZE_B >> 20
        return context
