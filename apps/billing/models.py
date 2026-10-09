from django.db import models
from django.conf import settings
from django.utils import timezone
from apps.companies.models import Company
from apps.ai_search.models import SearchJob


class SubscriptionPlan(models.Model):
    """
    Super Admin configurable subscription plans and pricing catalog.
    """
    class BillingCycle(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"
        LIFETIME = "lifetime", "Lifetime / Pay-as-you-go"

    class PlanStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    code = models.CharField(max_length=50, unique=True, help_text="Unique slug e.g. free, pro, enterprise")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    billing_cycle = models.CharField(max_length=20, choices=BillingCycle.choices, default=BillingCycle.MONTHLY)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=10, default="INR")
    included_credits = models.PositiveIntegerField(default=500, help_text="Monthly search / AI credits included")
    max_team_members = models.PositiveIntegerField(default=5)
    max_products = models.PositiveIntegerField(default=50)
    max_saved_vendors = models.PositiveIntegerField(default=100)
    search_limit_monthly = models.PositiveIntegerField(default=500)
    website_extraction_limit = models.PositiveIntegerField(default=500)
    ai_matching_limit = models.PositiveIntegerField(default=500)
    feature_access = models.JSONField(default=dict, blank=True, help_text="Feature flags e.g. {'export_pdf': True}")
    trial_days = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=PlanStatus.choices, default=PlanStatus.ACTIVE)
    is_public = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "price"]
        verbose_name = "Subscription Plan"
        verbose_name_plural = "Subscription Plans"

    def __str__(self):
        return f"{self.name} ({self.get_billing_cycle_display()} - {self.currency} {self.price})"


class Subscription(models.Model):
    """
    Active company subscription lifecycle.
    Keeps 100% backward compatibility with legacy Subscription queries.
    """
    class Plan(models.TextChoices):
        FREE = "free", "Free Plan"
        PRO = "pro", "Pro Plan"
        ENTERPRISE = "enterprise", "Enterprise Plan"

    class Status(models.TextChoices):
        TRIAL = "trial", "Trial"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past Due"
        EXPIRED = "expired", "Expired"
        SUSPENDED = "suspended", "Suspended"
        CANCELLED = "cancelled", "Cancelled"

    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan_tier = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions",
    )
    plan = models.CharField(
        max_length=20,
        choices=Plan.choices,
        default=Plan.FREE,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    credits_total = models.IntegerField(default=50)
    credits_used = models.IntegerField(default=0)
    valid_till = models.DateField(null=True, blank=True)
    start_date = models.DateTimeField(default=timezone.now)
    renewal_date = models.DateTimeField(null=True, blank=True)
    auto_renew = models.BooleanField(default=True)
    cancellation_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def credits_remaining(self):
        if hasattr(self.company, "credit_wallet"):
            return self.company.credit_wallet.total_available
        return max(0, self.credits_total - self.credits_used)

    @property
    def usage_percentage(self):
        if hasattr(self.company, "credit_wallet"):
            return self.company.credit_wallet.usage_percentage
        if self.credits_total <= 0:
            return 0
        return round((self.credits_used / self.credits_total) * 100, 1)

    def save(self, *args, **kwargs):
        if self.plan_tier and not self.plan:
            self.plan = self.plan_tier.code
        if self.renewal_date and not self.valid_till:
            self.valid_till = self.renewal_date.date()
        super().save(*args, **kwargs)

    def __str__(self):
        plan_name = self.plan_tier.name if self.plan_tier else self.get_plan_display()
        return f"{self.company.name} - {plan_name} ({self.credits_remaining}/{self.credits_total})"


# Alias for explicit naming
CompanySubscription = Subscription


class CompanyCreditWallet(models.Model):
    """
    Dedicated company-level credit wallet holding categorized balances.
    """
    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="credit_wallet",
    )
    subscription_credits = models.PositiveIntegerField(default=0)
    purchased_credits = models.PositiveIntegerField(default=0)
    promotional_credits = models.PositiveIntegerField(default=0)
    reserved_credits = models.PositiveIntegerField(default=0)
    credits_consumed = models.PositiveIntegerField(default=0)
    last_reloaded_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company Credit Wallet"
        verbose_name_plural = "Company Credit Wallets"

    @property
    def total_available(self):
        return max(0, (self.subscription_credits + self.purchased_credits + self.promotional_credits) - self.reserved_credits)

    @property
    def available_credits(self):
        return self.total_available

    @property
    def total_pool(self):
        return self.subscription_credits + self.purchased_credits + self.promotional_credits

    @property
    def usage_percentage(self):
        pool = self.credits_consumed + self.total_available
        if pool <= 0:
            return 0
        return round((self.credits_consumed / pool) * 100, 1)

    def sync_legacy_subscription(self):
        """Synchronizes wallet balance with legacy subscription fields."""
        sub = getattr(self.company, "subscription", None)
        if sub:
            sub.credits_total = self.total_pool + self.credits_consumed
            sub.credits_used = self.credits_consumed
            sub.save(update_fields=["credits_total", "credits_used", "updated_at"])

    def __str__(self):
        return f"{self.company.name} Wallet: {self.total_available} available (Pool: {self.total_pool})"


class FeatureCreditCost(models.Model):
    """
    Super Admin configurable rate card for AI features.
    """
    class ChargingUnit(models.TextChoices):
        PER_JOB = "per_job", "Per Search Job"
        PER_WEBSITE = "per_website", "Per Processed Website"
        PER_COMPANY = "per_company", "Per Matched Company"
        PER_OPERATION = "per_op", "Per Operation / Export"

    feature_code = models.CharField(max_length=50, unique=True, help_text="System code e.g. vendor_discovery, ai_matching")
    display_name = models.CharField(max_length=100)
    credit_cost = models.PositiveIntegerField(default=1)
    charging_unit = models.CharField(max_length=20, choices=ChargingUnit.choices, default=ChargingUnit.PER_JOB)
    is_active = models.BooleanField(default=True)
    description = models.CharField(max_length=255, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feature_cost_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["feature_code"]
        verbose_name = "Feature Credit Cost"
        verbose_name_plural = "Feature Credit Costs"

    def __str__(self):
        return f"{self.display_name} ({self.credit_cost} credits / {self.get_charging_unit_display()})"


class CreditTransaction(models.Model):
    """
    Auditable credit ledger recording all credit allocations, deductions, refunds, and adjustments.
    """
    class TransactionType(models.TextChoices):
        ALLOCATION = "allocation", "Subscription Allocation"
        PURCHASE = "purchase", "Credit Purchase"
        DEDUCTION = "deduction", "Feature Usage Deduction"
        REFUND = "refund", "Failed Job Refund"
        EXPIRY = "expiry", "Credit Expiry"
        ADJUSTMENT = "adjustment", "Admin Adjustment"
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    class TransactionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        REVERSED = "reversed", "Reversed / Refunded"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="credit_transactions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="credit_transactions",
    )
    wallet = models.ForeignKey(
        CompanyCreditWallet,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    transaction_type = models.CharField(
        max_length=20,
        choices=TransactionType.choices,
        default=TransactionType.DEDUCTION,
    )
    credits = models.IntegerField(help_text="Positive for additions, negative for deductions")
    balance_after = models.IntegerField(default=0)
    feature_code = models.CharField(max_length=50, blank=True, default="vendor_discovery")
    search_job = models.ForeignKey(
        SearchJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="credit_transactions",
    )
    receipt_number = models.CharField(max_length=50, blank=True, null=True, unique=True)
    idempotency_key = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=TransactionStatus.choices,
        default=TransactionStatus.COMPLETED,
    )
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_credit_transactions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Credit Transaction"
        verbose_name_plural = "Credit Transactions"

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            import random
            import string
            for _ in range(10):
                code = "".join(random.choices(string.digits, k=6))
                candidate = f"RCP-{code}"
                if not CreditTransaction.objects.filter(receipt_number=candidate).exists():
                    self.receipt_number = candidate
                    break
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.receipt_number or 'RCP'} - {self.company.name} - {self.transaction_type} {self.credits} ({self.notes})"


class UsageRecord(models.Model):
    """
    Granular billable operation record linking user, company, feature, and credits charged.
    """
    class ProcessingStatus(models.TextChoices):
        RESERVED = "reserved", "Credits Reserved"
        COMPLETED = "completed", "Successfully Processed"
        FAILED = "failed", "Processing Failed"
        REFUNDED = "refunded", "Credits Refunded"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="usage_records",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usage_records",
    )
    feature_code = models.CharField(max_length=50)
    search_job = models.ForeignKey(
        SearchJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usage_records",
    )
    usage_quantity = models.PositiveIntegerField(default=1)
    credits_charged = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.COMPLETED,
    )
    idempotency_key = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Usage Record"
        verbose_name_plural = "Usage Records"

    def __str__(self):
        return f"{self.company.name} - {self.user} - {self.feature_code} ({self.credits_charged} credits) [{self.status}]"


class Payment(models.Model):
    """
    Payment transaction history (supports manual/dev activation and gateway webhooks).
    """
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Successful"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="INR")
    provider = models.CharField(max_length=50, default="Development Gateway / Manual Approval")
    provider_reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SUCCESS,
    )
    payment_date = models.DateTimeField(default=timezone.now)
    failure_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Payment"
        verbose_name_plural = "Payments"

    def __str__(self):
        return f"Payment #{self.id} - {self.company.name} - {self.currency} {self.amount} ({self.status})"


class Invoice(models.Model):
    """
    Official billing invoice for subscription renewals, upgrades, and top-ups.
    """
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PAID = "paid", "Paid"
        VOID = "void", "Void"
        OVERDUE = "overdue", "Overdue"

    invoice_number = models.CharField(max_length=50, unique=True)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    payment = models.OneToOneField(
        Payment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice",
    )
    billing_period_start = models.DateField()
    billing_period_end = models.DateField()
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="INR")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PAID,
    )
    issue_date = models.DateField(default=timezone.now)
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def paid_at(self):
        if self.payment and self.payment.payment_date:
            return self.payment.payment_date
        return self.created_at if self.status == self.Status.PAID else None

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Invoice"
        verbose_name_plural = "Invoices"

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            import random
            import string
            year = timezone.now().strftime("%Y")
            code = "".join(random.choices(string.digits, k=5))
            self.invoice_number = f"INV-{year}-{code}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice_number} - {self.company.name} - {self.currency} {self.total_amount} ({self.status})"
