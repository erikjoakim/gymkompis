import base64
import io
import logging
from uuid import uuid4

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from PIL import Image, ImageDraw
from openai import OpenAI

from .models import Exercise


logger = logging.getLogger(__name__)


def build_exercise_image_prompt(exercise: Exercise) -> str:
    primary_muscles = ", ".join(exercise.primary_muscles[:3]) or "the primary working muscles"
    secondary_muscles = ", ".join(exercise.secondary_muscles[:2])
    stabilizers = ", ".join(exercise.stabilizers[:2])
    exercise_kind = "isometric hold" if exercise.is_static or exercise.supports_time and not exercise.supports_reps else "exercise"
    extra_cue = "Show the held position clearly." if exercise.is_static else "Show the main working position clearly."
    unilateral_cue = "Use a unilateral pose." if exercise.unilateral else "Use a bilateral pose."

    details = [
        f"Create a clean, anatomy-aware instructional fitness illustration of {exercise.name}.",
        f"Depict it as a {exercise_kind} using {exercise.equipment or 'the intended gym setup'}.",
        f"Movement pattern: {exercise.movement_pattern or 'general training movement'}.",
        f"Emphasize the primary muscles: {primary_muscles}.",
        extra_cue,
        unilateral_cue,
        "Use a neutral light background, clear gym-safe clothing, and no logos, watermark, or text overlays.",
        "Keep the body proportions realistic and the pose stable and mechanically plausible.",
    ]
    if secondary_muscles:
        details.append(f"Secondary muscles involved include {secondary_muscles}.")
    if stabilizers:
        details.append(f"Stabilizers include {stabilizers}.")
    return " ".join(details)


def _render_mock_image_bytes(exercise: Exercise) -> bytes:
    image = Image.new("RGB", (1024, 1024), color=(245, 242, 234))
    draw = ImageDraw.Draw(image)
    lines = [
        "GymKompis Draft Image",
        exercise.name,
        exercise.category or "",
        exercise.movement_pattern or "",
    ]
    y = 120
    for line in lines:
        if not line:
            continue
        draw.text((80, y), line, fill=(29, 42, 36))
        y += 90
    draw.rectangle((120, 420, 904, 884), outline=(46, 107, 86), width=8)
    draw.text((180, 620), "AI image placeholder", fill=(46, 107, 86))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _generate_openai_image_bytes(exercise: Exercise, prompt: str) -> tuple[bytes, str]:
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    request_kwargs = {
        "model": settings.OPENAI_IMAGE_MODEL,
        "prompt": prompt,
        "size": settings.OPENAI_IMAGE_SIZE,
        "quality": settings.OPENAI_IMAGE_QUALITY,
        "output_format": "png",
        "moderation": "auto",
    }
    image_model = (settings.OPENAI_IMAGE_MODEL or "").lower()
    if image_model.startswith("dall-e"):
        request_kwargs["response_format"] = "b64_json"

    response = client.images.generate(**request_kwargs)
    b64_json = response.data[0].b64_json
    if not b64_json:
        raise ValueError("Image generation response did not include base64 image data.")
    response_model = getattr(response, "model", None) or settings.OPENAI_IMAGE_MODEL
    return base64.b64decode(b64_json), response_model


def _store_generated_image(exercise: Exercise, image_bytes: bytes, suffix: str = "png") -> None:
    filename = f"{exercise.external_id}_{timezone.now().strftime('%Y%m%d%H%M%S')}.{suffix}"
    if exercise.generated_image:
        try:
            exercise.generated_image.delete(save=False)
        except Exception:
            logger.warning("Could not delete previous generated image for exercise=%s", exercise.pk)
    exercise.generated_image.save(filename, ContentFile(image_bytes), save=False)


def generate_exercise_image_bytes(exercise: Exercise, prompt: str, *, use_mock: bool = False) -> tuple[bytes, str]:
    try:
        if use_mock or settings.OPENAI_MOCK_RESPONSES or not settings.OPENAI_API_KEY:
            image_bytes = _render_mock_image_bytes(exercise)
            image_source = "mock-placeholder-v1"
        else:
            image_bytes, image_source = _generate_openai_image_bytes(exercise, prompt)
    except Exception as exc:
        raise
    return image_bytes, image_source


def save_exercise_image_preview(exercise: Exercise, image_bytes: bytes, *, suffix: str = "png") -> str:
    filename = f"exercise_image_previews/{exercise.external_id}_{timezone.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}.{suffix}"
    return default_storage.save(filename, ContentFile(image_bytes))


def delete_exercise_image_preview(storage_name: str | None) -> None:
    if not storage_name:
        return
    try:
        if default_storage.exists(storage_name):
            default_storage.delete(storage_name)
    except Exception:
        logger.warning("Could not delete preview image %s", storage_name)


def build_exercise_image_preview(exercise: Exercise, prompt: str, *, use_mock: bool = False) -> dict:
    image_bytes, image_source = generate_exercise_image_bytes(exercise, prompt, use_mock=use_mock)
    storage_name = save_exercise_image_preview(exercise, image_bytes)
    return {
        "storage_name": storage_name,
        "image_url": default_storage.url(storage_name),
        "image_source": image_source,
        "prompt": prompt,
    }


def attach_preview_image_to_exercise(
    exercise: Exercise,
    *,
    storage_name: str,
    prompt: str,
    image_source: str,
    mark_reviewed: bool = True,
) -> Exercise:
    with default_storage.open(storage_name, "rb") as image_file:
        image_bytes = image_file.read()
    _store_generated_image(exercise, image_bytes)
    exercise.image_prompt = prompt
    exercise.image_error_message = ""
    exercise.image_status = Exercise.ImageStatus.REVIEWED if mark_reviewed else Exercise.ImageStatus.AI_DRAFT
    exercise.image_source = image_source
    exercise.image_generated_at = timezone.now()
    exercise.save(
        update_fields=[
            "generated_image",
            "image_status",
            "image_prompt",
            "image_source",
            "image_generated_at",
            "image_error_message",
            "updated_at",
        ]
    )
    delete_exercise_image_preview(storage_name)
    return exercise


def generate_and_attach_exercise_image(exercise: Exercise, *, use_mock: bool = False) -> Exercise:
    prompt = build_exercise_image_prompt(exercise)
    exercise.image_prompt = prompt
    exercise.image_error_message = ""

    try:
        image_bytes, image_source = generate_exercise_image_bytes(exercise, prompt, use_mock=use_mock)
        _store_generated_image(exercise, image_bytes)
        exercise.image_status = Exercise.ImageStatus.AI_DRAFT
        exercise.image_source = image_source
        exercise.image_generated_at = timezone.now()
        exercise.save(
            update_fields=[
                "generated_image",
                "image_status",
                "image_prompt",
                "image_source",
                "image_generated_at",
                "image_error_message",
                "updated_at",
            ]
        )
    except Exception as exc:
        exercise.image_status = Exercise.ImageStatus.FAILED
        exercise.image_error_message = str(exc)
        exercise.image_prompt = prompt
        exercise.image_generated_at = timezone.now()
        exercise.save(update_fields=["image_status", "image_error_message", "image_prompt", "image_generated_at", "updated_at"])
        raise

    return exercise
