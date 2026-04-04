from django.urls import path

from .views import (
    current_program_view,
    generate_program_view,
    manual_program_create_view,
    manual_program_day_detail_view,
    manual_program_detail_view,
    manual_program_list_view,
    program_detail_view,
)


urlpatterns = [
    path("current/", current_program_view, name="current_program"),
    path("generate/", generate_program_view, name="generate_program"),
    path("manual/", manual_program_list_view, name="manual_program_list"),
    path("manual/create/", manual_program_create_view, name="manual_program_create"),
    path("manual/<int:draft_id>/", manual_program_detail_view, name="manual_program_detail"),
    path(
        "manual/<int:draft_id>/days/<int:day_id>/",
        manual_program_day_detail_view,
        name="manual_program_day_detail",
    ),
    path("<int:program_id>/", program_detail_view, name="program_detail"),
]
