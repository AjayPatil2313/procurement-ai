from django.contrib import admin
from apps.billing.models import (
    SubscriptionPlan,
    Subscription,
    CompanyCreditWallet,
    FeatureCreditCost,
    CreditTransaction,
    UsageRecord,
    Payment,
    Invoice,
)


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "billing_cycle", "price", "currency", "included_credits", "status", "order")
    list_filter = ("billing_cycle", "status", "is_public")
    search_fields = ("name", "code")
    ordering = ("order", "price")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("company", "plan_tier", "status", "credits_remaining", "valid_till", "updated_at")
    list_filter = ("status", "plan")
    search_fields = ("company__name",)
    ordering = ("-updated_at",)


@admin.register(CompanyCreditWallet)
class CompanyCreditWalletAdmin(admin.ModelAdmin):
    list_display = ("company", "subscription_credits", "purchased_credits", "reserved_credits", "credits_consumed", "total_available", "updated_at")
    search_fields = ("company__name",)
    ordering = ("-updated_at",)


@admin.register(FeatureCreditCost)
class FeatureCreditCostAdmin(admin.ModelAdmin):
    list_display = ("display_name", "feature_code", "credit_cost", "charging_unit", "is_active", "updated_at")
    list_filter = ("charging_unit", "is_active")
    search_fields = ("display_name", "feature_code")


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    list_display = ("receipt_number", "company", "user", "transaction_type", "credits", "balance_after", "feature_code", "created_at")
    list_filter = ("transaction_type", "status", "feature_code")
    search_fields = ("company__name", "user__email", "receipt_number", "notes")
    ordering = ("-created_at",)


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = ("company", "user", "feature_code", "credits_charged", "status", "started_at")
    list_filter = ("status", "feature_code")
    search_fields = ("company__name", "user__email", "idempotency_key")
    ordering = ("-started_at",)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "amount", "currency", "provider", "status", "payment_date")
    list_filter = ("status", "provider")
    search_fields = ("company__name", "provider_reference")
    ordering = ("-created_at",)


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "company", "total_amount", "currency", "status", "issue_date", "due_date")
    list_filter = ("status",)
    search_fields = ("company__name", "invoice_number")
    ordering = ("-created_at",)
