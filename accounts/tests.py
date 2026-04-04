from django.test import TestCase
from django.urls import reverse

from .models import User


class UserProfileTests(TestCase):
    def test_profile_created_for_new_user(self):
        user = User.objects.create_user(email="person@example.com", password="password123")
        self.assertTrue(hasattr(user, "profile"))
        self.assertEqual(user.profile.preferred_weight_unit, "kg")

    def test_authenticated_user_can_open_password_change_page(self):
        user = User.objects.create_user(email="person2@example.com", password="password123")
        self.client.login(email="person2@example.com", password="password123")
        response = self.client.get(reverse("password_change"))
        self.assertEqual(response.status_code, 200)

    def test_authenticated_user_can_change_password(self):
        user = User.objects.create_user(email="person3@example.com", password="password123")
        self.client.login(email="person3@example.com", password="password123")
        response = self.client.post(
            reverse("password_change"),
            {
                "old_password": "password123",
                "new_password1": "safer-password456",
                "new_password2": "safer-password456",
            },
        )
        self.assertRedirects(response, reverse("password_change_done"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("safer-password456"))
