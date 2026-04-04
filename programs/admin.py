from django.contrib import admin
from django.utils.html import format_html

from .models import Exercise, ManualProgramDay, ManualProgramDraft, ManualProgramExercise, ProgramGenerationRequest, TrainingProgram


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


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = (
        "image_preview",
        "name",
        "external_id",
        "modality",
        "category",
        "library_role",
        "supports_reps",
        "supports_time",
        "instructions_status",
        "image_status",
        "is_active",
    )
    list_filter = (
        "modality",
        "library_role",
        "supports_reps",
        "supports_time",
        "instructions_status",
        "image_status",
        "is_active",
    )
    search_fields = ("name", "external_id", "equipment", "category", "movement_pattern")
    readonly_fields = ("image_preview_large",)
    actions = ("mark_images_reviewed", "reset_image_status")

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "external_id",
                    "source_dataset",
                    "name",
                    "aliases",
                    "modality",
                    "library_role",
                    "equipment",
                    "category",
                    "movement_pattern",
                    "primary_muscles",
                    "secondary_muscles",
                    "stabilizers",
                    "unilateral",
                    "is_static",
                    "supports_reps",
                    "supports_time",
                    "is_active",
                )
            },
        ),
        (
            "Instruction Content",
            {
                "fields": (
                    "instructions",
                    "instructions_status",
                    "instruction_source",
                    "default_video_url",
                )
            },
        ),
        (
            "Image Content",
            {
                "fields": (
                    "image_preview_large",
                    "image_url",
                    "generated_image",
                    "image_status",
                    "image_prompt",
                    "image_source",
                    "image_generated_at",
                    "image_error_message",
                )
            },
        ),
    )

    @admin.action(description="Mark selected image drafts as reviewed")
    def mark_images_reviewed(self, request, queryset):
        queryset.update(image_status=Exercise.ImageStatus.REVIEWED)

    @admin.action(description="Reset image status to missing")
    def reset_image_status(self, request, queryset):
        queryset.update(image_status=Exercise.ImageStatus.MISSING, image_error_message="")

    def image_preview(self, obj):
        if obj.display_image_url:
            return format_html('<img src="{}" alt="{}" style="width:48px;height:48px;object-fit:cover;border-radius:8px;" />', obj.display_image_url, obj.name)
        return "-"
    image_preview.short_description = "Image"

    def image_preview_large(self, obj):
        if obj.display_image_url:
            return format_html('<img src="{}" alt="{}" style="max-width:240px;border-radius:12px;" />', obj.display_image_url, obj.name)
        return "No image available."
    image_preview_large.short_description = "Preview"


class ManualProgramExerciseInline(admin.TabularInline):
    model = ManualProgramExercise
    extra = 0


@admin.register(ManualProgramDay)
class ManualProgramDayAdmin(admin.ModelAdmin):
    list_display = ("draft", "day_key", "name", "day_type")
    list_filter = ("day_key", "day_type")
    search_fields = ("draft__name", "draft__user__email", "name")
    inlines = [ManualProgramExerciseInline]


@admin.register(ManualProgramDraft)
class ManualProgramDraftAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "weight_unit", "updated_at", "published_at", "published_program")
    search_fields = ("name", "user__email")


@admin.register(ManualProgramExercise)
class ManualProgramExerciseAdmin(admin.ModelAdmin):
    list_display = ("day", "exercise", "block_type", "order", "prescription_type", "sets_count")
    list_filter = ("block_type", "prescription_type")
    search_fields = ("day__draft__name", "exercise__name")
