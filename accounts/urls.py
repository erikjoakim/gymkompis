from django.urls import path

from django.contrib.auth import views as auth_views

from .views import UserLoginView, UserLogoutView, onboarding_view, profile_view, signup_view


urlpatterns = [
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", UserLogoutView.as_view(), name="logout"),
    path("password-reset/", auth_views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", auth_views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("signup/", signup_view, name="signup"),
    path("onboarding/", onboarding_view, name="onboarding"),
    path("profile/", profile_view, name="profile"),
]
