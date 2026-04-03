from decimal import Decimal

from django import forms


class ExerciseSubmissionForm(forms.Form):
    exercise_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Optional notes about pain, ease, or technique"}),
    )

    def __init__(self, *args, exercise: dict, **kwargs):
        super().__init__(*args, **kwargs)
        self.exercise = exercise
        for planned_set in exercise.get("set_plan", []):
            set_number = planned_set["set_number"]
            self.fields[f"set_{set_number}_reps"] = forms.IntegerField(required=False, min_value=0)
            self.fields[f"set_{set_number}_weight"] = forms.DecimalField(required=False, min_value=Decimal("0"), decimal_places=2, max_digits=8)
            self.fields[f"set_{set_number}_rpe"] = forms.FloatField(required=False, min_value=1, max_value=10)
            self.fields[f"set_{set_number}_notes"] = forms.CharField(required=False)

    def actual_sets(self):
        rows = []
        for planned_set in self.exercise.get("set_plan", []):
            set_number = planned_set["set_number"]
            reps = self.cleaned_data.get(f"set_{set_number}_reps")
            completed = reps is not None
            weight = self.cleaned_data.get(f"set_{set_number}_weight") if completed else None
            effort_rpe = self.cleaned_data.get(f"set_{set_number}_rpe") if completed else None
            rows.append(
                {
                    "set_number": set_number,
                    "completed": completed,
                    "reps": reps,
                    "weight": float(weight) if weight is not None else None,
                    "effort_rpe": effort_rpe,
                    "notes": self.cleaned_data.get(f"set_{set_number}_notes", ""),
                }
            )
        return rows

    @property
    def set_rows(self):
        rows = []
        for planned_set in self.exercise.get("set_plan", []):
            set_number = planned_set["set_number"]
            rows.append(
                {
                    "planned": planned_set,
                    "reps": self[f"set_{set_number}_reps"],
                    "weight": self[f"set_{set_number}_weight"],
                    "rpe": self[f"set_{set_number}_rpe"],
                    "notes": self[f"set_{set_number}_notes"],
                }
            )
        return rows
