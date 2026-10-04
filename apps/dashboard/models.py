from django.conf import settings
from django.db import models
from apps.companies.models import Company


class ActivityLog(models.Model):
    class ActivityType(models.TextChoices):
        SUPPLIER_FOUND = "supplier_found", "Supplier Found"
        LEAD_UPDATED = "lead_updated", "Lead Updated"
        REQUIREMENT_CREATED = "requirement_created", "Requirement Created"
        PRODUCT_CREATED = "product_created", "Product Created"
        MEMBER_INVITED = "member_invited", "Member Invited"
        PLAN_UPGRADED = "plan_upgraded", "Plan Upgraded"
        INQUIRY_SENT = "inquiry_sent", "Inquiry Sent"
        SEARCH_STARTED = "search_started", "Search Started"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="activity_logs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
    )
    activity_type = models.CharField(
        max_length=30,
        choices=ActivityType.choices,
        default=ActivityType.SUPPLIER_FOUND,
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    icon_type = models.CharField(max_length=50, default="building")
    color = models.CharField(max_length=20, default="blue")  # green, blue, purple, orange, etc.
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.company.name} - {self.title}"
