from django.urls import path

from .views import (
    clone_program_to_draft_view,
    current_program_view,
    generate_program_view,
    library_admin_view,
    library_admin_images_view,
    manual_program_create_view,
    manual_program_day_detail_view,
    manual_program_detail_view,
    manual_program_list_view,
    program_detail_view,
    program_history_view,
    restore_program_view,
)


urlpatterns = [
    path("current/", current_program_view, name="current_program"),
    path("history/", program_history_view, name="program_history"),
    path("library-admin/", library_admin_view, name="library_admin"),
    path("library-admin/images/", library_admin_images_view, name="library_admin_images"),
    path("generate/", generate_program_view, name="generate_program"),
    path("manual/", manual_program_list_view, name="manual_program_list"),
    path("manual/create/", manual_program_create_view, name="manual_program_create"),
    path("manual/<int:draft_id>/", manual_program_detail_view, name="manual_program_detail"),
    path(
        "manual/<int:draft_id>/days/<int:day_id>/",
        manual_program_day_detail_view,
        name="manual_program_day_detail",
    ),
    path("<int:program_id>/clone-to-draft/", clone_program_to_draft_view, name="clone_program_to_draft"),
    path("<int:program_id>/restore/", restore_program_view, name="restore_program"),
    path("<int:program_id>/", program_detail_view, name="program_detail"),
]
