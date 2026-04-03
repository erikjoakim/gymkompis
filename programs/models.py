from django.conf import settings
from django.db import models


class TrainingProgram(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"
        DRAFT = "draft", "Draft"

    class Source(models.TextChoices):
        AI_GENERATED = "ai_generated", "AI generated"
        MANUAL = "manual", "Manual"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="training_programs")
    name = models.CharField(max_length=120)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    request_prompt = models.TextField(blank=True)
    current_program = models.JSONField(default=dict, blank=True)
    version_number = models.PositiveIntegerField(default=1)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.AI_GENERATED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.name} ({self.user.email})"


class ProgramGenerationRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="program_generation_requests")
    prompt_text = models.TextField()
    attached_history_summary = models.JSONField(null=True, blank=True)
    llm_model = models.CharField(max_length=64, blank=True)
    prompt_version = models.CharField(max_length=64, blank=True)
    raw_llm_response = models.TextField(blank=True)
    validated_program_json = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)
    token_usage_input = models.PositiveIntegerField(null=True, blank=True)
    token_usage_output = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Program request #{self.pk} for {self.user.email}"
