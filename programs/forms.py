from django import forms

from .models import DAY_KEY_CHOICES, DAY_TYPE_CHOICES, Exercise, ProgramDraft, ProgramDraftDay, ProgramDraftExercise


class ProgramGenerateForm(forms.Form):
    prompt_text = forms.CharField(
        label="Describe the program you want",
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": (
                    "Example: I want a 4-day program to build strength and muscle, "
                    "avoid heavy overhead pressing, and keep sessions under 60 minutes."
                ),
            }
        ),
    )


class ManualProgramDraftForm(forms.ModelForm):
    selected_days = forms.MultipleChoiceField(
        choices=DAY_KEY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Days in this plan",
    )

    class Meta:
        model = ProgramDraft
        fields = ("name", "goal_summary", "duration_weeks", "weight_unit", "program_notes", "selected_days")
        widgets = {
            "goal_summary": forms.Textarea(attrs={"rows": 3}),
            "program_notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(["name", "goal_summary", "duration_weeks", "weight_unit", "selected_days", "program_notes"])
        if self.instance and self.instance.pk and not self.is_bound:
            self.fields["selected_days"].initial = list(
                self.instance.days.order_by("day_key").values_list("day_key", flat=True)
            )

    def clean_selected_days(self):
        selected_days = self.cleaned_data.get("selected_days") or []
        if not selected_days:
            raise forms.ValidationError("Select at least one day for this plan.")
        return selected_days


class ManualProgramDayForm(forms.ModelForm):
    class Meta:
        model = ProgramDraftDay
        fields = ("day_key", "name", "day_type", "notes")
        widgets = {
            "day_key": forms.Select(choices=DAY_KEY_CHOICES),
            "day_type": forms.Select(choices=DAY_TYPE_CHOICES),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class ManualDayCopyForm(forms.Form):
    target_day_ids = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Copy this setup to",
    )

    def __init__(self, *args, available_days=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_day_ids"].choices = [
            (str(day.id), f"{day.day_label} - {day.name}") for day in (available_days or [])
        ]

    def clean_target_day_ids(self):
        target_day_ids = self.cleaned_data.get("target_day_ids") or []
        if not target_day_ids:
            raise forms.ValidationError("Select at least one day to copy to.")
        return target_day_ids


class ExerciseLibraryFilterForm(forms.Form):
    query = forms.CharField(required=False, label="Search")
    modality = forms.ChoiceField(required=False)
    brand = forms.ChoiceField(required=False)
    library_role = forms.ChoiceField(
        required=False,
        choices=[("", "Any role"), *Exercise.LibraryRole.choices],
        label="Role",
    )
    supports_time = forms.BooleanField(required=False, label="Time-based")

    def __init__(self, *args, modality_choices=None, brand_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["modality"].choices = [("", "Any modality"), *(modality_choices or [])]
        self.fields["brand"].choices = [("", "Any brand"), *(brand_choices or [])]


class LibraryImportAdminForm(forms.Form):
    overwrite = forms.BooleanField(required=False, initial=True, label="Overwrite existing records")
    ai_instructions = forms.BooleanField(required=False, label="Use AI draft instructions during import")


class LibraryEnrichAdminForm(forms.Form):
    limit = forms.IntegerField(min_value=1, max_value=1000, initial=50, label="Records to process")
    overwrite = forms.BooleanField(required=False, label="Recompute existing values too")
    use_ai = forms.BooleanField(required=False, label="Use AI fallback for unresolved metadata")


class LibraryAdminFilterForm(forms.Form):
    query = forms.CharField(required=False, label="Search")
    brand = forms.ChoiceField(required=False, label="Brand")
    only_incomplete = forms.BooleanField(required=False, initial=True, label="Only incomplete or pending review")
    limit = forms.IntegerField(min_value=1, max_value=200, initial=25, label="Rows")

    def __init__(self, *args, brand_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].choices = [("", "Any brand"), *(brand_choices or [])]


class LibraryExerciseReviewForm(forms.Form):
    brand = forms.CharField(required=False)
    line = forms.CharField(required=False)
    modality = forms.ChoiceField(choices=Exercise.Modality.choices)
    equipment = forms.CharField(required=False)
    category = forms.CharField(required=False)
    movement_pattern = forms.CharField(required=False, label="Movement")
    primary_muscles = forms.CharField(required=False, help_text="Comma-separated")
    secondary_muscles = forms.CharField(required=False, help_text="Comma-separated")
    stabilizers = forms.CharField(required=False, help_text="Comma-separated")
    supports_reps = forms.BooleanField(required=False)
    supports_time = forms.BooleanField(required=False)
    is_static = forms.BooleanField(required=False)
    instructions = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))

    @staticmethod
    def list_to_text(values):
        if isinstance(values, (list, tuple)):
            return ", ".join(str(item) for item in values if item)
        return values or ""

    @classmethod
    def initial_from_exercise(cls, exercise, suggested_values=None):
        suggested_values = suggested_values or {}
        return {
            "brand": suggested_values.get("brand", exercise.brand),
            "line": suggested_values.get("line", exercise.line),
            "modality": suggested_values.get("modality", exercise.modality),
            "equipment": suggested_values.get("equipment", exercise.equipment),
            "category": suggested_values.get("category", exercise.category),
            "movement_pattern": suggested_values.get("movement_pattern", exercise.movement_pattern),
            "primary_muscles": cls.list_to_text(suggested_values.get("primary_muscles", exercise.primary_muscles)),
            "secondary_muscles": cls.list_to_text(suggested_values.get("secondary_muscles", exercise.secondary_muscles)),
            "stabilizers": cls.list_to_text(suggested_values.get("stabilizers", exercise.stabilizers)),
            "supports_reps": suggested_values.get("supports_reps", exercise.supports_reps),
            "supports_time": suggested_values.get("supports_time", exercise.supports_time),
            "is_static": suggested_values.get("is_static", exercise.is_static),
            "instructions": suggested_values.get("instructions", exercise.instructions),
        }

    @staticmethod
    def parse_text_list(value):
        return [item.strip() for item in (value or "").split(",") if item.strip()]


class ExerciseImagePromptForm(forms.Form):
    exercise_id = forms.IntegerField(widget=forms.HiddenInput())
    prompt = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 8}),
        label="Image prompt",
    )


class ExerciseImageCopyForm(forms.Form):
    source_exercise_id = forms.IntegerField(widget=forms.HiddenInput())
    target_exercise_ids = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Copy this saved image to",
    )

    def __init__(self, *args, available_exercises=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_exercise_ids"].choices = [
            (
                str(exercise.id),
                " / ".join(
                    part
                    for part in [
                        exercise.name,
                        exercise.brand,
                        exercise.line,
                        exercise.external_id,
                    ]
                    if part
                ),
            )
            for exercise in (available_exercises or [])
        ]

    def clean_target_exercise_ids(self):
        target_exercise_ids = self.cleaned_data.get("target_exercise_ids") or []
        if not target_exercise_ids:
            raise forms.ValidationError("Select at least one exercise to copy the image to.")
        return target_exercise_ids


class UserExerciseSubmissionForm(forms.Form):
    name = forms.CharField()
    aliases = forms.CharField(required=False, help_text="Comma-separated alternate names")
    brand = forms.CharField(required=False)
    line = forms.CharField(required=False)
    modality = forms.ChoiceField(choices=Exercise.Modality.choices)
    library_role = forms.ChoiceField(choices=Exercise.LibraryRole.choices)
    equipment = forms.CharField(required=False)
    category = forms.CharField(required=False)
    movement_pattern = forms.CharField(required=False, label="Movement")
    primary_muscles = forms.CharField(required=False, help_text="Comma-separated")
    secondary_muscles = forms.CharField(required=False, help_text="Comma-separated")
    stabilizers = forms.CharField(required=False, help_text="Comma-separated")
    supports_reps = forms.BooleanField(required=False, initial=True)
    supports_time = forms.BooleanField(required=False)
    is_static = forms.BooleanField(required=False)
    instructions = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))
    submission_query = forms.CharField(widget=forms.HiddenInput(), required=False)
    source_kind = forms.CharField(widget=forms.HiddenInput(), required=False)

    @classmethod
    def initial_from_suggestion(cls, suggestion, *, submission_query="", source_kind=Exercise.SourceKind.AI_SUGGESTED):
        return {
            "name": suggestion.get("name", ""),
            "aliases": LibraryExerciseReviewForm.list_to_text(suggestion.get("aliases", [])),
            "brand": suggestion.get("brand", ""),
            "line": suggestion.get("line", ""),
            "modality": suggestion.get("modality", Exercise.Modality.OTHER),
            "library_role": suggestion.get("library_role", Exercise.LibraryRole.MAIN),
            "equipment": suggestion.get("equipment", ""),
            "category": suggestion.get("category", ""),
            "movement_pattern": suggestion.get("movement_pattern", ""),
            "primary_muscles": LibraryExerciseReviewForm.list_to_text(suggestion.get("primary_muscles", [])),
            "secondary_muscles": LibraryExerciseReviewForm.list_to_text(suggestion.get("secondary_muscles", [])),
            "stabilizers": LibraryExerciseReviewForm.list_to_text(suggestion.get("stabilizers", [])),
            "supports_reps": suggestion.get("supports_reps", True),
            "supports_time": suggestion.get("supports_time", False),
            "is_static": suggestion.get("is_static", False),
            "instructions": suggestion.get("instructions", ""),
            "submission_query": submission_query,
            "source_kind": source_kind,
        }


class AddExerciseToDayForm(forms.Form):
    exercise_id = forms.IntegerField(widget=forms.HiddenInput())
    block_type = forms.ChoiceField(choices=ProgramDraftExercise.BlockType.choices, widget=forms.HiddenInput())


class ManualExerciseConfigForm(forms.ModelForm):
    class Meta:
        model = ProgramDraftExercise
        fields = (
            "block_type",
            "order",
            "prescription_type",
            "sets_count",
            "target_reps",
            "target_seconds",
            "load_guidance",
            "target_effort_rpe",
            "rest_seconds_override",
            "notes",
        )
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_reps"].required = False
        self.fields["target_seconds"].required = False
        self.fields["load_guidance"].required = False
        self.fields["target_effort_rpe"].required = False
        self.fields["rest_seconds_override"].required = False
        self.fields["notes"].required = False

    def clean(self):
        cleaned_data = super().clean()
        prescription_type = cleaned_data.get("prescription_type")
        target_reps = cleaned_data.get("target_reps")
        target_seconds = cleaned_data.get("target_seconds")
        exercise = getattr(self.instance, "exercise", None)
        if prescription_type == ProgramDraftExercise.PrescriptionType.REPS and not target_reps:
            self.add_error("target_reps", "Enter a rep target for rep-based work.")
        if prescription_type == ProgramDraftExercise.PrescriptionType.TIME and not target_seconds:
            self.add_error("target_seconds", "Enter a time target in seconds for time-based work.")
        if exercise and prescription_type == ProgramDraftExercise.PrescriptionType.REPS and not exercise.supports_reps:
            self.add_error("prescription_type", "This exercise is configured for time-based work.")
        if exercise and prescription_type == ProgramDraftExercise.PrescriptionType.TIME and not exercise.supports_time:
            self.add_error("prescription_type", "This exercise is configured for rep-based work.")
        return cleaned_data
