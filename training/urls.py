from django.urls import path

from .views import (
    complete_session_view,
    submit_exercise_view,
    swap_exercise_view,
    train_day_view,
    train_index_view,
    workout_detail_view,
    workout_history_view,
)


urlpatterns = [
    path("", train_index_view, name="train_index"),
    path("history/", workout_history_view, name="workout_history"),
    path("history/<int:session_id>/", workout_detail_view, name="workout_detail"),
    path("day/<slug:day_key>/", train_day_view, name="train_day"),
    path("session/<int:session_id>/exercise/<slug:exercise_key>/submit/", submit_exercise_view, name="submit_exercise"),
    path("session/<int:session_id>/exercise/<slug:exercise_key>/swap/", swap_exercise_view, name="swap_exercise"),
    path("session/<int:session_id>/complete/", complete_session_view, name="complete_session"),
]
