from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings

from accounts.models import User

from .image_generation import build_exercise_image_prompt, generate_and_attach_exercise_image
from .library import import_exercise_library
from .manual_services import create_manual_exercise_for_day, publish_manual_program
from .models import Exercise, ManualProgramDay, ManualProgramDraft, TrainingProgram
from .services import generate_program_for_user, restore_program_for_user


@override_settings(OPENAI_MOCK_RESPONSES=True, OPENAI_API_KEY="")
class ProgramGenerationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="program@example.com", password="password123")

    def test_generate_program_creates_active_program(self):
        program = generate_program_for_user(self.user, "Build a beginner three-day program.")
        self.assertEqual(program.status, TrainingProgram.Status.ACTIVE)
        self.assertEqual(program.current_program["version"], 1)
        self.assertEqual(program.current_program["weight_unit"], "kg")
        self.assertTrue(program.current_program["days"][0]["warmup"])
        self.assertEqual(program.current_program["days"][0]["warmup"][0]["set_plan"][0]["prescription_type"], "time")
        self.assertEqual(program.current_program["days"][0]["exercises"][0]["set_plan"][0]["prescription_type"], "reps")

    def test_generating_second_program_archives_previous(self):
        first = generate_program_for_user(self.user, "First")
        second = generate_program_for_user(self.user, "Second")
        first.refresh_from_db()
        self.assertEqual(first.status, TrainingProgram.Status.ARCHIVED)
        self.assertEqual(second.status, TrainingProgram.Status.ACTIVE)

    def test_restore_program_creates_new_active_copy(self):
        first = generate_program_for_user(self.user, "First")
        second = generate_program_for_user(self.user, "Second")
        first.refresh_from_db()
        self.assertEqual(first.status, TrainingProgram.Status.ARCHIVED)

        restored = restore_program_for_user(self.user, first)

        second.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual(second.status, TrainingProgram.Status.ARCHIVED)
        self.assertEqual(first.status, TrainingProgram.Status.ARCHIVED)
        self.assertEqual(restored.status, TrainingProgram.Status.ACTIVE)
        self.assertEqual(restored.version_number, second.version_number + 1)
        self.assertEqual(restored.name, first.name)
        self.assertEqual(restored.current_program, first.current_program)


class ManualProgramBuilderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="manual@example.com", password="password123")

    def test_publish_manual_program_creates_manual_training_program(self):
        exercise = Exercise.objects.create(
            external_id="bw_999",
            name="Test Plank",
            modality=Exercise.Modality.BODYWEIGHT,
            category="Core",
            movement_pattern="Anti-extension hold",
            supports_reps=False,
            supports_time=True,
            is_static=True,
            instructions="Hold a solid plank position with steady breathing.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )
        draft = ManualProgramDraft.objects.create(
            user=self.user,
            name="Manual Strength Draft",
            goal_summary="Build strength manually.",
            duration_weeks=6,
            weight_unit="kg",
        )
        day = ManualProgramDay.objects.create(draft=draft, day_key="monday", name="Core Day", day_type="training")
        entry = create_manual_exercise_for_day(day, exercise, block_type="main")
        entry.target_seconds = 45
        entry.save(update_fields=["target_seconds"])

        program = publish_manual_program(draft)

        self.assertEqual(program.source, TrainingProgram.Source.MANUAL)
        self.assertEqual(program.current_program["program_name"], "Manual Strength Draft")
        self.assertEqual(program.current_program["days"][0]["exercises"][0]["set_plan"][0]["target_seconds"], 45)
        draft.refresh_from_db()
        self.assertEqual(draft.published_program, program)

    def test_import_exercise_library_creates_seeded_instructions(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath("bodyweight_exercises.json").write_text(
                '[{"exercise_id":"bw_123","name":"Push-Up","equipment":"Body Weight","category":"Upper Body Push","movement_pattern":"Horizontal press","primary_muscles":["Chest"],"secondary_muscles":["Triceps"],"stabilizers":["Core"],"unilateral":false}]',
                encoding="utf-8",
            )
            root.joinpath("free_weight_exercises.json").write_text("[]", encoding="utf-8")
            root.joinpath("machine_exercises.json").write_text("[]", encoding="utf-8")
            root.joinpath("static_exercises.json").write_text("[]", encoding="utf-8")

            result = import_exercise_library(base_dir=root)

        exercise = Exercise.objects.get(external_id="bw_123")
        self.assertEqual(result["created"], 1)
        self.assertEqual(exercise.instructions_status, Exercise.InstructionStatus.SEEDED)
        self.assertTrue(exercise.instructions)

    def test_mock_image_generation_attaches_draft_image(self):
        exercise = Exercise.objects.create(
            external_id="static_999",
            name="Front Plank",
            modality=Exercise.Modality.BODYWEIGHT,
            category="Core",
            movement_pattern="Anti-extension isometric hold",
            supports_reps=False,
            supports_time=True,
            is_static=True,
            instructions="Hold a strong plank position.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )

        prompt = build_exercise_image_prompt(exercise)
        generate_and_attach_exercise_image(exercise, use_mock=True)
        exercise.refresh_from_db()

        self.assertIn("Front Plank", prompt)
        self.assertEqual(exercise.image_status, Exercise.ImageStatus.AI_DRAFT)
        self.assertTrue(bool(exercise.generated_image))
