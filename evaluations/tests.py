from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from programs.services import generate_program_for_user
from training.services import complete_session, get_active_program, get_or_create_session, get_program_day

from .models import WorkoutEvaluation
from .services import evaluate_period_for_user, evaluate_session_for_user


@override_settings(OPENAI_MOCK_RESPONSES=True, OPENAI_API_KEY="")
class WorkoutEvaluationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="eval@example.com", password="password123")
        generate_program_for_user(self.user, "Create a general fitness plan.")
        self.program = get_active_program(self.user)
        self.day = get_program_day(self.program, "monday")

    def test_session_evaluation_created(self):
        session = get_or_create_session(self.user, self.program, self.day)
        session = complete_session(session.id, self.user)
        evaluation, _token_usage = evaluate_session_for_user(self.user, session)
        self.assertEqual(evaluation.evaluation_type, WorkoutEvaluation.EvaluationType.SESSION)
        self.assertEqual(evaluation.evaluation_json["evaluation_type"], "session")

    def test_period_evaluation_created(self):
        session = get_or_create_session(self.user, self.program, self.day)
        session = complete_session(session.id, self.user)
        start_date = timezone.localdate() - timedelta(days=1)
        end_date = timezone.localdate()
        evaluation, _token_usage = evaluate_period_for_user(self.user, [session], start_date, end_date)
        self.assertEqual(evaluation.evaluation_type, WorkoutEvaluation.EvaluationType.PERIOD)
        self.assertEqual(evaluation.evaluation_json["evaluation_type"], "period")
