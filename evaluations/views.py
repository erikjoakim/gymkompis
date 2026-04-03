from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from training.models import WorkoutSession

from .forms import PeriodEvaluationForm
from .models import WorkoutEvaluation
from .services import evaluate_period_for_user, evaluate_session_for_user


@login_required
def request_session_evaluation_view(request, session_id):
    session = get_object_or_404(WorkoutSession, pk=session_id, user=request.user)
    try:
        evaluation, _token_usage = evaluate_session_for_user(request.user, session)
    except Exception as exc:
        messages.error(request, f"Evaluation failed: {exc}")
        return redirect("workout_detail", session_id=session.id)
    messages.success(request, "Workout evaluation created.")
    return redirect("evaluation_detail", evaluation_id=evaluation.id)


@login_required
def request_period_evaluation_view(request):
    form = PeriodEvaluationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        sessions = list(
            WorkoutSession.objects.filter(
                user=request.user,
                workout_date__gte=form.cleaned_data["start_date"],
                workout_date__lte=form.cleaned_data["end_date"],
                status=WorkoutSession.Status.COMPLETED,
            ).order_by("workout_date", "completed_at")
        )
        if not sessions:
            messages.error(request, "No completed sessions were found in that range.")
        else:
            try:
                evaluation, _token_usage = evaluate_period_for_user(
                    request.user,
                    sessions,
                    form.cleaned_data["start_date"],
                    form.cleaned_data["end_date"],
                )
            except Exception as exc:
                messages.error(request, f"Period evaluation failed: {exc}")
            else:
                messages.success(request, "Period evaluation created.")
                return redirect("evaluation_detail", evaluation_id=evaluation.id)

    recent_evaluations = WorkoutEvaluation.objects.filter(user=request.user)[:10]
    return render(
        request,
        "evaluations/request_period_evaluation.html",
        {"form": form, "recent_evaluations": recent_evaluations},
    )


@login_required
def evaluation_detail_view(request, evaluation_id):
    evaluation = get_object_or_404(WorkoutEvaluation, pk=evaluation_id, user=request.user)
    return render(request, "evaluations/evaluation_detail.html", {"evaluation": evaluation})
