from django.db import models
from apps.companies.models import Company
from apps.ai_search.models import SearchJob


class Subscription(models.Model):
    class Plan(models.TextChoices):
        FREE = "free", "Free Plan"
        PRO = "pro", "Pro Plan"
        ENTERPRISE = "enterprise", "Enterprise Plan"

    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.CharField(
        max_length=20,
        choices=Plan.choices,
        default=Plan.FREE,
    )
    credits_total = models.IntegerField(default=50)
    credits_used = models.IntegerField(default=0)
    valid_till = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def credits_remaining(self):
        return max(0, self.credits_total - self.credits_used)

    @property
    def usage_percentage(self):
        if self.credits_total <= 0:
            return 0
        return round((self.credits_used / self.credits_total) * 100, 1)

    def __str__(self):
        return f"{self.company.name} - {self.get_plan_display()} ({self.credits_remaining}/{self.credits_total})"


class CreditTransaction(models.Model):
    class TransactionType(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="credit_transactions",
    )
    transaction_type = models.CharField(
        max_length=10,
        choices=TransactionType.choices,
        default=TransactionType.DEBIT,
    )
    credits = models.IntegerField()
    search_job = models.ForeignKey(
        SearchJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="credit_transactions",
    )
    notes = models.CharField(max_length=255, blank=True)
    receipt_number = models.CharField(max_length=50, blank=True, null=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            import random
            import string
            for _ in range(10):
                code = "".join(random.choices(string.digits, k=5))
                candidate = f"RCP-{code}"
                if not CreditTransaction.objects.filter(receipt_number=candidate).exists():
                    self.receipt_number = candidate
                    break
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.receipt_number or 'RCP'} - {self.company.name} - {self.transaction_type} {self.credits} ({self.notes})"
