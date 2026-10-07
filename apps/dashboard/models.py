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


class SupportTicket(models.Model):
    class Category(models.TextChoices):
        AI_SEARCH = "ai_search", "AI Search & Lead Scraping"
        MATCHING_RULES = "matching_rules", "Scoring & Matching Rules"
        BILLING_CREDITS = "billing_credits", "Billing, Invoices & AI Credits"
        CATALOG_RFQ = "catalog_rfq", "Product Catalog & RFQs"
        ACCOUNT_RBAC = "account_rbac", "Account, RBAC & Team Access"
        TECHNICAL_ISSUE = "technical_issue", "Technical Issue / Bug"
        OTHER = "other", "General Inquiry"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent (Production Critical)"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    ticket_number = models.CharField(max_length=20, unique=True, blank=True)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="support_tickets",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets",
    )
    category = models.CharField(
        max_length=30,
        choices=Category.choices,
        default=Category.AI_SEARCH,
    )
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.MEDIUM,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    subject = models.CharField(max_length=255)
    description = models.TextField()
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=50, blank=True)
    resolution = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.ticket_number:
            import random
            import string
            code = "".join(random.choices(string.digits, k=5))
            self.ticket_number = f"TKT-{code}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ticket_number} - {self.subject} ({self.get_status_display()})"
