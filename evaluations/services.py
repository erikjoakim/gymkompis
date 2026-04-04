import json
import logging

from django.conf import settings
from django.db import transaction
from openai import OpenAI

from core.json_utils import extract_json_object, extract_response_text
from training.models import WorkoutSession

from .models import WorkoutEvaluation
from .prompts import build_evaluation_input, build_evaluation_instructions
from .schemas import (
    PERIOD_EVALUATION_SCHEMA,
    SESSION_EVALUATION_SCHEMA,
    validate_period_evaluation,
    validate_session_evaluation,
)


logger = logging.getLogger(__name__)


def _extract_token_usage(response):
    usage = getattr(response, "usage", None)
    if not usage:
        return None, None
    return getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)


def _mock_session_evaluation(session: WorkoutSession):
    return {
        "version": 1,
        "evaluation_type": "session",
        "session_id": session.id,
        "overall_summary": "Solid session with good adherence to the plan.",
        "adherence_score": 86,
        "effort_summary": "Most sets landed close to the target effort range.",
        "recovery_flag": "low",
        "progression_signals": ["You completed most planned sets and maintained stable performance."],
        "exercise_feedback": [
            {
                "exercise_key": item["exercise_key"],
                "comment": "Good consistency across the logged sets.",
                "suggested_next_step": "Keep the same form standard and progress gradually.",
            }
            for item in session.session_json.get("exercises", [])[:2]
        ],
        "recommendations": [
            "Maintain the same structure next session.",
            "Add a little more load only if form stays consistent.",
        ],
    }


def _mock_period_evaluation(sessions, start_date, end_date):
    return {
        "version": 1,
        "evaluation_type": "period",
        "evaluation_scope": {
            "scope_type": "date_range",
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "session_ids": [session.id for session in sessions],
        },
        "summary": {
            "overall_adherence_score": 80,
            "consistency_score": 82,
            "progression_score": 76,
            "recovery_risk": "low",
        },
        "highlights": ["Training consistency was solid over the selected period."],
        "exercise_trends": [],
        "issues": [],
        "recommendations": [
            "Keep the current frequency if recovery remains good.",
            "Use your next program refresh to progress your strongest lifts modestly.",
        ],
    }


def _llm_evaluate(payload: dict, evaluation_type: str):
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    instructions = build_evaluation_instructions()
    schema_hint = SESSION_EVALUATION_SCHEMA if evaluation_type == WorkoutEvaluation.EvaluationType.SESSION else PERIOD_EVALUATION_SCHEMA
    response = client.responses.create(
        model=settings.OPENAI_MODEL,
        instructions=instructions,
        input=build_evaluation_input(payload, evaluation_type, schema_hint),
        temperature=0.3,
    )
    data = extract_json_object(extract_response_text(response))
    if evaluation_type == WorkoutEvaluation.EvaluationType.SESSION:
        validate_session_evaluation(data)
    else:
        validate_period_evaluation(data)
    return data, _extract_token_usage(response)


@transaction.atomic
def evaluate_session_for_user(user, session: WorkoutSession, auto_generated: bool = False):
    payload = session.session_json
    if settings.OPENAI_API_KEY and not settings.OPENAI_MOCK_RESPONSES:
        evaluation_json, token_usage = _llm_evaluate(payload, WorkoutEvaluation.EvaluationType.SESSION)
    else:
        evaluation_json = _mock_session_evaluation(session)
        validate_session_evaluation(evaluation_json)
        token_usage = (None, None)

    evaluation = WorkoutEvaluation.objects.create(
        user=user,
        evaluation_type=WorkoutEvaluation.EvaluationType.SESSION,
        workout_session=session,
        included_session_ids=[session.id],
        requested_by_user=not auto_generated,
        auto_generated=auto_generated,
        llm_model=settings.OPENAI_MODEL,
        prompt_version=settings.OPENAI_EVALUATION_PROMPT_VERSION,
        input_json=payload,
        evaluation_json=evaluation_json,
        summary_text=evaluation_json["overall_summary"],
    )
    return evaluation, token_usage


@transaction.atomic
def evaluate_period_for_user(user, sessions, start_date, end_date):
    payload = {
        "version": 1,
        "evaluation_type": "period",
        "evaluation_scope": {
            "scope_type": "date_range",
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "session_ids": [session.id for session in sessions],
        },
        "sessions": [session.session_json for session in sessions],
    }
    if settings.OPENAI_API_KEY and not settings.OPENAI_MOCK_RESPONSES:
        evaluation_json, token_usage = _llm_evaluate(payload, WorkoutEvaluation.EvaluationType.PERIOD)
    else:
        evaluation_json = _mock_period_evaluation(sessions, start_date, end_date)
        validate_period_evaluation(evaluation_json)
        token_usage = (None, None)

    summary_text = ". ".join(evaluation_json.get("highlights", [])[:2]) or "Period evaluation created."
    evaluation = WorkoutEvaluation.objects.create(
        user=user,
        evaluation_type=WorkoutEvaluation.EvaluationType.PERIOD,
        evaluation_start_date=start_date,
        evaluation_end_date=end_date,
        included_session_ids=[session.id for session in sessions],
        requested_by_user=True,
        auto_generated=False,
        llm_model=settings.OPENAI_MODEL,
        prompt_version=settings.OPENAI_EVALUATION_PROMPT_VERSION,
        input_json=payload,
        evaluation_json=evaluation_json,
        summary_text=summary_text,
    )
    return evaluation, token_usage
