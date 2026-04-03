from django.conf import settings
from django.db import models

from training.models import WorkoutSession


class WorkoutEvaluation(models.Model):
    class EvaluationType(models.TextChoices):
        SESSION = "session", "Session"
        PERIOD = "period", "Period"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workout_evaluations")
    evaluation_type = models.CharField(max_length=16, choices=EvaluationType.choices)
    workout_session = models.ForeignKey(
        WorkoutSession,
        on_delete=models.CASCADE,
        related_name="evaluations",
        null=True,
        blank=True,
    )
    evaluation_start_date = models.DateField(null=True, blank=True)
    evaluation_end_date = models.DateField(null=True, blank=True)
    included_session_ids = models.JSONField(default=list, blank=True)
    requested_by_user = models.BooleanField(default=True)
    auto_generated = models.BooleanField(default=False)
    llm_model = models.CharField(max_length=64, blank=True)
    prompt_version = models.CharField(max_length=64, blank=True)
    input_json = models.JSONField(default=dict, blank=True)
    evaluation_json = models.JSONField(default=dict, blank=True)
    summary_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.evaluation_type} evaluation for {self.user.email}"
