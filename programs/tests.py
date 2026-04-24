import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import base64

from django.core.management import call_command
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User

from .image_generation import build_exercise_image_prompt, generate_and_attach_exercise_image
from .library import enrich_exercise_metadata, import_exercise_library
from .draft_services import create_program_draft_exercise_for_day, publish_program_draft
from .manual_services import copy_manual_day, create_manual_exercise_for_day, publish_manual_program
from .models import (
    Exercise,
    ManualProgramDay,
    ManualProgramDraft,
    ManualProgramExercise,
    ProgramDraft,
    ProgramDraftDay,
    ProgramDraftExercise,
    ProgramDraftRevision,
    ProgramGenerationRequest,
    TrainingProgram,
)
from .schemas import clone_sample_program
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

    @override_settings(OPENAI_MOCK_RESPONSES=False, OPENAI_API_KEY="test-key")
    def test_generate_program_retries_empty_response_and_succeeds(self):
        valid_program = clone_sample_program("kg")
        valid_program["program_name"] = "Retry Success Plan"
        valid_program["goal_summary"] = "Retry after an empty model response."
        empty_response = SimpleNamespace(
            id="resp-empty",
            status="completed",
            output_text="",
            output=[],
            usage=SimpleNamespace(input_tokens=12, output_tokens=0),
            incomplete_details=None,
        )
        valid_response = SimpleNamespace(
            id="resp-valid",
            status="completed",
            output_text=json.dumps(valid_program),
            output=[],
            usage=SimpleNamespace(input_tokens=12, output_tokens=450),
            incomplete_details=None,
        )
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=[empty_response, valid_response])))

        with patch("programs.services.OpenAI", return_value=client):
            program = generate_program_for_user(self.user, "Build a retry-safe plan.")

        self.assertEqual(program.name, "Retry Success Plan")
        self.assertEqual(client.responses.create.call_count, 2)
        request = ProgramGenerationRequest.objects.get()
        self.assertEqual(request.status, ProgramGenerationRequest.Status.SUCCEEDED)
        self.assertEqual(request.token_usage_output, 450)
        self.assertIn("Retry Success Plan", request.raw_llm_response)

    @override_settings(OPENAI_MOCK_RESPONSES=False, OPENAI_API_KEY="test-key")
    def test_generate_program_persists_failure_diagnostics_for_empty_responses(self):
        empty_response = SimpleNamespace(
            id="resp-empty",
            status="completed",
            output_text="",
            output=[],
            usage=SimpleNamespace(input_tokens=8, output_tokens=0),
            incomplete_details=None,
        )
        client = SimpleNamespace(
            responses=SimpleNamespace(create=Mock(side_effect=[empty_response, empty_response, empty_response]))
        )

        with patch("programs.services.OpenAI", return_value=client):
            with self.assertRaises(Exception) as exc_info:
                generate_program_for_user(self.user, "Build muscle")

        self.assertIn("Empty model response", str(exc_info.exception))
        self.assertIn("after 3 attempts", str(exc_info.exception))
        self.assertEqual(client.responses.create.call_count, 3)
        request = ProgramGenerationRequest.objects.get()
        self.assertEqual(request.status, ProgramGenerationRequest.Status.FAILED)
        self.assertIn("Empty model response", request.error_message)
        self.assertIn('"response_id": "resp-empty"', request.raw_llm_response)
        self.assertEqual(request.token_usage_input, 8)


class ManualProgramBuilderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="manual@example.com", password="password123")
        self.plank = Exercise.objects.create(
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
        self.row = Exercise.objects.create(
            external_id="fw_999",
            name="Test Row",
            modality=Exercise.Modality.DUMBBELL,
            category="Back",
            movement_pattern="Horizontal pull",
            supports_reps=True,
            supports_time=False,
            equipment="Dumbbell",
            instructions="Row with a stable torso.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )

    def test_publish_manual_program_creates_manual_training_program(self):
        draft = ManualProgramDraft.objects.create(
            user=self.user,
            name="Manual Strength Draft",
            goal_summary="Build strength manually.",
            duration_weeks=6,
            weight_unit="kg",
        )
        day = ManualProgramDay.objects.create(draft=draft, day_key="monday", name="Core Day", day_type="training")
        entry = create_manual_exercise_for_day(day, self.plank, block_type="main")
        entry.target_seconds = 45
        entry.save(update_fields=["target_seconds"])

        program = publish_manual_program(draft)

        self.assertEqual(program.source, TrainingProgram.Source.MANUAL)
        self.assertEqual(program.current_program["program_name"], "Manual Strength Draft")
        self.assertEqual(program.current_program["days"][0]["exercises"][0]["set_plan"][0]["target_seconds"], 45)
        draft.refresh_from_db()
        self.assertEqual(draft.published_program, program)

    def test_copy_manual_day_overwrites_target_days(self):
        draft = ManualProgramDraft.objects.create(
            user=self.user,
            name="Manual Strength Draft",
            goal_summary="Build strength manually.",
            duration_weeks=6,
            weight_unit="kg",
        )
        monday = ManualProgramDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Upper A",
            day_type="training",
            notes="Keep the pace steady.",
        )
        wednesday = ManualProgramDay.objects.create(
            draft=draft,
            day_key="wednesday",
            name="Old Wednesday",
            day_type="mobility",
            notes="Old notes.",
        )
        friday = ManualProgramDay.objects.create(
            draft=draft,
            day_key="friday",
            name="Old Friday",
            day_type="rest",
            notes="Rest only.",
        )
        warmup_entry = create_manual_exercise_for_day(monday, self.plank, block_type=ManualProgramExercise.BlockType.WARMUP)
        warmup_entry.sets_count = 2
        warmup_entry.target_seconds = 40
        warmup_entry.load_guidance = "Easy prep"
        warmup_entry.target_effort_rpe = 5
        warmup_entry.notes = "Brace hard."
        warmup_entry.save()
        main_entry = create_manual_exercise_for_day(monday, self.row, block_type=ManualProgramExercise.BlockType.MAIN)
        main_entry.order = 2
        main_entry.sets_count = 4
        main_entry.target_reps = "10-12"
        main_entry.rest_seconds_override = 90
        main_entry.load_guidance = "Leave 2 reps in reserve"
        main_entry.notes = "Pause at the top."
        main_entry.save()
        stale_entry = create_manual_exercise_for_day(wednesday, self.row, block_type=ManualProgramExercise.BlockType.MAIN)

        copy_manual_day(monday, [wednesday, friday])

        wednesday.refresh_from_db()
        friday.refresh_from_db()
        self.assertEqual(wednesday.name, "Upper A")
        self.assertEqual(wednesday.day_type, "training")
        self.assertEqual(wednesday.notes, "Keep the pace steady.")
        self.assertEqual(friday.name, "Upper A")
        self.assertEqual(friday.day_type, "training")
        self.assertEqual(friday.notes, "Keep the pace steady.")
        self.assertFalse(ManualProgramExercise.objects.filter(pk=stale_entry.pk).exists())

        for target_day in (wednesday, friday):
            copied_entries = list(target_day.manual_exercises.order_by("block_type", "order", "id"))
            self.assertEqual(len(copied_entries), 2)
            copied_by_block = {entry.block_type: entry for entry in copied_entries}
            self.assertEqual(copied_by_block[ManualProgramExercise.BlockType.WARMUP].exercise, self.plank)
            self.assertEqual(copied_by_block[ManualProgramExercise.BlockType.WARMUP].sets_count, 2)
            self.assertEqual(copied_by_block[ManualProgramExercise.BlockType.WARMUP].target_seconds, 40)
            self.assertEqual(copied_by_block[ManualProgramExercise.BlockType.WARMUP].notes, "Brace hard.")
            self.assertEqual(copied_by_block[ManualProgramExercise.BlockType.MAIN].exercise, self.row)
            self.assertEqual(copied_by_block[ManualProgramExercise.BlockType.MAIN].order, 2)
            self.assertEqual(copied_by_block[ManualProgramExercise.BlockType.MAIN].target_reps, "10-12")
            self.assertEqual(copied_by_block[ManualProgramExercise.BlockType.MAIN].rest_seconds_override, 90)
            self.assertEqual(copied_by_block[ManualProgramExercise.BlockType.MAIN].notes, "Pause at the top.")

    def test_manual_day_detail_copy_action_copies_to_selected_days(self):
        self.client.force_login(self.user)
        draft = ManualProgramDraft.objects.create(
            user=self.user,
            name="Manual Strength Draft",
            goal_summary="Build strength manually.",
            duration_weeks=6,
            weight_unit="kg",
        )
        monday = ManualProgramDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Full Body A",
            day_type="training",
            notes="Start with the big lift.",
        )
        wednesday = ManualProgramDay.objects.create(
            draft=draft,
            day_key="wednesday",
            name="Old Wednesday",
            day_type="rest",
            notes="Nothing planned.",
        )
        friday = ManualProgramDay.objects.create(
            draft=draft,
            day_key="friday",
            name="Old Friday",
            day_type="mobility",
            notes="Stretch only.",
        )
        entry = create_manual_exercise_for_day(monday, self.row, block_type=ManualProgramExercise.BlockType.MAIN)
        entry.target_reps = "6-8"
        entry.save(update_fields=["target_reps"])

        response = self.client.post(
            reverse("manual_program_day_detail", args=[draft.id, monday.id]),
            {
                "action": "copy_day",
                "copy-target_day_ids": [str(wednesday.id), str(friday.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        wednesday.refresh_from_db()
        friday.refresh_from_db()
        self.assertEqual(wednesday.name, "Full Body A")
        self.assertEqual(friday.name, "Full Body A")
        self.assertEqual(wednesday.manual_exercises.count(), 1)
        self.assertEqual(friday.manual_exercises.count(), 1)
        self.assertEqual(wednesday.manual_exercises.first().target_reps, "6-8")
        self.assertEqual(friday.manual_exercises.first().target_reps, "6-8")

    def test_manual_day_detail_groups_search_results_by_category(self):
        self.client.force_login(self.user)
        draft = ManualProgramDraft.objects.create(
            user=self.user,
            name="Manual Strength Draft",
            goal_summary="Build strength manually.",
            duration_weeks=6,
            weight_unit="kg",
        )
        day = ManualProgramDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Full Body A",
            day_type="training",
        )
        Exercise.objects.create(
            external_id="bw_1000",
            name="Push-Up",
            modality=Exercise.Modality.BODYWEIGHT,
            category="Chest",
            movement_pattern="Horizontal press",
            supports_reps=True,
            instructions="Press up from the floor.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )
        Exercise.objects.create(
            external_id="db_1000",
            name="Incline Press",
            modality=Exercise.Modality.DUMBBELL,
            category="Chest",
            movement_pattern="Incline press",
            supports_reps=True,
            equipment="Dumbbells",
            instructions="Press on an incline bench.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )
        Exercise.objects.create(
            external_id="db_1001",
            name="Split Squat",
            modality=Exercise.Modality.DUMBBELL,
            category="Legs",
            movement_pattern="Lunge",
            supports_reps=True,
            equipment="Dumbbells",
            instructions="Lower with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )

        response = self.client.get(
            reverse("manual_program_day_detail", args=[draft.id, day.id]),
            {"query": "Dumbbell"},
        )

        self.assertContains(response, '<details class="builder-category-group" open>', count=3, html=False)
        self.assertContains(response, "Back")
        self.assertContains(response, "Chest")
        self.assertContains(response, "Legs")
        self.assertNotContains(response, "keyup changed delay:300ms")
        self.assertContains(response, '<button type="submit">Search</button>', html=False)
        self.assertContains(response, 'hx-swap="innerHTML show:#manual-day-search-panel:top"', html=False)

    def test_manual_day_detail_search_results_support_image_lightbox(self):
        self.client.force_login(self.user)
        draft = ManualProgramDraft.objects.create(
            user=self.user,
            name="Manual Strength Draft",
            goal_summary="Build strength manually.",
            duration_weeks=6,
            weight_unit="kg",
        )
        day = ManualProgramDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Full Body A",
            day_type="training",
        )
        Exercise.objects.create(
            external_id="img_result_1",
            name="Chest Press",
            modality=Exercise.Modality.MACHINE,
            category="Chest",
            movement_pattern="Horizontal Press",
            equipment="Machine",
            image_url="https://example.com/images/chest-press.png",
            instructions="Press with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )

        response = self.client.get(
            reverse("manual_program_day_detail", args=[draft.id, day.id]),
            {"query": "Chest Press"},
        )

        self.assertContains(response, 'class="exercise-image-trigger"', html=False)
        self.assertContains(response, 'id="exercise-image-lightbox"', html=False)

    def test_manual_day_detail_filters_by_brand(self):
        self.client.force_login(self.user)
        draft = ManualProgramDraft.objects.create(
            user=self.user,
            name="Manual Strength Draft",
            goal_summary="Build strength manually.",
            duration_weeks=6,
            weight_unit="kg",
        )
        day = ManualProgramDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Full Body A",
            day_type="training",
        )
        technogym = Exercise.objects.create(
            external_id="tg_1000",
            name="Adductor",
            brand="Technogym",
            line="Selection 900",
            modality=Exercise.Modality.MACHINE,
            category="Lower Body",
            movement_pattern="Hip adduction",
            equipment="Selectorized machine",
            supports_reps=True,
            instructions="Move with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )
        Exercise.objects.create(
            external_id="hs_1000",
            name="Row",
            brand="Hammer Strength",
            line="Plate Loaded",
            modality=Exercise.Modality.MACHINE,
            category="Upper Body",
            movement_pattern="Horizontal pull",
            equipment="Plate-loaded machine",
            supports_reps=True,
            instructions="Pull with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )

        response = self.client.get(
            reverse("manual_program_day_detail", args=[draft.id, day.id]),
            {"brand": "Technogym"},
        )

        self.assertContains(response, "Any brand")
        self.assertContains(response, technogym.name)
        self.assertContains(response, "Technogym")
        self.assertNotContains(response, "Plate-loaded machine")

    def test_manual_day_detail_search_is_fuzzy_for_hyphens_and_spacing(self):
        self.client.force_login(self.user)
        draft = ManualProgramDraft.objects.create(
            user=self.user,
            name="Manual Strength Draft",
            goal_summary="Build strength manually.",
            duration_weeks=6,
            weight_unit="kg",
        )
        day = ManualProgramDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Full Body A",
            day_type="training",
        )
        Exercise.objects.create(
            external_id="lat_pulldown_machine",
            name="Lat Pulldown",
            modality=Exercise.Modality.MACHINE,
            category="Upper Body",
            movement_pattern="Vertical Pull",
            equipment="Machine",
            supports_reps=True,
            instructions="Pull with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )

        response = self.client.get(
            reverse("manual_program_day_detail", args=[draft.id, day.id]),
            {"query": "lat pull-down"},
        )

        self.assertContains(response, "Lat Pulldown")

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

    def test_import_exercise_library_normalizes_brand_catalog_records(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath("bodyweight_exercises.json").write_text("[]", encoding="utf-8")
            root.joinpath("free_weight_exercises.json").write_text("[]", encoding="utf-8")
            root.joinpath("machine_exercises.json").write_text("[]", encoding="utf-8")
            root.joinpath("static_exercises.json").write_text("[]", encoding="utf-8")
            root.joinpath("technogym_catalog.json").write_text(
                '{"brand":"Technogym","lines":[{"line":"Selection 900","type":"selectorized_strength","machines":[{"name":"Selection 900 Adductor","body_region":"lower_body","movement":"hip_adduction"}]}]}',
                encoding="utf-8",
            )

            result = import_exercise_library(base_dir=root)

        exercise = Exercise.objects.get(external_id="technogym__selection-900__adductor")
        self.assertEqual(result["created"], 1)
        self.assertEqual(exercise.name, "Adductor")
        self.assertEqual(exercise.brand, "Technogym")
        self.assertEqual(exercise.line, "Selection 900")
        self.assertEqual(exercise.modality, Exercise.Modality.MACHINE)
        self.assertEqual(exercise.category, "Lower Body")
        self.assertEqual(exercise.movement_pattern, "Hip Adduction")
        self.assertEqual(exercise.equipment, "Selectorized machine")
        self.assertEqual(exercise.primary_muscles, ["Adductors"])
        self.assertIn("source_machine", exercise.raw_catalog_data)

    def test_import_exercise_library_reads_merged_brand_catalog_format(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath("bodyweight_exercises.json").write_text("[]", encoding="utf-8")
            root.joinpath("free_weight_exercises.json").write_text("[]", encoding="utf-8")
            root.joinpath("machine_exercises.json").write_text("[]", encoding="utf-8")
            root.joinpath("static_exercises.json").write_text("[]", encoding="utf-8")
            root.joinpath("catalogs").mkdir()
            root.joinpath("catalogs", "brand_catalog.json").write_text(
                '{"dataset_name":"merged","brands":[{"brand":"Technogym","lines":[{"line":"Selection 900","type":"selectorized_strength","machines":[{"name":"Adductor","movement":"hip_adduction","primary_muscles":["adductors"],"secondary_muscles":["gluteus minimus"]}]}]},{"brand":"Rogue Fitness","lines":[{"line":"Conditioning","type":"cardio","machines":[{"name":"Echo Bike","movement":"air_bike","primary_muscles":["quadriceps","deltoids"],"secondary_muscles":["glutes","hamstrings"]}]}]}]}',
                encoding="utf-8",
            )

            result = import_exercise_library(base_dir=root)

        technogym = Exercise.objects.get(external_id="technogym__selection-900__adductor")
        rogue = Exercise.objects.get(external_id="rogue-fitness__conditioning__echo-bike")
        self.assertEqual(result["created"], 2)
        self.assertEqual(technogym.name, "Adductor")
        self.assertEqual(technogym.brand, "Technogym")
        self.assertEqual(technogym.line, "Selection 900")
        self.assertEqual(technogym.primary_muscles, ["Adductors"])
        self.assertEqual(technogym.secondary_muscles, ["Gluteus Minimus"])
        self.assertEqual(rogue.modality, Exercise.Modality.CARDIO)
        self.assertEqual(rogue.category, "Cardio")
        self.assertEqual(rogue.equipment, "Cardio machine")

    def test_import_exercise_library_reads_bodyweight_dataset_from_catalogs_folder(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath("free_weight_exercises.json").write_text("[]", encoding="utf-8")
            root.joinpath("machine_exercises.json").write_text("[]", encoding="utf-8")
            root.joinpath("static_exercises.json").write_text("[]", encoding="utf-8")
            root.joinpath("catalogs").mkdir()
            root.joinpath("catalogs", "bodyweight_exercises.json").write_text(
                '[{"exercise_id":"bw_456","name":"Bodyweight Squat","equipment":"Body Weight","category":"Lower Body","movement_pattern":"Squat","primary_muscles":["Quadriceps","Glutes"],"secondary_muscles":["Hamstrings"],"stabilizers":["Core"],"unilateral":false}]',
                encoding="utf-8",
            )

            result = import_exercise_library(base_dir=root)

        exercise = Exercise.objects.get(external_id="bw_456")
        self.assertEqual(result["created"], 1)
        self.assertEqual(exercise.modality, Exercise.Modality.BODYWEIGHT)
        self.assertEqual(exercise.equipment, "Body Weight")
        self.assertEqual(exercise.category, "Lower Body")
        self.assertEqual(exercise.movement_pattern, "Squat")

    def test_enrich_exercise_metadata_fills_missing_fields_from_catalog_data(self):
        exercise = Exercise.objects.create(
            external_id="technogym__selection-900__adductor",
            source_dataset="technogym_catalog",
            name="Adductor",
            raw_catalog_data={
                "brand": "Technogym",
                "line": "Selection 900",
                "type": "selectorized_strength",
                "body_region": "lower_body",
                "movement": "hip_adduction",
            },
        )

        changed_fields = enrich_exercise_metadata(exercise)
        exercise.refresh_from_db()

        self.assertIn("brand", changed_fields)
        self.assertEqual(exercise.brand, "Technogym")
        self.assertEqual(exercise.line, "Selection 900")
        self.assertEqual(exercise.modality, Exercise.Modality.MACHINE)
        self.assertEqual(exercise.category, "Lower Body")
        self.assertEqual(exercise.movement_pattern, "Hip Adduction")
        self.assertEqual(exercise.primary_muscles, ["Adductors"])
        self.assertTrue(exercise.instructions)

    def test_enrich_exercise_metadata_command_processes_incomplete_records(self):
        exercise = Exercise.objects.create(
            external_id="technogym__selection-900__lat-machine",
            source_dataset="technogym_catalog",
            name="Lat Machine",
            raw_catalog_data={
                "brand": "Technogym",
                "line": "Selection 900",
                "type": "selectorized_strength",
                "body_region": "upper_body",
                "movement": "vertical_pull",
            },
        )

        call_command("enrich_exercise_metadata", limit=10)
        exercise.refresh_from_db()

        self.assertEqual(exercise.brand, "Technogym")
        self.assertEqual(exercise.category, "Upper Body")
        self.assertEqual(exercise.movement_pattern, "Vertical Pull")
        self.assertTrue(exercise.primary_muscles)
        self.assertTrue(exercise.instructions)

    def test_enrich_exercise_metadata_keeps_bodyweight_exercise_as_bodyweight(self):
        exercise = Exercise.objects.create(
            external_id="bw_bodyweight_squat",
            source_dataset="bodyweight",
            name="Bodyweight Squat",
        )

        enrich_exercise_metadata(exercise)
        exercise.refresh_from_db()

        self.assertEqual(exercise.modality, Exercise.Modality.BODYWEIGHT)
        self.assertEqual(exercise.equipment, "Body Weight")

    def test_enrich_exercise_metadata_corrects_wrong_machine_modality_for_bodyweight(self):
        exercise = Exercise.objects.create(
            external_id="bw_bodyweight_squat_wrong",
            source_dataset="bodyweight",
            name="Bodyweight Squat",
            modality=Exercise.Modality.MACHINE,
            equipment="Machine",
        )

        enrich_exercise_metadata(exercise)
        exercise.refresh_from_db()

        self.assertEqual(exercise.modality, Exercise.Modality.BODYWEIGHT)
        self.assertEqual(exercise.equipment, "Body Weight")

    def test_library_admin_requires_staff(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("library_admin"))

        self.assertEqual(response.status_code, 403)

    def test_library_admin_displays_incomplete_records_with_suggestions(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        Exercise.objects.create(
            external_id="technogym__selection-900__adductor",
            source_dataset="technogym_catalog",
            name="Adductor",
            raw_catalog_data={
                "brand": "Technogym",
                "line": "Selection 900",
                "type": "selectorized_strength",
                "body_region": "lower_body",
                "movement": "hip_adduction",
            },
        )

        response = self.client.get(reverse("library_admin"))

        self.assertContains(response, "Library Admin")
        self.assertContains(response, "Adductor")
        self.assertContains(response, "Suggested Fixes")
        self.assertContains(response, "Hip Adduction")
        self.assertContains(response, "Apply suggestions")
        self.assertNotContains(response, "Suggested:</strong> Technogym", html=False)
        self.assertNotContains(response, "Suggested:</strong> Selection 900", html=False)

    def test_library_admin_pending_review_record_shows_metadata_complete_not_missing_complete(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        submitter = User.objects.create_user(email="submitter@example.com", password="password123")
        Exercise.objects.create(
            external_id="pending_ab_crunch",
            source_dataset="user",
            source_kind=Exercise.SourceKind.AI_SUGGESTED,
            name="Ab Crunch",
            created_by=submitter,
            modality=Exercise.Modality.MACHINE,
            library_role=Exercise.LibraryRole.MAIN,
            equipment="Machine",
            category="Core",
            movement_pattern="Abdominal Crunch",
            primary_muscles=["Abdominals"],
            instructions="Crunch with control.",
            instructions_status=Exercise.InstructionStatus.AI_DRAFT,
            verification_status=Exercise.VerificationStatus.PENDING_REVIEW,
        )

        response = self.client.get(reverse("library_admin"))

        self.assertContains(response, "Ab Crunch")
        self.assertContains(response, "Metadata: Complete")
        self.assertNotContains(response, "Missing: Complete")
        self.assertContains(response, "Status: Pending review")

    def test_library_admin_initial_queue_hides_complete_approved_records(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        Exercise.objects.create(
            external_id="approved_complete_record",
            name="Approved Complete",
            modality=Exercise.Modality.MACHINE,
            library_role=Exercise.LibraryRole.MAIN,
            equipment="Machine",
            category="Upper Body",
            movement_pattern="Vertical Pull",
            primary_muscles=["Lats"],
            instructions="Pull with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
            verification_status=Exercise.VerificationStatus.APPROVED,
        )
        Exercise.objects.create(
            external_id="pending_complete_record",
            source_dataset="user",
            source_kind=Exercise.SourceKind.AI_SUGGESTED,
            name="Pending Complete",
            modality=Exercise.Modality.MACHINE,
            library_role=Exercise.LibraryRole.MAIN,
            equipment="Machine",
            category="Upper Body",
            movement_pattern="Vertical Pull",
            primary_muscles=["Lats"],
            instructions="Pull with control.",
            instructions_status=Exercise.InstructionStatus.AI_DRAFT,
            verification_status=Exercise.VerificationStatus.PENDING_REVIEW,
        )

        response = self.client.get(reverse("library_admin"))

        self.assertNotContains(response, "Approved Complete")
        self.assertContains(response, "Pending Complete")

    def test_library_admin_apply_suggestions_updates_record(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        exercise = Exercise.objects.create(
            external_id="technogym__selection-900__adductor",
            source_dataset="technogym_catalog",
            name="Adductor",
            raw_catalog_data={
                "brand": "Technogym",
                "line": "Selection 900",
                "type": "selectorized_strength",
                "body_region": "lower_body",
                "movement": "hip_adduction",
            },
        )

        response = self.client.post(
            reverse("library_admin"),
            {
                "action": "apply_suggestions",
                "exercise_id": exercise.id,
            },
        )

        self.assertEqual(response.status_code, 302)
        exercise.refresh_from_db()
        self.assertEqual(exercise.brand, "Technogym")
        self.assertEqual(exercise.line, "Selection 900")
        self.assertEqual(exercise.category, "Lower Body")
        self.assertEqual(exercise.movement_pattern, "Hip Adduction")
        self.assertTrue(exercise.instructions)

    def test_library_admin_save_review_allows_manual_correction(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        exercise = Exercise.objects.create(
            external_id="bw_bodyweight_squat",
            source_dataset="bodyweight",
            name="Bodyweight Squat",
        )

        response = self.client.post(
            reverse("library_admin"),
            {
                "action": "save_review",
                "exercise_id": exercise.id,
                f"review-{exercise.id}-brand": "",
                f"review-{exercise.id}-line": "",
                f"review-{exercise.id}-modality": Exercise.Modality.BODYWEIGHT,
                f"review-{exercise.id}-equipment": "Body Weight",
                f"review-{exercise.id}-category": "Lower Body",
                f"review-{exercise.id}-movement_pattern": "Squat",
                f"review-{exercise.id}-primary_muscles": "Quadriceps, Glutes",
                f"review-{exercise.id}-secondary_muscles": "Hamstrings",
                f"review-{exercise.id}-stabilizers": "Core",
                f"review-{exercise.id}-supports_reps": "on",
                f"review-{exercise.id}-instructions": "Sit down and stand up with control.",
            },
        )

        self.assertEqual(response.status_code, 302)
        exercise.refresh_from_db()
        self.assertEqual(exercise.modality, Exercise.Modality.BODYWEIGHT)
        self.assertEqual(exercise.equipment, "Body Weight")
        self.assertEqual(exercise.category, "Lower Body")
        self.assertEqual(exercise.primary_muscles, ["Quadriceps", "Glutes"])
        self.assertEqual(exercise.secondary_muscles, ["Hamstrings"])
        self.assertEqual(exercise.stabilizers, ["Core"])
        self.assertTrue(exercise.supports_reps)
        self.assertEqual(exercise.instructions, "Sit down and stand up with control.")

    def test_library_admin_displays_duplicate_groups(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        Exercise.objects.create(
            external_id="dup_1",
            name="Lat Pulldown",
            brand="Technogym",
            line="Selection 900",
            category="Upper Body",
            movement_pattern="Vertical Pull",
            modality=Exercise.Modality.MACHINE,
            equipment="Selectorized machine",
        )
        Exercise.objects.create(
            external_id="dup_2",
            name="Lat Pulldown",
            brand="Technogym",
            line="Selection 900",
            category="Upper Body",
            movement_pattern="Vertical Pull",
            modality=Exercise.Modality.MACHINE,
            equipment="Selectorized machine",
        )
        Exercise.objects.create(
            external_id="not_dup_brand_variant",
            name="Lat Pulldown",
            brand="Hammer Strength",
            line="MTS",
            category="Upper Body",
            movement_pattern="Vertical Pull",
            modality=Exercise.Modality.MACHINE,
            equipment="Selectorized machine",
        )

        response = self.client.get(reverse("library_admin"))

        self.assertContains(response, "Duplicate Check")
        self.assertContains(response, "2 matching records")
        self.assertContains(response, "dup_1")
        self.assertContains(response, "dup_2")
        self.assertNotContains(response, "3 matching records")

    def test_library_image_admin_preview_can_be_saved_to_database(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        exercise = Exercise.objects.create(
            external_id="lat_machine_image",
            name="Lat Machine",
            brand="Technogym",
            line="Selection 900",
            modality=Exercise.Modality.MACHINE,
            category="Upper Body",
            movement_pattern="Vertical Pull",
            equipment="Selectorized machine",
            primary_muscles=["Lats"],
            secondary_muscles=["Biceps"],
            instructions="Pull with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )

        with TemporaryDirectory() as temp_dir, override_settings(
            MEDIA_ROOT=temp_dir,
            MEDIA_URL="/media/",
            EXERCISE_IMAGE_STATIC_DIR=Path(temp_dir) / "static" / "exercise_images",
            OPENAI_MOCK_RESPONSES=True,
            OPENAI_API_KEY="",
        ):
            response = self.client.get(reverse("library_admin_images"), {"selected": exercise.id})
            self.assertContains(response, "Exercise Image Generator")
            self.assertContains(response, exercise.name)

            custom_prompt = "Create a clear instructional illustration for a lat machine."
            response = self.client.post(
                reverse("library_admin_images"),
                {
                    "action": "generate_image_preview",
                    "exercise_id": exercise.id,
                    "prompt": custom_prompt,
                    "selected": exercise.id,
                },
                follow=True,
            )

            self.assertContains(response, "Generated a preview image")
            preview = self.client.session["library_admin_image_preview"]
            storage_name = preview["storage_name"]
            self.assertEqual(preview["exercise_id"], exercise.id)
            self.assertTrue(default_storage.exists(storage_name))

            response = self.client.post(
                reverse("library_admin_images"),
                {
                    "action": "save_generated_image",
                    "exercise_id": exercise.id,
                    "selected": exercise.id,
                },
                follow=True,
            )

            self.assertContains(response, "Saved the generated image")
            exercise.refresh_from_db()
            self.assertFalse(bool(exercise.generated_image))
            self.assertEqual(exercise.image_url, "/static/exercise_images/lat_machine_image.png")
            self.assertTrue((Path(temp_dir) / "static" / "exercise_images" / "lat_machine_image.png").exists())
            self.assertEqual(exercise.image_status, Exercise.ImageStatus.REVIEWED)
            self.assertEqual(exercise.image_prompt, custom_prompt)
            self.assertContains(response, "Copy Saved Image")
            self.assertNotIn("library_admin_image_preview", self.client.session)
            self.assertFalse(default_storage.exists(storage_name))

    def test_library_image_admin_search_results_show_existing_images(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        exercise = Exercise.objects.create(
            external_id="ab_crunch_with_image",
            name="Abdominal Crunch",
            modality=Exercise.Modality.MACHINE,
            category="Core",
            movement_pattern="Abdominal Crunch",
            equipment="Selectorized machine",
            image_url="https://example.com/images/ab-crunch.png",
            instructions="Crunch with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )

        response = self.client.get(reverse("library_admin_images"), {"query": "Abdominal Crunch"})

        self.assertContains(response, 'src="https://example.com/images/ab-crunch.png"', html=False)
        self.assertContains(response, f'alt="{exercise.name}"', html=False)
        self.assertContains(response, 'class="exercise-image-trigger"', html=False)
        self.assertContains(response, 'id="exercise-image-lightbox"', html=False)

    def test_library_image_admin_preview_can_be_ignored(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        exercise = Exercise.objects.create(
            external_id="ab_crunch_image",
            name="Abdominal Crunch",
            modality=Exercise.Modality.MACHINE,
            category="Core",
            movement_pattern="Abdominal Crunch",
            equipment="Selectorized machine",
            instructions="Crunch with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )

        with TemporaryDirectory() as temp_dir, override_settings(
            MEDIA_ROOT=temp_dir,
            MEDIA_URL="/media/",
            EXERCISE_IMAGE_STATIC_DIR=Path(temp_dir) / "static" / "exercise_images",
            OPENAI_MOCK_RESPONSES=True,
            OPENAI_API_KEY="",
        ):
            self.client.post(
                reverse("library_admin_images"),
                {
                    "action": "generate_image_preview",
                    "exercise_id": exercise.id,
                    "prompt": "Generate a clean crunch machine illustration.",
                    "selected": exercise.id,
                },
            )
            preview = self.client.session["library_admin_image_preview"]
            storage_name = preview["storage_name"]
            self.assertTrue(default_storage.exists(storage_name))

            response = self.client.post(
                reverse("library_admin_images"),
                {
                    "action": "ignore_generated_image",
                    "exercise_id": exercise.id,
                    "selected": exercise.id,
                },
                follow=True,
            )

            self.assertContains(response, "Ignored the generated preview")
            exercise.refresh_from_db()
            self.assertFalse(bool(exercise.generated_image))
            self.assertNotIn("library_admin_image_preview", self.client.session)
            self.assertFalse(default_storage.exists(storage_name))

    def test_library_image_admin_can_copy_saved_image_to_same_named_exercises(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        source = Exercise.objects.create(
            external_id="tg_900_ab_crunch",
            name="Abdominal Crunch",
            brand="Technogym",
            line="Selection 900",
            modality=Exercise.Modality.MACHINE,
            category="Core",
            movement_pattern="Abdominal Crunch",
            equipment="Selectorized machine",
            instructions="Crunch with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )
        target_same_name = Exercise.objects.create(
            external_id="tg_700_ab_crunch",
            name="Abdominal Crunch",
            brand="Technogym",
            line="Selection 700",
            modality=Exercise.Modality.MACHINE,
            category="Core",
            movement_pattern="Abdominal Crunch",
            equipment="Selectorized machine",
            instructions="Crunch with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )
        target_other_brand = Exercise.objects.create(
            external_id="generic_ab_crunch",
            name="Abdominal Crunch",
            modality=Exercise.Modality.MACHINE,
            category="Core",
            movement_pattern="Abdominal Crunch",
            equipment="Machine",
            instructions="Crunch with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )
        different_name = Exercise.objects.create(
            external_id="abdominal_machine",
            name="Abdominal",
            brand="Precor",
            line="Discovery Series",
            modality=Exercise.Modality.MACHINE,
            category="Core",
            movement_pattern="Abdominal Crunch",
            equipment="Selectorized machine",
            instructions="Crunch with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )

        with TemporaryDirectory() as temp_dir, override_settings(
            MEDIA_ROOT=temp_dir,
            MEDIA_URL="/media/",
            EXERCISE_IMAGE_STATIC_DIR=Path(temp_dir) / "static" / "exercise_images",
            OPENAI_MOCK_RESPONSES=True,
            OPENAI_API_KEY="",
        ):
            generate_and_attach_exercise_image(source, use_mock=True)
            source.refresh_from_db()

            response = self.client.get(reverse("library_admin_images"), {"selected": source.id})
            self.assertContains(response, "Copy Saved Image")
            self.assertContains(response, target_same_name.external_id)
            self.assertContains(response, target_other_brand.external_id)
            self.assertNotContains(response, different_name.external_id)

            response = self.client.post(
                reverse("library_admin_images"),
                {
                    "action": "copy_saved_image",
                    "source_exercise_id": source.id,
                    "target_exercise_ids": [str(target_same_name.id), str(target_other_brand.id)],
                    "selected": source.id,
                },
                follow=True,
            )

            self.assertContains(response, "Copied the saved image to 2 exercise records.")
            target_same_name.refresh_from_db()
            target_other_brand.refresh_from_db()
            different_name.refresh_from_db()
            self.assertFalse(bool(target_same_name.generated_image))
            self.assertFalse(bool(target_other_brand.generated_image))
            self.assertEqual(target_same_name.image_url, "/static/exercise_images/tg_700_ab_crunch.png")
            self.assertEqual(target_other_brand.image_url, "/static/exercise_images/generic_ab_crunch.png")
            self.assertTrue((Path(temp_dir) / "static" / "exercise_images" / "tg_700_ab_crunch.png").exists())
            self.assertTrue((Path(temp_dir) / "static" / "exercise_images" / "generic_ab_crunch.png").exists())
            self.assertEqual(target_same_name.image_prompt, source.image_prompt)
            self.assertEqual(target_other_brand.image_prompt, source.image_prompt)
            self.assertFalse(bool(different_name.generated_image))

    def test_library_image_admin_copy_candidates_follow_filtered_search_results(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        selected = Exercise.objects.create(
            external_id="tg_900_abdominal_crunch",
            name="Abdominal Crunch",
            brand="Technogym",
            line="Selection 900",
            modality=Exercise.Modality.MACHINE,
            category="Core",
            movement_pattern="Abdominal Crunch",
            equipment="Selectorized machine",
            instructions="Crunch with control.",
            instructions_status=Exercise.InstructionStatus.AI_DRAFT,
            instruction_source="gpt-4.1-mini:program-v1",
        )
        candidates = [
            Exercise.objects.create(
                external_id="lf_ab_crunch",
                name="Ab Crunch",
                brand="Life Fitness",
                line="Signature Series",
                modality=Exercise.Modality.MACHINE,
                category="Core",
                movement_pattern="Abdominal Crunch",
                equipment="Selectorized machine",
                instructions="Crunch with control.",
                instructions_status=Exercise.InstructionStatus.AI_DRAFT,
                instruction_source="gpt-4.1-mini:program-v1",
            ),
            Exercise.objects.create(
                external_id="matrix_ab_crunch",
                name="Ab Crunch",
                brand="Matrix Fitness",
                line="Ultra Series",
                modality=Exercise.Modality.MACHINE,
                category="Core",
                movement_pattern="Abdominal Crunch",
                equipment="Selectorized machine",
                instructions="Crunch with control.",
                instructions_status=Exercise.InstructionStatus.AI_DRAFT,
                instruction_source="gpt-4.1-mini:program-v1",
            ),
            Exercise.objects.create(
                external_id="precor_abdominal",
                name="Abdominal",
                brand="Precor",
                line="Discovery Series",
                modality=Exercise.Modality.MACHINE,
                category="Core",
                movement_pattern="Abdominal Crunch",
                equipment="Selectorized machine",
                instructions="Crunch with control.",
                instructions_status=Exercise.InstructionStatus.AI_DRAFT,
                instruction_source="gpt-4.1-mini:program-v1",
            ),
            Exercise.objects.create(
                external_id="tg_700_abdominal_crunch",
                name="Abdominal Crunch",
                brand="Technogym",
                line="Selection 700",
                modality=Exercise.Modality.MACHINE,
                category="Core",
                movement_pattern="Abdominal Crunch",
                equipment="Selectorized machine",
                instructions="Crunch with control.",
                instructions_status=Exercise.InstructionStatus.AI_DRAFT,
                instruction_source="gpt-4.1-mini:program-v1",
            ),
        ]
        excluded = Exercise.objects.create(
            external_id="tg_abductor",
            name="Abductor",
            brand="Technogym",
            line="Selection 900",
            modality=Exercise.Modality.MACHINE,
            category="Lower Body",
            movement_pattern="Hip Abduction",
            equipment="Selectorized machine",
            instructions="Move with control.",
            instructions_status=Exercise.InstructionStatus.AI_DRAFT,
            instruction_source="gpt-4.1-mini:program-v1",
        )

        with TemporaryDirectory() as temp_dir, override_settings(
            MEDIA_ROOT=temp_dir,
            MEDIA_URL="/media/",
            OPENAI_MOCK_RESPONSES=True,
            OPENAI_API_KEY="",
        ):
            generate_and_attach_exercise_image(selected, use_mock=True)
            selected.refresh_from_db()

            response = self.client.get(
                reverse("library_admin_images"),
                {"query": "Abdominal Crunch", "selected": selected.id},
            )

        self.assertEqual([exercise.id for exercise in response.context["copy_candidates"]], [item.id for item in candidates])
        self.assertNotIn(excluded.id, [exercise.id for exercise in response.context["copy_candidates"]])

    def test_library_admin_merge_duplicates_marks_canonical_and_hides_group(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        canonical = Exercise.objects.create(
            external_id="dup_canonical",
            name="Lat Pulldown",
            brand="Technogym",
            line="Selection 900",
            category="Upper Body",
            movement_pattern="Vertical Pull",
            modality=Exercise.Modality.MACHINE,
            equipment="Selectorized machine",
        )
        duplicate = Exercise.objects.create(
            external_id="dup_variant",
            name="Lat Pulldown",
            brand="Technogym",
            line="Selection 900",
            category="Upper Body",
            movement_pattern="Vertical Pull",
            modality=Exercise.Modality.MACHINE,
            equipment="Selectorized machine",
        )

        response = self.client.post(
            reverse("library_admin"),
            {
                "action": "merge_duplicates",
                "canonical_exercise_id": canonical.id,
                "duplicate_ids": [str(canonical.id), str(duplicate.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.canonical_exercise_id, canonical.id)

        response = self.client.get(reverse("library_admin"))
        self.assertNotContains(response, "2 matching records")

    def test_manual_builder_hides_non_canonical_duplicates_after_merge(self):
        self.client.force_login(self.user)
        draft = ManualProgramDraft.objects.create(
            user=self.user,
            name="Manual Strength Draft",
            goal_summary="Build strength manually.",
            duration_weeks=6,
            weight_unit="kg",
        )
        day = ManualProgramDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Full Body A",
            day_type="training",
        )
        canonical = Exercise.objects.create(
            external_id="lat_a",
            name="Lat Pulldown",
            brand="Technogym",
            line="Selection 900",
            modality=Exercise.Modality.MACHINE,
            category="Upper Body",
            movement_pattern="Vertical Pull",
            equipment="Selectorized machine",
            instructions="Pull down with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )
        duplicate = Exercise.objects.create(
            external_id="lat_b",
            name="Lat Pulldown",
            brand="Technogym",
            line="Selection 900",
            modality=Exercise.Modality.MACHINE,
            category="Upper Body",
            movement_pattern="Vertical Pull",
            equipment="Selectorized machine",
            instructions="Pull down with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
            canonical_exercise=canonical,
        )

        response = self.client.get(
            reverse("manual_program_day_detail", args=[draft.id, day.id]),
            {"query": "Lat Pulldown"},
        )

        exercise_results = response.context["exercise_results"]
        self.assertEqual([exercise.id for exercise in exercise_results], [canonical.id])

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

    @override_settings(
        OPENAI_MOCK_RESPONSES=False,
        OPENAI_API_KEY="test-key",
        OPENAI_IMAGE_MODEL="gpt-image-1.5",
    )
    def test_generate_image_omits_response_format_for_gpt_image_models(self):
        exercise = Exercise.objects.create(
            external_id="image_api_1",
            name="Abdominal Crunch",
            modality=Exercise.Modality.MACHINE,
            category="Core",
            movement_pattern="Abdominal Crunch",
            equipment="Selectorized machine",
            instructions="Crunch with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )
        client = SimpleNamespace(
            images=SimpleNamespace(
                generate=Mock(
                    return_value=SimpleNamespace(
                        data=[SimpleNamespace(b64_json=base64.b64encode(b"png-bytes").decode("ascii"))],
                    )
                )
            )
        )

        with patch("programs.image_generation.OpenAI", return_value=client):
            generate_and_attach_exercise_image(exercise)

        request_kwargs = client.images.generate.call_args.kwargs
        self.assertNotIn("response_format", request_kwargs)
        self.assertEqual(request_kwargs["output_format"], "png")
        exercise.refresh_from_db()
        self.assertEqual(exercise.image_source, "gpt-image-1.5")

    def test_manual_builder_no_results_offers_ai_exercise_draft(self):
        self.client.force_login(self.user)
        draft = ManualProgramDraft.objects.create(
            user=self.user,
            name="Manual Strength Draft",
            goal_summary="Build strength manually.",
            duration_weeks=6,
            weight_unit="kg",
        )
        day = ManualProgramDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Full Body A",
            day_type="training",
        )

        response = self.client.get(
            reverse("manual_program_day_detail", args=[draft.id, day.id]),
            {"query": "Cable Lat Pull-Down"},
        )

        self.assertContains(response, "No exercises match the current filter.")
        self.assertContains(response, "AI Exercise Search")
        self.assertContains(response, "Draft exercise with AI")
        self.assertContains(response, '<option value="cable">Cable</option>', html=False)

    def test_manual_builder_no_results_still_offers_ai_draft_with_cable_modality_filter(self):
        self.client.force_login(self.user)
        draft = ManualProgramDraft.objects.create(
            user=self.user,
            name="Manual Strength Draft",
            goal_summary="Build strength manually.",
            duration_weeks=6,
            weight_unit="kg",
        )
        day = ManualProgramDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Full Body A",
            day_type="training",
        )

        response = self.client.get(
            reverse("manual_program_day_detail", args=[draft.id, day.id]),
            {"query": "Cable Lat Pull-Down", "modality": Exercise.Modality.CABLE},
        )

        self.assertContains(response, "No exercises match the current filter.")
        self.assertContains(response, "AI Exercise Search")
        self.assertContains(response, "Draft exercise with AI")

    def test_manual_builder_still_offers_ai_draft_when_results_exist(self):
        self.client.force_login(self.user)
        draft = ManualProgramDraft.objects.create(
            user=self.user,
            name="Manual Strength Draft",
            goal_summary="Build strength manually.",
            duration_weeks=6,
            weight_unit="kg",
        )
        day = ManualProgramDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Full Body A",
            day_type="training",
        )
        Exercise.objects.create(
            external_id="machine_lat_pulldown",
            name="Lat Pulldown",
            modality=Exercise.Modality.MACHINE,
            category="Upper Body",
            movement_pattern="Vertical Pull",
            equipment="Machine",
            instructions="Pull down with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )

        response = self.client.get(
            reverse("manual_program_day_detail", args=[draft.id, day.id]),
            {"query": "Lat Pulldown"},
        )

        self.assertContains(response, "Lat Pulldown")
        self.assertContains(response, "AI Exercise Search")
        self.assertContains(response, "Draft exercise with AI")

    @override_settings(OPENAI_MOCK_RESPONSES=True, OPENAI_API_KEY="")
    def test_user_can_save_ai_suggested_exercise_as_pending_and_only_creator_sees_it(self):
        self.client.force_login(self.user)
        draft = ManualProgramDraft.objects.create(
            user=self.user,
            name="Manual Strength Draft",
            goal_summary="Build strength manually.",
            duration_weeks=6,
            weight_unit="kg",
        )
        day = ManualProgramDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Full Body A",
            day_type="training",
        )

        response = self.client.post(
            reverse("manual_program_day_detail", args=[draft.id, day.id]),
            {
                "action": "generate_ai_exercise_suggestion",
                "query": "Cable Lat Pull-Down",
            },
            follow=True,
        )

        self.assertContains(response, "Drafted a new exercise suggestion")
        self.assertContains(response, "Save pending exercise")
        self.assertContains(response, "Cable Lat Pull-Down")

        response = self.client.post(
            reverse("manual_program_day_detail", args=[draft.id, day.id]),
            {
                "action": "save_user_exercise_submission",
                "query": "Cable Lat Pull-Down",
                "submission-name": "Cable Lat Pull Down",
                "submission-aliases": "",
                "submission-brand": "",
                "submission-line": "",
                "submission-modality": Exercise.Modality.CABLE,
                "submission-library_role": Exercise.LibraryRole.MAIN,
                "submission-equipment": "Cable",
                "submission-category": "Upper Body",
                "submission-movement_pattern": "Vertical Pull",
                "submission-primary_muscles": "Lats, Upper Back",
                "submission-secondary_muscles": "Biceps",
                "submission-stabilizers": "Core",
                "submission-supports_reps": "on",
                "submission-instructions": "Set the cable high, pull the handle down toward the upper chest, and keep your torso steady.",
                "submission-submission_query": "Cable Lat Pull-Down",
                "submission-source_kind": Exercise.SourceKind.AI_SUGGESTED,
            },
            follow=True,
        )

        self.assertContains(response, "pending staff review")
        exercise = Exercise.objects.get(name="Cable Lat Pull Down")
        self.assertEqual(exercise.created_by, self.user)
        self.assertEqual(exercise.verification_status, Exercise.VerificationStatus.PENDING_REVIEW)
        self.assertEqual(exercise.source_kind, Exercise.SourceKind.AI_SUGGESTED)
        self.assertEqual(exercise.modality, Exercise.Modality.CABLE)

        response = self.client.get(
            reverse("manual_program_day_detail", args=[draft.id, day.id]),
            {"query": "Cable Lat Pull-Down"},
        )
        self.assertContains(response, "Cable Lat Pull Down")
        self.assertContains(response, "Pending staff review")

        other_user = User.objects.create_user(email="other@example.com", password="password123")
        other_draft = ManualProgramDraft.objects.create(
            user=other_user,
            name="Other Draft",
            goal_summary="Other user plan.",
            duration_weeks=6,
            weight_unit="kg",
        )
        other_day = ManualProgramDay.objects.create(
            draft=other_draft,
            day_key="monday",
            name="Other Day",
            day_type="training",
        )
        self.client.force_login(other_user)
        response = self.client.get(
            reverse("manual_program_day_detail", args=[other_draft.id, other_day.id]),
            {"query": "Cable Lat Pull-Down"},
        )
        self.assertNotContains(response, "Cable Lat Pull Down")

    def test_library_admin_can_approve_pending_user_exercise(self):
        staff_user = User.objects.create_user(email="staff@example.com", password="password123", is_staff=True)
        submitted = Exercise.objects.create(
            external_id="user__1__cable-lat-pull-down__abcd1234",
            source_dataset="user",
            source_kind=Exercise.SourceKind.AI_SUGGESTED,
            name="Cable Lat Pull Down",
            aliases=["Cable Lat Pull-Down"],
            created_by=self.user,
            modality=Exercise.Modality.CABLE,
            library_role=Exercise.LibraryRole.MAIN,
            equipment="Cable",
            category="Upper Body",
            movement_pattern="Vertical Pull",
            primary_muscles=["Lats", "Upper Back"],
            secondary_muscles=["Biceps"],
            stabilizers=["Core"],
            instructions="Pull down with control.",
            instructions_status=Exercise.InstructionStatus.AI_DRAFT,
            verification_status=Exercise.VerificationStatus.PENDING_REVIEW,
        )

        self.client.force_login(staff_user)
        response = self.client.post(
            reverse("library_admin"),
            {
                "action": "approve_exercise",
                "exercise_id": submitted.id,
            },
            follow=True,
        )

        self.assertContains(response, "Approved Cable Lat Pull Down")
        submitted.refresh_from_db()
        self.assertEqual(submitted.verification_status, Exercise.VerificationStatus.APPROVED)
        self.assertEqual(submitted.verified_by, staff_user)
        self.assertIsNotNone(submitted.verified_at)

        other_user = User.objects.create_user(email="approved-other@example.com", password="password123")
        other_draft = ManualProgramDraft.objects.create(
            user=other_user,
            name="Approved Draft",
            goal_summary="Approved search.",
            duration_weeks=6,
            weight_unit="kg",
        )
        other_day = ManualProgramDay.objects.create(
            draft=other_draft,
            day_key="monday",
            name="Approved Day",
            day_type="training",
        )
        self.client.force_login(other_user)
        response = self.client.get(
            reverse("manual_program_day_detail", args=[other_draft.id, other_day.id]),
            {"query": "Cable Lat Pull-Down"},
        )
        self.assertContains(response, "Cable Lat Pull Down")

    def test_library_admin_can_reject_pending_user_exercise(self):
        staff_user = User.objects.create_user(email="reject-staff@example.com", password="password123", is_staff=True)
        creator = User.objects.create_user(email="reject-owner@example.com", password="password123")
        submitted = Exercise.objects.create(
            external_id="user__2__cable-lat-pull-down__efgh5678",
            source_dataset="user",
            source_kind=Exercise.SourceKind.AI_SUGGESTED,
            name="Cable Lat Pull Down",
            aliases=["Cable Lat Pull-Down"],
            created_by=creator,
            modality=Exercise.Modality.CABLE,
            library_role=Exercise.LibraryRole.MAIN,
            equipment="Cable",
            category="Upper Body",
            movement_pattern="Vertical Pull",
            primary_muscles=["Lats", "Upper Back"],
            secondary_muscles=["Biceps"],
            stabilizers=["Core"],
            instructions="Pull down with control.",
            instructions_status=Exercise.InstructionStatus.AI_DRAFT,
            verification_status=Exercise.VerificationStatus.PENDING_REVIEW,
        )
        creator_draft = ManualProgramDraft.objects.create(
            user=creator,
            name="Creator Draft",
            goal_summary="Rejected search.",
            duration_weeks=6,
            weight_unit="kg",
        )
        creator_day = ManualProgramDay.objects.create(
            draft=creator_draft,
            day_key="monday",
            name="Creator Day",
            day_type="training",
        )

        self.client.force_login(staff_user)
        response = self.client.post(
            reverse("library_admin"),
            {
                "action": "reject_exercise",
                "exercise_id": submitted.id,
            },
            follow=True,
        )

        self.assertContains(response, "Rejected Cable Lat Pull Down")
        submitted.refresh_from_db()
        self.assertEqual(submitted.verification_status, Exercise.VerificationStatus.REJECTED)

        self.client.force_login(creator)
        response = self.client.get(
            reverse("manual_program_day_detail", args=[creator_draft.id, creator_day.id]),
            {"query": "Cable Lat Pull-Down"},
        )
        self.assertNotContains(response, "Cable Lat Pull Down")


@override_settings(OPENAI_MOCK_RESPONSES=True, OPENAI_API_KEY="")
class UnifiedProgramDraftTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="drafts@example.com", password="password123")
        self.client.force_login(self.user)
        self.row = Exercise.objects.create(
            external_id="fw_10001",
            name="Test Row",
            modality=Exercise.Modality.DUMBBELL,
            category="Back",
            movement_pattern="Horizontal pull",
            equipment="Dumbbell",
            supports_reps=True,
            supports_time=False,
            instructions="Row with a stable torso and controlled elbow path.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )

    def test_generate_program_view_creates_ai_seeded_draft(self):
        response = self.client.post(
            reverse("generate_program"),
            {"prompt_text": "Build me a balanced beginner plan."},
            follow=True,
        )

        draft = ProgramDraft.objects.get(user=self.user)
        self.assertEqual(draft.source, ProgramDraft.Source.AI_SEEDED)
        self.assertRedirects(response, reverse("manual_program_detail", args=[draft.id]))
        self.assertContains(response, "AI-seeded program draft is ready")

    def test_program_draft_can_publish_with_unified_publish_service(self):
        draft = ProgramDraft.objects.create(
            user=self.user,
            name="Unified Draft",
            goal_summary="Build strength.",
            duration_weeks=6,
            weight_unit="kg",
            source=ProgramDraft.Source.MANUAL,
        )
        day = ProgramDraftDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Full Body",
            day_type="training",
        )
        entry = create_program_draft_exercise_for_day(day, self.row, block_type=ProgramDraftExercise.BlockType.MAIN)
        entry.target_reps = "6-8"
        entry.save(update_fields=["target_reps"])

        program = publish_program_draft(draft)

        self.assertEqual(program.current_program["program_name"], "Unified Draft")
        self.assertEqual(program.current_program["days"][0]["exercises"][0]["name"], "Test Row")
        draft.refresh_from_db()
        self.assertEqual(draft.published_program, program)

    def test_manual_program_detail_can_complete_incomplete_days_with_ai(self):
        draft = ProgramDraft.objects.create(
            user=self.user,
            name="Hybrid Draft",
            goal_summary="Complete the missing days.",
            duration_weeks=8,
            weight_unit="kg",
            source=ProgramDraft.Source.MANUAL,
        )
        monday = ProgramDraftDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Upper",
            day_type="training",
        )
        ProgramDraftDay.objects.create(
            draft=draft,
            day_key="wednesday",
            name="Lower",
            day_type="training",
        )
        create_program_draft_exercise_for_day(monday, self.row, block_type=ProgramDraftExercise.BlockType.MAIN)

        response = self.client.post(
            reverse("manual_program_detail", args=[draft.id]),
            {"action": "complete_incomplete_days"},
            follow=True,
        )

        draft.refresh_from_db()
        wednesday = draft.days.get(day_key="wednesday")
        self.assertRedirects(response, reverse("manual_program_detail", args=[draft.id]))
        self.assertContains(response, "AI completed 1 incomplete day")
        self.assertTrue(
            wednesday.draft_exercises.filter(block_type=ProgramDraftExercise.BlockType.MAIN).exists()
        )
        self.assertEqual(draft.source, ProgramDraft.Source.HYBRID)

    def test_manual_program_detail_can_store_ai_evaluation(self):
        draft = ProgramDraft.objects.create(
            user=self.user,
            name="Evaluation Draft",
            goal_summary="Review me.",
            duration_weeks=8,
            weight_unit="kg",
            source=ProgramDraft.Source.MANUAL,
        )
        ProgramDraftDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Upper",
            day_type="training",
        )

        response = self.client.post(
            reverse("manual_program_detail", args=[draft.id]),
            {"action": "evaluate_draft"},
            follow=True,
        )

        draft.refresh_from_db()
        self.assertRedirects(response, reverse("manual_program_detail", args=[draft.id]))
        self.assertContains(response, "AI reviewed the draft")
        self.assertIn("findings", draft.latest_ai_evaluation)

    def test_manual_program_detail_can_apply_evaluation_suggested_action(self):
        draft = ProgramDraft.objects.create(
            user=self.user,
            name="Suggestion Draft",
            goal_summary="Needs AI action.",
            duration_weeks=8,
            weight_unit="kg",
            source=ProgramDraft.Source.MANUAL,
            latest_ai_evaluation={
                "summary": "Needs one completed day.",
                "findings": [],
                "suggested_actions": [
                    {
                        "action_type": "complete_day",
                        "target_day": "wednesday",
                        "reason": "Fill Wednesday.",
                    }
                ],
            },
        )
        ProgramDraftDay.objects.create(draft=draft, day_key="wednesday", name="Wednesday", day_type="training")

        response = self.client.post(
            reverse("manual_program_detail", args=[draft.id]),
            {"action": "apply_evaluation_action", "action_index": "0"},
            follow=True,
        )

        draft.refresh_from_db()
        self.assertRedirects(response, reverse("manual_program_detail", args=[draft.id]))
        self.assertContains(response, "Applied the AI suggestion")
        self.assertTrue(
            draft.days.get(day_key="wednesday").draft_exercises.filter(block_type=ProgramDraftExercise.BlockType.MAIN).exists()
        )

    def test_program_detail_can_clone_published_program_into_builder(self):
        program = generate_program_for_user(self.user, "Create a straightforward training plan.")

        response = self.client.post(
            reverse("clone_program_to_draft", args=[program.id]),
            follow=True,
        )

        draft = ProgramDraft.objects.exclude(request_prompt="").order_by("-id").first()
        self.assertIsNotNone(draft)
        self.assertRedirects(response, reverse("manual_program_detail", args=[draft.id]))
        self.assertContains(response, "Created an editable draft from this published program")
        self.assertEqual(draft.name, program.name)
        self.assertTrue(draft.days.exists())

    def test_manual_program_detail_can_restore_revision(self):
        draft = ProgramDraft.objects.create(
            user=self.user,
            name="Revision Draft",
            goal_summary="Before change",
            duration_weeks=6,
            weight_unit="kg",
            source=ProgramDraft.Source.MANUAL,
        )
        ProgramDraftDay.objects.create(draft=draft, day_key="monday", name="Monday", day_type="training")
        revision = ProgramDraftRevision.objects.create(
            draft=draft,
            revision_number=1,
            created_by_user=self.user,
            source=ProgramDraftRevision.Source.MANUAL,
            action_type="seed",
            summary="Initial snapshot",
            draft_snapshot_json={
                "name": "Restored Draft",
                "goal_summary": "Original state",
                "duration_weeks": 8,
                "weight_unit": "kg",
                "program_notes": "",
                "status": "draft",
                "source": "manual",
                "request_prompt": "",
                "ai_context_notes": "",
                "last_ai_action": "",
                "days": [
                    {
                        "day_key": "wednesday",
                        "name": "Wednesday",
                        "day_type": "training",
                        "notes": "",
                        "ai_locked": False,
                        "entries": [],
                    }
                ],
            },
        )

        response = self.client.post(
            reverse("manual_program_detail", args=[draft.id]),
            {"action": "restore_revision", "revision_id": revision.id},
            follow=True,
        )

        draft.refresh_from_db()
        self.assertRedirects(response, reverse("manual_program_detail", args=[draft.id]))
        self.assertContains(response, "Restored revision 1")
        self.assertEqual(draft.name, "Restored Draft")
        self.assertTrue(draft.days.filter(day_key="wednesday").exists())

    def test_manual_program_detail_shows_revision_diff_summary(self):
        draft = ProgramDraft.objects.create(
            user=self.user,
            name="Diff Draft",
            goal_summary="Current state",
            duration_weeks=6,
            weight_unit="kg",
            source=ProgramDraft.Source.MANUAL,
        )
        ProgramDraftDay.objects.create(draft=draft, day_key="monday", name="Monday Current", day_type="training")
        ProgramDraftRevision.objects.create(
            draft=draft,
            revision_number=1,
            created_by_user=self.user,
            source=ProgramDraftRevision.Source.MANUAL,
            action_type="snapshot",
            summary="Earlier state",
            draft_snapshot_json={
                "name": "Diff Draft",
                "goal_summary": "Earlier state",
                "duration_weeks": 6,
                "weight_unit": "kg",
                "program_notes": "",
                "status": "draft",
                "source": "manual",
                "request_prompt": "",
                "ai_context_notes": "",
                "last_ai_action": "",
                "days": [
                    {
                        "day_key": "monday",
                        "name": "Monday Earlier",
                        "day_type": "training",
                        "notes": "",
                        "ai_locked": False,
                        "entries": [],
                    }
                ],
            },
        )

        response = self.client.get(reverse("manual_program_detail", args=[draft.id]))

        self.assertContains(response, "Top-level changes")
        self.assertContains(response, "Monday")

    def test_ai_completion_skips_locked_days_and_preserves_locked_exercises(self):
        draft = ProgramDraft.objects.create(
            user=self.user,
            name="Locked Draft",
            goal_summary="Protect manual work.",
            duration_weeks=8,
            weight_unit="kg",
            source=ProgramDraft.Source.MANUAL,
        )
        monday = ProgramDraftDay.objects.create(
            draft=draft,
            day_key="monday",
            name="Monday",
            day_type="training",
            ai_locked=True,
        )
        wednesday = ProgramDraftDay.objects.create(
            draft=draft,
            day_key="wednesday",
            name="Wednesday",
            day_type="training",
        )
        locked_entry = create_program_draft_exercise_for_day(monday, self.row, block_type=ProgramDraftExercise.BlockType.MAIN)
        locked_entry.ai_locked = True
        locked_entry.target_reps = "5"
        locked_entry.save(update_fields=["ai_locked", "target_reps"])

        response = self.client.post(
            reverse("manual_program_detail", args=[draft.id]),
            {"action": "complete_selected_days", "selected_day_keys": ["monday"]},
            follow=True,
        )

        self.assertContains(response, "Unlock Monday before asking AI to rewrite those days")

        response = self.client.post(
            reverse("manual_program_detail", args=[draft.id]),
            {"action": "complete_incomplete_days"},
            follow=True,
        )

        monday.refresh_from_db()
        wednesday.refresh_from_db()
        self.assertRedirects(response, reverse("manual_program_detail", args=[draft.id]))
        self.assertTrue(monday.draft_exercises.filter(pk=locked_entry.pk).exists())
        self.assertFalse(monday.draft_exercises.exclude(pk=locked_entry.pk).exists())
        self.assertTrue(wednesday.draft_exercises.filter(block_type=ProgramDraftExercise.BlockType.MAIN).exists())
