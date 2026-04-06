from django.conf import settings
from django.db import models


DAY_KEY_CHOICES = [
    ("monday", "Monday"),
    ("tuesday", "Tuesday"),
    ("wednesday", "Wednesday"),
    ("thursday", "Thursday"),
    ("friday", "Friday"),
    ("saturday", "Saturday"),
    ("sunday", "Sunday"),
]


DAY_TYPE_CHOICES = [
    ("training", "Training"),
    ("rest", "Rest"),
    ("cardio", "Cardio"),
    ("mobility", "Mobility"),
    ("rehab", "Rehab"),
]


class Exercise(models.Model):
    class Modality(models.TextChoices):
        BARBELL = "barbell", "Barbell"
        DUMBBELL = "dumbbell", "Dumbbell"
        MACHINE = "machine", "Machine"
        BODYWEIGHT = "bodyweight", "Bodyweight"
        CABLE = "cable", "Cable"
        KETTLEBELL = "kettlebell", "Kettlebell"
        BAND = "band", "Band"
        MOBILITY = "mobility", "Mobility"
        CARDIO = "cardio", "Cardio"
        OTHER = "other", "Other"

    class LibraryRole(models.TextChoices):
        MAIN = "main", "Main"
        WARMUP = "warmup", "Warmup"
        BOTH = "both", "Both"

    class InstructionStatus(models.TextChoices):
        MISSING = "missing", "Missing"
        SEEDED = "seeded", "Seeded"
        AI_DRAFT = "ai_draft", "AI draft"
        REVIEWED = "reviewed", "Reviewed"

    class ImageStatus(models.TextChoices):
        MISSING = "missing", "Missing"
        AI_DRAFT = "ai_draft", "AI draft"
        REVIEWED = "reviewed", "Reviewed"
        FAILED = "failed", "Failed"

    external_id = models.CharField(max_length=64, unique=True)
    source_dataset = models.CharField(max_length=32, blank=True)
    name = models.CharField(max_length=160)
    brand = models.CharField(max_length=80, blank=True)
    line = models.CharField(max_length=120, blank=True)
    aliases = models.JSONField(default=list, blank=True)
    raw_catalog_data = models.JSONField(default=dict, blank=True)
    canonical_exercise = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="merged_variants",
    )
    modality = models.CharField(max_length=24, choices=Modality.choices, default=Modality.OTHER)
    library_role = models.CharField(max_length=16, choices=LibraryRole.choices, default=LibraryRole.MAIN)
    equipment = models.CharField(max_length=160, blank=True)
    category = models.CharField(max_length=120, blank=True)
    movement_pattern = models.CharField(max_length=160, blank=True)
    primary_muscles = models.JSONField(default=list, blank=True)
    secondary_muscles = models.JSONField(default=list, blank=True)
    stabilizers = models.JSONField(default=list, blank=True)
    unilateral = models.BooleanField(default=False)
    is_static = models.BooleanField(default=False)
    supports_reps = models.BooleanField(default=True)
    supports_time = models.BooleanField(default=False)
    instructions = models.TextField(blank=True)
    instructions_status = models.CharField(
        max_length=16,
        choices=InstructionStatus.choices,
        default=InstructionStatus.MISSING,
    )
    instruction_source = models.CharField(max_length=64, blank=True)
    default_video_url = models.URLField(blank=True)
    image_url = models.URLField(blank=True)
    generated_image = models.ImageField(upload_to="exercise_images/", blank=True)
    image_status = models.CharField(
        max_length=16,
        choices=ImageStatus.choices,
        default=ImageStatus.MISSING,
    )
    image_prompt = models.TextField(blank=True)
    image_source = models.CharField(max_length=64, blank=True)
    image_generated_at = models.DateTimeField(null=True, blank=True)
    image_error_message = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "external_id")

    def __str__(self):
        return self.name

    @property
    def exercise_key(self):
        return self.external_id.lower().replace("-", "_")

    @property
    def display_image_url(self):
        if self.generated_image:
            try:
                return self.generated_image.url
            except ValueError:
                return self.image_url
        return self.image_url


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


class ManualProgramDraft(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="manual_program_drafts")
    name = models.CharField(max_length=120)
    goal_summary = models.CharField(max_length=500, blank=True)
    duration_weeks = models.PositiveSmallIntegerField(default=8)
    weight_unit = models.CharField(max_length=2, default="kg")
    program_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)
    published_program = models.ForeignKey(
        "TrainingProgram",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_manual_drafts",
    )

    class Meta:
        ordering = ("-updated_at", "-created_at")

    def __str__(self):
        return f"{self.name} ({self.user.email})"


class ManualProgramDay(models.Model):
    draft = models.ForeignKey(ManualProgramDraft, on_delete=models.CASCADE, related_name="days")
    day_key = models.CharField(max_length=16, choices=DAY_KEY_CHOICES)
    name = models.CharField(max_length=120)
    day_type = models.CharField(max_length=16, choices=DAY_TYPE_CHOICES, default="training")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("day_key", "id")
        constraints = [
            models.UniqueConstraint(fields=["draft", "day_key"], name="unique_manual_day_per_draft"),
        ]

    def __str__(self):
        return f"{self.get_day_key_display()} - {self.name}"

    @property
    def day_label(self):
        return self.get_day_key_display()


class ManualProgramExercise(models.Model):
    class BlockType(models.TextChoices):
        WARMUP = "warmup", "Warmup"
        MAIN = "main", "Main"

    class PrescriptionType(models.TextChoices):
        REPS = "reps", "Reps"
        TIME = "time", "Time"

    day = models.ForeignKey(ManualProgramDay, on_delete=models.CASCADE, related_name="manual_exercises")
    exercise = models.ForeignKey(Exercise, on_delete=models.PROTECT, related_name="manual_program_entries")
    block_type = models.CharField(max_length=16, choices=BlockType.choices, default=BlockType.MAIN)
    order = models.PositiveSmallIntegerField(default=1)
    prescription_type = models.CharField(
        max_length=8,
        choices=PrescriptionType.choices,
        default=PrescriptionType.REPS,
    )
    sets_count = models.PositiveSmallIntegerField(default=3)
    target_reps = models.CharField(max_length=20, blank=True)
    target_seconds = models.PositiveIntegerField(null=True, blank=True)
    load_guidance = models.CharField(max_length=100, blank=True)
    target_effort_rpe = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    rest_seconds_override = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("block_type", "order", "id")

    def __str__(self):
        return f"{self.day} - {self.exercise.name}"
