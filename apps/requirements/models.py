from django.conf import settings
from django.db import models
from django.utils import timezone
from apps.companies.models import Company
from apps.catalog.models import Category


class Requirement(models.Model):
    class SearchScope(models.TextChoices):
        NEARBY = "nearby", "Nearby"
        COUNTRY = "country", "Country"
        GLOBAL = "global", "Global"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SEARCHING = "searching", "Searching"
        COMPLETED = "completed", "Completed"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="requirements",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_requirements",
    )
    item_name = models.CharField(max_length=255)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requirements",
    )
    description = models.TextField(blank=True)
    specifications = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=2, default=1.00)
    unit = models.CharField(max_length=30, default="pcs")
    target_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    currency = models.CharField(max_length=3, default="INR")
    delivery_city = models.CharField(max_length=100, blank=True)
    delivery_country = models.CharField(max_length=100, default="India")
    required_by = models.DateField(null=True, blank=True)
    search_scope = models.CharField(
        max_length=20,
        choices=SearchScope.choices,
        default=SearchScope.GLOBAL,
    )
    radius_km = models.IntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "is_deleted"]),
        ]

    def __str__(self):
        return f"{self.item_name} ({self.company.name})"

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at"])

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=["is_deleted", "deleted_at"])
