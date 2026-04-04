from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import DAY_KEY_CHOICES, Exercise, ManualProgramDraft, ManualProgramExercise, TrainingProgram
from .schemas import validate_current_program


DAY_ORDER = {day_key: index for index, (day_key, _label) in enumerate(DAY_KEY_CHOICES)}


def recommended_block_type(exercise: Exercise) -> str:
    if exercise.library_role == Exercise.LibraryRole.WARMUP:
        return ManualProgramExercise.BlockType.WARMUP
    return ManualProgramExercise.BlockType.MAIN


def recommended_prescription_type(exercise: Exercise) -> str:
    if exercise.supports_time and not exercise.supports_reps:
        return ManualProgramExercise.PrescriptionType.TIME
    return ManualProgramExercise.PrescriptionType.REPS


def create_manual_exercise_for_day(day, exercise: Exercise, block_type: str | None = None) -> ManualProgramExercise:
    selected_block_type = block_type or recommended_block_type(exercise)
    prescription_type = recommended_prescription_type(exercise)
    current_order = day.manual_exercises.filter(block_type=selected_block_type).count() + 1
    entry = ManualProgramExercise.objects.create(
        day=day,
        exercise=exercise,
        block_type=selected_block_type,
        order=current_order,
        prescription_type=prescription_type,
        sets_count=1 if selected_block_type == ManualProgramExercise.BlockType.WARMUP else 3,
        target_reps="8-10" if prescription_type == ManualProgramExercise.PrescriptionType.REPS else "",
        target_seconds=30 if prescription_type == ManualProgramExercise.PrescriptionType.TIME else None,
        load_guidance="Controlled effort",
        target_effort_rpe=Decimal("6.0") if selected_block_type == ManualProgramExercise.BlockType.WARMUP else Decimal("7.0"),
    )
    return entry


def _serialize_manual_exercise(entry: ManualProgramExercise) -> dict:
    exercise = entry.exercise
    set_plan = []
    for index in range(1, entry.sets_count + 1):
        item = {
            "set_number": index,
            "prescription_type": entry.prescription_type,
            "load_guidance": entry.load_guidance,
            "target_effort_rpe": float(entry.target_effort_rpe) if entry.target_effort_rpe is not None else None,
        }
        if entry.prescription_type == ManualProgramExercise.PrescriptionType.TIME:
            item["target_seconds"] = entry.target_seconds
        else:
            item["target_reps"] = entry.target_reps
        set_plan.append(item)

    return {
        "exercise_key": exercise.exercise_key,
        "name": exercise.name,
        "order": entry.order,
        "modality": exercise.modality,
        "focus": ", ".join(exercise.primary_muscles[:2]),
        "instructions": exercise.instructions or "",
        "image_url": exercise.display_image_url or None,
        "video_url": exercise.default_video_url or None,
        "rest_seconds": entry.rest_seconds_override if entry.rest_seconds_override is not None else 60,
        "notes": entry.notes,
        "set_plan": set_plan,
    }


def compile_manual_program(draft: ManualProgramDraft) -> dict:
    day_payloads = []
    training_like_days = 0
    days = list(draft.days.prefetch_related("manual_exercises__exercise"))
    days.sort(key=lambda item: DAY_ORDER.get(item.day_key, 99))
    for day in days:
        warmup_items = []
        main_items = []
        entries = list(day.manual_exercises.all())
        entries.sort(key=lambda entry: (entry.block_type, entry.order, entry.pk))
        for entry in entries:
            serialized = _serialize_manual_exercise(entry)
            if entry.block_type == ManualProgramExercise.BlockType.WARMUP:
                warmup_items.append(serialized)
            else:
                main_items.append(serialized)
        if day.day_type != "rest":
            training_like_days += 1
        day_payload = {
            "day_key": day.day_key,
            "day_label": day.day_label,
            "name": day.name,
            "type": day.day_type,
            "notes": day.notes,
            "exercises": main_items,
        }
        if warmup_items:
            day_payload["warmup"] = warmup_items
        day_payloads.append(day_payload)

    program_json = {
        "version": 1,
        "program_name": draft.name,
        "goal_summary": draft.goal_summary or "Manual program built in GymKompis.",
        "duration_weeks": draft.duration_weeks,
        "days_per_week": training_like_days or len(day_payloads),
        "weight_unit": draft.weight_unit,
        "program_notes": draft.program_notes,
        "days": day_payloads,
    }
    validate_current_program(program_json)
    return program_json


@transaction.atomic
def publish_manual_program(draft: ManualProgramDraft) -> TrainingProgram:
    program_json = compile_manual_program(draft)
    TrainingProgram.objects.filter(user=draft.user, status=TrainingProgram.Status.ACTIVE).update(
        status=TrainingProgram.Status.ARCHIVED
    )
    latest_program = TrainingProgram.objects.filter(user=draft.user).order_by("-version_number").first()
    version_number = 1 if latest_program is None else latest_program.version_number + 1
    program = TrainingProgram.objects.create(
        user=draft.user,
        name=draft.name,
        status=TrainingProgram.Status.ACTIVE,
        request_prompt="",
        current_program=program_json,
        version_number=version_number,
        source=TrainingProgram.Source.MANUAL,
    )
    draft.published_program = program
    draft.published_at = timezone.now()
    draft.save(update_fields=["published_program", "published_at", "updated_at"])
    return program
