from copy import deepcopy
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from programs.models import TrainingProgram

from .models import WorkoutSession


def get_active_program(user):
    return (
        TrainingProgram.objects.filter(user=user, status=TrainingProgram.Status.ACTIVE)
        .order_by("-created_at")
        .first()
    )


def get_program_days(program: TrainingProgram | None) -> list[dict]:
    if not program:
        return []
    return program.current_program.get("days", [])


def get_program_day(program: TrainingProgram, day_key: str) -> dict | None:
    for day in get_program_days(program):
        if day.get("day_key") == day_key:
            return day
    return None


def user_local_date(user):
    return timezone.now().astimezone(ZoneInfo(user.profile.timezone)).date()


def build_session_json(program: TrainingProgram, day: dict, workout_date):
    exercises = []
    for exercise in day.get("exercises", []):
        exercises.append(
            {
                "exercise_key": exercise["exercise_key"],
                "name": exercise["name"],
                "order": exercise["order"],
                "modality": exercise["modality"],
                "status": "pending",
                "planned": {"set_plan": deepcopy(exercise["set_plan"])},
                "actual_sets": [],
                "exercise_notes": "",
                "submitted_at": None,
            }
        )
    return {
        "version": 1,
        "program_id": program.id,
        "program_version": program.version_number,
        "workout_date": workout_date.isoformat(),
        "planned_day_key": day["day_key"],
        "planned_day_label": day["day_label"],
        "planned_day_name": day["name"],
        "weight_unit": program.current_program.get("weight_unit", "kg"),
        "status": "in_progress",
        "started_at": timezone.now().isoformat(),
        "completed_at": None,
        "session_notes": "",
        "evaluation_status": "not_requested",
        "exercises": exercises,
    }


def get_or_create_session(user, program: TrainingProgram, day: dict) -> WorkoutSession:
    workout_date = user_local_date(user)
    defaults = {
        "planned_day_label": day["day_label"],
        "planned_day_name": day["name"],
        "session_json": build_session_json(program, day, workout_date),
        "started_at": timezone.now(),
    }
    session, _created = WorkoutSession.objects.get_or_create(
        user=user,
        program=program,
        workout_date=workout_date,
        planned_day_key=day["day_key"],
        defaults=defaults,
    )
    return session


@transaction.atomic
def submit_exercise(session_id: int, user, exercise_key: str, actual_sets: list[dict], exercise_notes: str):
    session = WorkoutSession.objects.select_for_update().select_related("program", "user").get(
        pk=session_id,
        user=user,
    )
    data = deepcopy(session.session_json)
    for exercise in data.get("exercises", []):
        if exercise.get("exercise_key") != exercise_key:
            continue
        exercise["actual_sets"] = actual_sets
        exercise["exercise_notes"] = exercise_notes
        exercise["status"] = "completed"
        exercise["submitted_at"] = timezone.now().isoformat()
        break
    else:
        raise ValueError("Exercise not found in workout session.")

    session.session_json = data
    session.submission_version += 1
    session.last_exercise_submission_at = timezone.now()
    session.save(update_fields=["session_json", "submission_version", "last_exercise_submission_at", "updated_at"])
    return session


@transaction.atomic
def complete_session(session_id: int, user, session_notes: str = ""):
    session = WorkoutSession.objects.select_for_update().get(pk=session_id, user=user)
    data = deepcopy(session.session_json)
    data["status"] = "completed"
    data["session_notes"] = session_notes
    data["completed_at"] = timezone.now().isoformat()
    session.status = WorkoutSession.Status.COMPLETED
    session.completed_at = timezone.now()
    session.session_json = data
    session.save(update_fields=["status", "completed_at", "session_json", "updated_at"])
    return session
