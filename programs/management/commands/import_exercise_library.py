from django.core.management.base import BaseCommand

from programs.library import import_exercise_library


class Command(BaseCommand):
    help = "Import the exercise library from the JSON files in the project root."

    def add_arguments(self, parser):
        parser.add_argument("--overwrite", action="store_true", help="Overwrite existing exercise records.")
        parser.add_argument(
            "--ai-instructions",
            action="store_true",
            help="Generate AI draft instructions for imported exercises when OpenAI is configured.",
        )

    def handle(self, *args, **options):
        result = import_exercise_library(
            overwrite=options["overwrite"],
            ai_instructions=options["ai_instructions"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Exercise import complete. Created {result['created']}, updated {result['updated']}, scanned {result['total']}."
            )
        )
