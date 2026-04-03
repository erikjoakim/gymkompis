from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from programs.models import TrainingProgram
from training.models import WorkoutSession
from evaluations.models import WorkoutEvaluation


def home_view(request):
    if request.user.is_authenticated:
        return dashboard_view(request)
    return render(request, "core/home.html")


@login_required
def dashboard_view(request):
    active_program = (
        TrainingProgram.objects.filter(user=request.user, status=TrainingProgram.Status.ACTIVE)
        .order_by("-created_at")
        .first()
    )
    recent_sessions = WorkoutSession.objects.filter(user=request.user).order_by("-workout_date", "-updated_at")[:5]
    recent_evaluations = WorkoutEvaluation.objects.filter(user=request.user).order_by("-created_at")[:5]
    context = {
        "active_program": active_program,
        "recent_sessions": recent_sessions,
        "recent_evaluations": recent_evaluations,
    }
    return render(request, "core/dashboard.html", context)


def healthcheck_view(request):
    return JsonResponse({"ok": True})
