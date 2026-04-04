from copy import deepcopy


def infer_prescription_type(set_item: dict) -> str:
    if set_item.get("prescription_type") in {"reps", "time"}:
        return set_item["prescription_type"]
    if set_item.get("target_seconds") is not None:
        return "time"
    return "reps"


def normalize_set_plan_item(set_item: dict) -> dict:
    normalized = deepcopy(set_item)
    prescription_type = infer_prescription_type(normalized)
    normalized["prescription_type"] = prescription_type
    if prescription_type == "reps":
        normalized.setdefault("target_reps", "")
        normalized.setdefault("target_seconds", None)
    else:
        normalized.setdefault("target_seconds", 0)
        normalized.setdefault("target_reps", None)
    normalized.setdefault("load_guidance", "")
    normalized.setdefault("target_effort_rpe", None)
    return normalized


def normalize_exercise(exercise: dict, exercise_group: str = "main") -> dict:
    normalized = deepcopy(exercise)
    normalized["exercise_group"] = exercise_group
    normalized["set_plan"] = [normalize_set_plan_item(item) for item in normalized.get("set_plan", [])]
    return normalized


def get_day_warmup(day: dict) -> list[dict]:
    return [normalize_exercise(exercise, "warmup") for exercise in day.get("warmup", [])]


def get_day_main_exercises(day: dict) -> list[dict]:
    return [normalize_exercise(exercise, "main") for exercise in day.get("exercises", [])]


def get_day_all_exercises(day: dict) -> list[dict]:
    return [*get_day_warmup(day), *get_day_main_exercises(day)]


def get_day_blocks(day: dict) -> list[dict]:
    warmup = get_day_warmup(day)
    main = get_day_main_exercises(day)
    blocks = []
    if warmup:
        blocks.append({"key": "warmup", "label": "Warmup", "items": warmup})
    if main:
        blocks.append({"key": "main", "label": "Main Work", "items": main})
    return blocks
