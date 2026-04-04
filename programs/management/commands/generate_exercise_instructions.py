from django.core.management.base import BaseCommand

from programs.library import build_seed_instruction, generate_ai_instruction
from programs.models import Exercise


class Command(BaseCommand):
    help = "Fill missing exercise instructions with seeded or AI-generated drafts."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=25, help="Maximum number of exercises to process.")
        parser.add_argument("--ai", action="store_true", help="Use OpenAI to create draft instructions.")
        parser.add_argument("--overwrite", action="store_true", help="Regenerate instructions even if already set.")

    def handle(self, *args, **options):
        queryset = Exercise.objects.order_by("name")
        if not options["overwrite"]:
            queryset = queryset.filter(instructions="")
        exercises = list(queryset[: options["limit"]])
        updated = 0
        for exercise in exercises:
            payload = {
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
            if options["ai"]:
                exercise.instructions = generate_ai_instruction(payload)
                exercise.instructions_status = Exercise.InstructionStatus.AI_DRAFT
                exercise.instruction_source = "openai-command"
            else:
                exercise.instructions = build_seed_instruction(payload)
                exercise.instructions_status = Exercise.InstructionStatus.SEEDED
                exercise.instruction_source = "seed-template-v1"
            exercise.save(update_fields=["instructions", "instructions_status", "instruction_source", "updated_at"])
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Updated instructions for {updated} exercises."))
