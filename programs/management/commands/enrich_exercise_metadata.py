from django.core.management.base import BaseCommand

from programs.library import enrich_exercise_metadata, exercise_metadata_gaps
from programs.models import Exercise


class Command(BaseCommand):
    help = "Fill missing exercise metadata with deterministic rules and optional AI enrichment."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50, help="Maximum number of exercises to process.")
        parser.add_argument("--ai", action="store_true", help="Use OpenAI as a fallback for unresolved metadata.")
        parser.add_argument("--overwrite", action="store_true", help="Recompute fields even when they already have values.")

    def handle(self, *args, **options):
        queryset = Exercise.objects.order_by("name", "external_id")
        if not options["overwrite"]:
            queryset = [exercise for exercise in queryset if exercise_metadata_gaps(exercise)]
        else:
            queryset = list(queryset)

        exercises = list(queryset[: options["limit"]])
        updated = 0
        for exercise in exercises:
            changed_fields = enrich_exercise_metadata(
                exercise,
                overwrite=options["overwrite"],
                use_ai=options["ai"],
            )
            if changed_fields:
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Processed {len(exercises)} exercises and updated {updated} of them."))
