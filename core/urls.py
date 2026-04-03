from django.urls import path

from .views import dashboard_view, healthcheck_view, home_view


urlpatterns = [
    path("", home_view, name="home"),
    path("dashboard/", dashboard_view, name="dashboard"),
    path("health/", healthcheck_view, name="healthcheck"),
]
