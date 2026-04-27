import json
import logging
import re
from copy import deepcopy
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from openai import OpenAI

from core.json_utils import extract_json_object, extract_response_text

from .library import root_exercise_queryset
from .models import (
    DAY_KEY_CHOICES,
    Exercise,
    ProgramDraft,
    ProgramDraftAiRun,
    ProgramDraftDay,
    ProgramDraftExercise,
    ProgramDraftRevision,
    ProgramGenerationRequest,
    TrainingProgram,
)
from .prompts import (
    build_program_completion_input,
    build_program_completion_instructions,
    build_program_evaluation_input,
    build_program_evaluation_instructions,
)
from .schemas import clone_sample_program, validate_current_program
from .services import (
    ProgramGenerationFailure,
    _extract_token_usage,
    _generate_llm_program,
    _generate_mock_program,
    _serialize_response_debug,
    build_history_summary,
    build_program_profile_context,
)


logger = logging.getLogger(__name__)
DAY_ORDER = {day_key: index for index, (day_key, _label) in enumerate(DAY_KEY_CHOICES)}
DAY_LABELS = dict(DAY_KEY_CHOICES)


def recommended_block_type(exercise: Exercise) -> str:
    if exercise.library_role == Exercise.LibraryRole.WARMUP:
        return ProgramDraftExercise.BlockType.WARMUP
    return ProgramDraftExercise.BlockType.MAIN


def recommended_prescription_type(exercise: Exercise) -> str:
    if exercise.supports_time and not exercise.supports_reps:
        return ProgramDraftExercise.PrescriptionType.TIME
    return ProgramDraftExercise.PrescriptionType.REPS


def snapshot_external_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "_", (value or "").strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized[:64] or "custom_exercise"


def snapshot_payload_from_exercise(exercise: Exercise) -> dict:
    return {
        "exercise": exercise,
        "snapshot_external_id": exercise.external_id,
        "snapshot_name": exercise.name,
        "snapshot_modality": exercise.modality,
        "snapshot_focus": ", ".join(exercise.primary_muscles[:2]),
        "snapshot_instructions": exercise.instructions or "",
        "snapshot_image_url": exercise.display_image_url or "",
        "snapshot_video_url": exercise.default_video_url or "",
        "snapshot_category": exercise.category or "",
        "snapshot_brand": exercise.brand or "",
        "snapshot_line": exercise.line or "",
        "snapshot_supports_reps": exercise.supports_reps,
        "snapshot_supports_time": exercise.supports_time,
    }


def _default_load_guidance(block_type: str) -> str:
    return "Controlled effort" if block_type == ProgramDraftExercise.BlockType.WARMUP else "Leave 1-3 reps in reserve"


def create_program_draft_exercise_for_day(
    day,
    exercise: Exercise,
    block_type: str | None = None,
) -> ProgramDraftExercise:
    selected_block_type = block_type or recommended_block_type(exercise)
    prescription_type = recommended_prescription_type(exercise)
    current_order = day.draft_exercises.filter(block_type=selected_block_type).count() + 1
    entry = ProgramDraftExercise.objects.create(
        day=day,
        block_type=selected_block_type,
        order=current_order,
        prescription_type=prescription_type,
        sets_count=1 if selected_block_type == ProgramDraftExercise.BlockType.WARMUP else 3,
        target_reps="8-10" if prescription_type == ProgramDraftExercise.PrescriptionType.REPS else "",
        target_seconds=30 if prescription_type == ProgramDraftExercise.PrescriptionType.TIME else None,
        load_guidance=_default_load_guidance(selected_block_type),
        target_effort_rpe=Decimal("6.0") if selected_block_type == ProgramDraftExercise.BlockType.WARMUP else Decimal("7.0"),
        **snapshot_payload_from_exercise(exercise),
    )
    return entry


@transaction.atomic
def copy_program_draft_day(source_day: ProgramDraftDay, target_days: list[ProgramDraftDay]) -> list[ProgramDraftDay]:
    target_days = list(target_days)
    if not target_days:
        return []

    source_entries = list(source_day.draft_exercises.select_related("exercise").all())
    for target_day in target_days:
        if target_day.draft_id != source_day.draft_id:
            raise ValueError("Draft days can only be copied within the same draft.")
        if target_day.pk == source_day.pk:
            raise ValueError("A day cannot be copied onto itself.")

        target_day.name = source_day.name
        target_day.day_type = source_day.day_type
        target_day.notes = source_day.notes
        target_day.save(update_fields=["name", "day_type", "notes"])
        target_day.draft_exercises.all().delete()
        ProgramDraftExercise.objects.bulk_create(
            [
                ProgramDraftExercise(
                    day=target_day,
                    exercise=entry.exercise,
                    snapshot_external_id=entry.snapshot_external_id,
                    snapshot_name=entry.snapshot_name,
                    snapshot_modality=entry.snapshot_modality,
                    snapshot_focus=entry.snapshot_focus,
                    snapshot_instructions=entry.snapshot_instructions,
                    snapshot_image_url=entry.snapshot_image_url,
                    snapshot_video_url=entry.snapshot_video_url,
                    snapshot_category=entry.snapshot_category,
                    snapshot_brand=entry.snapshot_brand,
                    snapshot_line=entry.snapshot_line,
                    snapshot_supports_reps=entry.snapshot_supports_reps,
                    snapshot_supports_time=entry.snapshot_supports_time,
                    block_type=entry.block_type,
                    order=entry.order,
                    prescription_type=entry.prescription_type,
                    sets_count=entry.sets_count,
                    target_reps=entry.target_reps,
                    target_seconds=entry.target_seconds,
                    load_guidance=entry.load_guidance,
                    target_effort_rpe=entry.target_effort_rpe,
                    rest_seconds_override=entry.rest_seconds_override,
                    notes=entry.notes,
                    ai_locked=entry.ai_locked,
                )
                for entry in source_entries
            ]
        )
    return target_days


def sync_program_draft_days(draft: ProgramDraft, selected_day_keys: list[str]) -> None:
    selected_day_keys = list(dict.fromkeys(selected_day_keys))
    existing_days = {day.day_key: day for day in draft.days.all()}

    for day_key in list(existing_days):
        if day_key not in selected_day_keys:
            existing_days[day_key].delete()

    for day_key in selected_day_keys:
        if day_key in existing_days:
            continue
        ProgramDraftDay.objects.create(
            draft=draft,
            day_key=day_key,
            name=DAY_LABELS.get(day_key, day_key.title()),
            day_type="training",
            notes="",
        )


def draft_snapshot_json(draft: ProgramDraft) -> dict:
    days_payload = []
    days = list(draft.days.prefetch_related("draft_exercises__exercise").all())
    days.sort(key=lambda item: DAY_ORDER.get(item.day_key, 99))
    for day in days:
        entries_payload = []
        entries = list(day.draft_exercises.select_related("exercise").all())
        entries.sort(key=lambda entry: (entry.block_type, entry.order, entry.pk))
        for entry in entries:
            entries_payload.append(
                {
                    "exercise_id": entry.exercise_id,
                    "snapshot_external_id": entry.snapshot_external_id,
                    "snapshot_name": entry.snapshot_name,
                    "snapshot_modality": entry.snapshot_modality,
                    "snapshot_focus": entry.snapshot_focus,
                    "snapshot_instructions": entry.snapshot_instructions,
                    "snapshot_image_url": entry.snapshot_image_url,
                    "snapshot_video_url": entry.snapshot_video_url,
                    "snapshot_category": entry.snapshot_category,
                    "snapshot_brand": entry.snapshot_brand,
                    "snapshot_line": entry.snapshot_line,
                    "snapshot_supports_reps": entry.snapshot_supports_reps,
                    "snapshot_supports_time": entry.snapshot_supports_time,
                    "block_type": entry.block_type,
                    "order": entry.order,
                    "prescription_type": entry.prescription_type,
                    "sets_count": entry.sets_count,
                    "target_reps": entry.target_reps,
                    "target_seconds": entry.target_seconds,
                    "load_guidance": entry.load_guidance,
                    "target_effort_rpe": float(entry.target_effort_rpe) if entry.target_effort_rpe is not None else None,
                    "rest_seconds_override": entry.rest_seconds_override,
                    "notes": entry.notes,
                    "ai_locked": entry.ai_locked,
                }
            )
        days_payload.append(
            {
                "day_key": day.day_key,
                "name": day.name,
                "day_type": day.day_type,
                "notes": day.notes,
                "ai_locked": day.ai_locked,
                "entries": entries_payload,
            }
        )
    return {
        "name": draft.name,
        "goal_summary": draft.goal_summary,
        "duration_weeks": draft.duration_weeks,
        "weight_unit": draft.weight_unit,
        "program_notes": draft.program_notes,
        "status": draft.status,
        "source": draft.source,
        "request_prompt": draft.request_prompt,
        "ai_context_notes": draft.ai_context_notes,
        "last_ai_action": draft.last_ai_action,
        "days": days_payload,
    }


def _day_entry_counts(day_payload: dict) -> tuple[int, int]:
    warmups = 0
    mains = 0
    for entry in day_payload.get("entries", []):
        if entry.get("block_type") == ProgramDraftExercise.BlockType.WARMUP:
            warmups += 1
        else:
            mains += 1
    return warmups, mains


def compare_draft_snapshot_to_current(snapshot: dict, draft: ProgramDraft) -> dict:
    current = draft_snapshot_json(draft)
    snapshot_days = {day["day_key"]: day for day in snapshot.get("days", [])}
    current_days = {day["day_key"]: day for day in current.get("days", [])}
    added_days = sorted(day_key for day_key in current_days if day_key not in snapshot_days)
    removed_days = sorted(day_key for day_key in snapshot_days if day_key not in current_days)
    changed_days = []
    for day_key in sorted(set(snapshot_days) & set(current_days), key=lambda key: DAY_ORDER.get(key, 99)):
        old_day = snapshot_days[day_key]
        new_day = current_days[day_key]
        old_warmups, old_mains = _day_entry_counts(old_day)
        new_warmups, new_mains = _day_entry_counts(new_day)
        changes = []
        if old_day.get("name") != new_day.get("name"):
            changes.append("name")
        if old_day.get("day_type") != new_day.get("day_type"):
            changes.append("type")
        if old_day.get("notes", "") != new_day.get("notes", ""):
            changes.append("notes")
        if old_day.get("ai_locked", False) != new_day.get("ai_locked", False):
            changes.append("ai lock")
        if (old_warmups, old_mains) != (new_warmups, new_mains):
            changes.append("exercise counts")
        if changes:
            changed_days.append(
                {
                    "day_key": day_key,
                    "day_label": DAY_LABELS.get(day_key, day_key.title()),
                    "changes": changes,
                    "before": {"warmups": old_warmups, "mains": old_mains},
                    "after": {"warmups": new_warmups, "mains": new_mains},
                }
            )
    top_level_changes = []
    for field in ("name", "goal_summary", "duration_weeks", "weight_unit", "program_notes", "source", "status"):
        if snapshot.get(field) != current.get(field):
            top_level_changes.append(field.replace("_", " "))
    return {
        "top_level_changes": top_level_changes,
        "added_days": [DAY_LABELS.get(day_key, day_key.title()) for day_key in added_days],
        "removed_days": [DAY_LABELS.get(day_key, day_key.title()) for day_key in removed_days],
        "changed_days": changed_days,
        "has_changes": bool(top_level_changes or added_days or removed_days or changed_days),
    }


def create_draft_revision(
    draft: ProgramDraft,
    *,
    source: str,
    action_type: str,
    summary: str = "",
    created_by_user=None,
    ai_request_payload: dict | None = None,
    ai_response_payload: dict | None = None,
) -> ProgramDraftRevision:
    revision_number = (draft.revisions.aggregate(max_revision=Max("revision_number")).get("max_revision") or 0) + 1
    return ProgramDraftRevision.objects.create(
        draft=draft,
        revision_number=revision_number,
        created_by_user=created_by_user,
        source=source,
        action_type=action_type,
        summary=summary,
        draft_snapshot_json=draft_snapshot_json(draft),
        ai_request_payload=ai_request_payload or {},
        ai_response_payload=ai_response_payload or {},
    )


@transaction.atomic
def restore_draft_revision(revision: ProgramDraftRevision, *, created_by_user=None) -> ProgramDraft:
    draft = revision.draft
    snapshot = revision.draft_snapshot_json or {}
    draft.name = snapshot.get("name", draft.name)
    draft.goal_summary = snapshot.get("goal_summary", "")
    draft.duration_weeks = snapshot.get("duration_weeks") or draft.duration_weeks
    draft.weight_unit = snapshot.get("weight_unit") or draft.weight_unit
    draft.program_notes = snapshot.get("program_notes", "")
    draft.status = snapshot.get("status") or ProgramDraft.Status.DRAFT
    draft.source = snapshot.get("source") or draft.source
    draft.request_prompt = snapshot.get("request_prompt", "")
    draft.ai_context_notes = snapshot.get("ai_context_notes", "")
    draft.last_ai_action = snapshot.get("last_ai_action", "")
    draft.save(
        update_fields=[
            "name",
            "goal_summary",
            "duration_weeks",
            "weight_unit",
            "program_notes",
            "status",
            "source",
            "request_prompt",
            "ai_context_notes",
            "last_ai_action",
            "updated_at",
        ]
    )
    draft.days.all().delete()
    day_map = {}
    for day_payload in snapshot.get("days", []):
        day_map[day_payload["day_key"]] = ProgramDraftDay.objects.create(
            draft=draft,
            day_key=day_payload["day_key"],
            name=day_payload.get("name") or DAY_LABELS.get(day_payload["day_key"], day_payload["day_key"].title()),
            day_type=day_payload.get("day_type", "training"),
            notes=day_payload.get("notes", ""),
            ai_locked=day_payload.get("ai_locked", False),
        )
    entries = []
    for day_payload in snapshot.get("days", []):
        day = day_map[day_payload["day_key"]]
        for entry_payload in day_payload.get("entries", []):
            entries.append(
                ProgramDraftExercise(
                    day=day,
                    exercise_id=entry_payload.get("exercise_id"),
                    snapshot_external_id=entry_payload.get("snapshot_external_id", ""),
                    snapshot_name=entry_payload.get("snapshot_name", ""),
                    snapshot_modality=entry_payload.get("snapshot_modality", Exercise.Modality.OTHER),
                    snapshot_focus=entry_payload.get("snapshot_focus", ""),
                    snapshot_instructions=entry_payload.get("snapshot_instructions", ""),
                    snapshot_image_url=entry_payload.get("snapshot_image_url", ""),
                    snapshot_video_url=entry_payload.get("snapshot_video_url", ""),
                    snapshot_category=entry_payload.get("snapshot_category", ""),
                    snapshot_brand=entry_payload.get("snapshot_brand", ""),
                    snapshot_line=entry_payload.get("snapshot_line", ""),
                    snapshot_supports_reps=entry_payload.get("snapshot_supports_reps", True),
                    snapshot_supports_time=entry_payload.get("snapshot_supports_time", False),
                    block_type=entry_payload.get("block_type", ProgramDraftExercise.BlockType.MAIN),
                    order=entry_payload.get("order", 1),
                    prescription_type=entry_payload.get("prescription_type", ProgramDraftExercise.PrescriptionType.REPS),
                    sets_count=entry_payload.get("sets_count", 1),
                    target_reps=entry_payload.get("target_reps", ""),
                    target_seconds=entry_payload.get("target_seconds"),
                    load_guidance=entry_payload.get("load_guidance", ""),
                    target_effort_rpe=entry_payload.get("target_effort_rpe"),
                    rest_seconds_override=entry_payload.get("rest_seconds_override"),
                    notes=entry_payload.get("notes", ""),
                    ai_locked=entry_payload.get("ai_locked", False),
                )
            )
    ProgramDraftExercise.objects.bulk_create(entries)
    create_draft_revision(
        draft,
        source=ProgramDraftRevision.Source.SYSTEM,
        action_type="restore_revision",
        summary=f"Restored revision {revision.revision_number}",
        created_by_user=created_by_user,
    )
    return draft


def _rest_seconds_for_entry(entry: ProgramDraftExercise) -> int:
    if entry.rest_seconds_override is not None:
        return entry.rest_seconds_override
    if entry.block_type == ProgramDraftExercise.BlockType.WARMUP:
        return 30
    if entry.display_modality in {Exercise.Modality.CARDIO, Exercise.Modality.MOBILITY}:
        return 30
    return 60


def _serialize_program_draft_exercise(entry: ProgramDraftExercise, *, validate_output: bool = True) -> dict:
    exercise_key = (
        entry.exercise.exercise_key
        if entry.exercise_id
        else snapshot_external_key(entry.snapshot_external_id or entry.display_name)
    )
    instructions = entry.display_instructions
    if not validate_output and not instructions:
        instructions = ""
    set_plan = []
    for index in range(1, entry.sets_count + 1):
        item = {
            "set_number": index,
            "prescription_type": entry.prescription_type,
            "load_guidance": entry.load_guidance or "",
            "target_effort_rpe": float(entry.target_effort_rpe) if entry.target_effort_rpe is not None else None,
        }
        if entry.prescription_type == ProgramDraftExercise.PrescriptionType.TIME:
            item["target_seconds"] = entry.target_seconds
        else:
            item["target_reps"] = entry.target_reps
        set_plan.append(item)

    return {
        "exercise_key": exercise_key,
        "name": entry.display_name,
        "order": entry.order,
        "modality": entry.display_modality,
        "focus": entry.snapshot_focus or "",
        "instructions": instructions,
        "image_url": entry.display_image_url or None,
        "video_url": entry.display_video_url or None,
        "rest_seconds": _rest_seconds_for_entry(entry),
        "is_static": (
            bool(entry.exercise.is_static)
            if entry.exercise_id
            else entry.prescription_type == ProgramDraftExercise.PrescriptionType.TIME and not entry.supports_reps
        ),
        "supports_time": entry.supports_time,
        "supports_reps": entry.supports_reps,
        "notes": entry.notes or "",
        "set_plan": set_plan,
    }


def draft_to_program_json(
    draft: ProgramDraft,
    *,
    validate_output: bool = True,
    enforce_publish_ready: bool = False,
) -> dict:
    day_payloads = []
    training_like_days = 0
    days = list(draft.days.prefetch_related("draft_exercises__exercise"))
    days.sort(key=lambda item: DAY_ORDER.get(item.day_key, 99))
    for day in days:
        warmup_items = []
        main_items = []
        entries = list(day.draft_exercises.select_related("exercise").all())
        entries.sort(key=lambda entry: (entry.block_type, entry.order, entry.pk))
        for entry in entries:
            serialized = _serialize_program_draft_exercise(entry, validate_output=validate_output)
            if entry.block_type == ProgramDraftExercise.BlockType.WARMUP:
                warmup_items.append(serialized)
            else:
                main_items.append(serialized)

        if day.day_type != "rest":
            training_like_days += 1
            if enforce_publish_ready and not main_items:
                raise ValueError(f"{day.day_label} must include at least one main exercise before publishing.")
        day_payload = {
            "day_key": day.day_key,
            "day_label": day.day_label,
            "name": day.name,
            "type": day.day_type,
            "notes": day.notes or "",
            "exercises": main_items,
        }
        if warmup_items:
            day_payload["warmup"] = warmup_items
        day_payloads.append(day_payload)

    program_json = {
        "version": 1,
        "program_name": draft.name,
        "goal_summary": draft.goal_summary or "Program draft built in GymKompis.",
        "duration_weeks": draft.duration_weeks,
        "days_per_week": training_like_days or len(day_payloads),
        "weight_unit": draft.weight_unit,
        "program_notes": draft.program_notes or "",
        "days": day_payloads,
    }
    if validate_output:
        validate_current_program(program_json)
    return program_json


@transaction.atomic
def publish_program_draft(draft: ProgramDraft) -> TrainingProgram:
    program_json = draft_to_program_json(draft, validate_output=True, enforce_publish_ready=True)
    TrainingProgram.objects.filter(user=draft.user, status=TrainingProgram.Status.ACTIVE).update(
        status=TrainingProgram.Status.ARCHIVED
    )
    latest_program = TrainingProgram.objects.filter(user=draft.user).order_by("-version_number").first()
    version_number = 1 if latest_program is None else latest_program.version_number + 1
    program = TrainingProgram.objects.create(
        user=draft.user,
        name=draft.name,
        status=TrainingProgram.Status.ACTIVE,
        request_prompt=draft.request_prompt,
        current_program=program_json,
        version_number=version_number,
        source=TrainingProgram.Source.MANUAL if draft.source == ProgramDraft.Source.MANUAL else TrainingProgram.Source.AI_GENERATED,
    )
    draft.published_program = program
    draft.published_at = timezone.now()
    draft.status = ProgramDraft.Status.PUBLISHED
    draft.save(update_fields=["published_program", "published_at", "status", "updated_at"])
    create_draft_revision(
        draft,
        source=ProgramDraftRevision.Source.SYSTEM,
        action_type="publish",
        summary=f"Published to program version {program.version_number}",
        created_by_user=draft.user,
    )
    return program


def create_empty_program_draft(user, initial_payload: dict) -> ProgramDraft:
    draft = ProgramDraft.objects.create(
        user=user,
        name=initial_payload["name"],
        goal_summary=initial_payload.get("goal_summary", ""),
        duration_weeks=initial_payload.get("duration_weeks") or 8,
        weight_unit=initial_payload.get("weight_unit") or getattr(user.profile, "preferred_weight_unit", "kg"),
        program_notes=initial_payload.get("program_notes", ""),
        source=initial_payload.get("source") or ProgramDraft.Source.MANUAL,
        request_prompt=initial_payload.get("request_prompt", ""),
    )
    sync_program_draft_days(draft, initial_payload.get("selected_days") or [])
    create_draft_revision(
        draft,
        source=ProgramDraftRevision.Source.MANUAL,
        action_type="create_empty_draft",
        summary="Created empty draft",
        created_by_user=user,
    )
    return draft


def _resolve_exercise_for_program_item(item: dict) -> Exercise | None:
    key = snapshot_external_key(item.get("exercise_key") or "")
    name = (item.get("name") or "").strip()
    queryset = root_exercise_queryset().filter(is_active=True)
    exercise = queryset.filter(external_id__iexact=item.get("exercise_key", "")).first()
    if exercise:
        return exercise
    for candidate in queryset.filter(name__iexact=name)[:10]:
        if snapshot_external_key(candidate.exercise_key) == key:
            return candidate
    return queryset.filter(name__iexact=name).first()


def _default_prescription_from_set_plan(set_plan: list[dict]) -> tuple[str, str, int | None]:
    first_item = (set_plan or [{}])[0]
    prescription_type = first_item.get("prescription_type") or ProgramDraftExercise.PrescriptionType.REPS
    if prescription_type == ProgramDraftExercise.PrescriptionType.TIME:
        return prescription_type, "", first_item.get("target_seconds")
    return prescription_type, first_item.get("target_reps", ""), None


def _entry_from_program_item(day: ProgramDraftDay, item: dict, block_type: str, default_order: int) -> ProgramDraftExercise:
    exercise = _resolve_exercise_for_program_item(item)
    set_plan = item.get("set_plan") or []
    prescription_type, target_reps, target_seconds = _default_prescription_from_set_plan(set_plan)
    snapshot = snapshot_payload_from_exercise(exercise) if exercise else {
        "exercise": None,
        "snapshot_external_id": item.get("exercise_key", ""),
        "snapshot_name": item.get("name", ""),
        "snapshot_modality": item.get("modality", Exercise.Modality.OTHER),
        "snapshot_focus": item.get("focus", ""),
        "snapshot_instructions": item.get("instructions", ""),
        "snapshot_image_url": item.get("image_url") or "",
        "snapshot_video_url": item.get("video_url") or "",
        "snapshot_category": "",
        "snapshot_brand": "",
        "snapshot_line": "",
        "snapshot_supports_reps": prescription_type != ProgramDraftExercise.PrescriptionType.TIME,
        "snapshot_supports_time": prescription_type == ProgramDraftExercise.PrescriptionType.TIME,
    }
    return ProgramDraftExercise(
        day=day,
        block_type=block_type,
        order=item.get("order") or default_order,
        prescription_type=prescription_type,
        sets_count=max(len(set_plan), 1),
        target_reps=target_reps,
        target_seconds=target_seconds,
        load_guidance=(set_plan[0].get("load_guidance") if set_plan else "") or "",
        target_effort_rpe=(set_plan[0].get("target_effort_rpe") if set_plan else None),
        rest_seconds_override=item.get("rest_seconds"),
        notes=item.get("notes", "") or "",
        **snapshot,
    )


@transaction.atomic
def program_json_to_draft(
    program_json: dict,
    *,
    user,
    source: str,
    request_prompt: str = "",
    existing_draft: ProgramDraft | None = None,
) -> ProgramDraft:
    if existing_draft is None:
        draft = ProgramDraft.objects.create(
            user=user,
            name=program_json.get("program_name") or "Untitled Draft",
            goal_summary=program_json.get("goal_summary", ""),
            duration_weeks=program_json.get("duration_weeks") or 8,
            weight_unit=program_json.get("weight_unit") or getattr(user.profile, "preferred_weight_unit", "kg"),
            program_notes=program_json.get("program_notes", ""),
            source=source,
            request_prompt=request_prompt,
        )
    else:
        draft = existing_draft
        draft.name = program_json.get("program_name") or draft.name
        draft.goal_summary = program_json.get("goal_summary", "")
        draft.duration_weeks = program_json.get("duration_weeks") or draft.duration_weeks
        draft.weight_unit = program_json.get("weight_unit") or draft.weight_unit
        draft.program_notes = program_json.get("program_notes", "")
        draft.request_prompt = request_prompt or draft.request_prompt
        if source == ProgramDraft.Source.AI_SEEDED or draft.source == ProgramDraft.Source.AI_SEEDED:
            draft.source = source
        draft.save(
            update_fields=[
                "name",
                "goal_summary",
                "duration_weeks",
                "weight_unit",
                "program_notes",
                "request_prompt",
                "source",
                "updated_at",
            ]
        )
        draft.days.all().delete()

    day_objects = []
    exercise_objects = []
    for day_payload in program_json.get("days", []):
        day = ProgramDraftDay(
            draft=draft,
            day_key=day_payload["day_key"],
            name=day_payload.get("name") or DAY_LABELS.get(day_payload["day_key"], day_payload["day_key"].title()),
            day_type=day_payload.get("type", "training"),
            notes=day_payload.get("notes", "") or "",
        )
        day_objects.append(day)
    ProgramDraftDay.objects.bulk_create(day_objects)
    day_map = {day.day_key: day for day in draft.days.all()}

    for day_payload in program_json.get("days", []):
        day = day_map[day_payload["day_key"]]
        for index, item in enumerate(day_payload.get("warmup", []) or [], start=1):
            exercise_objects.append(_entry_from_program_item(day, item, ProgramDraftExercise.BlockType.WARMUP, index))
        for index, item in enumerate(day_payload.get("exercises", []) or [], start=1):
            exercise_objects.append(_entry_from_program_item(day, item, ProgramDraftExercise.BlockType.MAIN, index))
    ProgramDraftExercise.objects.bulk_create(exercise_objects)
    return draft


@transaction.atomic
def clone_training_program_to_draft(program: TrainingProgram, *, user=None, summary: str = "") -> ProgramDraft:
    draft_user = user or program.user
    draft = program_json_to_draft(
        deepcopy(program.current_program),
        user=draft_user,
        source=ProgramDraft.Source.HYBRID if program.source == TrainingProgram.Source.AI_GENERATED else ProgramDraft.Source.MANUAL,
        request_prompt=program.request_prompt,
    )
    if summary:
        draft.program_notes = "\n\n".join(part for part in [draft.program_notes.strip(), summary] if part).strip()
        draft.save(update_fields=["program_notes", "updated_at"])
    create_draft_revision(
        draft,
        source=ProgramDraftRevision.Source.SYSTEM,
        action_type="import_training_program",
        summary=f"Imported editable draft from program version {program.version_number}",
        created_by_user=draft_user,
    )
    return draft


def _mock_completed_day(day: ProgramDraftDay, sample_day: dict) -> dict:
    completed = deepcopy(sample_day)
    completed["day_key"] = day.day_key
    completed["day_label"] = day.day_label
    completed["name"] = day.name or sample_day.get("name") or day.day_label
    completed["type"] = day.day_type
    completed["notes"] = day.notes or sample_day.get("notes", "")
    return completed


def _build_mock_completion_program(draft: ProgramDraft, target_day_keys: list[str]) -> dict:
    current_json = draft_to_program_json(draft, validate_output=False, enforce_publish_ready=False)
    sample = clone_sample_program(draft.weight_unit)
    sample_days = sample.get("days", [])
    sample_index = 0
    updated_days = []
    for day_payload in current_json.get("days", []):
        if day_payload["day_key"] not in target_day_keys:
            updated_days.append(day_payload)
            continue
        if day_payload.get("exercises"):
            updated_days.append(day_payload)
            continue
        sample_day = sample_days[sample_index % len(sample_days)]
        sample_index += 1
        day = draft.days.get(day_key=day_payload["day_key"])
        updated_days.append(_mock_completed_day(day, sample_day))
    current_json["days"] = updated_days
    current_json["program_name"] = draft.name
    current_json["goal_summary"] = draft.goal_summary or current_json.get("goal_summary", "")
    current_json["duration_weeks"] = draft.duration_weeks
    current_json["weight_unit"] = draft.weight_unit
    current_json["program_notes"] = draft.program_notes or current_json.get("program_notes", "")
    return current_json


def _locked_exercise_summary_for_day(day: ProgramDraftDay) -> list[dict]:
    payload = []
    for entry in day.draft_exercises.filter(ai_locked=True).order_by("block_type", "order", "id"):
        payload.append(
            {
                "name": entry.display_name,
                "block_type": entry.block_type,
                "order": entry.order,
                "exercise_key": entry.exercise.exercise_key if entry.exercise_id else snapshot_external_key(entry.snapshot_external_id or entry.display_name),
            }
        )
    return payload


def _entry_identity(entry: ProgramDraftExercise | None = None, item: dict | None = None) -> str:
    if entry is not None:
        return (entry.exercise.exercise_key if entry.exercise_id else snapshot_external_key(entry.snapshot_external_id or entry.display_name)).lower()
    if item is not None:
        return snapshot_external_key(item.get("exercise_key") or item.get("name") or "").lower()
    return ""


def _parse_ai_json_response(response) -> tuple[dict, str, tuple[int | None, int | None]]:
    extracted_text = extract_response_text(response)
    token_usage = _extract_token_usage(response)
    debug_payload = _serialize_response_debug(response, extracted_text=extracted_text)
    if not extracted_text:
        raise ProgramGenerationFailure(
            "Empty model response.",
            raw_response=debug_payload,
            token_usage=token_usage,
            retryable=False,
        )
    try:
        payload = extract_json_object(extracted_text)
    except json.JSONDecodeError as exc:
        raise ProgramGenerationFailure(
            "Could not parse JSON from model response.",
            raw_response=extracted_text,
            token_usage=token_usage,
            retryable=False,
        ) from exc
    return payload, extracted_text, token_usage


def _update_draft_source_after_ai(draft: ProgramDraft) -> None:
    if draft.source == ProgramDraft.Source.MANUAL:
        draft.source = ProgramDraft.Source.HYBRID
        draft.save(update_fields=["source", "updated_at"])


@transaction.atomic
def seed_program_draft_with_ai(user, prompt_text: str) -> ProgramDraft:
    history_summary = build_history_summary(user)
    request_record = ProgramGenerationRequest.objects.create(
        user=user,
        prompt_text=prompt_text,
        attached_history_summary=history_summary,
        llm_model=settings.OPENAI_MODEL,
        prompt_version=settings.OPENAI_PROGRAM_PROMPT_VERSION,
    )
    try:
        if settings.OPENAI_API_KEY and not settings.OPENAI_MOCK_RESPONSES:
            program_json, raw_response, token_usage = _generate_llm_program(user, prompt_text, history_summary)
        else:
            program_json = _generate_mock_program(user, prompt_text)
            validate_current_program(program_json)
            raw_response = json.dumps(program_json, indent=2)
            token_usage = (None, None)

        draft = program_json_to_draft(
            program_json,
            user=user,
            source=ProgramDraft.Source.AI_SEEDED,
            request_prompt=prompt_text,
        )
        create_draft_revision(
            draft,
            source=ProgramDraftRevision.Source.AI,
            action_type="seed_full_program",
            summary="Created AI-seeded draft",
            created_by_user=user,
            ai_request_payload={"prompt_text": prompt_text, "history_summary": history_summary or {}},
            ai_response_payload=program_json,
        )
        request_record.validated_program_json = program_json
        request_record.raw_llm_response = raw_response
        request_record.token_usage_input = token_usage[0]
        request_record.token_usage_output = token_usage[1]
        request_record.status = ProgramGenerationRequest.Status.SUCCEEDED
        request_record.save(
            update_fields=[
                "validated_program_json",
                "raw_llm_response",
                "token_usage_input",
                "token_usage_output",
                "status",
            ]
        )
        return draft
    except Exception as exc:
        logger.exception("Program draft generation failed for user=%s", user.pk)
        request_record.status = ProgramGenerationRequest.Status.FAILED
        request_record.error_message = str(exc)
        update_fields = ["status", "error_message"]
        if isinstance(exc, ProgramGenerationFailure):
            request_record.raw_llm_response = exc.raw_response
            request_record.token_usage_input = exc.token_usage[0]
            request_record.token_usage_output = exc.token_usage[1]
            update_fields.extend(["raw_llm_response", "token_usage_input", "token_usage_output"])
        request_record.save(update_fields=update_fields)
        raise


def incomplete_day_keys_for_draft(draft: ProgramDraft) -> list[str]:
    return [
        day.day_key
        for day in draft.days.prefetch_related("draft_exercises").all()
        if day.day_type != "rest"
        and not day.ai_locked
        and not day.draft_exercises.filter(block_type=ProgramDraftExercise.BlockType.MAIN).exists()
    ]


@transaction.atomic
def complete_program_draft_with_ai(
    draft: ProgramDraft,
    *,
    action_type: str,
    target_day_keys: list[str],
) -> ProgramDraftAiRun:
    target_day_keys = list(dict.fromkeys(target_day_keys))
    if not target_day_keys:
        raise ValueError("Select at least one draft day for AI completion.")
    locked_target_days = list(draft.days.filter(day_key__in=target_day_keys, ai_locked=True).values_list("day_key", flat=True))
    if locked_target_days:
        labels = ", ".join(DAY_LABELS.get(day_key, day_key.title()) for day_key in locked_target_days)
        raise ValueError(f"Unlock {labels} before asking AI to rewrite those days.")

    ai_run = ProgramDraftAiRun.objects.create(
        draft=draft,
        user=draft.user,
        action_type=action_type,
        scope_payload={"target_days": target_day_keys},
        llm_model=settings.OPENAI_MODEL,
        prompt_version=settings.OPENAI_PROGRAM_PROMPT_VERSION,
        status=ProgramDraftAiRun.Status.PENDING,
    )
    try:
        if settings.OPENAI_API_KEY and not settings.OPENAI_MOCK_RESPONSES:
            current_json = draft_to_program_json(draft, validate_output=False, enforce_publish_ready=False)
            locked_day_keys = list(draft.days.filter(ai_locked=True).values_list("day_key", flat=True))
            locked_exercise_map = {
                day.day_key: _locked_exercise_summary_for_day(day)
                for day in draft.days.prefetch_related("draft_exercises").all()
                if day.draft_exercises.filter(ai_locked=True).exists()
            }
            prompt_text = build_program_completion_input(
                draft_snapshot=current_json,
                target_day_keys=target_day_keys,
                locked_day_keys=locked_day_keys,
                locked_exercise_map=locked_exercise_map,
                profile_context=build_program_profile_context(draft.user),
                history_summary=build_history_summary(draft.user),
            )
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            response = client.responses.create(
                model=settings.OPENAI_MODEL,
                instructions=build_program_completion_instructions(),
                input=prompt_text,
                temperature=0.4,
            )
            program_json, raw_response, token_usage = _parse_ai_json_response(response)
            validate_current_program(program_json)
        else:
            prompt_text = json.dumps({"mock_target_days": target_day_keys})
            program_json = _build_mock_completion_program(draft, target_day_keys)
            validate_current_program(program_json)
            raw_response = json.dumps(program_json, indent=2)
            token_usage = (None, None)

        current_day_map = {day.day_key: day for day in draft.days.all()}
        day_payload_map = {day["day_key"]: day for day in program_json.get("days", [])}
        for day_key in target_day_keys:
            if day_key not in current_day_map or day_key not in day_payload_map:
                continue
            day = current_day_map[day_key]
            day_payload = day_payload_map[day_key]
            locked_entries = list(day.draft_exercises.select_related("exercise").filter(ai_locked=True).order_by("block_type", "order", "id"))
            locked_identities = {_entry_identity(entry=entry) for entry in locked_entries}
            day.name = day_payload.get("name") or day.name
            day.day_type = day_payload.get("type") or day.day_type
            day.notes = day_payload.get("notes", "") or ""
            day.save(update_fields=["name", "day_type", "notes"])
            day.draft_exercises.all().delete()
            replacement_entries = []
            for entry in locked_entries:
                replacement_entries.append(
                    ProgramDraftExercise(
                        day=day,
                        exercise=entry.exercise,
                        snapshot_external_id=entry.snapshot_external_id,
                        snapshot_name=entry.snapshot_name,
                        snapshot_modality=entry.snapshot_modality,
                        snapshot_focus=entry.snapshot_focus,
                        snapshot_instructions=entry.snapshot_instructions,
                        snapshot_image_url=entry.snapshot_image_url,
                        snapshot_video_url=entry.snapshot_video_url,
                        snapshot_category=entry.snapshot_category,
                        snapshot_brand=entry.snapshot_brand,
                        snapshot_line=entry.snapshot_line,
                        snapshot_supports_reps=entry.snapshot_supports_reps,
                        snapshot_supports_time=entry.snapshot_supports_time,
                        block_type=entry.block_type,
                        order=entry.order,
                        prescription_type=entry.prescription_type,
                        sets_count=entry.sets_count,
                        target_reps=entry.target_reps,
                        target_seconds=entry.target_seconds,
                        load_guidance=entry.load_guidance,
                        target_effort_rpe=entry.target_effort_rpe,
                        rest_seconds_override=entry.rest_seconds_override,
                        notes=entry.notes,
                        ai_locked=True,
                    )
                )
            warmup_offset = max([entry.order for entry in locked_entries if entry.block_type == ProgramDraftExercise.BlockType.WARMUP], default=0)
            main_offset = max([entry.order for entry in locked_entries if entry.block_type == ProgramDraftExercise.BlockType.MAIN], default=0)
            for index, item in enumerate(day_payload.get("warmup", []) or [], start=1):
                if _entry_identity(item=item) in locked_identities:
                    continue
                replacement_entries.append(
                    _entry_from_program_item(day, item, ProgramDraftExercise.BlockType.WARMUP, warmup_offset + index)
                )
            for index, item in enumerate(day_payload.get("exercises", []) or [], start=1):
                if _entry_identity(item=item) in locked_identities:
                    continue
                replacement_entries.append(
                    _entry_from_program_item(day, item, ProgramDraftExercise.BlockType.MAIN, main_offset + index)
                )
            ProgramDraftExercise.objects.bulk_create(replacement_entries)

        ai_run.prompt_text = prompt_text
        ai_run.raw_llm_response = raw_response
        ai_run.validated_payload = {
            "target_days": target_day_keys,
            "program_days": [day_payload_map[key] for key in target_day_keys if key in day_payload_map],
        }
        ai_run.token_usage_input = token_usage[0]
        ai_run.token_usage_output = token_usage[1]
        ai_run.status = ProgramDraftAiRun.Status.SUCCEEDED
        ai_run.save(
            update_fields=[
                "prompt_text",
                "raw_llm_response",
                "validated_payload",
                "token_usage_input",
                "token_usage_output",
                "status",
            ]
        )
        draft.last_ai_action = action_type
        _update_draft_source_after_ai(draft)
        draft.save(update_fields=["last_ai_action", "updated_at"])
        create_draft_revision(
            draft,
            source=ProgramDraftRevision.Source.AI,
            action_type=action_type,
            summary=f"AI updated {len(target_day_keys)} day{'s' if len(target_day_keys) != 1 else ''}",
            created_by_user=draft.user,
            ai_request_payload={"target_days": target_day_keys, "prompt_text": prompt_text},
            ai_response_payload=ai_run.validated_payload or {},
        )
        return ai_run
    except Exception as exc:
        logger.exception("Draft AI completion failed for draft=%s", draft.pk)
        ai_run.status = ProgramDraftAiRun.Status.FAILED
        ai_run.error_message = str(exc)
        ai_run.save(update_fields=["status", "error_message"])
        raise


def _mock_draft_evaluation(draft: ProgramDraft) -> dict:
    findings = []
    suggested_actions = []
    for day in draft.days.prefetch_related("draft_exercises").all():
        main_count = day.draft_exercises.filter(block_type=ProgramDraftExercise.BlockType.MAIN).count()
        if day.day_type != "rest" and main_count == 0:
            findings.append(
                {
                    "severity": "high",
                    "type": "coverage",
                    "target": day.day_key,
                    "message": f"{day.day_label} has no main exercises yet.",
                    "suggested_fix": f"Ask AI to complete {day.day_label} or add at least one main exercise manually.",
                }
            )
            suggested_actions.append(
                {
                    "action_type": "complete_day",
                    "target_day": day.day_key,
                    "reason": f"Fill {day.day_label} with a complete training day.",
                }
            )
        warmup_count = day.draft_exercises.filter(block_type=ProgramDraftExercise.BlockType.WARMUP).count()
        if day.day_type == "training" and main_count > 0 and warmup_count == 0:
            findings.append(
                {
                    "severity": "medium",
                    "type": "warmup",
                    "target": day.day_key,
                    "message": f"{day.day_label} has work sets but no warmup block.",
                    "suggested_fix": f"Add 1-2 warmup items or ask AI to generate a warmup for {day.day_label}.",
            }
        )
    if not findings:
        findings.append(
            {
                "severity": "low",
                "type": "overview",
                "target": "draft",
                "message": "No obvious structural issues were found in the current draft.",
                "suggested_fix": "Review exercise variety and session length before publishing.",
            }
        )
    return {
        "summary": "AI review completed on the current draft.",
        "findings": findings,
        "suggested_actions": suggested_actions
        or (
            [
                {
                    "action_type": "complete_missing_days",
                    "reason": "Complete empty days before publishing.",
                }
            ]
            if any(item["severity"] == "high" for item in findings)
            else []
        ),
    }


@transaction.atomic
def evaluate_program_draft_with_ai(draft: ProgramDraft) -> ProgramDraftAiRun:
    ai_run = ProgramDraftAiRun.objects.create(
        draft=draft,
        user=draft.user,
        action_type="evaluate_program",
        scope_payload={},
        llm_model=settings.OPENAI_MODEL,
        prompt_version=settings.OPENAI_PROGRAM_PROMPT_VERSION,
        status=ProgramDraftAiRun.Status.PENDING,
    )
    try:
        if settings.OPENAI_API_KEY and not settings.OPENAI_MOCK_RESPONSES:
            prompt_text = build_program_evaluation_input(
                draft_snapshot=draft_to_program_json(draft, validate_output=False, enforce_publish_ready=False),
                profile_context=build_program_profile_context(draft.user),
                history_summary=build_history_summary(draft.user),
            )
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            response = client.responses.create(
                model=settings.OPENAI_MODEL,
                instructions=build_program_evaluation_instructions(),
                input=prompt_text,
                temperature=0.3,
            )
            evaluation, raw_response, token_usage = _parse_ai_json_response(response)
        else:
            prompt_text = json.dumps({"task": "mock_evaluate_program", "draft_id": draft.id})
            evaluation = _mock_draft_evaluation(draft)
            raw_response = json.dumps(evaluation, indent=2)
            token_usage = (None, None)

        if not isinstance(evaluation, dict) or not isinstance(evaluation.get("findings", []), list):
            raise ValueError("AI evaluation returned an invalid response format.")

        ai_run.prompt_text = prompt_text
        ai_run.raw_llm_response = raw_response
        ai_run.validated_payload = evaluation
        ai_run.token_usage_input = token_usage[0]
        ai_run.token_usage_output = token_usage[1]
        ai_run.status = ProgramDraftAiRun.Status.SUCCEEDED
        ai_run.save(
            update_fields=[
                "prompt_text",
                "raw_llm_response",
                "validated_payload",
                "token_usage_input",
                "token_usage_output",
                "status",
            ]
        )
        draft.latest_ai_evaluation = evaluation
        draft.last_ai_action = "evaluate_program"
        _update_draft_source_after_ai(draft)
        draft.save(update_fields=["latest_ai_evaluation", "last_ai_action", "updated_at"])
        create_draft_revision(
            draft,
            source=ProgramDraftRevision.Source.AI,
            action_type="evaluate_program",
            summary="Stored AI draft evaluation",
            created_by_user=draft.user,
            ai_request_payload={"prompt_text": prompt_text},
            ai_response_payload=evaluation,
        )
        return ai_run
    except Exception as exc:
        logger.exception("Draft AI evaluation failed for draft=%s", draft.pk)
        ai_run.status = ProgramDraftAiRun.Status.FAILED
        ai_run.error_message = str(exc)
        ai_run.save(update_fields=["status", "error_message"])
        raise


def apply_evaluation_suggested_action(draft: ProgramDraft, action_payload: dict) -> ProgramDraftAiRun | None:
    action_type = (action_payload or {}).get("action_type", "")
    if action_type == "complete_missing_days":
        target_day_keys = incomplete_day_keys_for_draft(draft)
        if not target_day_keys:
            raise ValueError("There are no incomplete unlocked days to complete.")
        return complete_program_draft_with_ai(
            draft,
            action_type="complete_missing_days",
            target_day_keys=target_day_keys,
        )
    if action_type == "complete_day":
        target_day = action_payload.get("target_day")
        if not target_day:
            raise ValueError("The suggested action did not specify a target day.")
        return complete_program_draft_with_ai(
            draft,
            action_type="complete_day",
            target_day_keys=[target_day],
        )
    raise ValueError(f"Unsupported suggested action: {action_type}")
