from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_variables

from core.models import Role


class SignUpForm(UserCreationForm):
    first_name = forms.CharField(label=_("Vorname (optional)"), max_length=64, required=False)
    last_name = forms.CharField(label=_("Nachname (optional)"), max_length=64, required=False)
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True}),
        label=_("E-Mail*"),
        max_length=254,
        required=True,
    )
    password1 = forms.CharField(label=_("Passwort*"), required=True, widget=forms.PasswordInput)
    password2 = forms.CharField(
        label=_("Passwort wiederholen*"), required=True, widget=forms.PasswordInput
    )
    accepts_terms = forms.BooleanField(required=True)

    class Meta:
        model = User
        fields = (
            "email",
            "first_name",
            "last_name",
            "password1",
            "password2",
            "accepts_terms",
        )

    def clean_accepts_terms(self):
        accepts_terms = self.cleaned_data["accepts_terms"]
        if not accepts_terms:
            raise forms.ValidationError(
                _("Für einen Zugang musst du der Datenschutzerklärung zustimmen.")
            )
        return True

    def clean_email(self):
        """
        Check that lowercase user email is unique (used as username)
        """
        email = self.cleaned_data["email"]
        if User.objects.filter(username=email.lower()).exists():
            raise forms.ValidationError(email.lower() + _(" existiert bereits."))
        return email


class InviteForm(forms.Form):
    """Collects the email and role of a user to invite to a project."""

    email = forms.EmailField(
        label="E-Mail",
        max_length=254,
        required=True,
    )
    role = forms.ChoiceField(
        choices=[
            (s.value, s.label)
            for s in [
                Role.EDITOR,
                Role.OBSERVER,
            ]
        ],
        initial=Role.EDITOR,
        required=True,
    )


class AuthForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={"autofocus": True}),
        label="E-Mail",
        max_length=254,
        required=True,
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={}), label="Passwort", required=True
    )

    def clean_username(self):
        """
        force lowercase (used as username)
        """
        return self.cleaned_data["username"].lower()


class ChangeAccountDataForm(forms.ModelForm):
    """Edits a user's email/first/last name, gated behind re-entering their current
    password."""

    current_password = forms.CharField(
        widget=forms.PasswordInput(attrs={}), label="Aktuelles Passwort", required=True
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["email"].label = "E-mail"
        self.fields["first_name"].label = "Vorname"
        self.fields["last_name"].label = "Nachname"

    class Meta:
        model = User
        fields = (
            "email",
            "first_name",
            "last_name",
        )

    @sensitive_variables()
    def clean_current_password(self) -> str | None:
        """
        Check given password
        """
        cleaned_pw = self.cleaned_data["current_password"]
        if not self.instance.check_password(cleaned_pw):
            error = forms.ValidationError(
                _("Your password was entered incorrectly. Please enter it again."),
                code="password_mismatch",
            )
            self.add_error("current_password", error)
        else:
            return cleaned_pw

    def clean_email(self) -> str:
        """
        Check that lowercase user email is unique (used as username)
        """
        email = self.cleaned_data["email"].lower()
        # Does another user with this email exist already?
        if User.objects.filter(username=email).exclude(id=self.instance.pk).exists():
            raise forms.ValidationError(email + _(" existiert bereits."))
        return email

    def save(self, commit: bool = True) -> User:
        """
        Sync the username to the (lowercased) email before saving.
        """
        self.instance.username = self.instance.email.lower()
        user = super().save(commit=commit)
        print(user.username, user.email)
        return user
