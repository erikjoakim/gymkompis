import json
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from openai import OpenAI

from core.json_utils import extract_json_object, extract_response_text

from .models import Exercise


SOURCE_FILES = {
    "bodyweight": "bodyweight_exercises.json",
    "freeweight": "free_weight_exercises.json",
    "machine": "machine_exercises.json",
    "static": "static_exercises.json",
}

CATALOG_PATHS = ("*_catalog.json", "catalogs/*.json")

CATALOG_TYPE_MODALITY = {
    "selectorized_strength": Exercise.Modality.MACHINE,
    "medical_selectorized_strength": Exercise.Modality.MACHINE,
    "plate_loaded_strength": Exercise.Modality.MACHINE,
    "free_weight_and_racks": Exercise.Modality.OTHER,
    "cardio": Exercise.Modality.CARDIO,
    "performance_cardio": Exercise.Modality.CARDIO,
    "cable_strength": Exercise.Modality.CABLE,
}

CATALOG_TYPE_EQUIPMENT = {
    "selectorized_strength": "Selectorized machine",
    "medical_selectorized_strength": "Medical selectorized machine",
    "plate_loaded_strength": "Plate-loaded machine",
    "free_weight_and_racks": "Rack / free weight station",
    "cardio": "Cardio machine",
    "performance_cardio": "Performance cardio machine",
    "cable_strength": "Cable station",
}

BODY_REGION_CATEGORY_MAP = {
    "upper_body": "Upper Body",
    "lower_body": "Lower Body",
    "core": "Core",
    "core_lower_back": "Lower Back",
    "full_body": "Full Body",
}

BODY_REGION_PRIMARY_MUSCLES = {
    "upper_body": ["Back", "Chest", "Shoulders", "Arms"],
    "lower_body": ["Quadriceps", "Hamstrings", "Glutes"],
    "core": ["Abdominals", "Obliques"],
    "core_lower_back": ["Lower Back", "Glutes"],
    "full_body": ["Full Body"],
}

MOVEMENT_METADATA = {
    "hip_adduction": {"category": "Lower Body", "primary": ["Adductors"], "secondary": ["Glutes"], "stabilizers": ["Core"]},
    "hip_abduction": {"category": "Lower Body", "primary": ["Glutes", "Abductors"], "secondary": ["Tensor Fasciae Latae"], "stabilizers": ["Core"]},
    "triceps_extension": {"category": "Upper Body", "primary": ["Triceps"], "secondary": ["Shoulders"], "stabilizers": ["Core"]},
    "shoulder_abduction": {"category": "Upper Body", "primary": ["Shoulders"], "secondary": ["Upper Traps"], "stabilizers": ["Core"]},
    "vertical_pull": {"category": "Upper Body", "primary": ["Lats", "Upper Back"], "secondary": ["Biceps"], "stabilizers": ["Core"]},
    "horizontal_pull": {"category": "Upper Body", "primary": ["Upper Back", "Lats"], "secondary": ["Biceps", "Rear Delts"], "stabilizers": ["Core"]},
    "rear_upper_back_pull": {"category": "Upper Body", "primary": ["Upper Back", "Rear Delts"], "secondary": ["Rhomboids"], "stabilizers": ["Core"]},
    "chest_fly": {"category": "Upper Body", "primary": ["Chest"], "secondary": ["Front Delts"], "stabilizers": ["Core"]},
    "rear_delt_fly": {"category": "Upper Body", "primary": ["Rear Delts", "Upper Back"], "secondary": ["Rhomboids"], "stabilizers": ["Core"]},
    "trunk_rotation": {"category": "Core", "primary": ["Obliques"], "secondary": ["Abdominals"], "stabilizers": ["Lower Back"]},
    "abdominal_flexion": {"category": "Core", "primary": ["Abdominals"], "secondary": ["Obliques"], "stabilizers": ["Hip Flexors"]},
    "abdominal_crunch": {"category": "Core", "primary": ["Abdominals"], "secondary": ["Obliques"], "stabilizers": ["Hip Flexors"]},
    "spinal_extension": {"category": "Lower Back", "primary": ["Lower Back"], "secondary": ["Glutes", "Hamstrings"], "stabilizers": ["Core"]},
    "knee_extension": {"category": "Lower Body", "primary": ["Quadriceps"], "secondary": [], "stabilizers": []},
    "knee_flexion": {"category": "Lower Body", "primary": ["Hamstrings"], "secondary": ["Calves"], "stabilizers": ["Glutes"]},
    "dual_knee_flexion_extension": {"category": "Lower Body", "primary": ["Quadriceps", "Hamstrings"], "secondary": [], "stabilizers": []},
    "leg_press": {"category": "Lower Body", "primary": ["Quadriceps", "Glutes"], "secondary": ["Hamstrings"], "stabilizers": ["Core"]},
    "linear_leg_press": {"category": "Lower Body", "primary": ["Quadriceps", "Glutes"], "secondary": ["Hamstrings"], "stabilizers": ["Core"]},
    "multi_hip": {"category": "Lower Body", "primary": ["Glutes", "Hip Flexors"], "secondary": ["Adductors", "Abductors"], "stabilizers": ["Core"]},
    "horizontal_press": {"category": "Upper Body", "primary": ["Chest"], "secondary": ["Triceps", "Front Delts"], "stabilizers": ["Core"]},
    "incline_press": {"category": "Upper Body", "primary": ["Upper Chest"], "secondary": ["Triceps", "Front Delts"], "stabilizers": ["Core"]},
    "wide_horizontal_press": {"category": "Upper Body", "primary": ["Chest"], "secondary": ["Front Delts"], "stabilizers": ["Core"]},
    "vertical_press": {"category": "Upper Body", "primary": ["Shoulders"], "secondary": ["Triceps"], "stabilizers": ["Core"]},
    "row": {"category": "Upper Body", "primary": ["Upper Back", "Lats"], "secondary": ["Biceps"], "stabilizers": ["Core"]},
    "hip_thrust": {"category": "Lower Body", "primary": ["Glutes"], "secondary": ["Hamstrings"], "stabilizers": ["Core"]},
    "squat": {"category": "Lower Body", "primary": ["Quadriceps", "Glutes"], "secondary": ["Hamstrings"], "stabilizers": ["Core"]},
    "treadmill": {"category": "Cardio", "primary": ["Quadriceps", "Hamstrings", "Calves"], "secondary": ["Glutes"], "stabilizers": ["Core"]},
    "upright_bike": {"category": "Cardio", "primary": ["Quadriceps"], "secondary": ["Hamstrings", "Glutes"], "stabilizers": []},
    "recumbent_bike": {"category": "Cardio", "primary": ["Quadriceps"], "secondary": ["Hamstrings", "Glutes"], "stabilizers": []},
    "elliptical": {"category": "Cardio", "primary": ["Quadriceps", "Glutes"], "secondary": ["Hamstrings", "Shoulders"], "stabilizers": ["Core"]},
    "adaptive_elliptical": {"category": "Cardio", "primary": ["Quadriceps", "Glutes"], "secondary": ["Hamstrings", "Shoulders"], "stabilizers": ["Core"]},
    "stair_climber": {"category": "Cardio", "primary": ["Glutes", "Quadriceps"], "secondary": ["Hamstrings", "Calves"], "stabilizers": ["Core"]},
    "upper_body_ergometer": {"category": "Cardio", "primary": ["Shoulders", "Arms"], "secondary": ["Upper Back"], "stabilizers": ["Core"]},
    "cross_trainer": {"category": "Cardio", "primary": ["Quadriceps", "Glutes"], "secondary": ["Hamstrings", "Shoulders"], "stabilizers": ["Core"]},
    "performance_treadmill": {"category": "Cardio", "primary": ["Quadriceps", "Hamstrings", "Calves"], "secondary": ["Glutes"], "stabilizers": ["Core"]},
    "curved_or_multidrive_treadmill": {"category": "Cardio", "primary": ["Quadriceps", "Hamstrings", "Calves"], "secondary": ["Glutes"], "stabilizers": ["Core"]},
    "performance_bike": {"category": "Cardio", "primary": ["Quadriceps"], "secondary": ["Hamstrings", "Glutes"], "stabilizers": []},
    "rower": {"category": "Cardio", "primary": ["Upper Back", "Lats", "Legs"], "secondary": ["Biceps", "Glutes"], "stabilizers": ["Core"]},
    "adjustable_cable_pulley": {"category": "Upper Body", "primary": ["Full Body"], "secondary": [], "stabilizers": ["Core"]},
    "chest_press_or_fly": {"category": "Upper Body", "primary": ["Chest"], "secondary": ["Front Delts", "Triceps"], "stabilizers": ["Core"]},
    "elbow_flexion": {"category": "Upper Body", "primary": ["Biceps"], "secondary": ["Brachialis", "Forearms"], "stabilizers": ["Core"]},
    "calf_raise": {"category": "Lower Body", "primary": ["Calves"], "secondary": ["Tibialis Posterior"], "stabilizers": ["Core"]},
    "guided_squat_press": {"category": "Lower Body", "primary": ["Quadriceps", "Glutes"], "secondary": ["Chest", "Shoulders", "Triceps"], "stabilizers": ["Core"]},
    "air_bike": {"category": "Cardio", "primary": ["Quadriceps", "Shoulders"], "secondary": ["Glutes", "Hamstrings", "Arms"], "stabilizers": ["Core"]},
    "adaptive_cardio": {"category": "Cardio", "primary": ["Quadriceps", "Glutes"], "secondary": ["Hamstrings", "Calves", "Shoulders"], "stabilizers": ["Core"]},
    "elliptical_variant": {"category": "Cardio", "primary": ["Quadriceps", "Glutes"], "secondary": ["Hamstrings", "Calves", "Shoulders"], "stabilizers": ["Core"]},
    "multi": {"category": "Full Body", "primary": ["Full Body"], "secondary": [], "stabilizers": ["Core"]},
}


def _slugify_value(value: str, *, fallback: str = "item") -> str:
    slug = slugify(value or "")
    return slug or fallback


def _normalize_duplicate_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def root_exercise_queryset():
    return Exercise.objects.filter(canonical_exercise__isnull=True)


def visible_exercise_queryset(user=None):
    queryset = root_exercise_queryset().filter(is_active=True)
    if user and getattr(user, "is_staff", False):
        return queryset

    filters = Q(verification_status=Exercise.VerificationStatus.APPROVED)
    if user and getattr(user, "is_authenticated", False):
        filters |= Q(
            verification_status=Exercise.VerificationStatus.PENDING_REVIEW,
            created_by=user,
        )
    return queryset.filter(filters)


def resolve_canonical_exercise(exercise: Exercise | None) -> Exercise | None:
    if not exercise:
        return None
    return exercise.canonical_exercise or exercise


def _titleize_identifier(value: str) -> str:
    text = (value or "").replace("_", " ").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).title()


def _normalize_string_list(values) -> list[str]:
    normalized = []
    for value in values or []:
        text = re.sub(r"\s+", " ", str(value).strip())
        if not text:
            continue
        normalized.append(_titleize_identifier(text.replace("-", " ")))
    return normalized


def _brand_source_dataset(brand: str) -> str:
    return f"{_slugify_value(brand)[:24]}_catalog"[:32]


def _user_submission_external_id(user, name: str) -> str:
    slug = _slugify_value(name, fallback="exercise")[:32]
    return f"user__{getattr(user, 'pk', 'anon')}__{slug}__{uuid.uuid4().hex[:8]}"


def _catalog_paths(root: Path) -> list[Path]:
    paths: set[Path] = set()
    for pattern in CATALOG_PATHS:
        paths.update(root.glob(pattern))
    return sorted(path for path in paths if path.is_file())


def _resolve_source_file(root: Path, filename: str) -> Path | None:
    candidates = [
        root / filename,
        root / "catalogs" / filename,
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def infer_modality(source_dataset: str, equipment: str, category: str, *, catalog_type: str = "", name: str = "", movement_pattern: str = "") -> str:
    equipment_text = (equipment or "").lower()
    category_text = (category or "").lower()
    name_text = (name or "").lower()
    movement_text = (movement_pattern or "").lower()
    if catalog_type in CATALOG_TYPE_MODALITY:
        return CATALOG_TYPE_MODALITY[catalog_type]
    if source_dataset in {"bodyweight", "static"} or "bodyweight" in name_text or "body weight" in name_text:
        return Exercise.Modality.BODYWEIGHT
    if "body weight" in equipment_text or "bodyweight" in equipment_text:
        return Exercise.Modality.BODYWEIGHT
    if "barbell" in equipment_text:
        return Exercise.Modality.BARBELL
    if "dumbbell" in equipment_text:
        return Exercise.Modality.DUMBBELL
    if "kettlebell" in equipment_text:
        return Exercise.Modality.KETTLEBELL
    if "cable" in equipment_text:
        return Exercise.Modality.CABLE
    if "machine" in equipment_text or "smith" in equipment_text:
        return Exercise.Modality.MACHINE
    if "band" in equipment_text:
        return Exercise.Modality.BAND
    if "mobility" in category_text:
        return Exercise.Modality.MOBILITY
    if "isometric" in movement_text or "hold" in movement_text:
        return Exercise.Modality.BODYWEIGHT if source_dataset in {"bodyweight", "static"} else Exercise.Modality.OTHER
    if source_dataset == "machine":
        return Exercise.Modality.MACHINE
    return Exercise.Modality.OTHER


def infer_library_role(source_dataset: str, category: str, *, catalog_type: str = "") -> str:
    category_text = (category or "").lower()
    if catalog_type in {"cardio", "performance_cardio"}:
        return Exercise.LibraryRole.BOTH
    if category_text in {"mobility", "shoulder health"}:
        return Exercise.LibraryRole.BOTH
    if source_dataset == "static":
        return Exercise.LibraryRole.BOTH
    return Exercise.LibraryRole.MAIN


def infer_supports_time(source_dataset: str, movement_pattern: str, category: str, *, catalog_type: str = "") -> bool:
    pattern = (movement_pattern or "").lower()
    category_text = (category or "").lower()
    if catalog_type in {"cardio", "performance_cardio"}:
        return True
    return source_dataset == "static" or "hold" in pattern or "isometric" in pattern or category_text in {"mobility", "cardio"}


def infer_supports_reps(source_dataset: str, movement_pattern: str, category: str, *, catalog_type: str = "") -> bool:
    if source_dataset == "static" or catalog_type in {"cardio", "performance_cardio"}:
        return False
    pattern = (movement_pattern or "").lower()
    category_text = (category or "").lower()
    return "hold" not in pattern and category_text not in {"mobility", "cardio"}


def infer_equipment(equipment: str, *, brand: str = "", line: str = "", catalog_type: str = "", modality: str = "") -> str:
    equipment_text = (equipment or "").strip().lower()
    if modality == Exercise.Modality.BODYWEIGHT and equipment_text in {"", "machine"}:
        return "Body Weight"
    if equipment:
        return equipment
    if catalog_type in CATALOG_TYPE_EQUIPMENT:
        return CATALOG_TYPE_EQUIPMENT[catalog_type]
    if modality == Exercise.Modality.MACHINE:
        return "Machine"
    if modality == Exercise.Modality.CABLE:
        return "Cable"
    if modality == Exercise.Modality.CARDIO:
        return "Cardio machine"
    if brand and line:
        return f"{brand} {line}"
    return ""


def infer_movement_pattern(movement_pattern: str, movement: str = "", name: str = "") -> str:
    if movement_pattern:
        return movement_pattern
    if movement:
        return _titleize_identifier(movement)
    return name


def infer_category(category: str, *, body_region: str = "", movement: str = "", movement_pattern: str = "", modality: str = "") -> str:
    if category:
        return category
    if movement in MOVEMENT_METADATA:
        return MOVEMENT_METADATA[movement]["category"]
    if body_region in BODY_REGION_CATEGORY_MAP:
        return BODY_REGION_CATEGORY_MAP[body_region]
    if modality == Exercise.Modality.CARDIO:
        return "Cardio"
    text = f"{movement_pattern} {movement}".lower()
    if any(token in text for token in ("press", "row", "pull", "delt", "chest", "lat", "arm")):
        return "Upper Body"
    if any(token in text for token in ("leg", "hip", "squat", "curl", "extension", "thrust")):
        return "Lower Body"
    if any(token in text for token in ("abdominal", "crunch", "torso", "rotation", "back")):
        return "Core"
    return ""


def infer_muscle_groups(primary_muscles, secondary_muscles, stabilizers, *, movement: str = "", body_region: str = "", category: str = "") -> tuple[list[str], list[str], list[str]]:
    if primary_muscles or secondary_muscles or stabilizers:
        return (
            _normalize_string_list(primary_muscles),
            _normalize_string_list(secondary_muscles),
            _normalize_string_list(stabilizers),
        )
    if movement in MOVEMENT_METADATA:
        item = MOVEMENT_METADATA[movement]
        return list(item["primary"]), list(item["secondary"]), list(item["stabilizers"])
    if body_region in BODY_REGION_PRIMARY_MUSCLES:
        return list(BODY_REGION_PRIMARY_MUSCLES[body_region]), [], []
    if category == "Cardio":
        return ["Cardiovascular System"], [], []
    return [], [], []


def _should_prefer_inferred_modality(current_modality: str, inferred_modality: str) -> bool:
    if current_modality == Exercise.Modality.OTHER:
        return True
    if inferred_modality == Exercise.Modality.BODYWEIGHT and current_modality != Exercise.Modality.BODYWEIGHT:
        return True
    return False


def build_seed_instruction(payload: dict) -> str:
    name = payload["name"]
    equipment = payload.get("equipment") or "the recommended setup"
    movement_pattern = payload.get("movement_pattern") or "the movement"
    category = payload.get("category") or "general training"
    is_static = payload.get("is_static", False)
    unilateral = payload.get("unilateral", False)

    setup_sentence = f"Set up for {name.lower()} using {equipment.lower()} and move into a stable start position."
    if is_static:
        movement_sentence = (
            f"Hold the intended {movement_pattern.lower()} position with steady breathing and controlled full-body tension."
        )
    else:
        movement_sentence = (
            f"Perform the {movement_pattern.lower()} pattern in a smooth, controlled way and stay balanced through the full range you can own."
        )

    if unilateral:
        cue_sentence = "Keep both sides as even as possible and complete the prescribed work on each side."
    elif category.lower() in {"mobility", "shoulder health"}:
        cue_sentence = "Move without forcing the range and stop short of sharp pain or pinching."
    else:
        cue_sentence = "Keep your torso braced, stay in control, and stop the set if your form breaks down."

    return " ".join([setup_sentence, movement_sentence, cue_sentence])


def build_instruction_prompt(exercise_payload: dict) -> tuple[str, str]:
    instructions = (
        "Write concise exercise instructions for a gym exercise library. "
        "Return plain text only, no markdown. "
        "Use 2 or 3 sentences. "
        "Include setup, the main movement cue, and one safety or form cue. "
        "Keep the wording clear for general users and avoid medical claims."
    )
    payload = json.dumps(exercise_payload, ensure_ascii=False)
    return instructions, payload


def generate_ai_instruction(exercise_payload: dict) -> str:
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    instructions, prompt = build_instruction_prompt(exercise_payload)
    response = client.responses.create(
        model=settings.OPENAI_MODEL,
        instructions=instructions,
        input=prompt,
        temperature=0.2,
    )
    return extract_response_text(response).strip()


def build_metadata_prompt(exercise_payload: dict) -> tuple[str, str]:
    instructions = (
        "You normalize gym exercise metadata. "
        "Return only a JSON object with the keys category, movement_pattern, primary_muscles, secondary_muscles, stabilizers, and equipment. "
        "Use arrays of strings for the muscle keys. "
        "Prefer concise, practical values suitable for a gym exercise library."
    )
    return instructions, json.dumps(exercise_payload, ensure_ascii=False)


def generate_ai_exercise_metadata(exercise_payload: dict) -> dict:
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    instructions, prompt = build_metadata_prompt(exercise_payload)
    response = client.responses.create(
        model=settings.OPENAI_MODEL,
        instructions=instructions,
        input=prompt,
        temperature=0.2,
    )
    data = extract_json_object(extract_response_text(response))
    return {
        "category": data.get("category", ""),
        "movement_pattern": data.get("movement_pattern", ""),
        "primary_muscles": data.get("primary_muscles") or [],
        "secondary_muscles": data.get("secondary_muscles") or [],
        "stabilizers": data.get("stabilizers") or [],
        "equipment": data.get("equipment", ""),
    }


def _movement_key_from_query(query: str) -> str:
    text = " ".join((query or "").lower().replace("-", " ").replace("_", " ").split())
    if not text:
        return ""
    if "lat" in text and any(token in text for token in ("pull", "pulldown", "pull down", "traction")):
        return "vertical_pull"
    if "row" in text:
        return "horizontal_pull"
    if "chest press" in text or ("press" in text and "shoulder" not in text and "overhead" not in text):
        return "horizontal_press"
    if "shoulder press" in text or "overhead press" in text:
        return "vertical_press"
    if "curl" in text and "leg" not in text:
        return "elbow_flexion"
    if "leg curl" in text:
        return "knee_flexion"
    if "extension" in text and "leg" in text:
        return "knee_extension"
    if "crunch" in text or "abdominal" in text:
        return "abdominal_crunch"
    if "adductor" in text:
        return "hip_adduction"
    if "abductor" in text:
        return "hip_abduction"
    if "squat" in text:
        return "squat"
    if "leg press" in text:
        return "leg_press"
    if "hip thrust" in text:
        return "hip_thrust"
    if "calf" in text:
        return "calf_raise"
    if "bike" in text:
        return "upright_bike"
    if "treadmill" in text or "run" in text:
        return "treadmill"
    if "rower" in text or "rowing" in text:
        return "rower"
    if "cable" in text and "pulley" in text:
        return "adjustable_cable_pulley"
    return ""


def build_exercise_suggestion_prompt(search_query: str) -> tuple[str, str]:
    instructions = (
        "You draft metadata for a gym exercise library when a user searches for a missing exercise. "
        "Return only a JSON object with the keys name, aliases, brand, line, modality, library_role, equipment, "
        "category, movement_pattern, primary_muscles, secondary_muscles, stabilizers, supports_reps, supports_time, "
        "is_static, unilateral, and instructions. "
        "Use concise gym-library values and arrays of strings for list fields. "
        "If the query looks generic, keep brand and line empty."
    )
    return instructions, json.dumps({"query": search_query}, ensure_ascii=False)


def _deterministic_exercise_suggestion(search_query: str) -> dict:
    query = re.sub(r"\s+", " ", (search_query or "").strip())
    movement = _movement_key_from_query(query)
    normalized_name = _titleize_identifier(query.replace("/", " "))
    modality = infer_modality("user", query, "", name=query, movement_pattern=movement)
    movement_pattern = infer_movement_pattern("", movement, normalized_name)
    category = infer_category("", movement=movement, movement_pattern=movement_pattern, modality=modality)
    primary_muscles, secondary_muscles, stabilizers = infer_muscle_groups(
        [],
        [],
        [],
        movement=movement,
        category=category,
    )
    is_static = any(token in query.lower() for token in ("hold", "plank", "isometric"))
    supports_time = is_static or modality == Exercise.Modality.CARDIO
    supports_reps = not supports_time or modality not in {Exercise.Modality.CARDIO, Exercise.Modality.MOBILITY}
    equipment = infer_equipment("", modality=modality)
    payload = {
        "name": normalized_name,
        "aliases": [],
        "brand": "",
        "line": "",
        "modality": modality,
        "library_role": infer_library_role("user", category),
        "equipment": equipment,
        "category": category,
        "movement_pattern": movement_pattern,
        "primary_muscles": primary_muscles,
        "secondary_muscles": secondary_muscles,
        "stabilizers": stabilizers,
        "supports_reps": supports_reps,
        "supports_time": supports_time,
        "is_static": is_static,
        "unilateral": False,
    }
    payload["instructions"] = build_seed_instruction(
        {
            "name": payload["name"],
            "equipment": payload["equipment"],
            "category": payload["category"],
            "movement_pattern": payload["movement_pattern"],
            "primary_muscles": payload["primary_muscles"],
            "secondary_muscles": payload["secondary_muscles"],
            "stabilizers": payload["stabilizers"],
            "unilateral": payload["unilateral"],
            "is_static": payload["is_static"],
        }
    )
    return payload


def generate_ai_exercise_suggestion(search_query: str) -> dict:
    fallback = _deterministic_exercise_suggestion(search_query)
    if settings.OPENAI_MOCK_RESPONSES or not settings.OPENAI_API_KEY:
        return fallback

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    instructions, prompt = build_exercise_suggestion_prompt(search_query)
    response = client.responses.create(
        model=settings.OPENAI_MODEL,
        instructions=instructions,
        input=prompt,
        temperature=0.2,
    )
    data = extract_json_object(extract_response_text(response))
    return {
        "name": data.get("name") or fallback["name"],
        "aliases": data.get("aliases") or fallback["aliases"],
        "brand": data.get("brand", ""),
        "line": data.get("line", ""),
        "modality": data.get("modality") or fallback["modality"],
        "library_role": data.get("library_role") or fallback["library_role"],
        "equipment": data.get("equipment") or fallback["equipment"],
        "category": data.get("category") or fallback["category"],
        "movement_pattern": data.get("movement_pattern") or fallback["movement_pattern"],
        "primary_muscles": data.get("primary_muscles") or fallback["primary_muscles"],
        "secondary_muscles": data.get("secondary_muscles") or fallback["secondary_muscles"],
        "stabilizers": data.get("stabilizers") or fallback["stabilizers"],
        "supports_reps": data.get("supports_reps", fallback["supports_reps"]),
        "supports_time": data.get("supports_time", fallback["supports_time"]),
        "is_static": data.get("is_static", fallback["is_static"]),
        "unilateral": data.get("unilateral", fallback["unilateral"]),
        "instructions": data.get("instructions") or fallback["instructions"],
    }


def _instruction_payload_from_record(record: dict) -> dict:
    return {
        "name": record["name"],
        "equipment": record["equipment"],
        "category": record["category"],
        "movement_pattern": record["movement_pattern"],
        "primary_muscles": record["primary_muscles"],
        "secondary_muscles": record["secondary_muscles"],
        "stabilizers": record["stabilizers"],
        "unilateral": record["unilateral"],
        "is_static": record["is_static"],
    }


def _split_catalog_name(full_name: str, line_name: str) -> str:
    normalized_name = (full_name or "").strip()
    normalized_line = (line_name or "").strip()
    if not normalized_name or not normalized_line:
        return normalized_name
    if normalized_name.lower().startswith(normalized_line.lower()):
        remainder = normalized_name[len(normalized_line) :].lstrip(" -/")
        return remainder or normalized_name
    return normalized_name


def normalize_exercise_record(source_dataset: str, record: dict) -> dict:
    movement = record.get("movement", "")
    movement_pattern = infer_movement_pattern(record.get("movement_pattern", ""), movement, record.get("name", ""))
    category = infer_category(
        record.get("category", ""),
        body_region=record.get("body_region", ""),
        movement=movement,
        movement_pattern=movement_pattern,
    )
    modality = infer_modality(
        source_dataset,
        record.get("equipment", ""),
        category,
        name=record.get("name", ""),
        movement_pattern=movement_pattern,
    )
    equipment = infer_equipment(record.get("equipment", ""), modality=modality)
    primary_muscles, secondary_muscles, stabilizers = infer_muscle_groups(
        record.get("primary_muscles", []),
        record.get("secondary_muscles", []),
        record.get("stabilizers", []),
        movement=movement,
        body_region=record.get("body_region", ""),
        category=category,
    )
    is_static = source_dataset == "static" or "hold" in movement_pattern.lower() or "isometric" in movement_pattern.lower()
    supports_time = infer_supports_time(source_dataset, movement_pattern, category)
    supports_reps = infer_supports_reps(source_dataset, movement_pattern, category)

    payload = {
        "external_id": record["exercise_id"],
        "source_dataset": source_dataset,
        "source_kind": Exercise.SourceKind.SYSTEM,
        "name": record["name"],
        "brand": record.get("brand", ""),
        "line": record.get("line", ""),
        "aliases": list(record.get("aliases", [])),
        "raw_catalog_data": record.get("raw_catalog_data", {}),
        "modality": modality,
        "library_role": infer_library_role(source_dataset, category),
        "equipment": equipment,
        "category": category,
        "movement_pattern": movement_pattern,
        "primary_muscles": primary_muscles,
        "secondary_muscles": secondary_muscles,
        "stabilizers": stabilizers,
        "unilateral": bool(record.get("unilateral")),
        "is_static": is_static,
        "supports_reps": supports_reps,
        "supports_time": supports_time,
        "instructions": "",
        "instructions_status": Exercise.InstructionStatus.MISSING,
        "instruction_source": "",
        "default_video_url": "",
        "image_url": "",
        "image_status": Exercise.ImageStatus.MISSING,
        "image_prompt": "",
        "image_source": "",
        "image_error_message": "",
        "image_generated_at": None,
        "verification_status": Exercise.VerificationStatus.APPROVED,
        "verified_by": None,
        "verified_at": None,
        "review_notes": "",
        "is_active": True,
    }
    return payload


def normalize_catalog_machine_record(brand: str, line_name: str, catalog_type: str, machine: dict) -> dict:
    full_name = machine.get("name", "").strip()
    name = _split_catalog_name(full_name, line_name)
    movement = machine.get("movement", "")
    body_region = machine.get("body_region", "")
    modality = infer_modality(_brand_source_dataset(brand), "", "", catalog_type=catalog_type)
    movement_pattern = infer_movement_pattern("", movement, name)
    category = infer_category("", body_region=body_region, movement=movement, movement_pattern=movement_pattern, modality=modality)
    equipment = infer_equipment("", brand=brand, line=line_name, catalog_type=catalog_type, modality=modality)
    primary_muscles, secondary_muscles, stabilizers = infer_muscle_groups(
        [],
        [],
        [],
        movement=movement,
        body_region=body_region,
        category=category,
    )
    external_id = machine.get("exercise_id") or f"{_slugify_value(brand)}__{_slugify_value(line_name)}__{_slugify_value(name)}"
    aliases = [full_name] if full_name and full_name != name else []
    source_dataset = _brand_source_dataset(brand)
    record = {
        "exercise_id": external_id,
        "name": name,
        "brand": brand,
        "line": line_name,
        "aliases": aliases,
        "equipment": equipment,
        "category": category,
        "movement": movement,
        "movement_pattern": movement_pattern,
        "primary_muscles": machine.get("primary_muscles", primary_muscles),
        "secondary_muscles": machine.get("secondary_muscles", secondary_muscles),
        "stabilizers": machine.get("stabilizers", stabilizers),
        "unilateral": False,
        "body_region": body_region,
        "raw_catalog_data": {
            "brand": brand,
            "line": line_name,
            "type": catalog_type,
            "body_region": body_region,
            "movement": movement,
            "source_name": full_name,
            "source_machine": machine,
        },
    }
    payload = normalize_exercise_record(source_dataset, record)
    payload["source_kind"] = Exercise.SourceKind.CATALOG
    payload["modality"] = modality
    payload["library_role"] = infer_library_role(source_dataset, payload["category"], catalog_type=catalog_type)
    payload["supports_time"] = infer_supports_time(source_dataset, payload["movement_pattern"], payload["category"], catalog_type=catalog_type)
    payload["supports_reps"] = infer_supports_reps(source_dataset, payload["movement_pattern"], payload["category"], catalog_type=catalog_type)
    return payload


def _catalog_brand_entries(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    if payload.get("brand"):
        return [payload]

    brand_entries = []
    for brand_payload in payload.get("brands", []):
        if not isinstance(brand_payload, dict):
            continue
        brand = (brand_payload.get("brand") or "").strip()
        if not brand:
            continue
        brand_entries.append(
            {
                "brand": brand,
                "lines": brand_payload.get("lines", []),
            }
        )
    return brand_entries


def load_source_records(base_dir: Path | str | None = None) -> list[dict]:
    root = Path(base_dir or settings.BASE_DIR)
    records: list[dict] = []
    for source_dataset, filename in SOURCE_FILES.items():
        path = _resolve_source_file(root, filename)
        if not path:
            continue
        source_items = json.loads(path.read_text(encoding="utf-8"))
        for item in source_items:
            records.append(normalize_exercise_record(source_dataset, item))

    for path in _catalog_paths(root):
        if path.name in SOURCE_FILES.values():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for brand_payload in _catalog_brand_entries(payload):
            brand = brand_payload["brand"]
            for line in brand_payload.get("lines", []):
                line_name = line.get("line", "").strip()
                catalog_type = line.get("type", "").strip()
                for machine in line.get("machines", []):
                    records.append(normalize_catalog_machine_record(brand, line_name, catalog_type, machine))
    return records


def _apply_instruction_defaults(defaults: dict, *, ai_instructions: bool) -> None:
    payload_for_instruction = _instruction_payload_from_record(defaults)
    if ai_instructions and settings.OPENAI_API_KEY and not settings.OPENAI_MOCK_RESPONSES:
        defaults["instructions"] = generate_ai_instruction(payload_for_instruction)
        defaults["instructions_status"] = Exercise.InstructionStatus.AI_DRAFT
        defaults["instruction_source"] = f"{settings.OPENAI_MODEL}:{settings.OPENAI_PROGRAM_PROMPT_VERSION}"
    else:
        defaults["instructions"] = build_seed_instruction(payload_for_instruction)
        defaults["instructions_status"] = Exercise.InstructionStatus.SEEDED
        defaults["instruction_source"] = "seed-template-v1"


def import_exercise_library(
    *,
    base_dir: Path | str | None = None,
    overwrite: bool = False,
    ai_instructions: bool = False,
) -> dict:
    created = 0
    updated = 0
    records = load_source_records(base_dir=base_dir)
    for record in records:
        defaults = record.copy()
        _apply_instruction_defaults(defaults, ai_instructions=ai_instructions)

        obj, was_created = Exercise.objects.get_or_create(
            external_id=record["external_id"],
            defaults=defaults,
        )
        if was_created:
            created += 1
            continue
        if overwrite:
            for field, value in defaults.items():
                setattr(obj, field, value)
            obj.save()
            updated += 1
    return {"created": created, "updated": updated, "total": len(records)}


def create_user_exercise_submission(user, payload: dict, *, submission_query: str = "", source_kind: str = Exercise.SourceKind.AI_SUGGESTED) -> tuple[Exercise, bool]:
    name = re.sub(r"\s+", " ", str(payload.get("name", "")).strip())
    brand = re.sub(r"\s+", " ", str(payload.get("brand", "")).strip())
    line = re.sub(r"\s+", " ", str(payload.get("line", "")).strip())
    existing = visible_exercise_queryset(user).filter(
        name__iexact=name,
        brand__iexact=brand,
        line__iexact=line,
    ).first()
    if existing:
        return existing, False

    movement_pattern = re.sub(r"\s+", " ", str(payload.get("movement_pattern", "")).strip())
    category = re.sub(r"\s+", " ", str(payload.get("category", "")).strip())
    modality = payload.get("modality") or infer_modality("user", payload.get("equipment", ""), category, name=name, movement_pattern=movement_pattern)
    primary_muscles = _normalize_string_list(payload.get("primary_muscles") or [])
    secondary_muscles = _normalize_string_list(payload.get("secondary_muscles") or [])
    stabilizers = _normalize_string_list(payload.get("stabilizers") or [])
    equipment = payload.get("equipment") or infer_equipment("", modality=modality)
    instructions = re.sub(r"\s+", " ", str(payload.get("instructions", "")).strip())
    if not instructions:
        instructions = build_seed_instruction(
            {
                "name": name,
                "equipment": equipment,
                "category": category,
                "movement_pattern": movement_pattern,
                "primary_muscles": primary_muscles,
                "secondary_muscles": secondary_muscles,
                "stabilizers": stabilizers,
                "unilateral": bool(payload.get("unilateral")),
                "is_static": bool(payload.get("is_static")),
            }
        )

    alias_values = _normalize_string_list(payload.get("aliases") or [])
    if submission_query:
        normalized_submission_query = re.sub(r"\s+", " ", str(submission_query).strip())
        if normalized_submission_query and normalized_submission_query not in alias_values and normalized_submission_query.lower() != name.lower():
            alias_values.append(normalized_submission_query)

    exercise = Exercise.objects.create(
        external_id=_user_submission_external_id(user, name),
        source_dataset="user",
        source_kind=source_kind,
        name=name,
        brand=brand,
        line=line,
        created_by=user,
        aliases=alias_values,
        raw_catalog_data={
            "submission_query": submission_query,
            "submitted_payload": payload,
            "submitted_at": timezone.now().isoformat(),
        },
        modality=modality,
        library_role=payload.get("library_role") or infer_library_role("user", category),
        equipment=equipment,
        category=category,
        movement_pattern=movement_pattern,
        primary_muscles=primary_muscles,
        secondary_muscles=secondary_muscles,
        stabilizers=stabilizers,
        unilateral=bool(payload.get("unilateral")),
        is_static=bool(payload.get("is_static")),
        supports_reps=bool(payload.get("supports_reps", True)),
        supports_time=bool(payload.get("supports_time")),
        instructions=instructions,
        instructions_status=Exercise.InstructionStatus.AI_DRAFT if source_kind == Exercise.SourceKind.AI_SUGGESTED else Exercise.InstructionStatus.SEEDED,
        instruction_source=f"{settings.OPENAI_MODEL}:exercise-discovery-v1" if source_kind == Exercise.SourceKind.AI_SUGGESTED else "seed-template-v1",
        verification_status=Exercise.VerificationStatus.PENDING_REVIEW,
        verified_by=None,
        verified_at=None,
        review_notes="",
        is_active=True,
    )
    return exercise, True


def exercise_metadata_gaps(exercise: Exercise) -> list[str]:
    gaps = []
    if exercise.modality == Exercise.Modality.OTHER:
        gaps.append("modality")
    if not exercise.equipment:
        gaps.append("equipment")
    if not exercise.category:
        gaps.append("category")
    if not exercise.movement_pattern:
        gaps.append("movement_pattern")
    if not exercise.primary_muscles:
        gaps.append("primary_muscles")
    if not exercise.instructions:
        gaps.append("instructions")
    return gaps


def find_duplicate_exercise_groups(exercises) -> list[dict]:
    grouped = {}
    for exercise in exercises:
        if exercise.canonical_exercise_id:
            continue
        key = (
            _normalize_duplicate_text(exercise.name),
            _normalize_duplicate_text(exercise.brand),
            _normalize_duplicate_text(exercise.line),
        )
        if not key[0]:
            continue
        grouped.setdefault(key, []).append(exercise)

    duplicates = []
    for _key, items in grouped.items():
        if len(items) < 2:
            continue
        ordered_items = sorted(items, key=lambda exercise: (exercise.name.lower(), exercise.external_id.lower()))
        duplicates.append(
            {
                "label": ordered_items[0].name,
                "brand": ordered_items[0].brand,
                "line": ordered_items[0].line,
                "count": len(ordered_items),
                "exercises": ordered_items,
            }
        )
    duplicates.sort(key=lambda item: (item["label"].lower(), (item["brand"] or "").lower(), (item["line"] or "").lower()))
    return duplicates


def merge_exercise_duplicates(canonical: Exercise, duplicates: list[Exercise]) -> list[int]:
    canonical = resolve_canonical_exercise(canonical)
    merged_ids: list[int] = []
    alias_values = list(canonical.aliases or [])

    fill_fields = (
        "brand",
        "line",
        "equipment",
        "category",
        "movement_pattern",
        "instructions",
        "default_video_url",
        "image_url",
    )
    list_fields = ("primary_muscles", "secondary_muscles", "stabilizers")

    for duplicate in duplicates:
        duplicate = resolve_canonical_exercise(duplicate)
        if not duplicate or duplicate.pk == canonical.pk:
            continue

        if duplicate.name and duplicate.name != canonical.name and duplicate.name not in alias_values:
            alias_values.append(duplicate.name)
        for alias in duplicate.aliases or []:
            if alias and alias not in alias_values:
                alias_values.append(alias)

        if canonical.modality == Exercise.Modality.OTHER and duplicate.modality != Exercise.Modality.OTHER:
            canonical.modality = duplicate.modality
        if not canonical.library_role and duplicate.library_role:
            canonical.library_role = duplicate.library_role
        if not canonical.instructions_status and duplicate.instructions_status:
            canonical.instructions_status = duplicate.instructions_status
        if not canonical.instruction_source and duplicate.instruction_source:
            canonical.instruction_source = duplicate.instruction_source
        if not canonical.image_status and duplicate.image_status:
            canonical.image_status = duplicate.image_status
        if not canonical.image_source and duplicate.image_source:
            canonical.image_source = duplicate.image_source

        for field in fill_fields:
            if not getattr(canonical, field) and getattr(duplicate, field):
                setattr(canonical, field, getattr(duplicate, field))
        for field in list_fields:
            if not getattr(canonical, field) and getattr(duplicate, field):
                setattr(canonical, field, getattr(duplicate, field))

        duplicate.canonical_exercise = canonical
        duplicate.save(update_fields=["canonical_exercise", "updated_at"])
        merged_ids.append(duplicate.id)

    if alias_values != list(canonical.aliases or []):
        canonical.aliases = alias_values
    canonical.save()
    return merged_ids


def suggested_exercise_updates(exercise: Exercise, *, overwrite: bool = False, review_only: bool = False) -> dict:
    metadata = _exercise_metadata_payload(exercise)
    suggestions = {}
    field_updates = {
        "brand": metadata["brand"],
        "line": metadata["line"],
        "modality": metadata["modality"],
        "equipment": metadata["equipment"],
        "category": metadata["category"],
        "movement_pattern": metadata["movement_pattern"],
        "primary_muscles": metadata["primary_muscles"],
        "secondary_muscles": metadata["secondary_muscles"],
        "stabilizers": metadata["stabilizers"],
        "is_static": metadata["is_static"],
        "supports_time": metadata["supports_time"],
        "supports_reps": metadata["supports_reps"],
    }
    if review_only:
        field_updates = {
            field: value
            for field, value in field_updates.items()
            if field not in {"brand", "line"}
        }
    for field, suggested_value in field_updates.items():
        current_value = getattr(exercise, field)
        has_meaningful_value = bool(current_value)
        if field == "modality" and current_value == Exercise.Modality.OTHER:
            has_meaningful_value = False
        if field == "modality" and metadata["modality"] == Exercise.Modality.BODYWEIGHT and current_value != Exercise.Modality.BODYWEIGHT:
            has_meaningful_value = False
        if field == "equipment" and metadata["modality"] == Exercise.Modality.BODYWEIGHT and (current_value or "").strip().lower() == "machine":
            has_meaningful_value = False
        if field in {"supports_time", "supports_reps", "is_static"} and current_value is False:
            has_meaningful_value = False
        if has_meaningful_value and not overwrite:
            continue
        if current_value != suggested_value and suggested_value not in ("", [], None):
            suggestions[field] = {
                "current": current_value,
                "suggested": suggested_value,
            }

    if overwrite or not exercise.instructions:
        instruction_payload = _instruction_payload_from_record(
            {
                "name": exercise.name,
                "equipment": metadata["equipment"] or exercise.equipment,
                "category": metadata["category"] or exercise.category,
                "movement_pattern": metadata["movement_pattern"] or exercise.movement_pattern,
                "primary_muscles": metadata["primary_muscles"] or exercise.primary_muscles,
                "secondary_muscles": metadata["secondary_muscles"] or exercise.secondary_muscles,
                "stabilizers": metadata["stabilizers"] or exercise.stabilizers,
                "unilateral": exercise.unilateral,
                "is_static": metadata["is_static"] or exercise.is_static,
            }
        )
        suggested_instructions = build_seed_instruction(instruction_payload)
        if exercise.instructions != suggested_instructions:
            suggestions["instructions"] = {
                "current": exercise.instructions,
                "suggested": suggested_instructions,
            }
    return suggestions


def _exercise_metadata_payload(exercise: Exercise) -> dict:
    raw = exercise.raw_catalog_data or {}
    source_machine = raw.get("source_machine", {})
    body_region = raw.get("body_region") or source_machine.get("body_region", "")
    movement = raw.get("movement") or source_machine.get("movement", "")
    catalog_type = raw.get("type", "")
    brand = exercise.brand or raw.get("brand", "")
    line = exercise.line or raw.get("line", "")
    inferred_modality = infer_modality(
        exercise.source_dataset,
        exercise.equipment,
        exercise.category,
        catalog_type=catalog_type,
        name=exercise.name,
        movement_pattern=exercise.movement_pattern,
    )
    modality = inferred_modality if _should_prefer_inferred_modality(exercise.modality, inferred_modality) else exercise.modality
    movement_pattern = infer_movement_pattern(exercise.movement_pattern, movement, exercise.name)
    category = infer_category(
        exercise.category,
        body_region=body_region,
        movement=movement,
        movement_pattern=movement_pattern,
        modality=modality,
    )
    equipment = infer_equipment(
        exercise.equipment,
        brand=brand,
        line=line,
        catalog_type=catalog_type,
        modality=modality,
    )
    primary_muscles, secondary_muscles, stabilizers = infer_muscle_groups(
        exercise.primary_muscles,
        exercise.secondary_muscles,
        exercise.stabilizers,
        movement=movement,
        body_region=body_region,
        category=category,
    )
    is_static = exercise.is_static or "hold" in movement_pattern.lower() or "isometric" in movement_pattern.lower()
    supports_time = exercise.supports_time or infer_supports_time(
        exercise.source_dataset,
        movement_pattern,
        category,
        catalog_type=catalog_type,
    )
    supports_reps = exercise.supports_reps if exercise.supports_reps else infer_supports_reps(
        exercise.source_dataset,
        movement_pattern,
        category,
        catalog_type=catalog_type,
    )
    return {
        "brand": brand,
        "line": line,
        "body_region": body_region,
        "movement": movement,
        "catalog_type": catalog_type,
        "modality": modality,
        "equipment": equipment,
        "category": category,
        "movement_pattern": movement_pattern,
        "primary_muscles": primary_muscles,
        "secondary_muscles": secondary_muscles,
        "stabilizers": stabilizers,
        "is_static": is_static,
        "supports_time": supports_time,
        "supports_reps": supports_reps,
    }


def enrich_exercise_metadata(exercise: Exercise, *, overwrite: bool = False, use_ai: bool = False) -> list[str]:
    metadata = _exercise_metadata_payload(exercise)
    changed_fields: list[str] = []

    field_updates = {
        "brand": metadata["brand"],
        "line": metadata["line"],
        "modality": metadata["modality"],
        "equipment": metadata["equipment"],
        "category": metadata["category"],
        "movement_pattern": metadata["movement_pattern"],
        "primary_muscles": metadata["primary_muscles"],
        "secondary_muscles": metadata["secondary_muscles"],
        "stabilizers": metadata["stabilizers"],
        "is_static": metadata["is_static"],
        "supports_time": metadata["supports_time"],
        "supports_reps": metadata["supports_reps"],
    }
    for field, value in field_updates.items():
        current_value = getattr(exercise, field)
        has_meaningful_value = bool(current_value)
        if field == "modality" and current_value == Exercise.Modality.OTHER:
            has_meaningful_value = False
        if field == "modality" and metadata["modality"] == Exercise.Modality.BODYWEIGHT and current_value != Exercise.Modality.BODYWEIGHT:
            has_meaningful_value = False
        if field == "equipment" and metadata["modality"] == Exercise.Modality.BODYWEIGHT and (current_value or "").strip().lower() == "machine":
            has_meaningful_value = False
        if field in {"supports_time", "supports_reps", "is_static"} and current_value is False:
            has_meaningful_value = False
        if has_meaningful_value and not overwrite:
            continue
        if current_value != value:
            setattr(exercise, field, value)
            changed_fields.append(field)

    remaining_needs_ai = [
        field
        for field in ("equipment", "category", "movement_pattern", "primary_muscles", "secondary_muscles", "stabilizers")
        if not getattr(exercise, field)
    ]
    if use_ai and remaining_needs_ai and settings.OPENAI_API_KEY and not settings.OPENAI_MOCK_RESPONSES:
        ai_metadata = generate_ai_exercise_metadata(
            {
                "name": exercise.name,
                "brand": exercise.brand,
                "line": exercise.line,
                "equipment": exercise.equipment,
                "category": exercise.category,
                "movement_pattern": exercise.movement_pattern,
                "body_region": metadata["body_region"],
                "movement": metadata["movement"],
                "catalog_type": metadata["catalog_type"],
                "raw_catalog_data": exercise.raw_catalog_data,
            }
        )
        for field, value in ai_metadata.items():
            if value and not getattr(exercise, field):
                setattr(exercise, field, value)
                changed_fields.append(field)

    if overwrite or not exercise.instructions:
        payload = _instruction_payload_from_record(
            {
                "name": exercise.name,
                "equipment": exercise.equipment,
                "category": exercise.category,
                "movement_pattern": exercise.movement_pattern,
                "primary_muscles": exercise.primary_muscles,
                "secondary_muscles": exercise.secondary_muscles,
                "stabilizers": exercise.stabilizers,
                "unilateral": exercise.unilateral,
                "is_static": exercise.is_static,
            }
        )
        if use_ai and settings.OPENAI_API_KEY and not settings.OPENAI_MOCK_RESPONSES:
            instructions = generate_ai_instruction(payload)
            instruction_status = Exercise.InstructionStatus.AI_DRAFT
            instruction_source = "openai-enrichment"
        else:
            instructions = build_seed_instruction(payload)
            instruction_status = Exercise.InstructionStatus.SEEDED
            instruction_source = "seed-template-v1"
        if exercise.instructions != instructions:
            exercise.instructions = instructions
            changed_fields.append("instructions")
        if exercise.instructions_status != instruction_status:
            exercise.instructions_status = instruction_status
            changed_fields.append("instructions_status")
        if exercise.instruction_source != instruction_source:
            exercise.instruction_source = instruction_source
            changed_fields.append("instruction_source")

    if changed_fields:
        exercise.save(update_fields=[*dict.fromkeys(changed_fields), "updated_at"])
    return changed_fields
