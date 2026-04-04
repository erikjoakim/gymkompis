import re
from datetime import timedelta
from statistics import mean

from django.db.models import Q
from django.utils import timezone

from programs.models import Exercise

from .models import WorkoutSession


ROLLING_WINDOW_SESSIONS = 8
ROLLING_WINDOW_DAYS = 60
LOW_RPE_THRESHOLD = 7.5
HIGH_RPE_THRESHOLD = 9.25
MIN_SIMILARITY_SCORE = 0.55
NAME_TOKEN_STOPWORDS = {
    "and",
    "the",
    "with",
    "on",
    "to",
    "a",
    "an",
}

LOWER_BODY_TOKENS = {
    "squat",
    "leg",
    "glute",
    "hamstring",
    "quad",
    "calf",
    "lunge",
    "hip",
    "deadlift",
    "hinge",
    "rdl",
    "split",
    "step",
}
UPPER_BODY_TOKENS = {
    "chest",
    "press",
    "bench",
    "row",
    "pulldown",
    "pull",
    "shoulder",
    "lat",
    "biceps",
    "triceps",
    "curl",
    "fly",
}
CORE_TOKENS = {
    "plank",
    "twist",
    "hollow",
    "core",
    "ab",
    "situp",
    "sit-up",
    "crunch",
    "carry",
}

MODALITY_FAMILIES = {
    "barbell": "loaded_freeweight",
    "dumbbell": "loaded_freeweight",
    "kettlebell": "loaded_freeweight",
    "machine": "guided_load",
    "cable": "guided_load",
    "band": "assistance_resistance",
    "bodyweight": "body_control",
    "mobility": "body_control",
    "cardio": "body_control",
    "other": "other",
}


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (name or "").lower())).strip()


def _name_tokens(name: str) -> set[str]:
    return {
        token
        for token in _normalize_name(name).split()
        if len(token) > 1 and token not in NAME_TOKEN_STOPWORDS
    }


def _modality_family(modality: str | None) -> str:
    return MODALITY_FAMILIES.get((modality or "").lower(), "other")


def _muscle_set(values) -> set[str]:
    if isinstance(values, (list, tuple)):
        return {_normalize_name(item) for item in values if item}
    if isinstance(values, str):
        return {_normalize_name(item) for item in values.split(",") if item.strip()}
    return set()


def _infer_region(name: str, movement_pattern: str = "", primary_muscles=None) -> str:
    tokens = _name_tokens(f"{name} {movement_pattern}")
    muscles = _muscle_set(primary_muscles)
    if tokens & LOWER_BODY_TOKENS or muscles & {"quadriceps", "hamstrings", "glutes", "calves", "adductors"}:
        return "lower"
    if tokens & CORE_TOKENS or muscles & {"abdominals", "core", "obliques", "lower back"}:
        return "core"
    if tokens & UPPER_BODY_TOKENS or muscles & {"chest", "back", "lats", "shoulders", "biceps", "triceps"}:
        return "upper"
    return "general"


def _lookup_library_exercise(exercise_key: str | None, name: str | None):
    query = Q()
    if exercise_key:
        query |= Q(external_id__iexact=exercise_key) | Q(external_id__iexact=exercise_key.replace("_", "-"))
    if name:
        query |= Q(name__iexact=name)
    if not query:
        return None
    return Exercise.objects.filter(is_active=True).filter(query).first()


def _exercise_metadata(exercise_data: dict) -> dict:
    library_exercise = _lookup_library_exercise(exercise_data.get("exercise_key"), exercise_data.get("name"))
    if library_exercise:
        movement_pattern = library_exercise.movement_pattern or ""
        primary_muscles = library_exercise.primary_muscles or []
        return {
            "name": library_exercise.name,
            "exercise_key": exercise_data.get("exercise_key") or library_exercise.exercise_key,
            "modality": exercise_data.get("modality") or library_exercise.modality,
            "movement_pattern": movement_pattern,
            "primary_muscles": primary_muscles,
            "is_static": library_exercise.is_static,
            "supports_time": library_exercise.supports_time,
            "supports_reps": library_exercise.supports_reps,
            "region": _infer_region(library_exercise.name, movement_pattern, primary_muscles),
        }

    movement_pattern = exercise_data.get("movement_pattern", "") or ""
    focus = exercise_data.get("focus", "")
    return {
        "name": exercise_data.get("name", ""),
        "exercise_key": exercise_data.get("exercise_key", ""),
        "modality": exercise_data.get("modality", ""),
        "movement_pattern": movement_pattern,
        "primary_muscles": [item.strip() for item in focus.split(",") if item.strip()],
        "is_static": bool(exercise_data.get("is_static")),
        "supports_time": any(item.get("prescription_type") == "time" for item in exercise_data.get("set_plan", [])),
        "supports_reps": any(item.get("prescription_type") == "reps" for item in exercise_data.get("set_plan", [])),
        "region": _infer_region(exercise_data.get("name", ""), movement_pattern, focus),
    }


def _parse_target_reps(target_reps: str | None) -> tuple[int | None, int | None]:
    if not target_reps:
        return None, None
    values = [int(item) for item in re.findall(r"\d+", target_reps)]
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], values[0]
    return min(values), max(values)


def _loaded_increment(modality: str | None, region: str, weight_unit: str) -> float | None:
    unit = (weight_unit or "kg").lower()
    if (modality or "").lower() == "barbell":
        return 5.0 if region == "lower" and unit == "kg" else 10.0 if region == "lower" else 2.5 if unit == "kg" else 5.0
    if (modality or "").lower() == "dumbbell":
        return 2.0 if unit == "kg" else 5.0
    if (modality or "").lower() == "kettlebell":
        return 4.0 if unit == "kg" else 9.0
    if (modality or "").lower() == "machine":
        return 5.0 if unit == "kg" else 10.0
    if (modality or "").lower() == "cable":
        return 2.5 if unit == "kg" else 5.0
    return None


def _round_to_step(value: float, step: float | None) -> float:
    if not step:
        return round(value, 2)
    rounded = round(value / step) * step
    return round(rounded, 2)


def _summarize_actual_sets(actual_sets: list[dict]) -> dict:
    completed_sets = [item for item in actual_sets if item.get("completed")]
    reps_values = [item["reps"] for item in completed_sets if item.get("reps") is not None]
    seconds_values = [item["seconds"] for item in completed_sets if item.get("seconds") is not None]
    weight_values = [float(item["weight"]) for item in completed_sets if item.get("weight") is not None]
    rpe_values = [float(item["effort_rpe"]) for item in completed_sets if item.get("effort_rpe") is not None]
    return {
        "completed_sets": len(completed_sets),
        "avg_reps": mean(reps_values) if reps_values else None,
        "min_reps": min(reps_values) if reps_values else None,
        "max_reps": max(reps_values) if reps_values else None,
        "avg_seconds": mean(seconds_values) if seconds_values else None,
        "max_seconds": max(seconds_values) if seconds_values else None,
        "avg_weight": mean(weight_values) if weight_values else None,
        "avg_rpe": mean(rpe_values) if rpe_values else None,
        "final_rpe": rpe_values[-1] if rpe_values else None,
    }


def _history_records(user, weight_unit: str, current_session_id: int | None = None) -> list[dict]:
    cutoff_date = timezone.localdate() - timedelta(days=ROLLING_WINDOW_DAYS)
    sessions = list(
        WorkoutSession.objects.filter(
            user=user,
            status=WorkoutSession.Status.COMPLETED,
            workout_date__gte=cutoff_date,
        )
        .exclude(pk=current_session_id)
        .order_by("-workout_date", "-completed_at")[:ROLLING_WINDOW_SESSIONS]
    )
    records = []
    for session in sessions:
        data = session.session_json or {}
        if data.get("weight_unit") and data.get("weight_unit") != weight_unit:
            continue
        for exercise in data.get("exercises", []):
            actual_sets = [item for item in exercise.get("actual_sets", []) if item.get("completed")]
            if not actual_sets:
                continue
            records.append(
                {
                    "exercise_key": exercise.get("exercise_key"),
                    "name": exercise.get("name"),
                    "modality": exercise.get("modality"),
                    "session_date": session.workout_date,
                    "planned_set_count": len(exercise.get("planned", {}).get("set_plan", [])),
                    "summary": _summarize_actual_sets(actual_sets),
                    "metadata": _exercise_metadata(exercise),
                }
            )
    return records


def _similarity_score(current_meta: dict, history_record: dict) -> tuple[float, str]:
    history_meta = history_record["metadata"]
    current_key = current_meta.get("exercise_key")
    history_key = history_record.get("exercise_key")
    current_name = current_meta.get("name", "")
    history_name = history_record.get("name", "")

    if current_key and history_key and current_key == history_key:
        return 1.0, "exact"
    if _normalize_name(current_name) and _normalize_name(current_name) == _normalize_name(history_name):
        return 0.95, "exact"

    current_tokens = _name_tokens(current_name)
    history_tokens = _name_tokens(history_name)
    token_overlap = (
        len(current_tokens & history_tokens) / len(current_tokens | history_tokens)
        if current_tokens and history_tokens
        else 0
    )
    muscle_overlap = 0
    current_muscles = _muscle_set(current_meta.get("primary_muscles"))
    history_muscles = _muscle_set(history_meta.get("primary_muscles"))
    if current_muscles and history_muscles:
        muscle_overlap = len(current_muscles & history_muscles) / len(current_muscles | history_muscles)

    score = 0.0
    if current_meta.get("movement_pattern") and current_meta.get("movement_pattern") == history_meta.get("movement_pattern"):
        score += 0.45
    if current_meta.get("region") == history_meta.get("region"):
        score += 0.15
    if current_meta.get("modality") == history_record.get("modality"):
        score += 0.2
    elif _modality_family(current_meta.get("modality")) == _modality_family(history_record.get("modality")):
        score += 0.1
    score += token_overlap * 0.15
    score += muscle_overlap * 0.25
    return score, "similar"


def _choose_best_match(current_exercise: dict, history_records: list[dict]) -> dict | None:
    current_meta = _exercise_metadata(current_exercise)
    candidates = []
    for record in history_records:
        score, match_type = _similarity_score(current_meta, record)
        if match_type == "exact" or score >= MIN_SIMILARITY_SCORE:
            candidates.append(
                {
                    "score": score,
                    "match_type": match_type,
                    "record": record,
                }
            )
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            1 if item["match_type"] == "exact" else 0,
            item["score"],
            item["record"]["session_date"],
        ),
        reverse=True,
    )
    return candidates[0]


def _recommend_time_progression(current_exercise: dict, match: dict | None) -> dict:
    planned_target = max(
        [item.get("target_seconds") or 0 for item in current_exercise.get("set_plan", []) if item.get("prescription_type") == "time"],
        default=0,
    )
    planned_set_count = len(current_exercise.get("set_plan", []))
    current_meta = _exercise_metadata(current_exercise)
    increment = 10 if current_meta.get("is_static") else 5
    if not match:
        return {
            "match_type": "none",
            "confidence": "none",
            "suggested_weight": None,
            "suggested_seconds": planned_target or None,
            "suggested_target_rpe": max(
                [item.get("target_effort_rpe") for item in current_exercise.get("set_plan", []) if item.get("target_effort_rpe")],
                default=None,
            ),
            "guidance_text": "Use the planned time target and adjust by feel.",
            "short_guidance": "No match",
            "reason": "No comparable completed sessions were found in the current rolling window.",
        }

    summary = match["record"]["summary"]
    latest_seconds = int(round(summary["max_seconds"] or planned_target or 0))
    final_rpe = summary["final_rpe"]
    completed_full_work = summary["completed_sets"] >= max(1, planned_set_count)
    if latest_seconds and completed_full_work and final_rpe is not None and latest_seconds >= planned_target and final_rpe <= LOW_RPE_THRESHOLD:
        suggested_seconds = latest_seconds + increment
        action_text = "Increase hold time slightly."
    elif latest_seconds and (not completed_full_work or (final_rpe is not None and final_rpe > HIGH_RPE_THRESHOLD)):
        suggested_seconds = max(planned_target, latest_seconds)
        action_text = "Stay at a similar duration today."
    else:
        suggested_seconds = max(planned_target, latest_seconds)
        action_text = "Repeat a similar duration."

    return {
        "match_type": match["match_type"],
        "confidence": "high" if match["match_type"] == "exact" else "medium",
        "suggested_weight": None,
        "suggested_seconds": suggested_seconds or None,
        "suggested_target_rpe": max(
            [item.get("target_effort_rpe") for item in current_exercise.get("set_plan", []) if item.get("target_effort_rpe")],
            default=None,
        ),
        "guidance_text": f"{action_text} Suggested time: {suggested_seconds} sec.",
        "short_guidance": f"{suggested_seconds} sec" if suggested_seconds else "-",
        "reason": f"Recent {match['match_type']} match on {match['record']['session_date'].isoformat()} reached about {latest_seconds} sec.",
    }


def _recommend_reps_progression(current_exercise: dict, match: dict | None, weight_unit: str) -> dict:
    target_reps = next((item.get("target_reps") for item in current_exercise.get("set_plan", []) if item.get("target_reps")), "")
    target_min, target_max = _parse_target_reps(target_reps)
    planned_set_count = len(current_exercise.get("set_plan", []))
    target_rpe = max(
        [item.get("target_effort_rpe") for item in current_exercise.get("set_plan", []) if item.get("target_effort_rpe")],
        default=None,
    )
    current_meta = _exercise_metadata(current_exercise)

    if not match:
        return {
            "match_type": "none",
            "confidence": "none",
            "suggested_weight": None,
            "suggested_seconds": None,
            "suggested_target_rpe": target_rpe,
            "guidance_text": "No recent match yet. Start conservatively and work around the target RPE.",
            "short_guidance": "No match",
            "reason": "No comparable completed sessions were found in the current rolling window.",
        }

    summary = match["record"]["summary"]
    avg_weight = summary["avg_weight"]
    min_reps = summary["min_reps"]
    final_rpe = summary["final_rpe"]
    completed_full_work = summary["completed_sets"] >= max(1, planned_set_count)
    increment = _loaded_increment(current_exercise.get("modality"), current_meta.get("region"), weight_unit)
    same_modality = current_exercise.get("modality") == match["record"].get("modality")

    suggested_weight = None
    if avg_weight is not None and increment is not None and (match["match_type"] == "exact" or same_modality):
        if completed_full_work and target_max and min_reps is not None and min_reps >= target_max and (final_rpe is None or final_rpe <= LOW_RPE_THRESHOLD):
            suggested_weight = _round_to_step(avg_weight + increment, increment)
            action_text = f"Increase the load by about {increment:g} {weight_unit}."
        elif not completed_full_work or (target_min and min_reps is not None and (min_reps < target_min or (final_rpe is not None and final_rpe > HIGH_RPE_THRESHOLD))):
            suggested_weight = max(0, _round_to_step(avg_weight - increment, increment))
            action_text = f"Reduce slightly and rebuild quality reps."
        else:
            suggested_weight = _round_to_step(avg_weight, increment)
            action_text = "Stay near your recent working load."
    elif avg_weight is not None and match["match_type"] == "exact":
        suggested_weight = round(avg_weight, 2)
        action_text = "Repeat the recent working load."
    else:
        action_text = "Use your recent comparable work as a conservative starting point."

    confidence = "high" if match["match_type"] == "exact" else "low" if not same_modality else "medium"
    guidance_text = action_text
    if suggested_weight is not None:
        guidance_text = f"{action_text} Suggested load: {suggested_weight:g} {weight_unit}."

    reason_bits = [f"Recent {match['match_type']} match on {match['record']['session_date'].isoformat()}"]
    if avg_weight is not None:
        reason_bits.append(f"avg load {avg_weight:.1f} {weight_unit}")
    if min_reps is not None:
        reason_bits.append(f"minimum reps {min_reps}")
    if final_rpe is not None:
        reason_bits.append(f"final RPE {final_rpe:.1f}")

    return {
        "match_type": match["match_type"],
        "confidence": confidence,
        "suggested_weight": suggested_weight,
        "suggested_seconds": None,
        "suggested_target_rpe": target_rpe,
        "guidance_text": guidance_text,
        "short_guidance": f"{suggested_weight:g} {weight_unit}" if suggested_weight is not None else "-",
        "reason": ", ".join(reason_bits) + ".",
    }


def recommendation_for_exercise(user, current_exercise: dict, weight_unit: str, current_session_id: int | None = None) -> dict:
    history_records = _history_records(user, weight_unit, current_session_id=current_session_id)
    match = _choose_best_match(current_exercise, history_records)
    if any(item.get("prescription_type") == "time" for item in current_exercise.get("set_plan", [])):
        return _recommend_time_progression(current_exercise, match)
    return _recommend_reps_progression(current_exercise, match, weight_unit)


def build_progression_recommendations(user, exercises: list[dict], weight_unit: str, current_session_id: int | None = None) -> dict:
    history_records = _history_records(user, weight_unit, current_session_id=current_session_id)
    recommendations = {}
    for exercise in exercises:
        match = _choose_best_match(exercise, history_records)
        if any(item.get("prescription_type") == "time" for item in exercise.get("set_plan", [])):
            recommendations[exercise["exercise_key"]] = _recommend_time_progression(exercise, match)
        else:
            recommendations[exercise["exercise_key"]] = _recommend_reps_progression(exercise, match, weight_unit)
    return recommendations
