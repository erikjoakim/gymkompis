from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from programs.models import Exercise
from programs.services import generate_program_for_user
from evaluations.models import WorkoutEvaluation

from .forms import ExerciseSubmissionForm
from .models import WorkoutSession
from .progression import build_progression_recommendations
from .services import complete_session, get_active_program, get_or_create_session, get_program_day, submit_exercise_set, swap_session_exercise


@override_settings(OPENAI_MOCK_RESPONSES=True, OPENAI_API_KEY="")
class WorkoutSessionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="training@example.com", password="password123")
        generate_program_for_user(self.user, "Create a straightforward training plan.")
        self.program = get_active_program(self.user)
        self.day = get_program_day(self.program, "monday")

    def test_session_creation_and_completion(self):
        session = get_or_create_session(self.user, self.program, self.day)
        self.assertEqual(session.status, WorkoutSession.Status.IN_PROGRESS)
        expected_count = len(self.day.get("warmup", [])) + len(self.day["exercises"])
        self.assertEqual(len(session.session_json["exercises"]), expected_count)

        session = submit_exercise_set(
            session.id,
            self.user,
            self.day["warmup"][0]["exercise_key"],
            {
                "set_number": 1,
                "prescription_type": "time",
                "completed": True,
                "reps": None,
                "seconds": 300,
                "weight": None,
                "effort_rpe": 3,
                "notes": "",
                "started_at": "2026-04-04T08:00:00+00:00",
                "ended_at": "2026-04-04T08:05:00+00:00",
            },
            "Felt good",
        )
        self.assertEqual(session.session_json["exercises"][0]["status"], "completed")
        self.assertEqual(session.session_json["exercises"][0]["actual_sets"][0]["seconds"], 300)
        self.assertEqual(session.session_json["exercises"][0]["actual_sets"][0]["duration_seconds"], 300)

        session = complete_session(session.id, self.user, "Done", 7.5)
        self.assertEqual(session.status, WorkoutSession.Status.COMPLETED)
        self.assertEqual(session.session_json["status"], "completed")
        self.assertEqual(session.session_json["overall_effort_rpe"], 7.5)

    def test_progression_suggests_increment_for_successful_exact_match(self):
        WorkoutSession.objects.create(
            user=self.user,
            program=self.program,
            planned_day_key="monday",
            planned_day_label="Monday",
            planned_day_name="Full Body A",
            workout_date=timezone.localdate() - timedelta(days=7),
            status=WorkoutSession.Status.COMPLETED,
            completed_at=timezone.now(),
            session_json={
                "weight_unit": "kg",
                "exercises": [
                    {
                        "exercise_key": "chest_press_machine",
                        "name": "Chest Press (Machine)",
                        "modality": "machine",
                        "planned": {"set_plan": [{"set_number": 1}, {"set_number": 2}, {"set_number": 3}]},
                        "actual_sets": [
                            {"set_number": 1, "completed": True, "reps": 10, "weight": 40, "effort_rpe": 7},
                            {"set_number": 2, "completed": True, "reps": 10, "weight": 40, "effort_rpe": 7},
                            {"set_number": 3, "completed": True, "reps": 10, "weight": 40, "effort_rpe": 7.5},
                        ],
                    }
                ],
            },
        )

        chest_press = next(item for item in self.day["exercises"] if item["exercise_key"] == "chest_press_machine")
        recommendations = build_progression_recommendations(self.user, [chest_press], "kg")

        self.assertEqual(recommendations["chest_press_machine"]["match_type"], "exact")
        self.assertEqual(recommendations["chest_press_machine"]["suggested_weight"], 45.0)
        self.assertIn("Suggested load: 45", recommendations["chest_press_machine"]["guidance_text"])

    def test_progression_can_use_similar_exercise_history(self):
        Exercise.objects.create(
            external_id="lat-pulldown",
            name="Lat Pulldown",
            modality=Exercise.Modality.MACHINE,
            movement_pattern="vertical pull",
            primary_muscles=["back", "lats"],
            supports_reps=True,
            instructions="Pull down with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )
        Exercise.objects.create(
            external_id="close-grip-lat-pulldown",
            name="Close Grip Lat Pulldown",
            modality=Exercise.Modality.MACHINE,
            movement_pattern="vertical pull",
            primary_muscles=["back", "lats"],
            supports_reps=True,
            instructions="Pull down with control.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
        )
        WorkoutSession.objects.create(
            user=self.user,
            program=self.program,
            planned_day_key="friday",
            planned_day_label="Friday",
            planned_day_name="Full Body B",
            workout_date=timezone.localdate() - timedelta(days=10),
            status=WorkoutSession.Status.COMPLETED,
            completed_at=timezone.now(),
            session_json={
                "weight_unit": "kg",
                "exercises": [
                    {
                        "exercise_key": "close_grip_lat_pulldown",
                        "name": "Close Grip Lat Pulldown",
                        "modality": "machine",
                        "planned": {"set_plan": [{"set_number": 1}, {"set_number": 2}]},
                        "actual_sets": [
                            {"set_number": 1, "completed": True, "reps": 12, "weight": 50, "effort_rpe": 8},
                            {"set_number": 2, "completed": True, "reps": 12, "weight": 50, "effort_rpe": 8},
                        ],
                    }
                ],
            },
        )

        lat_pulldown = {
            "exercise_key": "lat_pulldown",
            "name": "Lat Pulldown",
            "modality": "machine",
            "focus": "Back, lats",
            "set_plan": [
                {
                    "set_number": 1,
                    "prescription_type": "reps",
                    "target_reps": "10-12",
                    "target_effort_rpe": 8,
                }
            ],
        }
        recommendations = build_progression_recommendations(self.user, [lat_pulldown], "kg")

        self.assertEqual(recommendations["lat_pulldown"]["match_type"], "similar")
        self.assertEqual(recommendations["lat_pulldown"]["suggested_weight"], 50.0)
        self.assertIn("Recent similar match", recommendations["lat_pulldown"]["reason"])

    def test_submit_exercise_set_marks_exercise_complete_only_after_all_sets(self):
        session = get_or_create_session(self.user, self.program, self.day)
        exercise_key = self.day["exercises"][0]["exercise_key"]

        session = submit_exercise_set(
            session.id,
            self.user,
            exercise_key,
            {
                "set_number": 1,
                "prescription_type": "reps",
                "completed": True,
                "reps": 10,
                "seconds": None,
                "weight": 40,
                "effort_rpe": 7,
                "notes": "",
            },
            "Strong start",
        )
        exercise_state = next(item for item in session.session_json["exercises"] if item["exercise_key"] == exercise_key)
        self.assertEqual(exercise_state["status"], "pending")
        self.assertEqual(len(exercise_state["actual_sets"]), 1)
        self.assertEqual(exercise_state["exercise_notes"], "Strong start")

        for set_number in (2, 3):
            session = submit_exercise_set(
                session.id,
                self.user,
                exercise_key,
                {
                    "set_number": set_number,
                    "prescription_type": "reps",
                    "completed": True,
                    "reps": 10,
                    "seconds": None,
                    "weight": 40,
                    "effort_rpe": 7.5,
                    "notes": "",
                },
                "All sets done",
            )

        exercise_state = next(item for item in session.session_json["exercises"] if item["exercise_key"] == exercise_key)
        self.assertEqual(exercise_state["status"], "completed")
        self.assertEqual(len(exercise_state["actual_sets"]), 3)
        self.assertEqual(exercise_state["exercise_notes"], "All sets done")

    def test_submit_exercise_set_tracks_duration_and_rest_before(self):
        session = get_or_create_session(self.user, self.program, self.day)
        exercise_key = self.day["exercises"][0]["exercise_key"]

        session = submit_exercise_set(
            session.id,
            self.user,
            exercise_key,
            {
                "set_number": 1,
                "prescription_type": "reps",
                "completed": True,
                "reps": 10,
                "seconds": None,
                "weight": 40,
                "effort_rpe": 7,
                "notes": "",
                "started_at": "2026-04-04T08:00:00+00:00",
                "ended_at": "2026-04-04T08:00:30+00:00",
            },
            "",
        )
        session = submit_exercise_set(
            session.id,
            self.user,
            exercise_key,
            {
                "set_number": 2,
                "prescription_type": "reps",
                "completed": True,
                "reps": 10,
                "seconds": None,
                "weight": 40,
                "effort_rpe": 7.5,
                "notes": "",
                "started_at": "2026-04-04T08:02:00+00:00",
                "ended_at": "2026-04-04T08:02:35+00:00",
            },
            "",
        )

        exercise_state = next(item for item in session.session_json["exercises"] if item["exercise_key"] == exercise_key)
        first_set = next(item for item in exercise_state["actual_sets"] if item["set_number"] == 1)
        second_set = next(item for item in exercise_state["actual_sets"] if item["set_number"] == 2)
        self.assertEqual(first_set["duration_seconds"], 30)
        self.assertIsNone(first_set["rest_before_seconds"])
        self.assertEqual(second_set["duration_seconds"], 35)
        self.assertEqual(second_set["rest_before_seconds"], 90)

    def test_bodyweight_rep_exercise_does_not_show_weight_input(self):
        exercise = {
            "exercise_key": "bodyweight_squat",
            "name": "Bodyweight Squat",
            "modality": "bodyweight",
            "set_plan": [
                {
                    "set_number": 1,
                    "prescription_type": "reps",
                    "target_reps": "12-15",
                }
            ],
        }
        form = ExerciseSubmissionForm(
            data={"set_1_reps": "15", "set_1_rpe": "6", "save_set_number": "1"},
            exercise=exercise,
            target_set_number=1,
        )

        self.assertNotIn("set_1_weight", form.fields)
        self.assertTrue(form.is_valid())
        actual_set = form.actual_set_for_target()
        self.assertEqual(actual_set["reps"], 15)
        self.assertIsNone(actual_set["weight"])

    def test_next_set_prefills_weight_from_last_completed_set_only(self):
        exercise = {
            "exercise_key": "chest_press_machine",
            "name": "Chest Press (Machine)",
            "modality": "machine",
            "set_plan": [
                {"set_number": 1, "prescription_type": "reps", "target_reps": "10"},
                {"set_number": 2, "prescription_type": "reps", "target_reps": "10"},
                {"set_number": 3, "prescription_type": "reps", "target_reps": "10"},
            ],
        }
        form = ExerciseSubmissionForm(
            exercise=exercise,
            progression={"suggested_weight": 42.5},
            saved_actual_sets=[
                {
                    "set_number": 1,
                    "completed": True,
                    "reps": 10,
                    "weight": 40,
                    "effort_rpe": 7,
                }
            ],
        )

        self.assertEqual(form.current_set_number, 2)
        self.assertEqual(form.current_set_row["set_number"], 2)
        self.assertEqual(form.fields["set_2_weight"].initial, 40)
        self.assertIsNone(form.fields["set_2_reps"].initial)
        self.assertEqual(len(form.completed_set_rows), 1)

    def test_workout_detail_uses_existing_evaluation_link(self):
        session = get_or_create_session(self.user, self.program, self.day)
        evaluation = WorkoutEvaluation.objects.create(
            user=self.user,
            evaluation_type=WorkoutEvaluation.EvaluationType.SESSION,
            workout_session=session,
            included_session_ids=[session.id],
            evaluation_json={
                "overall_summary": "Already evaluated.",
                "adherence_score": 80,
                "effort_summary": "Solid effort.",
                "recovery_flag": "low",
                "progression_signals": [],
                "recommendations": ["Keep going."],
            },
            summary_text="Already evaluated.",
        )

        self.client.force_login(self.user)
        response = self.client.get(f"/train/history/{session.id}/")
        content = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn(f"/evaluations/{evaluation.id}/", content)
        self.assertNotIn(f"/evaluations/session/{session.id}/request/", content)

    def test_train_day_uses_mobile_compact_training_classes(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("train_day", args=[self.day["day_key"]]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="stack training-day-stack"', html=False)
        self.assertContains(response, 'class="card training-exercise-card"', html=False)
        self.assertContains(response, 'class="card training-finish-card"', html=False)
        self.assertContains(response, 'class="current-set-panel"', html=False)
        self.assertNotContains(response, "<strong>Instructions:</strong>", html=False)

    def test_swap_session_exercise_updates_current_slot_only_for_today(self):
        session = get_or_create_session(self.user, self.program, self.day)
        target_exercise = self.day["exercises"][0]
        Exercise.objects.create(
            external_id="incline-chest-press-machine",
            name="Incline Chest Press (Machine)",
            modality=Exercise.Modality.MACHINE,
            movement_pattern=target_exercise.get("movement_pattern", "horizontal press"),
            primary_muscles=["chest", "shoulders", "triceps"],
            supports_reps=True,
            instructions="Press with control and keep your shoulders down.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
            is_active=True,
        )

        swapped = swap_session_exercise(
            session.id,
            self.user,
            current_exercise_key=target_exercise["exercise_key"],
            replacement_external_id="incline-chest-press-machine",
        )

        exercise_state = next(
            item
            for item in swapped.session_json["exercises"]
            if item["name"] == "Incline Chest Press (Machine)"
        )
        self.assertTrue(exercise_state["is_substituted"])
        self.assertEqual(exercise_state["original_exercise_key"], target_exercise["exercise_key"])
        self.assertEqual(exercise_state["original_name"], target_exercise["name"])
        self.assertEqual(exercise_state["substituted_from_exercise_key"], target_exercise["exercise_key"])
        self.assertEqual(exercise_state["substituted_from_name"], target_exercise["name"])
        self.assertEqual(exercise_state["status"], "pending")
        self.assertEqual(exercise_state["actual_sets"], [])

    def test_suggest_substitutions_excludes_same_named_library_match(self):
        target_exercise = self.day["exercises"][0]
        Exercise.objects.create(
            external_id="same-name-duplicate",
            name=target_exercise["name"],
            modality=Exercise.Modality.MACHINE,
            movement_pattern=target_exercise.get("movement_pattern", ""),
            primary_muscles=["chest"],
            supports_reps=True,
            instructions="Duplicate by name.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
            is_active=True,
        )
        Exercise.objects.create(
            external_id="clearly-different-option",
            name="Incline Chest Press (Machine)",
            modality=Exercise.Modality.MACHINE,
            movement_pattern=target_exercise.get("movement_pattern", ""),
            primary_muscles=["chest"],
            supports_reps=True,
            instructions="Alternative option.",
            instructions_status=Exercise.InstructionStatus.SEEDED,
            is_active=True,
        )

        from .substitutions import suggest_substitutions

        options = suggest_substitutions(self.user, target_exercise, excluded_keys=set())
        option_names = [item["name"] for item in options]

        self.assertNotIn(target_exercise["name"], option_names)
        self.assertIn("Incline Chest Press (Machine)", option_names)
