import json


def build_program_generation_instructions() -> str:
    return (
        "You are GymKompis, an assistant that creates gym programs for Django-based training software. "
        "Return strict JSON only with no markdown, no prose, and no code fences. "
        "Follow the requested schema exactly. "
        "Training days may include an optional warmup array and a required exercises array. "
        "Warmup items should be low-intensity preparation work such as easy cardio, dynamic mobility, or activation drills. "
        "Every exercise must include video_url if a suitable demonstration link is available, otherwise null. "
        "Every set_plan item must include prescription_type. "
        "Use prescription_type='reps' for normal strength/hypertrophy work and include target_reps. "
        "Use prescription_type='time' for static holds, timed mobility, timed cardio, and warmups, and include target_seconds. "
        "Do not invent extra properties beyond the schema. "
        "Use conservative, realistic, beginner-safe exercise selection when limitations are unclear."
    )


def build_program_generation_input(prompt_text: str, profile_context: dict, history_summary: dict | None, schema: dict) -> str:
    payload = {
        "task": "Generate a structured training program",
        "user_request": prompt_text,
        "profile_context": profile_context,
        "history_summary": history_summary,
        "schema_requirements": {
            "top_level_summary": {
                "required_fields": [
                    "version",
                    "program_name",
                    "goal_summary",
                    "duration_weeks",
                    "days_per_week",
                    "weight_unit",
                    "days",
                ],
                "day_types": ["training", "rest", "cardio", "mobility", "rehab"],
                "set_prescription_types": ["reps", "time"],
                "warmup_supported": True,
            },
            "json_schema": schema,
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def build_program_completion_instructions() -> str:
    return (
        "You are GymKompis, an assistant that completes partially-built gym programs. "
        "Return strict JSON only with no markdown, no prose, and no code fences. "
        "Return a complete program object that follows the requested schema exactly. "
        "Preserve the existing draft as much as possible and only substantially change the requested target days. "
        "Keep non-target days aligned with the incoming draft. "
        "Every exercise must include instructions, rest_seconds, and set_plan. "
        "Use realistic, beginner-safe, gym-appropriate programming."
    )


def build_program_completion_input(
    *,
    draft_snapshot: dict,
    target_day_keys: list[str],
    locked_day_keys: list[str] | None = None,
    locked_exercise_map: dict | None = None,
    profile_context: dict,
    history_summary: dict | None,
) -> str:
    payload = {
        "task": "Complete selected days inside an existing editable training program draft",
        "target_day_keys": target_day_keys,
        "locked_day_keys": locked_day_keys or [],
        "locked_exercises_by_day": locked_exercise_map or {},
        "profile_context": profile_context,
        "history_summary": history_summary,
        "current_draft": draft_snapshot,
        "requirements": {
            "preserve_non_target_days": True,
            "preserve_locked_days": True,
            "preserve_locked_exercises": True,
            "return_full_program_json": True,
            "keep_top_level_metadata_consistent": True,
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def build_program_evaluation_instructions() -> str:
    return (
        "You are GymKompis, an assistant that evaluates editable gym program drafts. "
        "Return strict JSON only with no markdown, no prose, and no code fences. "
        "Do not rewrite the program. Only evaluate it. "
        "Return an object with summary, findings, and suggested_actions. "
        "Each finding should include severity, type, target, message, and suggested_fix."
    )


def build_program_evaluation_input(*, draft_snapshot: dict, profile_context: dict, history_summary: dict | None) -> str:
    payload = {
        "task": "Evaluate an editable training program draft without mutating it",
        "profile_context": profile_context,
        "history_summary": history_summary,
        "current_draft": draft_snapshot,
        "required_output": {
            "summary": "string",
            "findings": [
                {
                    "severity": "high|medium|low",
                    "type": "string",
                    "target": "draft or day key",
                    "message": "string",
                    "suggested_fix": "string",
                }
            ],
            "suggested_actions": [
                {
                    "action_type": "string",
                    "reason": "string",
                }
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False)
