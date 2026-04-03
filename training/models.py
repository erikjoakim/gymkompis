from django.conf import settings
from django.db import models

from programs.models import TrainingProgram


class WorkoutSession(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workout_sessions")
    program = models.ForeignKey(TrainingProgram, on_delete=models.CASCADE, related_name="workout_sessions")
    planned_day_key = models.CharField(max_length=16)
    planned_day_label = models.CharField(max_length=32, blank=True)
    planned_day_name = models.CharField(max_length=120)
    workout_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IN_PROGRESS)
    session_json = models.JSONField(default=dict, blank=True)
    submission_version = models.PositiveIntegerField(default=0)
    last_exercise_submission_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-workout_date", "-updated_at")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "program", "workout_date", "planned_day_key"],
                name="unique_workout_session_per_user_program_day",
            )
        ]

    def __str__(self):
        return f"{self.user.email} - {self.workout_date} - {self.planned_day_name}"
