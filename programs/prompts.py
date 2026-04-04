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
