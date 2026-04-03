from django.urls import path

from .views import subscription_view


urlpatterns = [
    path("", subscription_view, name="subscription"),
]
