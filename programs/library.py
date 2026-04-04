import json
from pathlib import Path

from django.conf import settings

from openai import OpenAI

from core.json_utils import extract_response_text

from .models import Exercise


SOURCE_FILES = {
    "bodyweight": "bodyweight_exercises.json",
    "freeweight": "free_weight_exercises.json",
    "machine": "machine_exercises.json",
    "static": "static_exercises.json",
}


def infer_modality(source_dataset: str, equipment: str, category: str) -> str:
    equipment_text = (equipment or "").lower()
    category_text = (category or "").lower()
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
    if source_dataset in {"bodyweight", "static"}:
        return Exercise.Modality.BODYWEIGHT
    if source_dataset == "machine":
        return Exercise.Modality.MACHINE
    return Exercise.Modality.OTHER


def infer_library_role(source_dataset: str, category: str) -> str:
    category_text = (category or "").lower()
    if category_text in {"mobility", "shoulder health"}:
        return Exercise.LibraryRole.BOTH
    if source_dataset == "static":
        return Exercise.LibraryRole.BOTH
    return Exercise.LibraryRole.MAIN


def infer_supports_time(source_dataset: str, movement_pattern: str, category: str) -> bool:
    pattern = (movement_pattern or "").lower()
    category_text = (category or "").lower()
    return source_dataset == "static" or "hold" in pattern or "isometric" in pattern or category_text == "mobility"


def infer_supports_reps(source_dataset: str, movement_pattern: str, category: str) -> bool:
    if source_dataset == "static":
        return False
    pattern = (movement_pattern or "").lower()
    category_text = (category or "").lower()
    return "hold" not in pattern and category_text != "mobility"


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


def normalize_exercise_record(source_dataset: str, record: dict) -> dict:
    movement_pattern = record.get("movement_pattern", "")
    category = record.get("category", "")
    equipment = record.get("equipment", "")
    is_static = source_dataset == "static" or "hold" in movement_pattern.lower() or "isometric" in movement_pattern.lower()
    supports_time = infer_supports_time(source_dataset, movement_pattern, category)
    supports_reps = infer_supports_reps(source_dataset, movement_pattern, category)

    payload = {
        "external_id": record["exercise_id"],
        "source_dataset": source_dataset,
        "name": record["name"],
        "aliases": [],
        "modality": infer_modality(source_dataset, equipment, category),
        "library_role": infer_library_role(source_dataset, category),
        "equipment": equipment,
        "category": category,
        "movement_pattern": movement_pattern,
        "primary_muscles": record.get("primary_muscles", []),
        "secondary_muscles": record.get("secondary_muscles", []),
        "stabilizers": record.get("stabilizers", []),
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
        "is_active": True,
    }
    return payload


def load_source_records(base_dir: Path | str | None = None) -> list[dict]:
    root = Path(base_dir or settings.BASE_DIR)
    records: list[dict] = []
    for source_dataset, filename in SOURCE_FILES.items():
        path = root / filename
        if not path.exists():
            continue
        source_items = json.loads(path.read_text(encoding="utf-8"))
        for item in source_items:
            records.append(normalize_exercise_record(source_dataset, item))
    return records


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
        payload_for_instruction = {
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
        if ai_instructions and settings.OPENAI_API_KEY and not settings.OPENAI_MOCK_RESPONSES:
            defaults["instructions"] = generate_ai_instruction(payload_for_instruction)
            defaults["instructions_status"] = Exercise.InstructionStatus.AI_DRAFT
            defaults["instruction_source"] = f"{settings.OPENAI_MODEL}:{settings.OPENAI_PROGRAM_PROMPT_VERSION}"
        else:
            defaults["instructions"] = build_seed_instruction(payload_for_instruction)
            defaults["instructions_status"] = Exercise.InstructionStatus.SEEDED
            defaults["instruction_source"] = "seed-template-v1"

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
