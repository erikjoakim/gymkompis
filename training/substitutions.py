from collections import Counter

from programs.models import Exercise
from programs.structure import infer_prescription_type


MODALITY_FAMILIES = {
    "barbell": "loaded_freeweight",
    "dumbbell": "loaded_freeweight",
    "kettlebell": "loaded_freeweight",
    "machine": "guided_load",
    "cable": "guided_load",
    "band": "assistance_resistance",
    "bodyweight": "body_control",
    "mobility": "body_control",
    "cardio": "conditioning",
    "other": "other",
}

NAME_TOKEN_STOPWORDS = {
    "and",
    "the",
    "with",
    "on",
    "to",
    "a",
    "an",
}


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").lower().replace("-", " ").replace("_", " ").split())


def _tokenize(value: str | None) -> set[str]:
    return {
        token
        for token in _normalize_text(value).split()
        if len(token) > 1 and token not in NAME_TOKEN_STOPWORDS
    }


def _modality_family(modality: str | None) -> str:
    return MODALITY_FAMILIES.get(_normalize_text(modality), "other")


def _muscle_set(values) -> set[str]:
    if isinstance(values, str):
        return {item.strip().lower() for item in values.split(",") if item.strip()}
    if isinstance(values, (list, tuple)):
        return {str(item).strip().lower() for item in values if str(item).strip()}
    return set()


def _current_prescription_type(exercise: dict) -> str:
    for set_item in exercise.get("set_plan", []):
        return infer_prescription_type(set_item)
    return "reps"


def _equipment_keywords(text: str | None) -> set[str]:
    raw = _normalize_text(text)
    if not raw:
        return set()
    tokens = set(raw.split())
    if "full gym" in raw or "commercial gym" in raw or "gym access" in raw:
        tokens.add("full_gym")
    if "home gym" in raw or "home" in raw:
        tokens.add("home")
    if "bodyweight" in raw:
        tokens.add("bodyweight")
    return tokens


def _equipment_allowed(user, exercise: Exercise) -> bool:
    access = _equipment_keywords(getattr(user.profile, "equipment_access", ""))
    if not access or "full_gym" in access or "gym" in access:
        return True

    modality = (exercise.modality or "").lower()
    if modality == Exercise.Modality.BODYWEIGHT:
        return True
    if modality == Exercise.Modality.BAND and "band" in access:
        return True
    if modality == Exercise.Modality.DUMBBELL and "dumbbell" in access:
        return True
    if modality == Exercise.Modality.KETTLEBELL and "kettlebell" in access:
        return True
    if modality == Exercise.Modality.CARDIO and any(token in access for token in {"bike", "treadmill", "rower", "cardio"}):
        return True
    if "home" in access and modality in {Exercise.Modality.BODYWEIGHT, Exercise.Modality.BAND, Exercise.Modality.DUMBBELL, Exercise.Modality.KETTLEBELL}:
        return True
    return False


def _current_exercise_fingerprint(exercise: dict) -> dict:
    return {
        "exercise_key": exercise.get("exercise_key"),
        "name": exercise.get("name", ""),
        "modality": exercise.get("modality", ""),
        "exercise_group": exercise.get("exercise_group", "main"),
        "movement_pattern": exercise.get("movement_pattern", ""),
        "primary_muscles": _muscle_set(exercise.get("primary_muscles") or exercise.get("focus")),
        "category": _normalize_text(exercise.get("category")),
        "prescription_type": _current_prescription_type(exercise),
    }


def _candidate_fingerprint(exercise: Exercise) -> dict:
    return {
        "exercise_key": exercise.exercise_key,
        "name": exercise.name,
        "modality": exercise.modality,
        "movement_pattern": exercise.movement_pattern or "",
        "primary_muscles": _muscle_set(exercise.primary_muscles),
        "category": _normalize_text(exercise.category),
    }


def _is_role_compatible(current_group: str, candidate: Exercise) -> bool:
    if current_group == "warmup":
        return candidate.library_role in {Exercise.LibraryRole.WARMUP, Exercise.LibraryRole.BOTH}
    return candidate.library_role in {Exercise.LibraryRole.MAIN, Exercise.LibraryRole.BOTH}


def _supports_prescription(candidate: Exercise, prescription_type: str) -> bool:
    if prescription_type == "time":
        return candidate.supports_time
    return candidate.supports_reps


def _score_candidate(current: dict, candidate: Exercise) -> tuple[float, str]:
    candidate_meta = _candidate_fingerprint(candidate)
    score = 0.0
    reasons = []

    if current.get("movement_pattern") and current["movement_pattern"] == candidate_meta["movement_pattern"]:
        score += 0.42
        reasons.append("same movement pattern")

    muscle_overlap = 0.0
    current_muscles = current.get("primary_muscles", set())
    candidate_muscles = candidate_meta["primary_muscles"]
    if current_muscles and candidate_muscles:
        muscle_overlap = len(current_muscles & candidate_muscles) / len(current_muscles | candidate_muscles)
        if muscle_overlap:
            score += muscle_overlap * 0.28
            reasons.append("targets similar muscles")

    current_modality = _normalize_text(current.get("modality"))
    candidate_modality = _normalize_text(candidate.modality)
    if current_modality == candidate_modality:
        score += 0.16
        reasons.append("same modality")
    elif _modality_family(current_modality) == _modality_family(candidate_modality):
        score += 0.08
        reasons.append("similar equipment style")

    name_overlap = 0.0
    current_tokens = _tokenize(current.get("name"))
    candidate_tokens = _tokenize(candidate.name)
    if current_tokens and candidate_tokens:
        name_overlap = len(current_tokens & candidate_tokens) / len(current_tokens | candidate_tokens)
        score += name_overlap * 0.1

    if current.get("category") and current["category"] == candidate_meta["category"]:
        score += 0.04

    if candidate.instructions:
        score += 0.03

    if candidate.default_video_url:
        score += 0.02

    if not reasons and name_overlap:
        reasons.append("similar movement naming")

    primary_reason = reasons[0] if reasons else "compatible alternative"
    return score, primary_reason


def suggest_substitutions(user, current_exercise: dict, *, excluded_keys: set[str] | None = None, limit: int = 4) -> list[dict]:
    current = _current_exercise_fingerprint(current_exercise)
    excluded = {item for item in (excluded_keys or set()) if item}
    current_name_normalized = _normalize_text(current.get("name"))
    candidates = []

    queryset = Exercise.objects.filter(is_active=True)
    for candidate in queryset:
        candidate_key = candidate.exercise_key
        candidate_name_normalized = _normalize_text(candidate.name)
        if candidate_key == current.get("exercise_key"):
            continue
        if current_name_normalized and candidate_name_normalized == current_name_normalized:
            continue
        if candidate_key in excluded:
            continue
        if not _is_role_compatible(current.get("exercise_group", "main"), candidate):
            continue
        if not _supports_prescription(candidate, current["prescription_type"]):
            continue
        if not _equipment_allowed(user, candidate):
            continue

        score, reason = _score_candidate(current, candidate)
        if score <= 0.18:
            continue
        candidates.append(
            {
                "exercise_key": candidate.exercise_key,
                "external_id": candidate.external_id,
                "name": candidate.name,
                "modality": candidate.get_modality_display(),
                "instructions": candidate.instructions,
                "video_url": candidate.default_video_url,
                "image_url": candidate.display_image_url,
                "reason": reason.capitalize(),
                "score": score,
            }
        )

    candidates.sort(key=lambda item: (item["score"], item["name"]), reverse=True)

    deduped = []
    seen_names = Counter()
    for item in candidates:
        seen_names[item["name"]] += 1
        if seen_names[item["name"]] > 1:
            continue
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped
