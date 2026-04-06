import json
import logging
from copy import deepcopy
from json import JSONDecodeError

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from jsonschema import ValidationError
from openai import OpenAI

from core.json_utils import extract_json_object, extract_response_text
from training.models import WorkoutSession

from .models import ProgramGenerationRequest, TrainingProgram
from .prompts import build_program_generation_input, build_program_generation_instructions
from .schemas import CURRENT_PROGRAM_SCHEMA, clone_sample_program, validate_current_program, validate_history_summary


logger = logging.getLogger(__name__)
PROGRAM_GENERATION_MAX_ATTEMPTS = 3


class ProgramGenerationFailure(Exception):
    def __init__(
        self,
        message: str,
        *,
        raw_response: str = "",
        token_usage: tuple[int | None, int | None] = (None, None),
        retryable: bool = False,
    ):
        super().__init__(message)
        self.raw_response = raw_response
        self.token_usage = token_usage
        self.retryable = retryable


def build_program_profile_context(user) -> dict:
    profile = user.profile
    age = None
    if profile.birth_year:
        current_year = timezone.localdate().year
        age = current_year - profile.birth_year

    return {
        "age": age,
        "birth_year": profile.birth_year,
        "training_experience": profile.training_experience,
        "injuries_limitations": profile.injuries_limitations,
        "equipment_access": profile.equipment_access,
        "preferred_weight_unit": profile.preferred_weight_unit,
        "preferred_language": profile.preferred_language,
    }


def build_history_summary(user) -> dict | None:
    profile = user.profile
    limit = profile.plan_history_window_sessions or settings.DEFAULT_PLAN_HISTORY_WINDOW_SESSIONS
    sessions = list(
        WorkoutSession.objects.filter(user=user, status=WorkoutSession.Status.COMPLETED)
        .order_by("-workout_date", "-completed_at")[:limit]
    )
    if not sessions:
        return None

    sessions = list(reversed(sessions))
    effort_values = []
    exercise_trends = {}
    skipped_sessions = 0
    reported_issues = []

    for session in sessions:
        data = session.session_json or {}
        for exercise in data.get("exercises", []):
            name = exercise.get("name", "")
            key = exercise.get("exercise_key", "")
            trend = exercise_trends.setdefault(
                key,
                {
                    "exercise_key": key,
                    "name": name,
                    "best_recent_weight": None,
                    "best_recent_reps": None,
                    "best_recent_seconds": None,
                    "trend_note": "Completed recently.",
                },
            )
            for actual_set in exercise.get("actual_sets", []):
                if not actual_set.get("completed"):
                    continue
                reps = actual_set.get("reps")
                seconds = actual_set.get("seconds")
                weight = actual_set.get("weight")
                effort = actual_set.get("effort_rpe")
                if reps is not None:
                    trend["best_recent_reps"] = max(trend["best_recent_reps"] or 0, reps)
                if seconds is not None:
                    trend["best_recent_seconds"] = max(trend["best_recent_seconds"] or 0, seconds)
                if weight is not None:
                    trend["best_recent_weight"] = max(trend["best_recent_weight"] or 0, weight)
                if effort is not None:
                    effort_values.append(float(effort))
                note = actual_set.get("notes")
                if note:
                    reported_issues.append(note)
            if exercise.get("status") == "skipped":
                skipped_sessions += 1
            if exercise.get("exercise_notes"):
                reported_issues.append(exercise["exercise_notes"])

    summary = {
        "version": 1,
        "session_count": len(sessions),
        "date_range": {
            "start_date": sessions[0].workout_date.isoformat(),
            "end_date": sessions[-1].workout_date.isoformat(),
        },
        "adherence_summary": {
            "completed_sessions": len(sessions),
            "skipped_sessions": skipped_sessions,
            "average_effort_rpe": round(sum(effort_values) / len(effort_values), 2) if effort_values else None,
        },
        "exercise_trends": list(exercise_trends.values()),
        "reported_issues": reported_issues[:10],
    }
    validate_history_summary(summary)
    return summary


def _generate_mock_program(user, prompt_text: str) -> dict:
    program = clone_sample_program(user.profile.preferred_weight_unit)
    program["goal_summary"] = prompt_text[:500]
    program["program_name"] = f"GymKompis Plan for {user.profile.effective_display_name}"
    return program


def _extract_token_usage(response):
    usage = getattr(response, "usage", None)
    if not usage:
        return None, None
    return getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)


def _serialize_response_debug(response, *, extracted_text: str = "") -> str:
    output_summary = []
    for item in getattr(response, "output", []) or []:
        content_items = []
        for content in getattr(item, "content", []) or []:
            content_items.append(
                {
                    "type": getattr(content, "type", None),
                    "text": getattr(content, "text", None),
                }
            )
        output_summary.append(
            {
                "type": getattr(item, "type", None),
                "status": getattr(item, "status", None),
                "role": getattr(item, "role", None),
                "content": content_items,
            }
        )
    payload = {
        "response_id": getattr(response, "id", None),
        "status": getattr(response, "status", None),
        "incomplete_details": getattr(response, "incomplete_details", None),
        "output_text": extracted_text or getattr(response, "output_text", None) or "",
        "output": output_summary,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _parse_program_generation_response(response):
    extracted_text = extract_response_text(response)
    token_usage = _extract_token_usage(response)
    debug_payload = _serialize_response_debug(response, extracted_text=extracted_text)
    if not extracted_text:
        raise ProgramGenerationFailure(
            "Empty model response.",
            raw_response=debug_payload,
            token_usage=token_usage,
            retryable=True,
        )

    try:
        data = extract_json_object(extracted_text)
    except JSONDecodeError as exc:
        raise ProgramGenerationFailure(
            "Could not parse JSON from model response.",
            raw_response=extracted_text,
            token_usage=token_usage,
            retryable=True,
        ) from exc

    try:
        validate_current_program(data)
    except ValidationError as exc:
        raise ProgramGenerationFailure(
            f"Model response did not match program schema: {exc.message}",
            raw_response=extracted_text,
            token_usage=token_usage,
            retryable=True,
        ) from exc

    return data, extracted_text, token_usage


def _generate_llm_program(user, prompt_text: str, history_summary: dict | None):
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    instructions = build_program_generation_instructions()
    user_context = build_program_generation_input(
        prompt_text=prompt_text,
        profile_context=build_program_profile_context(user),
        history_summary=history_summary,
        schema=CURRENT_PROGRAM_SCHEMA,
    )
    last_failure = None

    for attempt in range(1, PROGRAM_GENERATION_MAX_ATTEMPTS + 1):
        response = client.responses.create(
            model=settings.OPENAI_MODEL,
            instructions=instructions,
            input=user_context,
            temperature=0.4,
        )
        try:
            return _parse_program_generation_response(response)
        except ProgramGenerationFailure as exc:
            last_failure = exc
            if exc.retryable and attempt < PROGRAM_GENERATION_MAX_ATTEMPTS:
                logger.warning(
                    "Program generation attempt %s/%s failed for user=%s: %s",
                    attempt,
                    PROGRAM_GENERATION_MAX_ATTEMPTS,
                    user.pk,
                    exc,
                )
                continue
            message = str(exc)
            if attempt > 1:
                message = f"{message} after {attempt} attempts."
            raise ProgramGenerationFailure(
                message,
                raw_response=exc.raw_response,
                token_usage=exc.token_usage,
                retryable=False,
            ) from exc

    if last_failure:
        raise last_failure
    raise ProgramGenerationFailure("Program generation failed without a model response.")


def generate_program_for_user(user, prompt_text: str):
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

        with transaction.atomic():
            TrainingProgram.objects.filter(user=user, status=TrainingProgram.Status.ACTIVE).update(
                status=TrainingProgram.Status.ARCHIVED
            )
            latest_program = TrainingProgram.objects.filter(user=user).order_by("-version_number").first()
            version_number = 1 if latest_program is None else latest_program.version_number + 1
            program = TrainingProgram.objects.create(
                user=user,
                name=program_json["program_name"],
                status=TrainingProgram.Status.ACTIVE,
                request_prompt=prompt_text,
                current_program=program_json,
                version_number=version_number,
                source=TrainingProgram.Source.AI_GENERATED,
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
            return program
    except Exception as exc:
        logger.exception("Program generation failed for user=%s", user.pk)
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


@transaction.atomic
def restore_program_for_user(user, archived_program: TrainingProgram) -> TrainingProgram:
    if archived_program.user_id != user.id:
        raise ValueError("You can only restore your own programs.")
    if archived_program.status != TrainingProgram.Status.ARCHIVED:
        raise ValueError("Only archived programs can be restored.")

    TrainingProgram.objects.filter(user=user, status=TrainingProgram.Status.ACTIVE).update(
        status=TrainingProgram.Status.ARCHIVED
    )
    latest_program = TrainingProgram.objects.filter(user=user).order_by("-version_number").first()
    version_number = 1 if latest_program is None else latest_program.version_number + 1
    restored_program = TrainingProgram.objects.create(
        user=user,
        name=archived_program.name,
        status=TrainingProgram.Status.ACTIVE,
        request_prompt=archived_program.request_prompt,
        current_program=deepcopy(archived_program.current_program),
        version_number=version_number,
        source=archived_program.source,
    )
    return restored_program
