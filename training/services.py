from copy import deepcopy
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from programs.models import Exercise
from programs.models import TrainingProgram
from programs.library import resolve_canonical_exercise
from programs.structure import get_day_all_exercises

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


def _lookup_library_exercise(exercise_key: str | None, name: str | None):
    if exercise_key:
        exercise = Exercise.objects.filter(external_id__iexact=exercise_key.replace("_", "-"), is_active=True).first()
        if exercise:
            return resolve_canonical_exercise(exercise)
    if name:
        exercise = Exercise.objects.filter(name__iexact=name, is_active=True, canonical_exercise__isnull=True).first()
        return resolve_canonical_exercise(exercise)
    return None


def build_session_exercise_snapshot(exercise: dict) -> dict:
    return {
        "exercise_key": exercise["exercise_key"],
        "name": exercise["name"],
        "order": exercise["order"],
        "modality": exercise["modality"],
        "image_url": exercise.get("image_url"),
        "video_url": exercise.get("video_url"),
        "instructions": exercise.get("instructions", ""),
        "focus": exercise.get("focus", ""),
        "movement_pattern": exercise.get("movement_pattern", ""),
        "category": exercise.get("category", ""),
        "primary_muscles": exercise.get("primary_muscles", []),
        "exercise_group": exercise.get("exercise_group", "main"),
        "status": "pending",
        "planned": {"set_plan": deepcopy(exercise["set_plan"])},
        "actual_sets": [],
        "exercise_notes": "",
        "submitted_at": None,
        "is_substituted": False,
        "original_exercise_key": exercise["exercise_key"],
        "original_name": exercise["name"],
        "substituted_from_exercise_key": None,
        "substituted_from_name": None,
    }


def session_display_exercise(session_exercise: dict) -> dict:
    exercise = {
        "exercise_key": session_exercise.get("exercise_key"),
        "name": session_exercise.get("name"),
        "order": session_exercise.get("order", 0),
        "modality": session_exercise.get("modality"),
        "image_url": session_exercise.get("image_url"),
        "video_url": session_exercise.get("video_url"),
        "instructions": session_exercise.get("instructions", ""),
        "focus": session_exercise.get("focus", ""),
        "movement_pattern": session_exercise.get("movement_pattern", ""),
        "category": session_exercise.get("category", ""),
        "primary_muscles": session_exercise.get("primary_muscles", []),
        "exercise_group": session_exercise.get("exercise_group", "main"),
        "set_plan": deepcopy(session_exercise.get("planned", {}).get("set_plan", [])),
        "is_substituted": bool(session_exercise.get("is_substituted")),
        "original_exercise_key": session_exercise.get("original_exercise_key"),
        "original_name": session_exercise.get("original_name"),
        "substituted_from_exercise_key": session_exercise.get("substituted_from_exercise_key"),
        "substituted_from_name": session_exercise.get("substituted_from_name"),
    }

    if exercise["instructions"] and exercise["video_url"] and exercise["image_url"]:
        return exercise

    library_exercise = _lookup_library_exercise(exercise["exercise_key"], exercise["name"])
    if library_exercise:
        exercise["image_url"] = exercise["image_url"] or library_exercise.display_image_url
        exercise["video_url"] = exercise["video_url"] or library_exercise.default_video_url
        exercise["instructions"] = exercise["instructions"] or library_exercise.instructions
        exercise["movement_pattern"] = exercise["movement_pattern"] or library_exercise.movement_pattern
        exercise["category"] = exercise["category"] or library_exercise.category
        exercise["primary_muscles"] = exercise["primary_muscles"] or library_exercise.primary_muscles
    return exercise


def build_session_json(program: TrainingProgram, day: dict, workout_date):
    exercises = []
    for exercise in get_day_all_exercises(day):
        exercises.append(build_session_exercise_snapshot(exercise))
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
        "overall_effort_rpe": None,
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


def sync_session_display_fields(session: WorkoutSession, day: dict) -> WorkoutSession:
    day_exercises = {
        exercise.get("exercise_key"): exercise
        for exercise in get_day_all_exercises(day)
    }
    changed = False
    data = deepcopy(session.session_json)

    for session_exercise in data.get("exercises", []):
        original_key = session_exercise.get("original_exercise_key") or session_exercise.get("exercise_key")
        planned_exercise = day_exercises.get(original_key) or day_exercises.get(session_exercise.get("exercise_key"))

        if planned_exercise:
            for source_key, target_key in (
                ("video_url", "video_url"),
                ("image_url", "image_url"),
                ("instructions", "instructions"),
                ("focus", "focus"),
                ("movement_pattern", "movement_pattern"),
                ("category", "category"),
                ("primary_muscles", "primary_muscles"),
            ):
                if not session_exercise.get(target_key) and planned_exercise.get(source_key):
                    session_exercise[target_key] = deepcopy(planned_exercise.get(source_key))
                    changed = True

        display_exercise = session_display_exercise(session_exercise)
        for key in ("video_url", "image_url", "instructions", "movement_pattern", "category", "primary_muscles"):
            value = display_exercise.get(key)
            if value and session_exercise.get(key) != value:
                session_exercise[key] = deepcopy(value)
                changed = True

    if changed:
        session.session_json = data
        session.save(update_fields=["session_json", "updated_at"])
    return session


def _latest_completed_set_end(data: dict, exclude_exercise_key: str | None = None, exclude_set_number: int | None = None):
    latest_end = None
    for exercise in data.get("exercises", []):
        for actual_set in exercise.get("actual_sets", []):
            if not actual_set.get("completed"):
                continue
            if exclude_exercise_key and exclude_set_number is not None:
                if exercise.get("exercise_key") == exclude_exercise_key and int(actual_set.get("set_number", 0)) == int(exclude_set_number):
                    continue
            ended_at = parse_datetime(actual_set.get("ended_at") or "")
            if ended_at is None:
                ended_at = parse_datetime(actual_set.get("submitted_at") or "")
            if ended_at is None:
                continue
            if latest_end is None or ended_at > latest_end:
                latest_end = ended_at
    return latest_end


@transaction.atomic
def submit_exercise_set(session_id: int, user, exercise_key: str, actual_set: dict, exercise_notes: str):
    session = WorkoutSession.objects.select_for_update().select_related("program", "user").get(
        pk=session_id,
        user=user,
    )
    data = deepcopy(session.session_json)
    for exercise in data.get("exercises", []):
        if exercise.get("exercise_key") != exercise_key:
            continue
        submitted_at = timezone.now().isoformat()
        started_at = parse_datetime(actual_set.get("started_at") or "")
        ended_at = parse_datetime(actual_set.get("ended_at") or "")
        duration_seconds = actual_set.get("duration_seconds")
        if started_at and ended_at and duration_seconds in (None, ""):
            duration_seconds = max(0, int(round((ended_at - started_at).total_seconds())))

        latest_previous_end = _latest_completed_set_end(
            data,
            exclude_exercise_key=exercise_key,
            exclude_set_number=actual_set.get("set_number"),
        )
        rest_before_seconds = None
        if started_at and latest_previous_end and started_at >= latest_previous_end:
            rest_before_seconds = max(0, int(round((started_at - latest_previous_end).total_seconds())))

        actual_set["duration_seconds"] = duration_seconds
        actual_set["rest_before_seconds"] = rest_before_seconds
        actual_set["submitted_at"] = submitted_at
        actual_sets = [item for item in exercise.get("actual_sets", []) if item.get("set_number") != actual_set.get("set_number")]
        actual_sets.append(actual_set)
        actual_sets.sort(key=lambda item: item.get("set_number", 0))
        exercise["actual_sets"] = actual_sets
        exercise["exercise_notes"] = exercise_notes
        planned_set_numbers = {
            item.get("set_number")
            for item in exercise.get("planned", {}).get("set_plan", [])
        }
        completed_set_numbers = {
            item.get("set_number")
            for item in actual_sets
            if item.get("completed")
        }
        exercise["status"] = "completed" if planned_set_numbers and planned_set_numbers.issubset(completed_set_numbers) else "pending"
        exercise["submitted_at"] = submitted_at
        break
    else:
        raise ValueError("Exercise not found in workout session.")

    session.session_json = data
    session.submission_version += 1
    session.last_exercise_submission_at = timezone.now()
    session.save(update_fields=["session_json", "submission_version", "last_exercise_submission_at", "updated_at"])
    return session


@transaction.atomic
def swap_session_exercise(session_id: int, user, current_exercise_key: str, replacement_external_id: str):
    session = WorkoutSession.objects.select_for_update().get(pk=session_id, user=user)
    data = deepcopy(session.session_json)
    replacement = Exercise.objects.filter(external_id__iexact=replacement_external_id, is_active=True).first()
    replacement = resolve_canonical_exercise(replacement)
    if replacement is None:
        raise ValueError("Replacement exercise not found.")

    replacement_key = replacement.exercise_key
    for other_exercise in data.get("exercises", []):
        if other_exercise.get("exercise_key") == replacement_key and other_exercise.get("exercise_key") != current_exercise_key:
            raise ValueError("That exercise is already part of today's workout.")

    for exercise in data.get("exercises", []):
        if exercise.get("exercise_key") != current_exercise_key:
            continue
        if exercise.get("actual_sets"):
            raise ValueError("This exercise has already been started and cannot be swapped.")

        prescription_types = {
            item.get("prescription_type") or "reps"
            for item in exercise.get("planned", {}).get("set_plan", [])
        }
        uses_time = "time" in prescription_types
        if uses_time and not replacement.supports_time:
            raise ValueError("Replacement exercise does not support time-based work.")
        if not uses_time and not replacement.supports_reps:
            raise ValueError("Replacement exercise does not support rep-based work.")

        exercise["is_substituted"] = True
        exercise["original_exercise_key"] = exercise.get("original_exercise_key") or exercise.get("exercise_key")
        exercise["original_name"] = exercise.get("original_name") or exercise.get("name")
        exercise["substituted_from_exercise_key"] = exercise.get("exercise_key")
        exercise["substituted_from_name"] = exercise.get("name")
        exercise["exercise_key"] = replacement_key
        exercise["name"] = replacement.name
        exercise["modality"] = replacement.modality
        exercise["image_url"] = replacement.display_image_url
        exercise["video_url"] = replacement.default_video_url
        exercise["instructions"] = replacement.instructions
        exercise["focus"] = ", ".join(replacement.primary_muscles[:3]) if replacement.primary_muscles else exercise.get("focus", "")
        exercise["movement_pattern"] = replacement.movement_pattern
        exercise["category"] = replacement.category
        exercise["primary_muscles"] = replacement.primary_muscles
        exercise["exercise_notes"] = ""
        exercise["submitted_at"] = None
        exercise["status"] = "pending"
        exercise["actual_sets"] = []
        break
    else:
        raise ValueError("Exercise not found in workout session.")

    session.session_json = data
    session.save(update_fields=["session_json", "updated_at"])
    return session


@transaction.atomic
def complete_session(session_id: int, user, session_notes: str = "", overall_effort_rpe=None):
    session = WorkoutSession.objects.select_for_update().get(pk=session_id, user=user)
    data = deepcopy(session.session_json)
    data["status"] = "completed"
    data["session_notes"] = session_notes
    data["overall_effort_rpe"] = float(overall_effort_rpe) if overall_effort_rpe not in ("", None) else None
    data["completed_at"] = timezone.now().isoformat()
    session.status = WorkoutSession.Status.COMPLETED
    session.completed_at = timezone.now()
    session.session_json = data
    session.save(update_fields=["status", "completed_at", "session_json", "updated_at"])
    return session
