from django.test import TestCase

from .models import User


class UserProfileTests(TestCase):
    def test_profile_created_for_new_user(self):
        user = User.objects.create_user(email="person@example.com", password="password123")
        self.assertTrue(hasattr(user, "profile"))
        self.assertEqual(user.profile.preferred_weight_unit, "kg")
