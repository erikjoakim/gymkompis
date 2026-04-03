from django.urls import path

from .views import evaluation_detail_view, request_period_evaluation_view, request_session_evaluation_view


urlpatterns = [
    path("period/request/", request_period_evaluation_view, name="request_period_evaluation"),
    path("session/<int:session_id>/request/", request_session_evaluation_view, name="request_session_evaluation"),
    path("<int:evaluation_id>/", evaluation_detail_view, name="evaluation_detail"),
]
