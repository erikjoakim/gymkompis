from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from programs.models import Exercise
from programs.services import generate_program_for_user

from .models import WorkoutSession
from .progression import build_progression_recommendations
from .services import complete_session, get_active_program, get_or_create_session, get_program_day, submit_exercise


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

        session = submit_exercise(
            session.id,
            self.user,
            self.day["warmup"][0]["exercise_key"],
            [
                {
                    "set_number": 1,
                    "prescription_type": "time",
                    "completed": True,
                    "reps": None,
                    "seconds": 300,
                    "weight": None,
                    "effort_rpe": 3,
                    "notes": "",
                }
            ],
            "Felt good",
        )
        self.assertEqual(session.session_json["exercises"][0]["status"], "completed")
        self.assertEqual(session.session_json["exercises"][0]["actual_sets"][0]["seconds"], 300)

        session = complete_session(session.id, self.user, "Done")
        self.assertEqual(session.status, WorkoutSession.Status.COMPLETED)
        self.assertEqual(session.session_json["status"], "completed")

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
