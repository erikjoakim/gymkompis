from jsonschema import Draft202012Validator


SESSION_EVALUATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "version",
        "evaluation_type",
        "session_id",
        "overall_summary",
        "adherence_score",
        "effort_summary",
        "recovery_flag",
        "progression_signals",
        "recommendations",
    ],
    "properties": {
        "version": {"type": "integer", "const": 1},
        "evaluation_type": {"type": "string", "const": "session"},
        "session_id": {"type": "integer", "minimum": 1},
        "overall_summary": {"type": "string", "minLength": 1, "maxLength": 1000},
        "adherence_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "effort_summary": {"type": "string", "maxLength": 500},
        "recovery_flag": {"type": "string", "enum": ["none", "low", "moderate", "high"]},
        "progression_signals": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
        "exercise_feedback": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["exercise_key", "comment"],
                "properties": {
                    "exercise_key": {"type": "string"},
                    "comment": {"type": "string", "maxLength": 400},
                    "suggested_next_step": {"type": "string", "maxLength": 200},
                },
            },
        },
        "recommendations": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "string"}},
    },
}

PERIOD_EVALUATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["version", "evaluation_type", "evaluation_scope", "summary", "highlights", "recommendations"],
    "properties": {
        "version": {"type": "integer", "const": 1},
        "evaluation_type": {"type": "string", "const": "period"},
        "evaluation_scope": {
            "type": "object",
            "additionalProperties": False,
            "required": ["scope_type", "session_ids"],
            "properties": {
                "scope_type": {"type": "string", "enum": ["date_range", "session_list"]},
                "start_date": {"type": ["string", "null"]},
                "end_date": {"type": ["string", "null"]},
                "session_ids": {"type": "array", "items": {"type": "integer"}},
            },
        },
        "summary": {
            "type": "object",
            "additionalProperties": False,
            "required": ["overall_adherence_score", "consistency_score", "progression_score", "recovery_risk"],
            "properties": {
                "overall_adherence_score": {"type": "integer", "minimum": 0, "maximum": 100},
                "consistency_score": {"type": "integer", "minimum": 0, "maximum": 100},
                "progression_score": {"type": "integer", "minimum": 0, "maximum": 100},
                "recovery_risk": {"type": "string", "enum": ["none", "low", "moderate", "high"]},
            },
        },
        "highlights": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
        "exercise_trends": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["exercise_key", "name", "trend", "note"],
                "properties": {
                    "exercise_key": {"type": "string"},
                    "name": {"type": "string"},
                    "trend": {"type": "string", "enum": ["improving", "stable", "declining", "mixed"]},
                    "note": {"type": "string", "maxLength": 300},
                },
            },
        },
        "issues": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "string"}},
    },
}


def validate_session_evaluation(data: dict) -> None:
    Draft202012Validator(SESSION_EVALUATION_SCHEMA).validate(data)


def validate_period_evaluation(data: dict) -> None:
    Draft202012Validator(PERIOD_EVALUATION_SCHEMA).validate(data)
