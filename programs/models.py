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
    class SourceKind(models.TextChoices):
        SYSTEM = "system", "System"
        CATALOG = "catalog", "Catalog"
        USER_SUBMITTED = "user_submitted", "User submitted"
        AI_SUGGESTED = "ai_suggested", "AI suggested"

    class VerificationStatus(models.TextChoices):
        APPROVED = "approved", "Approved"
        PENDING_REVIEW = "pending_review", "Pending review"
        REJECTED = "rejected", "Rejected"

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
    source_kind = models.CharField(max_length=24, choices=SourceKind.choices, default=SourceKind.SYSTEM)
    name = models.CharField(max_length=160)
    brand = models.CharField(max_length=80, blank=True)
    line = models.CharField(max_length=120, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="submitted_exercises",
    )
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
    verification_status = models.CharField(
        max_length=24,
        choices=VerificationStatus.choices,
        default=VerificationStatus.APPROVED,
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verified_exercises",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
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

    @property
    def can_copy_saved_image(self):
        static_prefix = f"{settings.STATIC_URL}exercise_images/"
        return bool(self.generated_image or (self.image_url and self.image_url.startswith(static_prefix)))


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


class ProgramDraft(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        AI_SEEDED = "ai_seeded", "AI seeded"
        HYBRID = "hybrid", "Hybrid"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="program_drafts")
    name = models.CharField(max_length=120)
    goal_summary = models.CharField(max_length=500, blank=True)
    duration_weeks = models.PositiveSmallIntegerField(default=8)
    weight_unit = models.CharField(max_length=2, default="kg")
    program_notes = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    request_prompt = models.TextField(blank=True)
    ai_context_notes = models.TextField(blank=True)
    last_ai_action = models.CharField(max_length=64, blank=True)
    latest_ai_evaluation = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)
    published_program = models.ForeignKey(
        "TrainingProgram",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_program_drafts",
    )

    class Meta:
        ordering = ("-updated_at", "-created_at")

    def __str__(self):
        return f"{self.name} ({self.user.email})"


class ProgramDraftDay(models.Model):
    draft = models.ForeignKey(ProgramDraft, on_delete=models.CASCADE, related_name="days")
    day_key = models.CharField(max_length=16, choices=DAY_KEY_CHOICES)
    name = models.CharField(max_length=120)
    day_type = models.CharField(max_length=16, choices=DAY_TYPE_CHOICES, default="training")
    notes = models.TextField(blank=True)
    ai_locked = models.BooleanField(default=False)

    class Meta:
        ordering = ("day_key", "id")
        constraints = [
            models.UniqueConstraint(fields=["draft", "day_key"], name="unique_program_draft_day_per_draft"),
        ]

    def __str__(self):
        return f"{self.get_day_key_display()} - {self.name}"

    @property
    def day_label(self):
        return self.get_day_key_display()


class ProgramDraftExercise(models.Model):
    class BlockType(models.TextChoices):
        WARMUP = "warmup", "Warmup"
        MAIN = "main", "Main"

    class PrescriptionType(models.TextChoices):
        REPS = "reps", "Reps"
        TIME = "time", "Time"

    day = models.ForeignKey(ProgramDraftDay, on_delete=models.CASCADE, related_name="draft_exercises")
    exercise = models.ForeignKey(Exercise, on_delete=models.SET_NULL, null=True, blank=True, related_name="program_draft_entries")
    snapshot_external_id = models.CharField(max_length=64, blank=True)
    snapshot_name = models.CharField(max_length=160)
    snapshot_modality = models.CharField(max_length=24, choices=Exercise.Modality.choices, default=Exercise.Modality.OTHER)
    snapshot_focus = models.CharField(max_length=300, blank=True)
    snapshot_instructions = models.TextField(blank=True)
    snapshot_image_url = models.URLField(blank=True)
    snapshot_video_url = models.URLField(blank=True)
    snapshot_category = models.CharField(max_length=120, blank=True)
    snapshot_brand = models.CharField(max_length=80, blank=True)
    snapshot_line = models.CharField(max_length=120, blank=True)
    snapshot_supports_reps = models.BooleanField(default=True)
    snapshot_supports_time = models.BooleanField(default=False)
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
    ai_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("block_type", "order", "id")

    def __str__(self):
        return f"{self.day} - {self.display_name}"

    @property
    def display_name(self):
        return self.snapshot_name or (self.exercise.name if self.exercise else "")

    @property
    def display_modality(self):
        return self.snapshot_modality or (self.exercise.modality if self.exercise else Exercise.Modality.OTHER)

    @property
    def display_category(self):
        return self.snapshot_category or (self.exercise.category if self.exercise else "")

    @property
    def display_instructions(self):
        return self.snapshot_instructions or (self.exercise.instructions if self.exercise else "")

    @property
    def display_image_url(self):
        return self.snapshot_image_url or (self.exercise.display_image_url if self.exercise else "")

    @property
    def display_video_url(self):
        return self.snapshot_video_url or (self.exercise.default_video_url if self.exercise else "")

    @property
    def display_brand(self):
        return self.snapshot_brand or (self.exercise.brand if self.exercise else "")

    @property
    def display_line(self):
        return self.snapshot_line or (self.exercise.line if self.exercise else "")

    @property
    def supports_reps(self):
        if self.exercise_id:
            return self.exercise.supports_reps
        return self.snapshot_supports_reps

    @property
    def supports_time(self):
        if self.exercise_id:
            return self.exercise.supports_time
        return self.snapshot_supports_time


class ProgramDraftAiRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    draft = models.ForeignKey(ProgramDraft, on_delete=models.CASCADE, related_name="ai_runs")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="program_draft_ai_runs")
    action_type = models.CharField(max_length=64)
    scope_payload = models.JSONField(default=dict, blank=True)
    prompt_text = models.TextField(blank=True)
    llm_model = models.CharField(max_length=64, blank=True)
    prompt_version = models.CharField(max_length=64, blank=True)
    raw_llm_response = models.TextField(blank=True)
    validated_payload = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)
    token_usage_input = models.PositiveIntegerField(null=True, blank=True)
    token_usage_output = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.action_type} for draft #{self.draft_id}"


class ProgramDraftRevision(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        AI = "ai", "AI"
        SYSTEM = "system", "System"

    draft = models.ForeignKey(ProgramDraft, on_delete=models.CASCADE, related_name="revisions")
    revision_number = models.PositiveIntegerField()
    created_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="program_draft_revisions",
    )
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.SYSTEM)
    action_type = models.CharField(max_length=64)
    summary = models.CharField(max_length=255, blank=True)
    draft_snapshot_json = models.JSONField(default=dict, blank=True)
    ai_request_payload = models.JSONField(default=dict, blank=True)
    ai_response_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-revision_number")
        constraints = [
            models.UniqueConstraint(fields=["draft", "revision_number"], name="unique_program_draft_revision_number"),
        ]

    def __str__(self):
        return f"Revision {self.revision_number} for draft #{self.draft_id}"


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
