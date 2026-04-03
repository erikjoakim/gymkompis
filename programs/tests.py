from django.test import TestCase, override_settings

from accounts.models import User

from .models import TrainingProgram
from .services import generate_program_for_user


@override_settings(OPENAI_MOCK_RESPONSES=True, OPENAI_API_KEY="")
class ProgramGenerationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="program@example.com", password="password123")

    def test_generate_program_creates_active_program(self):
        program = generate_program_for_user(self.user, "Build a beginner three-day program.")
        self.assertEqual(program.status, TrainingProgram.Status.ACTIVE)
        self.assertEqual(program.current_program["version"], 1)
        self.assertEqual(program.current_program["weight_unit"], "kg")

    def test_generating_second_program_archives_previous(self):
        first = generate_program_for_user(self.user, "First")
        second = generate_program_for_user(self.user, "Second")
        first.refresh_from_db()
        self.assertEqual(first.status, TrainingProgram.Status.ARCHIVED)
        self.assertEqual(second.status, TrainingProgram.Status.ACTIVE)
