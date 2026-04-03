from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ExerciseSubmissionForm
from .models import WorkoutSession
from .services import complete_session, get_active_program, get_or_create_session, get_program_day, get_program_days, submit_exercise


def _build_pending_exercise_forms(day: dict, session: WorkoutSession, bound_form=None, bound_exercise_key=None):
    status_by_key = {
        exercise["exercise_key"]: exercise.get("status", "pending")
        for exercise in session.session_json.get("exercises", [])
    }
    exercise_forms = []
    for exercise in day.get("exercises", []):
        if status_by_key.get(exercise["exercise_key"]) == "completed":
            continue
        form = bound_form if bound_exercise_key == exercise["exercise_key"] else ExerciseSubmissionForm(exercise=exercise)
        exercise_forms.append({"exercise": exercise, "form": form})
    return exercise_forms


@login_required
def train_index_view(request):
    program = get_active_program(request.user)
    return render(
        request,
        "training/train_index.html",
        {
            "program": program,
            "days": get_program_days(program),
        },
    )


@login_required
def train_day_view(request, day_key):
    program = get_active_program(request.user)
    if not program:
        messages.info(request, "Generate a program before starting training.")
        return redirect("generate_program")

    day = get_program_day(program, day_key)
    if not day:
        messages.error(request, "That day does not exist in the active program.")
        return redirect("train_index")

    session = None
    exercise_forms = []
    if day.get("type") == "training":
        session = get_or_create_session(request.user, program, day)
        exercise_forms = _build_pending_exercise_forms(day, session)

    return render(
        request,
        "training/train_day.html",
        {
            "program": program,
            "day": day,
            "session": session,
            "exercise_forms": exercise_forms,
        },
    )


@login_required
def submit_exercise_view(request, session_id, exercise_key):
    session = get_object_or_404(WorkoutSession, pk=session_id, user=request.user)
    day = get_program_day(session.program, session.planned_day_key)
    exercise = next((item for item in day.get("exercises", []) if item.get("exercise_key") == exercise_key), None)
    if exercise is None:
        messages.error(request, "Exercise not found.")
        return redirect("train_day", day_key=session.planned_day_key)

    form = ExerciseSubmissionForm(request.POST or None, exercise=exercise)
    if request.method == "POST" and form.is_valid():
        session = submit_exercise(
            session_id=session.id,
            user=request.user,
            exercise_key=exercise_key,
            actual_sets=form.actual_sets(),
            exercise_notes=form.cleaned_data["exercise_notes"],
        )
        if getattr(request, "htmx", False):
            exercise_forms = _build_pending_exercise_forms(day, session)
            return render(
                request,
                "training/partials/exercise_list.html",
                {
                    "exercise_forms": exercise_forms,
                    "session": session,
                },
            )
        messages.success(request, f"{exercise['name']} saved.")
    else:
        if getattr(request, "htmx", False):
            exercise_forms = _build_pending_exercise_forms(day, session, bound_form=form, bound_exercise_key=exercise_key)
            return render(
                request,
                "training/partials/exercise_list.html",
                {
                    "exercise_forms": exercise_forms,
                    "session": session,
                },
                status=400,
            )
        messages.error(request, "Please correct the exercise form.")

    return redirect("train_day", day_key=session.planned_day_key)


@login_required
def complete_session_view(request, session_id):
    session = get_object_or_404(WorkoutSession, pk=session_id, user=request.user)
    if request.method == "POST":
        complete_session(session.id, request.user, request.POST.get("session_notes", ""))
        messages.success(request, "Workout session completed.")
        return redirect("workout_detail", session_id=session.id)
    return redirect("train_day", day_key=session.planned_day_key)


@login_required
def workout_history_view(request):
    sessions = WorkoutSession.objects.filter(user=request.user).select_related("program")
    return render(request, "training/workout_history.html", {"sessions": sessions})


@login_required
def workout_detail_view(request, session_id):
    session = get_object_or_404(WorkoutSession.objects.select_related("program"), pk=session_id, user=request.user)
    return render(request, "training/workout_detail.html", {"session": session, "session_json": session.session_json})
