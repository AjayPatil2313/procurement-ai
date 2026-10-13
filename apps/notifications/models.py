from django.conf import settings
from django.db import models


class NotificationTemplate(models.Model):
    class Category(models.TextChoices):
        BUYER = "BUYER", "Sales (Buyer)"
        SELLER = "SELLER", "Vendor (Seller)"
        BOTH = "BOTH", "Sales & Vendor"
        BILLING = "BILLING", "Billing & Wallet"
        AUTH = "AUTH", "Authentication & Security"
        ADMIN = "ADMIN", "Super Admin Platform Alert"
        SYSTEM = "SYSTEM", "System General"

    name = models.CharField(max_length=150)
    event_key = models.CharField(max_length=100, unique=True, db_index=True)
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.SYSTEM,
    )
    subject = models.CharField(max_length=255)
    html_content = models.TextField()
    text_content = models.TextField(blank=True)
    variables = models.JSONField(
        default=list,
        blank=True,
        help_text="List of supported template variable names, e.g. ['user_name', 'company_name']",
    )
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(
        default=False,
        help_text="System-critical template (e.g. security reset) that cannot be casually deactivated",
    )
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "name"]
        verbose_name = "Notification Template"
        verbose_name_plural = "Notification Templates"

    def __str__(self):
        return f"[{self.category}] {self.name} ({self.event_key})"


class NotificationRule(models.Model):
    class ModuleType(models.TextChoices):
        PROCUREMENT = "PROCUREMENT", "Sales (Procurement)"
        SALES = "SALES", "Vendor (Catalog)"
        BILLING = "BILLING", "Billing & Wallet"
        AI_SEARCH = "AI_SEARCH", "AI Search & Matching"
        ADMIN = "ADMIN", "Super Admin Governance"
        AUTH = "AUTH", "Authentication"
        PLATFORM = "PLATFORM", "Platform-Wide"

    class CompanyTypeScope(models.TextChoices):
        BUYER = "BUYER", "Sales Only"
        SELLER = "SELLER", "Vendor Only"
        BOTH = "BOTH", "Hybrid (Both)"
        ALL = "ALL", "All Company Types"

    class RecipientType(models.TextChoices):
        ACTING_USER = "ACTING_USER", "Acting User"
        COMPANY_ADMINS = "COMPANY_ADMINS", "Designated Company Admin(s)"
        ALL_MEMBERS = "ALL_MEMBERS", "All Active Company Members"
        BILLING_CONTACTS = "BILLING_CONTACTS", "Billing & Admin Contacts"
        SUPER_ADMINS = "SUPER_ADMINS", "Platform Super Admins"
        CUSTOM = "CUSTOM", "Custom Email Addresses"

    class Severity(models.TextChoices):
        INFO = "INFO", "Informational"
        WARNING = "WARNING", "Warning"
        CRITICAL = "CRITICAL", "Critical / Action Required"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low Priority"
        NORMAL = "NORMAL", "Normal Priority"
        HIGH = "HIGH", "High Priority"
        URGENT = "URGENT", "Urgent / Instant Dispatch"

    name = models.CharField(max_length=150)
    event_key = models.CharField(max_length=100, db_index=True)
    module = models.CharField(
        max_length=30,
        choices=ModuleType.choices,
        default=ModuleType.PLATFORM,
    )
    company_type = models.CharField(
        max_length=20,
        choices=CompanyTypeScope.choices,
        default=CompanyTypeScope.ALL,
    )
    template = models.ForeignKey(
        NotificationTemplate,
        on_delete=models.CASCADE,
        related_name="rules",
    )
    recipient_type = models.CharField(
        max_length=30,
        choices=RecipientType.choices,
        default=RecipientType.ACTING_USER,
    )
    custom_recipients = models.TextField(
        blank=True,
        help_text="Comma-separated emails when recipient_type is CUSTOM or extra alert emails",
    )
    severity = models.CharField(
        max_length=20,
        choices=Severity.choices,
        default=Severity.INFO,
    )
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.NORMAL,
    )
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["module", "name"]
        verbose_name = "Notification Rule"
        verbose_name_plural = "Notification Rules"

    def __str__(self):
        return f"{self.name} [{self.event_key} -> {self.recipient_type}]"


class NotificationPreference(models.Model):
    class Category(models.TextChoices):
        PROCUREMENT = "PROCUREMENT", "Procurement & Sourcing Updates"
        SALES = "SALES", "Sales & Lead Inquiries"
        AI_SEARCH = "AI_SEARCH", "AI Discovery & Search Results"
        BILLING = "BILLING", "Wallet, Invoices & Credits (Essential)"
        SECURITY = "SECURITY", "Account & Security Alerts (Mandatory)"
        MARKETING = "MARKETING", "Product Tips & Platform News"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="user_notification_preferences",
    )
    category = models.CharField(
        max_length=30,
        choices=Category.choices,
    )
    email_enabled = models.BooleanField(default=True)
    in_app_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "company", "category")
        verbose_name = "Notification Preference"
        verbose_name_plural = "Notification Preferences"

    def __str__(self):
        return f"{self.user.email} - {self.category} (Email: {self.email_enabled})"

    @classmethod
    def is_email_permitted(cls, user, category):
        """Mandatory categories (SECURITY, BILLING) are always permitted."""
        if category in (cls.Category.SECURITY, cls.Category.BILLING):
            return True
        pref = cls.objects.filter(user=user, category=category).first()
        return pref.email_enabled if pref else True


class NotificationLog(models.Model):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued in Outbox"
        SENDING = "SENDING", "Sending"
        SENT = "SENT", "Delivered / Accepted"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled by Policy"

    class Severity(models.TextChoices):
        INFO = "INFO", "Info"
        WARNING = "WARNING", "Warning"
        CRITICAL = "CRITICAL", "Critical"

    event_key = models.CharField(max_length=100, db_index=True)
    template = models.ForeignKey(
        NotificationTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logs",
    )
    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_logs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_logs",
    )
    recipient_email = models.EmailField(db_index=True)
    recipient_name = models.CharField(max_length=150, blank=True)
    subject = models.CharField(max_length=255)
    html_body = models.TextField()
    text_body = models.TextField()
    context_data = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    severity = models.CharField(
        max_length=20,
        choices=Severity.choices,
        default=Severity.INFO,
        db_index=True,
    )
    idempotency_key = models.CharField(
        max_length=140,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )
    attempts_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    has_attachments = models.BooleanField(default=False)
    attachment_names = models.JSONField(default=list, blank=True)
    retry_after = models.DateTimeField(null=True, blank=True, db_index=True)
    last_error = models.TextField(blank=True)
    provider_message_id = models.CharField(max_length=150, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Email Delivery Log"
        verbose_name_plural = "Email Delivery Logs"

    def __str__(self):
        return f"{self.event_key} -> {self.recipient_email} [{self.status}]"


class EmailDeliveryAttempt(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "SUCCESS", "Delivered / Accepted"
        FAILED = "FAILED", "Failed"

    log = models.ForeignKey(
        NotificationLog,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    attempt_number = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
    )
    error_message = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Delivery Attempt"
        verbose_name_plural = "Delivery Attempts"

    def __str__(self):
        return f"Attempt #{self.attempt_number} for Log #{self.log_id}: {self.status}"


class SMTPSetting(models.Model):
    class BackendType(models.TextChoices):
        SMTP = "SMTP", "Standard SMTP Server"
        CONSOLE = "CONSOLE", "Console (Development Testing)"
        LOCMEM = "LOCMEM", "In-Memory Outbox (Unit Testing)"

    is_active = models.BooleanField(
        default=False,
        help_text="When active, overrides default environment SMTP configuration",
    )
    sender_name = models.CharField(
        max_length=100,
        default="AI Procurement & Sales Platform",
    )
    sender_email = models.EmailField(
        default="notifications@procurement-ai.com",
    )
    backend_type = models.CharField(
        max_length=20,
        choices=BackendType.choices,
        default=BackendType.SMTP,
    )
    host = models.CharField(max_length=150, default="smtp.gmail.com")
    port = models.PositiveIntegerField(default=587)
    use_tls = models.BooleanField(default=True)
    use_ssl = models.BooleanField(default=False)
    username = models.CharField(max_length=150, blank=True)
    password_encrypted = models.CharField(
        max_length=255,
        blank=True,
        help_text="Stored masked / protected secret key",
    )
    timeout = models.PositiveIntegerField(default=10)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SMTP Setting"
        verbose_name_plural = "SMTP Settings"

    def __str__(self):
        return f"SMTP Config: {self.sender_email} on {self.host}:{self.port} (Active: {self.is_active})"

    @classmethod
    def get_active_config(cls):
        """Returns the primary active SMTP configuration if enabled."""
        return cls.objects.filter(is_active=True).first()

    def set_password(self, raw_password):
        """Encrypts raw SMTP password using Django cryptographic signing."""
        if not raw_password:
            self.password_encrypted = ""
            return
        from django.core import signing
        self.password_encrypted = signing.dumps(str(raw_password), salt="tc_smtp_salt")

    def get_password(self):
        """Safely decrypts SMTP password, gracefully falling back to plain text for legacy rows."""
        if not self.password_encrypted:
            return ""
        from django.core import signing
        try:
            return signing.loads(self.password_encrypted, salt="tc_smtp_salt")
        except Exception:
            return self.password_encrypted
