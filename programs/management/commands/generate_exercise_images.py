from django.core.management.base import BaseCommand

from programs.image_generation import generate_and_attach_exercise_image
from programs.models import Exercise


class Command(BaseCommand):
    help = "Generate draft exercise images for the exercise library."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10, help="Maximum number of exercises to process.")
        parser.add_argument("--overwrite", action="store_true", help="Regenerate images even if one already exists.")
        parser.add_argument("--mock", action="store_true", help="Generate local placeholder images instead of calling OpenAI.")
        parser.add_argument("--external-id", type=str, help="Generate an image only for a single exercise external_id.")

    def handle(self, *args, **options):
        queryset = Exercise.objects.filter(is_active=True).order_by("name")
        if options.get("external_id"):
            queryset = queryset.filter(external_id=options["external_id"])
        elif not options["overwrite"]:
            queryset = queryset.filter(generated_image="")

        exercises = list(queryset[: options["limit"]])
        if not exercises:
            self.stdout.write("No exercises matched the image-generation criteria.")
            return

        generated = 0
        failed = 0
        for exercise in exercises:
            try:
                generate_and_attach_exercise_image(exercise, use_mock=options["mock"])
            except Exception as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"{exercise.external_id} failed: {exc}"))
            else:
                generated += 1
                self.stdout.write(self.style.SUCCESS(f"{exercise.external_id} -> image draft saved"))

        self.stdout.write(
            self.style.SUCCESS(f"Exercise image generation complete. Generated {generated}, failed {failed}.")
        )
