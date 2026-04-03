from django.contrib import admin

from .models import ProgramGenerationRequest, TrainingProgram


@admin.register(TrainingProgram)
class TrainingProgramAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "status", "version_number", "updated_at")
    list_filter = ("status", "source")
    search_fields = ("name", "user__email")


@admin.register(ProgramGenerationRequest)
class ProgramGenerationRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "llm_model", "created_at")
    list_filter = ("status", "llm_model", "prompt_version")
    search_fields = ("user__email", "prompt_text")
