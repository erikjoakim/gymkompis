from django.urls import path

from .views import current_program_view, generate_program_view, program_detail_view


urlpatterns = [
    path("current/", current_program_view, name="current_program"),
    path("generate/", generate_program_view, name="generate_program"),
    path("<int:program_id>/", program_detail_view, name="program_detail"),
]
