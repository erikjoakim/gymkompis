import copy

from jsonschema import Draft202012Validator, FormatChecker


CURRENT_PROGRAM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "version",
        "program_name",
        "goal_summary",
        "duration_weeks",
        "days_per_week",
        "weight_unit",
        "days",
    ],
    "properties": {
        "version": {"type": "integer", "const": 1},
        "program_name": {"type": "string", "minLength": 1, "maxLength": 120},
        "goal_summary": {"type": "string", "minLength": 1, "maxLength": 500},
        "duration_weeks": {"type": "integer", "minimum": 1, "maximum": 24},
        "days_per_week": {"type": "integer", "minimum": 1, "maximum": 7},
        "weight_unit": {"type": "string", "enum": ["kg", "lb"]},
        "program_notes": {"type": "string", "maxLength": 1000},
        "days": {
            "type": "array",
            "minItems": 1,
            "maxItems": 7,
            "items": {"$ref": "#/$defs/day"},
        },
    },
    "$defs": {
        "day": {
            "type": "object",
            "additionalProperties": False,
            "required": ["day_key", "day_label", "name", "type", "exercises"],
            "properties": {
                "day_key": {
                    "type": "string",
                    "enum": [
                        "monday",
                        "tuesday",
                        "wednesday",
                        "thursday",
                        "friday",
                        "saturday",
                        "sunday",
                    ],
                },
                "day_label": {"type": "string"},
                "name": {"type": "string", "minLength": 1, "maxLength": 120},
                "type": {"type": "string", "enum": ["training", "rest", "cardio", "mobility", "rehab"]},
                "notes": {"type": "string", "maxLength": 500},
                "exercises": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/exercise"},
                },
            },
        },
        "exercise": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "exercise_key",
                "name",
                "order",
                "modality",
                "instructions",
                "video_url",
                "rest_seconds",
                "set_plan",
            ],
            "properties": {
                "exercise_key": {"type": "string", "pattern": r"^[a-z0-9_-]+$", "maxLength": 80},
                "name": {"type": "string", "minLength": 1, "maxLength": 120},
                "order": {"type": "integer", "minimum": 1},
                "modality": {
                    "type": "string",
                    "enum": [
                        "barbell",
                        "dumbbell",
                        "machine",
                        "bodyweight",
                        "cable",
                        "kettlebell",
                        "band",
                        "mobility",
                        "cardio",
                        "other",
                    ],
                },
                "focus": {"type": "string", "maxLength": 300},
                "instructions": {"type": "string", "minLength": 1, "maxLength": 1000},
                "video_url": {"type": ["string", "null"], "format": "uri", "maxLength": 500},
                "rest_seconds": {"type": "integer", "minimum": 0, "maximum": 600},
                "notes": {"type": "string", "maxLength": 500},
                "set_plan": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 10,
                    "items": {"$ref": "#/$defs/set_plan"},
                },
            },
        },
        "set_plan": {
            "type": "object",
            "additionalProperties": False,
            "required": ["set_number", "target_reps"],
            "properties": {
                "set_number": {"type": "integer", "minimum": 1, "maximum": 20},
                "target_reps": {"type": "string", "minLength": 1, "maxLength": 20},
                "load_guidance": {"type": "string", "maxLength": 100},
                "target_effort_rpe": {"type": ["number", "null"], "minimum": 1, "maximum": 10},
            },
        },
    },
}

HISTORY_SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["version", "session_count", "date_range", "adherence_summary", "exercise_trends"],
    "properties": {
        "version": {"type": "integer", "const": 1},
        "session_count": {"type": "integer", "minimum": 0, "maximum": 100},
        "date_range": {
            "type": "object",
            "additionalProperties": False,
            "required": ["start_date", "end_date"],
            "properties": {
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
            },
        },
        "adherence_summary": {
            "type": "object",
            "additionalProperties": False,
            "required": ["completed_sessions", "skipped_sessions"],
            "properties": {
                "completed_sessions": {"type": "integer", "minimum": 0},
                "skipped_sessions": {"type": "integer", "minimum": 0},
                "average_effort_rpe": {"type": ["number", "null"], "minimum": 1, "maximum": 10},
            },
        },
        "exercise_trends": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["exercise_key", "name"],
                "properties": {
                    "exercise_key": {"type": "string"},
                    "name": {"type": "string"},
                    "best_recent_weight": {"type": ["number", "null"]},
                    "best_recent_reps": {"type": ["integer", "null"]},
                    "trend_note": {"type": "string", "maxLength": 300},
                },
            },
        },
        "reported_issues": {
            "type": "array",
            "items": {"type": "string", "maxLength": 300},
        },
    },
}

_FORMAT_CHECKER = FormatChecker()


def validate_current_program(data: dict) -> None:
    Draft202012Validator(CURRENT_PROGRAM_SCHEMA, format_checker=_FORMAT_CHECKER).validate(data)


def validate_history_summary(data: dict) -> None:
    Draft202012Validator(HISTORY_SUMMARY_SCHEMA, format_checker=_FORMAT_CHECKER).validate(data)


def sample_program(weight_unit: str = "kg") -> dict:
    return {
        "version": 1,
        "program_name": "GymKompis Starter Strength",
        "goal_summary": "Build full-body strength and consistency with manageable sessions.",
        "duration_weeks": 8,
        "days_per_week": 3,
        "weight_unit": weight_unit,
        "program_notes": "Focus on controlled reps and consistent attendance.",
        "days": [
            {
                "day_key": "monday",
                "day_label": "Monday",
                "name": "Full Body A",
                "type": "training",
                "notes": "Start each movement with 1-2 lighter warmup sets.",
                "exercises": [
                    {
                        "exercise_key": "leg_press_machine",
                        "name": "Leg Press (Machine)",
                        "order": 1,
                        "modality": "machine",
                        "focus": "Leg strength",
                        "instructions": "Drive through the whole foot and avoid locking the knees.",
                        "video_url": "https://www.youtube.com/watch?v=IZxyjW7MPJQ",
                        "rest_seconds": 90,
                        "notes": "",
                        "set_plan": [
                            {
                                "set_number": 1,
                                "target_reps": "10-12",
                                "load_guidance": "Light to moderate",
                                "target_effort_rpe": 7,
                            },
                            {
                                "set_number": 2,
                                "target_reps": "10-12",
                                "load_guidance": "Light to moderate",
                                "target_effort_rpe": 7,
                            },
                            {
                                "set_number": 3,
                                "target_reps": "10-12",
                                "load_guidance": "Moderate",
                                "target_effort_rpe": 8,
                            },
                        ],
                    },
                    {
                        "exercise_key": "chest_press_machine",
                        "name": "Chest Press (Machine)",
                        "order": 2,
                        "modality": "machine",
                        "focus": "Upper body push strength",
                        "instructions": "Keep your shoulders down and press in a smooth arc.",
                        "video_url": "https://www.youtube.com/watch?v=igD7slG0QVU",
                        "rest_seconds": 75,
                        "notes": "",
                        "set_plan": [
                            {
                                "set_number": 1,
                                "target_reps": "8-10",
                                "load_guidance": "Moderate",
                                "target_effort_rpe": 7,
                            },
                            {
                                "set_number": 2,
                                "target_reps": "8-10",
                                "load_guidance": "Moderate",
                                "target_effort_rpe": 7,
                            },
                            {
                                "set_number": 3,
                                "target_reps": "8-10",
                                "load_guidance": "Moderate",
                                "target_effort_rpe": 8,
                            },
                        ],
                    },
                ],
            },
            {
                "day_key": "wednesday",
                "day_label": "Wednesday",
                "name": "Mobility + Cardio",
                "type": "mobility",
                "notes": "Walk 20-30 minutes and add gentle mobility work.",
                "exercises": [],
            },
            {
                "day_key": "friday",
                "day_label": "Friday",
                "name": "Full Body B",
                "type": "training",
                "notes": "Aim to finish within 60 minutes.",
                "exercises": [
                    {
                        "exercise_key": "lat_pulldown",
                        "name": "Lat Pulldown",
                        "order": 1,
                        "modality": "machine",
                        "focus": "Upper back strength",
                        "instructions": "Pull elbows down toward your sides without leaning back excessively.",
                        "video_url": "https://www.youtube.com/watch?v=CAwf7n6Luuc",
                        "rest_seconds": 75,
                        "notes": "",
                        "set_plan": [
                            {
                                "set_number": 1,
                                "target_reps": "10-12",
                                "load_guidance": "Moderate",
                                "target_effort_rpe": 7,
                            },
                            {
                                "set_number": 2,
                                "target_reps": "10-12",
                                "load_guidance": "Moderate",
                                "target_effort_rpe": 8,
                            },
                        ],
                    },
                    {
                        "exercise_key": "goblet_squat",
                        "name": "Goblet Squat",
                        "order": 2,
                        "modality": "dumbbell",
                        "focus": "Leg and core control",
                        "instructions": "Keep your chest tall and sit between your hips.",
                        "video_url": "https://www.youtube.com/watch?v=MeIiIdhvXT4",
                        "rest_seconds": 90,
                        "notes": "",
                        "set_plan": [
                            {
                                "set_number": 1,
                                "target_reps": "8-10",
                                "load_guidance": "Moderate",
                                "target_effort_rpe": 7,
                            },
                            {
                                "set_number": 2,
                                "target_reps": "8-10",
                                "load_guidance": "Moderate",
                                "target_effort_rpe": 8,
                            },
                        ],
                    },
                ],
            },
        ],
    }


def clone_sample_program(weight_unit: str = "kg") -> dict:
    return copy.deepcopy(sample_program(weight_unit))
