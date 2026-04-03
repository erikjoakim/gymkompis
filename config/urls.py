from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("accounts/", include("accounts.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("programs/", include("programs.urls")),
    path("train/", include("training.urls")),
    path("evaluations/", include("evaluations.urls")),
    path("subscription/", include("subscriptions.urls")),
]
