from django.test import TestCase, override_settings

from accounts.models import User
from programs.services import generate_program_for_user

from .models import WorkoutSession
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
        self.assertEqual(len(session.session_json["exercises"]), len(self.day["exercises"]))

        session = submit_exercise(
            session.id,
            self.user,
            self.day["exercises"][0]["exercise_key"],
            [
                {
                    "set_number": 1,
                    "completed": True,
                    "reps": 10,
                    "weight": 50,
                    "effort_rpe": 7,
                    "notes": "",
                }
            ],
            "Felt good",
        )
        self.assertEqual(session.session_json["exercises"][0]["status"], "completed")

        session = complete_session(session.id, self.user, "Done")
        self.assertEqual(session.status, WorkoutSession.Status.COMPLETED)
        self.assertEqual(session.session_json["status"], "completed")
