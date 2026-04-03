from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("The email address must be set.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


class UserProfile(models.Model):
    class SubscriptionTier(models.TextChoices):
        FREE = "free", "Free"
        PREMIUM = "premium", "Premium"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(max_length=150, blank=True)
    birth_year = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1900), MaxValueValidator(2100)],
    )
    training_experience = models.CharField(max_length=64, blank=True)
    injuries_limitations = models.TextField(blank=True)
    equipment_access = models.TextField(blank=True)
    preferred_language = models.CharField(max_length=12, default="en")
    timezone = models.CharField(max_length=64, default="Europe/Stockholm")
    preferred_weight_unit = models.CharField(max_length=2, default="kg")
    subscription_tier = models.CharField(
        max_length=16,
        choices=SubscriptionTier.choices,
        default=SubscriptionTier.FREE,
    )
    onboarding_completed = models.BooleanField(default=False)
    plan_history_window_sessions = models.PositiveSmallIntegerField(null=True, blank=True)

    def __str__(self):
        return f"Profile for {self.user.email}"

    @property
    def effective_display_name(self):
        if self.display_name:
            return self.display_name
        return self.user.first_name or self.user.email

    @property
    def is_premium(self):
        return self.subscription_tier == self.SubscriptionTier.PREMIUM


@receiver(post_save, sender=User)
def ensure_profile_exists(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
    else:
        UserProfile.objects.get_or_create(user=instance)
