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
from .manual_services import copy_manual_day, create_manual_exercise_for_day, publish_manual_program
from .models import Exercise, ManualProgramDay, ManualProgramDraft, ManualProgramExercise, ProgramGenerationRequest, TrainingProgram
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
            self.assertTrue(bool(exercise.generated_image))
            self.assertEqual(exercise.image_status, Exercise.ImageStatus.REVIEWED)
            self.assertEqual(exercise.image_prompt, custom_prompt)
            self.assertNotIn("library_admin_image_preview", self.client.session)
            self.assertFalse(default_storage.exists(storage_name))

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
