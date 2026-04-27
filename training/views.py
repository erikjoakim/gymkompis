from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from evaluations.models import WorkoutEvaluation

from .forms import ExerciseSubmissionForm
from .models import WorkoutSession
from .progression import build_progression_recommendations
from .services import (
    complete_session,
    get_active_program,
    get_or_create_session,
    get_program_day,
    get_program_days,
    session_display_exercise,
    submit_exercise_set,
    swap_session_exercise,
    sync_session_display_fields,
)
from .substitutions import suggest_substitutions


def _session_exercise_map(session: WorkoutSession):
    return {
        exercise["exercise_key"]: exercise
        for exercise in session.session_json.get("exercises", [])
    }


def _exercise_status_label(session_exercise: dict) -> str:
    if session_exercise.get("status") == "completed":
        return "Done"
    if session_exercise.get("actual_sets"):
        return "In progress"
    return "Not done"


def _is_completed_exercise(session: WorkoutSession, exercise_key: str) -> bool:
    session_exercise = _session_exercise_map(session).get(exercise_key)
    return bool(session_exercise and session_exercise.get("status") == "completed")


def _build_pending_exercise_forms(
    session: WorkoutSession,
    user,
    active_exercise_key=None,
    bound_form=None,
    bound_exercise_key=None,
):
    session_exercises = _session_exercise_map(session)
    session_exercise_rows = session.session_json.get("exercises", [])
    display_exercises = [session_display_exercise(exercise) for exercise in session_exercise_rows]
    active_keys = {exercise.get("exercise_key") for exercise in session.session_json.get("exercises", []) if exercise.get("exercise_key")}
    valid_keys = {exercise.get("exercise_key") for exercise in display_exercises if exercise.get("exercise_key")}
    if bound_exercise_key:
        active_exercise_key = bound_exercise_key
    if active_exercise_key not in valid_keys:
        active_exercise_key = next(
            (
                exercise.get("exercise_key")
                for exercise in session_exercise_rows
                if exercise.get("status", "pending") != "completed"
            ),
            None,
        )
    progression_map = build_progression_recommendations(
        user,
        display_exercises,
        weight_unit=session.session_json.get("weight_unit", "kg"),
        current_session_id=session.id,
    )
    exercise_forms = []
    completed_exercises = []
    for exercise in display_exercises:
        session_exercise = session_exercises.get(exercise["exercise_key"], {})
        is_current = exercise["exercise_key"] == active_exercise_key
        progression = progression_map.get(exercise["exercise_key"], {})
        substitutions = []
        form = None
        if is_current:
            substitutions = suggest_substitutions(
                user,
                exercise,
                excluded_keys=active_keys - {exercise.get("exercise_key")},
            )
            form = (
                bound_form
                if bound_exercise_key == exercise["exercise_key"]
                else ExerciseSubmissionForm(
                    exercise=exercise,
                    progression=progression,
                    saved_actual_sets=session_exercise.get("actual_sets", []),
                    initial_exercise_notes=session_exercise.get("exercise_notes", ""),
                )
            )
            if bound_exercise_key == exercise["exercise_key"] and hasattr(form, "progression"):
                form.progression = progression
        exercise_forms.append(
            {
                "exercise": exercise,
                "form": form,
                "progression": progression,
                "exercise_group": exercise.get("exercise_group", "main"),
                "group_label": "Warmup" if exercise.get("exercise_group") == "warmup" else "Main Work",
                "substitutions": substitutions,
                "is_current": is_current,
                "status": session_exercise.get("status", "pending"),
                "status_label": _exercise_status_label(session_exercise),
                "is_started": bool(session_exercise.get("actual_sets")),
                "is_substituted": exercise.get("is_substituted"),
                "original_name": exercise.get("original_name"),
            }
        )

    for exercise in session_exercise_rows:
        if exercise.get("status") != "completed":
            continue
        display_exercise = session_display_exercise(exercise)
        completed_exercises.append(
            {
                "exercise": display_exercise,
                "exercise_group": display_exercise.get("exercise_group", "main"),
                "group_label": "Warmup" if display_exercise.get("exercise_group") == "warmup" else "Main Work",
                "completed_label": "Done",
                "is_substituted": display_exercise.get("is_substituted"),
                "original_name": display_exercise.get("original_name"),
            }
        )
    return {
        "pending": exercise_forms,
        "completed": completed_exercises,
    }


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
    exercise_state = {"pending": [], "completed": []}
    if day.get("type") == "training":
        session = get_or_create_session(request.user, program, day)
        session = sync_session_display_fields(session, day)
        exercise_state = _build_pending_exercise_forms(
            session,
            request.user,
            active_exercise_key=request.GET.get("exercise"),
        )

    return render(
        request,
        "training/train_day.html",
        {
            "program": program,
            "day": day,
            "session": session,
            "exercise_forms": exercise_state["pending"],
            "completed_exercises": exercise_state["completed"],
            "training_scroll_target": "exercise",
        },
    )


@login_required
def submit_exercise_view(request, session_id, exercise_key):
    session = get_object_or_404(WorkoutSession, pk=session_id, user=request.user)
    session_exercise = _session_exercise_map(session).get(exercise_key)
    if session_exercise is None:
        messages.error(request, "Exercise not found.")
        return redirect("train_day", day_key=session.planned_day_key)
    exercise = session_display_exercise(session_exercise)

    progression = build_progression_recommendations(
        request.user,
        [exercise],
        weight_unit=session.session_json.get("weight_unit", "kg"),
        current_session_id=session.id,
    ).get(exercise_key, {})
    target_set_number = request.POST.get("save_set_number")
    form = ExerciseSubmissionForm(
        request.POST or None,
        exercise=exercise,
        progression=progression,
        saved_actual_sets=session_exercise.get("actual_sets", []),
        target_set_number=target_set_number,
        initial_exercise_notes=session_exercise.get("exercise_notes", ""),
    )
    if request.method == "POST" and form.is_valid():
        session = submit_exercise_set(
            session_id=session.id,
            user=request.user,
            exercise_key=exercise_key,
            actual_set=form.actual_set_for_target(),
            exercise_notes=form.cleaned_data["exercise_notes"],
        )
        if getattr(request, "htmx", False):
            active_exercise_key = None if _is_completed_exercise(session, exercise_key) else exercise_key
            training_scroll_target = "exercise" if active_exercise_key is None else "set"
            exercise_state = _build_pending_exercise_forms(
                session,
                request.user,
                active_exercise_key=active_exercise_key,
            )
            return render(
                request,
                "training/partials/exercise_list.html",
                {
                    "exercise_forms": exercise_state["pending"],
                    "completed_exercises": exercise_state["completed"],
                    "session": session,
                    "training_scroll_target": training_scroll_target,
                },
            )
        messages.success(request, f"{exercise['name']} set {target_set_number} saved.")
    else:
        if getattr(request, "htmx", False):
            form.progression = progression
            exercise_state = _build_pending_exercise_forms(
                session,
                request.user,
                bound_form=form,
                bound_exercise_key=exercise_key,
            )
            return render(
                request,
                "training/partials/exercise_list.html",
                {
                    "exercise_forms": exercise_state["pending"],
                    "completed_exercises": exercise_state["completed"],
                    "session": session,
                    "training_scroll_target": "set",
                },
                status=400,
            )
        messages.error(request, "Please correct the exercise form.")

    return redirect("train_day", day_key=session.planned_day_key)


@login_required
def swap_exercise_view(request, session_id, exercise_key):
    session = get_object_or_404(WorkoutSession, pk=session_id, user=request.user)
    replacement_external_id = request.POST.get("replacement_external_id", "")
    try:
        session = swap_session_exercise(
            session_id=session.id,
            user=request.user,
            current_exercise_key=exercise_key,
            replacement_external_id=replacement_external_id,
        )
        messages.success(request, "Exercise swapped for today's workout.")
    except ValueError as exc:
        messages.error(request, str(exc))

    if getattr(request, "htmx", False):
        exercise_state = _build_pending_exercise_forms(session, request.user)
        return render(
            request,
            "training/partials/exercise_list.html",
            {
                "exercise_forms": exercise_state["pending"],
                "completed_exercises": exercise_state["completed"],
                "session": session,
                "training_scroll_target": "exercise",
            },
        )
    return redirect("train_day", day_key=session.planned_day_key)


@login_required
def complete_session_view(request, session_id):
    session = get_object_or_404(WorkoutSession, pk=session_id, user=request.user)
    if request.method == "POST":
        complete_session(
            session.id,
            request.user,
            request.POST.get("session_notes", ""),
            request.POST.get("overall_effort_rpe"),
        )
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
    existing_evaluation = (
        WorkoutEvaluation.objects.filter(
            user=request.user,
            evaluation_type=WorkoutEvaluation.EvaluationType.SESSION,
            workout_session=session,
        )
        .order_by("-created_at")
        .first()
    )
    return render(
        request,
        "training/workout_detail.html",
        {
            "session": session,
            "session_json": session.session_json,
            "existing_evaluation": existing_evaluation,
        },
    )
