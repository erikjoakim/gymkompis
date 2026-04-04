from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeDoneView, PasswordChangeView
from django.contrib.auth.decorators import login_required
from django.urls import reverse_lazy
from django.shortcuts import redirect, render

from .forms import EmailAuthenticationForm, OnboardingForm, SignUpForm


class UserLoginView(LoginView):
    authentication_form = EmailAuthenticationForm
    template_name = "registration/login.html"


class UserLogoutView(LogoutView):
    pass


class UserPasswordChangeView(PasswordChangeView):
    template_name = "registration/password_change_form.html"
    success_url = reverse_lazy("password_change_done")


class UserPasswordChangeDoneView(PasswordChangeDoneView):
    template_name = "registration/password_change_done.html"


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Your account has been created.")
        return redirect("onboarding")

    return render(request, "accounts/signup.html", {"form": form})


@login_required
def onboarding_view(request):
    profile = request.user.profile
    form = OnboardingForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        profile = form.save(commit=False)
        profile.onboarding_completed = True
        profile.save()
        messages.success(request, "Your profile has been updated.")
        return redirect("dashboard")

    return render(request, "accounts/onboarding.html", {"form": form})


@login_required
def profile_view(request):
    profile = request.user.profile
    form = OnboardingForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile saved.")
        return redirect("profile")

    return render(request, "accounts/profile.html", {"form": form, "profile": profile})
