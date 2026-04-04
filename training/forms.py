from decimal import Decimal

from django import forms

from programs.structure import infer_prescription_type


class ExerciseSubmissionForm(forms.Form):
    exercise_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Optional notes about pain, ease, or technique"}),
    )

    def __init__(self, *args, exercise: dict, progression: dict | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.exercise = exercise
        self.progression = progression or {}
        suggested_weight = self.progression.get("suggested_weight")
        for planned_set in exercise.get("set_plan", []):
            set_number = planned_set["set_number"]
            prescription_type = infer_prescription_type(planned_set)
            if prescription_type == "time":
                self.fields[f"set_{set_number}_seconds"] = forms.IntegerField(required=False, min_value=0)
            else:
                self.fields[f"set_{set_number}_reps"] = forms.IntegerField(required=False, min_value=0)
                self.fields[f"set_{set_number}_weight"] = forms.DecimalField(
                    required=False,
                    min_value=Decimal("0"),
                    decimal_places=2,
                    max_digits=8,
                    initial=suggested_weight,
                )
            self.fields[f"set_{set_number}_rpe"] = forms.FloatField(required=False, min_value=1, max_value=10)
            self.fields[f"set_{set_number}_notes"] = forms.CharField(required=False)

    def actual_sets(self):
        rows = []
        for planned_set in self.exercise.get("set_plan", []):
            set_number = planned_set["set_number"]
            prescription_type = infer_prescription_type(planned_set)
            reps = self.cleaned_data.get(f"set_{set_number}_reps") if prescription_type == "reps" else None
            seconds = self.cleaned_data.get(f"set_{set_number}_seconds") if prescription_type == "time" else None
            completed = reps is not None if prescription_type == "reps" else seconds is not None
            weight = self.cleaned_data.get(f"set_{set_number}_weight") if completed and prescription_type == "reps" else None
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

    @property
    def set_rows(self):
        rows = []
        for planned_set in self.exercise.get("set_plan", []):
            set_number = planned_set["set_number"]
            prescription_type = infer_prescription_type(planned_set)
            guidance_display = self.progression.get("guidance_text") or planned_set.get("load_guidance") or "-"
            rows.append(
                {
                    "planned": planned_set,
                    "actual_value": self[f"set_{set_number}_seconds"] if prescription_type == "time" else self[f"set_{set_number}_reps"],
                    "show_weight": prescription_type == "reps",
                    "weight": self[f"set_{set_number}_weight"] if prescription_type == "reps" else None,
                    "rpe": self[f"set_{set_number}_rpe"],
                    "notes": self[f"set_{set_number}_notes"],
                    "guidance_display": guidance_display,
                }
            )
        return rows
