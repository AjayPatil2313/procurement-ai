from django.contrib import admin
from apps.billing.models import Subscription, CreditTransaction


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("company", "plan", "credits_total", "credits_used", "credits_remaining", "valid_till", "updated_at")
    list_filter = ("plan",)
    search_fields = ("company__name",)
    ordering = ("-updated_at",)


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    list_display = ("company", "transaction_type", "credits", "notes", "created_at")
    list_filter = ("transaction_type",)
    search_fields = ("company__name", "notes")
    ordering = ("-created_at",)
