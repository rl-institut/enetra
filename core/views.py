from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.core import mail
from django.core import signing
from django.http import Http404
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render  # noqa
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from ports.forms import CreateProjectForm
from ports.models import Project
from ports.util import get_template_scenarios
from ports.util import get_user_projects
from ports.util import prefetch_projects_users

from .forms import AuthForm
from .forms import SignUpForm


def projects_view(request):
    if not request.user.is_authenticated:
        # login may further redirect to the
        # LOGIN_REDIRECT_URL
        return redirect(reverse("core:login"))
    context = {}
    template_scenarios = get_template_scenarios(request.user)
    projects = get_user_projects(request.user)
    context["projects"] = projects
    prefetch_projects_users(projects)
    context["create_project_form"] = CreateProjectForm(template_queryset=template_scenarios)
    return render(request, template_name="core/projects.html", context=context)


def project_overview_view(request, project_internal_id):
    if not request.user.is_authenticated:
        # login may further redirect to the
        # LOGIN_REDIRECT_URL
        return redirect(reverse("core:login"))
    project = get_object_or_404(Project, internal_id=project_internal_id)
    # TODO: User permission
    if project.manager != request.user and not request.user.is_superuser:
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
    if request.method == "POST":
        # posted data: create new user instance
        form = SignUpForm(request.POST)
        if not form.is_valid():
            return render(request, "core/registration/signup.html", {"form": form})
        user = form.save()  # read necessary info from form
        user.refresh_from_db()
        user.username = user.email.lower()  # force lowercase for username
        # # user came here from invite: no further email needed
        # Work with invites?
        # user.is_active = form.cleaned_data["invited"]
        user.is_active = False
        user.save()
        if user.is_active:
            login(request, user, "django.contrib.auth.backends.ModelBackend")
            return redirect(reverse("core:landing_page"))
        else:
            token = signing.dumps(user.username)
            signup_url = request.build_absolute_uri(reverse("core:signup") + f"?token={token}")
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

    elif request.GET.get("token"):
        # token may be from signup process or invite
        try:
            email = signing.loads(request.GET["token"])
        except signing.BadSignature:
            return HttpResponse("Wrong signature", status=400)
        try:
            user = User.objects.get(username=email.lower())
            # token from signup: activate user
            user.is_active = True
            user.save(update_fields=["is_active"])
            form = AuthForm(initial={"username": user.email})
            return render(request, "core/registration/registration_success.html", {"form": form})
        except User.DoesNotExist:
            # token from invite: present registration form, fill in email from token
            form = SignUpForm(initial={"email": email, "invited": True})
            return render(request, "core/registration/signup.html", {"form": form})
    else:
        # GET, no token: normal registration
        return render(request, "core/registration/signup.html", {"form": SignUpForm()})
    raise Http404()


@login_required(login_url="/login/")
def changePassword(request):
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)  # auth user again
            messages.success(request, "Passwort erfolgreich geändert")
            return redirect(reverse("core:home"))
        else:
            messages.error(request, "Fehlerhafte Eingabe! Passwort nicht geändert.")
    else:
        form = PasswordChangeForm(request.user)
    # return view
    return render(request, "core/registration/password_change.html", {"form": form})


@login_required(login_url="/login/")
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
