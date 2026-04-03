from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import User, UserProfile


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autofocus": True}))


class SignUpForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("email", "first_name", "last_name")


class ProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = (
            "display_name",
            "birth_year",
            "training_experience",
            "injuries_limitations",
            "equipment_access",
            "preferred_language",
            "timezone",
            "preferred_weight_unit",
            "plan_history_window_sessions",
        )
        widgets = {
            "injuries_limitations": forms.Textarea(attrs={"rows": 3}),
            "equipment_access": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan_history_window_sessions"].help_text = (
            f"Leave blank to use the app default of {settings.DEFAULT_PLAN_HISTORY_WINDOW_SESSIONS} sessions."
        )


class OnboardingForm(ProfileForm):
    pass
