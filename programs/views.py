from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ProgramGenerateForm
from .models import TrainingProgram
from .services import generate_program_for_user


@login_required
def current_program_view(request):
    program = (
        TrainingProgram.objects.filter(user=request.user, status=TrainingProgram.Status.ACTIVE)
        .order_by("-created_at")
        .first()
    )
    return render(request, "programs/current_program.html", {"program": program})


@login_required
def generate_program_view(request):
    form = ProgramGenerateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            program = generate_program_for_user(request.user, form.cleaned_data["prompt_text"])
        except Exception as exc:
            messages.error(request, f"Program generation failed: {exc}")
        else:
            messages.success(request, "A new training program has been generated.")
            return redirect("program_detail", program_id=program.id)

    return render(request, "programs/generate_program.html", {"form": form})


@login_required
def program_detail_view(request, program_id):
    program = get_object_or_404(TrainingProgram, pk=program_id, user=request.user)
    return render(request, "programs/program_detail.html", {"program": program, "program_json": program.current_program})
