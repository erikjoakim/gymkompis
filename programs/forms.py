from django import forms


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
