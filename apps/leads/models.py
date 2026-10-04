from django.conf import settings
from django.db import models
from apps.companies.models import Company
from apps.ai_search.models import SearchResult, ExternalCompany


class SavedItem(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        NEGOTIATING = "negotiating", "Negotiating"
        INTERESTED = "interested", "Interested"
        WON = "won", "Won"
        LOST = "lost", "Lost"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="saved_items",
    )
    search_result = models.ForeignKey(
        SearchResult,
        on_delete=models.CASCADE,
        related_name="saved_records",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
    )
    notes = models.TextField(blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_saved_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.search_result.product_title} - {self.status}"


class Inquiry(models.Model):
    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        REPLIED = "replied", "Replied"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="inquiries",
    )
    search_result = models.ForeignKey(
        SearchResult,
        on_delete=models.CASCADE,
        related_name="inquiries",
    )
    subject = models.CharField(max_length=255)
    message = models.TextField()
    sent_to_email = models.EmailField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SENT,
    )
    sent_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Inquiries"
        ordering = ["-sent_at"]

    def __str__(self):
        return f"Inquiry to {self.sent_to_email}: {self.subject}"


class PriceHistory(models.Model):
    external_company = models.ForeignKey(
        ExternalCompany,
        on_delete=models.CASCADE,
        related_name="price_history",
    )
    item_name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-recorded_at"]

    def __str__(self):
        return f"{self.item_name} - {self.price} {self.currency}"
