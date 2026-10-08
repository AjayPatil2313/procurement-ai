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
        IN_DISCUSSION = "in_discussion", "In Discussion"
        REPLIED = "replied", "Replied / Quoted"
        WON = "won", "Accepted / Won"
        LOST = "lost", "Lost / Closed"
        FAILED = "failed", "Failed"

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
    quoted_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    quoted_currency = models.CharField(
        max_length=3,
        default="INR",
        blank=True,
    )
    delivery_terms = models.CharField(
        max_length=255,
        blank=True,
    )
    attachment = models.FileField(
        upload_to="inquiries/%Y/%m/",
        null=True,
        blank=True,
        help_text="Optional Quotation PDF, Technical Specs, or RFQ Document",
    )
    attachment_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Original filename of the attached document",
    )
    sent_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Inquiries"
        ordering = ["-sent_at"]

    def __str__(self):
        return f"Inquiry to {self.sent_to_email}: {self.subject}"

    def get_attachment_filename(self):
        if self.attachment_name:
            return self.attachment_name
        if self.attachment:
            return self.attachment.name.replace("\\", "/").split("/")[-1]
        return ""

    def get_attachment_extension(self):
        name = self.get_attachment_filename().lower()
        if "." in name:
            return name.rsplit(".", 1)[-1]
        return ""

    def get_attachment_size_display(self):
        if not self.attachment:
            return ""
        try:
            size = self.attachment.size
            if size < 1024:
                return f"{size} B"
            elif size < 1024 * 1024:
                return f"{size / 1024:.1f} KB"
            else:
                return f"{size / (1024 * 1024):.1f} MB"
        except Exception:
            return ""

    def ensure_initial_message(self):
        """Ensures the primary dispatched message is logged in the message thread."""
        if not self.messages.exists() and self.message:
            InquiryMessage.objects.create(
                inquiry=self,
                sender_name=self.company.name if self.company else "Our Team",
                message_type=InquiryMessage.MessageType.OUTBOUND,
                subject=self.subject,
                body=self.message,
                attachment=self.attachment,
                attachment_name=self.attachment_name,
            )


class InquiryMessage(models.Model):
    """
    Message stream row in an Inquiry conversation thread.
    Tracks outbound follow-ups, inbound supplier/buyer responses, and system events.
    """
    class MessageType(models.TextChoices):
        OUTBOUND = "outbound", "Outbound (Sent by Us)"
        INBOUND = "inbound", "Inbound (Counterparty Response)"
        SYSTEM = "system", "System Event"

    inquiry = models.ForeignKey(
        Inquiry,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inquiry_messages",
    )
    sender_name = models.CharField(max_length=150, blank=True)
    message_type = models.CharField(
        max_length=20,
        choices=MessageType.choices,
        default=MessageType.OUTBOUND,
    )
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    attachment = models.FileField(
        upload_to="inquiry_messages/%Y/%m/",
        null=True,
        blank=True,
        help_text="Attached commercial document or specification",
    )
    attachment_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Original filename of the attached document",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Message #{self.id} for Inquiry #{self.inquiry_id} ({self.message_type})"

    def get_attachment_filename(self):
        if self.attachment_name:
            return self.attachment_name
        if self.attachment:
            return self.attachment.name.replace("\\", "/").split("/")[-1]
        return ""

    def get_attachment_extension(self):
        name = self.get_attachment_filename().lower()
        if "." in name:
            return name.rsplit(".", 1)[-1]
        return ""

    def get_attachment_size_display(self):
        if not self.attachment:
            return ""
        try:
            size = self.attachment.size
            if size < 1024:
                return f"{size} B"
            elif size < 1024 * 1024:
                return f"{size / 1024:.1f} KB"
            else:
                return f"{size / (1024 * 1024):.1f} MB"
        except Exception:
            return ""


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
