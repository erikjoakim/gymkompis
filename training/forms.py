from decimal import Decimal

from django import forms

from programs.structure import infer_prescription_type


WEIGHT_INPUT_MODALITIES = {"barbell", "dumbbell", "machine", "cable", "kettlebell"}


def exercise_uses_weight_input(exercise: dict) -> bool:
    return (exercise.get("modality") or "").lower() in WEIGHT_INPUT_MODALITIES


class ExerciseSubmissionForm(forms.Form):
    exercise_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Optional notes about pain, ease, or technique"}),
    )

    def __init__(
        self,
        *args,
        exercise: dict,
        progression: dict | None = None,
        saved_actual_sets: list[dict] | None = None,
        target_set_number: int | None = None,
        initial_exercise_notes: str = "",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.exercise = exercise
        self.progression = progression or {}
        self.target_set_number = int(target_set_number) if target_set_number else None
        self.planned_sets = sorted(exercise.get("set_plan", []), key=lambda item: int(item["set_number"]))
        self.saved_actual_sets = {
            int(item["set_number"]): item
            for item in (saved_actual_sets or [])
            if item.get("set_number") is not None
        }
        suggested_weight = self.progression.get("suggested_weight")
        uses_weight_input = exercise_uses_weight_input(self.exercise)
        self.fields["exercise_notes"].initial = initial_exercise_notes
        self.current_set_number = self._determine_current_set_number()
        self.is_static_hold = bool(exercise.get("is_static"))
        for planned_set in self.planned_sets:
            set_number = planned_set["set_number"]
            prescription_type = infer_prescription_type(planned_set)
            saved_set = self.saved_actual_sets.get(set_number, {})
            if prescription_type == "time":
                self.fields[f"set_{set_number}_seconds"] = forms.IntegerField(
                    required=False,
                    min_value=0,
                    initial=saved_set.get("seconds"),
                )
            else:
                self.fields[f"set_{set_number}_reps"] = forms.IntegerField(
                    required=False,
                    min_value=0,
                    initial=saved_set.get("reps"),
                )
                if uses_weight_input:
                    self.fields[f"set_{set_number}_weight"] = forms.DecimalField(
                        required=False,
                        min_value=Decimal("0"),
                        decimal_places=2,
                        max_digits=8,
                        initial=self._weight_initial_for_set(
                            set_number,
                            saved_set=saved_set,
                            suggested_weight=suggested_weight,
                        ),
                    )
            self.fields[f"set_{set_number}_rpe"] = forms.FloatField(
                required=False,
                min_value=1,
                max_value=10,
                initial=saved_set.get("effort_rpe"),
            )
            self.fields[f"set_{set_number}_notes"] = forms.CharField(
                required=False,
                initial=saved_set.get("notes", ""),
            )
            self.fields[f"set_{set_number}_started_at"] = forms.CharField(
                required=False,
                initial=saved_set.get("started_at", ""),
                widget=forms.HiddenInput(),
            )
            self.fields[f"set_{set_number}_activated_at"] = forms.CharField(
                required=False,
                initial=saved_set.get("activated_at", ""),
                widget=forms.HiddenInput(),
            )
            self.fields[f"set_{set_number}_ended_at"] = forms.CharField(
                required=False,
                initial=saved_set.get("ended_at", ""),
                widget=forms.HiddenInput(),
            )
            self.fields[f"set_{set_number}_duration_seconds"] = forms.IntegerField(
                required=False,
                min_value=0,
                initial=saved_set.get("duration_seconds"),
                widget=forms.HiddenInput(),
            )

    def clean(self):
        cleaned_data = super().clean()
        if not self.target_set_number:
            return cleaned_data

        planned_set = next(
            (item for item in self.exercise.get("set_plan", []) if int(item["set_number"]) == self.target_set_number),
            None,
        )
        if not planned_set:
            raise forms.ValidationError("Selected set was not found.")

        prescription_type = infer_prescription_type(planned_set)
        if prescription_type == "time":
            value = cleaned_data.get(f"set_{self.target_set_number}_seconds")
            field_name = f"set_{self.target_set_number}_seconds"
            error_text = "Enter the actual time before saving this set."
        else:
            value = cleaned_data.get(f"set_{self.target_set_number}_reps")
            field_name = f"set_{self.target_set_number}_reps"
            error_text = "Enter the actual reps before saving this set."

        if value is None:
            self.add_error(field_name, error_text)
        return cleaned_data

    def _determine_current_set_number(self):
        for planned_set in self.planned_sets:
            saved_set = self.saved_actual_sets.get(int(planned_set["set_number"]), {})
            if not saved_set.get("completed"):
                return int(planned_set["set_number"])
        return int(self.planned_sets[-1]["set_number"]) if self.planned_sets else None

    def _last_completed_weight_before(self, set_number: int):
        for previous_set_number in range(int(set_number) - 1, 0, -1):
            saved_set = self.saved_actual_sets.get(previous_set_number, {})
            if saved_set.get("completed") and saved_set.get("weight") is not None:
                return saved_set.get("weight")
        return None

    def _weight_initial_for_set(self, set_number: int, *, saved_set: dict, suggested_weight):
        if saved_set.get("weight") is not None:
            return saved_set.get("weight")
        previous_weight = self._last_completed_weight_before(set_number)
        if previous_weight is not None:
            return previous_weight
        return suggested_weight

    def actual_sets(self):
        rows = []
        uses_weight_input = exercise_uses_weight_input(self.exercise)
        for planned_set in self.planned_sets:
            set_number = planned_set["set_number"]
            prescription_type = infer_prescription_type(planned_set)
            reps = self.cleaned_data.get(f"set_{set_number}_reps") if prescription_type == "reps" else None
            seconds = self.cleaned_data.get(f"set_{set_number}_seconds") if prescription_type == "time" else None
            completed = reps is not None if prescription_type == "reps" else seconds is not None
            weight = (
                self.cleaned_data.get(f"set_{set_number}_weight")
                if completed and prescription_type == "reps" and uses_weight_input
                else None
            )
            effort_rpe = self.cleaned_data.get(f"set_{set_number}_rpe") if completed else None
            rows.append(
                {
                    "set_number": set_number,
                    "prescription_type": prescription_type,
                    "completed": completed,
                    "reps": reps,
                    "seconds": seconds,
                    "weight": float(weight) if weight is not None else None,
                    "effort_rpe": effort_rpe,
                    "notes": self.cleaned_data.get(f"set_{set_number}_notes", ""),
                }
            )
        return rows

    def actual_set_for_target(self):
        if not self.target_set_number:
            raise ValueError("No set number selected for submission.")
        for row in self.actual_sets():
            if int(row["set_number"]) == self.target_set_number:
                row.update(self.set_timing_for_target())
                return row
        raise ValueError("Selected set was not found in form data.")

    @property
    def set_rows(self):
        rows = []
        uses_weight_input = exercise_uses_weight_input(self.exercise)
        for planned_set in self.planned_sets:
            set_number = planned_set["set_number"]
            prescription_type = infer_prescription_type(planned_set)
            guidance_display = (
                self.progression.get("short_guidance")
                or planned_set.get("load_guidance")
                or "-"
            )
            rows.append(
                {
                    "planned": planned_set,
                    "set_number": set_number,
                    "actual_value": self[f"set_{set_number}_seconds"] if prescription_type == "time" else self[f"set_{set_number}_reps"],
                    "show_weight": prescription_type == "reps" and uses_weight_input,
                    "weight": self[f"set_{set_number}_weight"] if prescription_type == "reps" and uses_weight_input else None,
                    "rpe": self[f"set_{set_number}_rpe"],
                    "notes": self[f"set_{set_number}_notes"],
                    "guidance_display": guidance_display,
                    "saved": set_number in self.saved_actual_sets and self.saved_actual_sets[set_number].get("completed"),
                    "saved_actual_set": self.saved_actual_sets.get(set_number),
                    "started_at": self[f"set_{set_number}_started_at"],
                    "activated_at": self[f"set_{set_number}_activated_at"],
                    "ended_at": self[f"set_{set_number}_ended_at"],
                    "duration_seconds": self[f"set_{set_number}_duration_seconds"],
                    "saved_duration_seconds": self.saved_actual_sets.get(set_number, {}).get("duration_seconds"),
                    "saved_rest_before_seconds": self.saved_actual_sets.get(set_number, {}).get("rest_before_seconds"),
                    "is_current": self.current_set_number == set_number,
                }
            )
        return rows

    @property
    def period_count(self):
        return len(self.planned_sets)

    @property
    def static_hold_seconds(self):
        first_time_set = next(
            (
                item
                for item in self.planned_sets
                if infer_prescription_type(item) == "time" and item.get("target_seconds") is not None
            ),
            None,
        )
        return first_time_set.get("target_seconds") if first_time_set else None

    @property
    def static_rest_seconds(self):
        return self.exercise.get("rest_seconds", 0)

    @property
    def current_set_row(self):
        return next((row for row in self.set_rows if row["is_current"]), None)

    @property
    def completed_set_rows(self):
        return [row for row in self.set_rows if row["saved"]]

    def set_timing_for_target(self):
        if not self.target_set_number:
            raise ValueError("No set number selected for submission.")
        return {
            "started_at": self.cleaned_data.get(f"set_{self.target_set_number}_started_at") or "",
            "activated_at": self.cleaned_data.get(f"set_{self.target_set_number}_activated_at") or "",
            "ended_at": self.cleaned_data.get(f"set_{self.target_set_number}_ended_at") or "",
            "duration_seconds": self.cleaned_data.get(f"set_{self.target_set_number}_duration_seconds"),
        }
