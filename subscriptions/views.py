from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .services import user_has_premium


@login_required
def subscription_view(request):
    return render(
        request,
        "subscriptions/subscription.html",
        {"has_premium": user_has_premium(request.user)},
    )
