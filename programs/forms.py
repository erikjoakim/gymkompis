from django import forms

from .models import DAY_KEY_CHOICES, DAY_TYPE_CHOICES, Exercise, ManualProgramDay, ManualProgramDraft, ManualProgramExercise


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
    class Meta:
        model = ManualProgramDraft
        fields = ("name", "goal_summary", "duration_weeks", "weight_unit", "program_notes")
        widgets = {
            "goal_summary": forms.Textarea(attrs={"rows": 3}),
            "program_notes": forms.Textarea(attrs={"rows": 4}),
        }


class ManualProgramDayForm(forms.ModelForm):
    class Meta:
        model = ManualProgramDay
        fields = ("day_key", "name", "day_type", "notes")
        widgets = {
            "day_key": forms.Select(choices=DAY_KEY_CHOICES),
            "day_type": forms.Select(choices=DAY_TYPE_CHOICES),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class ExerciseLibraryFilterForm(forms.Form):
    query = forms.CharField(required=False, label="Search")
    modality = forms.ChoiceField(required=False)
    category = forms.ChoiceField(required=False)
    library_role = forms.ChoiceField(
        required=False,
        choices=[("", "Any role"), *Exercise.LibraryRole.choices],
        label="Role",
    )
    supports_time = forms.BooleanField(required=False, label="Time-based")

    def __init__(self, *args, modality_choices=None, category_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["modality"].choices = [("", "Any modality"), *(modality_choices or [])]
        self.fields["category"].choices = [("", "Any category"), *(category_choices or [])]


class AddExerciseToDayForm(forms.Form):
    exercise_id = forms.IntegerField(widget=forms.HiddenInput())
    block_type = forms.ChoiceField(choices=ManualProgramExercise.BlockType.choices, widget=forms.HiddenInput())


class ManualExerciseConfigForm(forms.ModelForm):
    class Meta:
        model = ManualProgramExercise
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
        if prescription_type == ManualProgramExercise.PrescriptionType.REPS and not target_reps:
            self.add_error("target_reps", "Enter a rep target for rep-based work.")
        if prescription_type == ManualProgramExercise.PrescriptionType.TIME and not target_seconds:
            self.add_error("target_seconds", "Enter a time target in seconds for time-based work.")
        if exercise and prescription_type == ManualProgramExercise.PrescriptionType.REPS and not exercise.supports_reps:
            self.add_error("prescription_type", "This exercise is configured for time-based work.")
        if exercise and prescription_type == ManualProgramExercise.PrescriptionType.TIME and not exercise.supports_time:
            self.add_error("prescription_type", "This exercise is configured for rep-based work.")
        return cleaned_data
