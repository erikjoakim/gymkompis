from django.contrib import admin

from .models import WorkoutSession


@admin.register(WorkoutSession)
class WorkoutSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "workout_date", "planned_day_name", "status", "updated_at")
    list_filter = ("status", "workout_date")
    search_fields = ("user__email", "planned_day_name", "planned_day_key")
