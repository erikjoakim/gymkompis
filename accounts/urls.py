from django.urls import path

from django.contrib.auth import views as auth_views

from .views import (
    UserLoginView,
    UserLogoutView,
    UserPasswordChangeDoneView,
    UserPasswordChangeView,
    onboarding_view,
    profile_view,
    signup_view,
)


urlpatterns = [
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", UserLogoutView.as_view(), name="logout"),
    path("password-change/", UserPasswordChangeView.as_view(), name="password_change"),
    path("password-change/done/", UserPasswordChangeDoneView.as_view(), name="password_change_done"),
    path("password-reset/", auth_views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", auth_views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("reset/complete/", auth_views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("signup/", signup_view, name="signup"),
    path("onboarding/", onboarding_view, name="onboarding"),
    path("profile/", profile_view, name="profile"),
]
