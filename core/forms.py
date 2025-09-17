from django import forms
from django.contrib.auth.forms import AuthenticationForm


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
