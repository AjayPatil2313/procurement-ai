from django.conf import settings
from django.db import models


class NotificationTemplate(models.Model):
    class Category(models.TextChoices):
        SALES = "SALES", "Sales"
        VENDOR = "VENDOR", "Vendor"
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
    use_custom_layout = models.BooleanField(
        default=False,
        help_text="If True, overrides global master header/footer with template-specific header/footer",
    )
    header_html = models.TextField(
        blank=True,
        default="",
        help_text="Template-specific custom header HTML. If blank, global master header is used.",
    )
    footer_html = models.TextField(
        blank=True,
        default="",
        help_text="Template-specific custom footer HTML. If blank, global master footer is used.",
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
        SALES = "SALES", "Sales Only"
        VENDOR = "VENDOR", "Vendor Only"
        BUYER = "BUYER", "Sales Only (Legacy)"
        SELLER = "SELLER", "Vendor Only (Legacy)"
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

    class DeliveryMode(models.TextChoices):
        AUTOMATIC = "AUTOMATIC", "Automatic"
        MANUAL = "MANUAL", "Manual"
        BOTH = "BOTH", "Both (Auto & Manual)"

    class Channel(models.TextChoices):
        EMAIL = "EMAIL", "Email Only"
        IN_APP = "IN_APP", "In-App Only"
        PUSH = "PUSH", "Web Push Only"
        BOTH = "BOTH", "Email & In-App"
        IN_APP_PUSH = "IN_APP_PUSH", "In-App & Web Push"
        ALL = "ALL", "All Channels (Email, In-App & Web Push)"

    class TargetAudience(models.TextChoices):
        USER = "USER", "User / Company User"
        COMPANY_ADMIN = "COMPANY_ADMIN", "Company Admin"
        SUPER_ADMIN = "SUPER_ADMIN", "Super Admin Platform Alert"

    name = models.CharField(max_length=150)
    event_key = models.CharField(max_length=100, db_index=True)
    description = models.TextField(blank=True, default="")
    target_audience = models.CharField(
        max_length=30,
        choices=TargetAudience.choices,
        default=TargetAudience.USER,
        db_index=True,
    )
    delivery_mode = models.CharField(
        max_length=20,
        choices=DeliveryMode.choices,
        default=DeliveryMode.AUTOMATIC,
        db_index=True,
    )
    channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
        default=Channel.BOTH,
        db_index=True,
        help_text="Notification delivery channels: Email, In-App, or Both",
    )
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
    subject_override = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Custom subject override. If empty, linked template subject is used.",
    )
    cc_recipients = models.TextField(
        blank=True,
        default="",
        help_text="Comma-separated CC email addresses",
    )
    bcc_recipients = models.TextField(
        blank=True,
        default="",
        help_text="Comma-separated BCC email addresses",
    )
    threshold_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Threshold value e.g. low credits cutoff",
    )
    cooldown_hours = models.PositiveIntegerField(
        default=24,
        help_text="Hours to wait before sending duplicate threshold alert",
    )
    reminder_days_before = models.PositiveIntegerField(
        default=5,
        help_text="Days prior to expiry for reminder dispatch",
    )
    is_mandatory_auth = models.BooleanField(
        default=False,
        help_text="Security-critical auth notification that cannot be disabled to prevent authentication breakage",
    )
    allow_manual_send = models.BooleanField(
        default=True,
        help_text="Allow authorized admins to manually trigger/resend this notification",
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
        ordering = ["target_audience", "name"]
        verbose_name = "Notification Rule"
        verbose_name_plural = "Notification Rules"

    def __str__(self):
        return f"{self.name} [{self.event_key} -> {self.delivery_mode}]"


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
    push_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "company", "category")
        verbose_name = "Notification Preference"
        verbose_name_plural = "Notification Preferences"

    def __str__(self):
        return f"{self.user.email} - {self.category} (Email: {self.email_enabled}, Push: {self.push_enabled})"

    @classmethod
    def is_email_permitted(cls, user, category):
        """Mandatory categories (SECURITY, BILLING) are always permitted."""
        if category in (cls.Category.SECURITY, cls.Category.BILLING):
            return True
        pref = cls.objects.filter(user=user, category=category).first()
        return pref.email_enabled if pref else True

    @classmethod
    def is_in_app_permitted(cls, user, category):
        """Mandatory categories (SECURITY, BILLING) are always permitted."""
        if category in (cls.Category.SECURITY, cls.Category.BILLING):
            return True
        pref = cls.objects.filter(user=user, category=category).first()
        return pref.in_app_enabled if pref else True

    @classmethod
    def is_push_permitted(cls, user, category):
        """Mandatory categories (SECURITY, BILLING) are always permitted."""
        if category in (cls.Category.SECURITY, cls.Category.BILLING):
            return True
        pref = cls.objects.filter(user=user, category=category).first()
        return pref.push_enabled if pref else True


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
    delivery_mode = models.CharField(
        max_length=20,
        default="AUTOMATIC",
        db_index=True,
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_notifications",
    )
    cc_recipients = models.TextField(blank=True, default="")
    bcc_recipients = models.TextField(blank=True, default="")
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


class EmailLayoutSetting(models.Model):
    name = models.CharField(max_length=120, default="Global Master Layout")
    is_active = models.BooleanField(
        default=True,
        help_text="When active, this master layout governs the header and footer of all emails",
    )

    # 1. Header Section
    brand_name = models.CharField(max_length=120, default="TradeConnect")
    brand_tagline = models.CharField(max_length=150, default="Buy &bull; Sell &bull; Grow")
    header_title = models.CharField(max_length=150, default="B2B Procurement & Sales Platform")
    header_subtitle = models.CharField(max_length=200, default="Smarter Sourcing. Stronger Partnerships.")
    logo_url = models.URLField(
        blank=True,
        default="",
        help_text="Custom Logo image URL (If blank, platform modern brand icon is rendered)",
    )
    header_bg_color = models.CharField(max_length=30, default="#ffffff")
    header_text_color = models.CharField(max_length=30, default="#0f172a")
    use_custom_header_html = models.BooleanField(
        default=False,
        help_text="If enabled, uses raw custom header HTML below instead of structured fields",
    )
    custom_header_html = models.TextField(
        blank=True,
        default="",
        help_text="Custom raw HTML for email header",
    )

    # 2. Trust Badges Row
    show_trust_badges = models.BooleanField(default=True)
    badge_1_icon = models.CharField(max_length=50, default="🛡️")
    badge_1_text = models.CharField(max_length=100, default="Secure & Verified")
    badge_2_icon = models.CharField(max_length=50, default="☑️")
    badge_2_text = models.CharField(max_length=100, default="Trusted B2B Platform")
    badge_3_icon = models.CharField(max_length=50, default="🎧")
    badge_3_text = models.CharField(max_length=100, default="24/7 Support")

    # 3. Footer Section
    footer_brand_name = models.CharField(max_length=120, default="TradeConnect")
    website_url = models.CharField(max_length=200, default="https://tradeconnect.com")
    support_email = models.EmailField(default="support@tradeconnect.com")
    compliance_text = models.TextField(
        default="You received this operational email because your company account is registered on the TradeConnect B2B AI Procurement & Sales Platform."
    )
    copyright_text = models.CharField(max_length=200, default="© 2026 TradeConnect Inc. All rights reserved.")
    social_linkedin = models.URLField(blank=True, default="https://linkedin.com")
    social_facebook = models.URLField(blank=True, default="https://facebook.com")
    social_youtube = models.URLField(blank=True, default="https://youtube.com")
    social_twitter = models.URLField(blank=True, default="https://twitter.com")
    use_custom_footer_html = models.BooleanField(
        default=False,
        help_text="If enabled, uses raw custom footer HTML below instead of structured fields",
    )
    custom_footer_html = models.TextField(
        blank=True,
        default="",
        help_text="Custom raw HTML for email footer",
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Email Layout Setting"
        verbose_name_plural = "Email Layout Settings"

    def __str__(self):
        return f"{self.name} (Active: {self.is_active})"

    @classmethod
    def get_active_layout(cls):
        """Returns the primary active layout setting or creates the default."""
        layout = cls.objects.filter(is_active=True).first()
        if not layout:
            layout = cls.objects.first()
            if not layout:
                layout = cls.objects.create(name="Global Master Layout", is_active=True)
            else:
                layout.is_active = True
                layout.save(update_fields=["is_active"])
        return layout


class DeviceToken(models.Model):
    class DeviceType(models.TextChoices):
        CHROME = "CHROME", "Google Chrome"
        EDGE = "EDGE", "Microsoft Edge"
        FIREFOX = "FIREFOX", "Mozilla Firefox"
        SAFARI = "SAFARI", "Apple Safari"
        OTHER = "OTHER", "Other Web Browser"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens",
    )
    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="device_tokens",
    )
    token = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="FCM registration token issued by browser",
    )
    endpoint = models.TextField(blank=True, default="")
    device_type = models.CharField(
        max_length=20,
        choices=DeviceType.choices,
        default=DeviceType.OTHER,
    )
    device_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        help_text="User device or browser summary (e.g. Chrome on Windows 11)",
    )
    user_agent = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen_at"]
        verbose_name = "Device Push Token"
        verbose_name_plural = "Device Push Tokens"
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["company", "is_active"]),
        ]

    def __str__(self):
        return f"{self.user.email} - {self.device_name or self.device_type} (Active: {self.is_active})"


class PushDeliveryLog(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Dispatch"
        SUCCESS = "SUCCESS", "Delivered to FCM"
        FAILED = "FAILED", "Failed Delivery"

    notification = models.ForeignKey(
        "dashboard.Notification",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="push_delivery_logs",
    )
    device = models.ForeignKey(
        DeviceToken,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="push_delivery_logs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_delivery_logs",
    )
    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="push_delivery_logs",
    )
    event_key = models.CharField(max_length=100, blank=True, default="", db_index=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    attempt_count = models.IntegerField(default=1)
    error_code = models.CharField(max_length=100, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Push Delivery Log"
        verbose_name_plural = "Push Delivery Logs"
        indexes = [
            models.Index(fields=["user", "status", "-created_at"]),
            models.Index(fields=["company", "status"]),
            models.Index(fields=["event_key", "-created_at"]),
        ]

    def __str__(self):
        return f"PushLog #{self.id} for {self.user.email} - {self.status}"



