from django.contrib import admin

from .models import WorkoutEvaluation


@admin.register(WorkoutEvaluation)
class WorkoutEvaluationAdmin(admin.ModelAdmin):
    list_display = ("user", "evaluation_type", "workout_session", "created_at")
    list_filter = ("evaluation_type", "auto_generated", "prompt_version")
    search_fields = ("user__email", "summary_text")
