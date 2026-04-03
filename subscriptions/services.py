from accounts.models import UserProfile
from .models import Subscription


def user_has_premium(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.profile.subscription_tier == UserProfile.SubscriptionTier.PREMIUM:
        return True
    return user.subscriptions.filter(status__in=[Subscription.Status.ACTIVE, Subscription.Status.TRIALING]).exists()


def can_auto_evaluate(user) -> bool:
    return user_has_premium(user)


def can_request_manual_evaluation(user) -> bool:
    return user.is_authenticated


def can_generate_program(user) -> bool:
    return user.is_authenticated
