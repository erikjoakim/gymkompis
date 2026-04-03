from django.contrib import admin

from .models import BillingEvent, Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan_code", "status", "current_period_end", "cancel_at_period_end")
    list_filter = ("status", "provider", "cancel_at_period_end")
    search_fields = ("user__email", "stripe_customer_id", "stripe_subscription_id")


@admin.register(BillingEvent)
class BillingEventAdmin(admin.ModelAdmin):
    list_display = ("stripe_event_id", "event_type", "user", "processed_at", "created_at")
    list_filter = ("event_type", "processed_at")
    search_fields = ("stripe_event_id", "user__email")
