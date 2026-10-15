from django.conf import settings
from django.db import models
from django.utils import timezone
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
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at"])

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=["is_deleted", "deleted_at"])

    def save(self, *args, **kwargs):
        if not self.ticket_number:
            import random
            import string
            code = "".join(random.choices(string.digits, k=5))
            self.ticket_number = f"TKT-{code}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ticket_number} - {self.subject} ({self.get_status_display()})"


class Notification(models.Model):
    class NotificationType(models.TextChoices):
        PROPOSAL_SENT = "proposal_sent", "Proposal / RFQ Sent"
        INQUIRY_REPLY = "inquiry_reply", "Inquiry Follow-up / Reply"
        QUOTE_RECEIVED = "quote_received", "Quote Recorded / Received"
        STATUS_CHANGED = "status_changed", "Stage Transition"
        DEAL_WON = "deal_won", "Deal Won"
        DEAL_LOST = "deal_lost", "Deal Lost"
        TICKET_UPDATE = "ticket_update", "Support Ticket Update"
        LOW_CREDITS = "low_credits", "Low Credits Warning"
        CREDITS_EXHAUSTED = "credits_exhausted", "Credits Exhausted"
        PLAN_UPGRADED = "plan_upgraded", "Subscription Plan Updated"
        PAYMENT_SUCCESS = "payment_success", "Payment Received"
        INVOICE_GENERATED = "invoice_generated", "Invoice Generated"
        SYSTEM = "system", "System Notification"

    class Priority(models.TextChoices):
        INFO = "INFO", "Info"
        SUCCESS = "SUCCESS", "Success"
        WARNING = "WARNING", "Warning"
        CRITICAL = "CRITICAL", "Critical"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    notification_type = models.CharField(
        max_length=30,
        choices=NotificationType.choices,
        default=NotificationType.SYSTEM,
    )
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.INFO,
        db_index=True,
    )
    event_key = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    link = models.CharField(max_length=500, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    icon = models.CharField(max_length=50, default="fa-bell")
    color = models.CharField(max_length=30, default="blue")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Notification for {self.user}: {self.title}"

    def mark_as_read(self):
        if not self.is_read:
            self.is_read = True
            self.save(update_fields=["is_read"])

    def mark_as_unread(self):
        if self.is_read:
            self.is_read = False
            self.save(update_fields=["is_read"])

    def archive(self):
        if not self.is_archived:
            self.is_archived = True
            self.save(update_fields=["is_archived"])

    def restore(self):
        if self.is_archived:
            self.is_archived = False
            self.save(update_fields=["is_archived"])


class FAQ(models.Model):
    category = models.CharField(max_length=100, default="Getting Started & Accounts")
    icon = models.CharField(max_length=50, default="fa-circle-question")
    question = models.CharField(max_length=255)
    answer = models.TextField()
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "FAQ"
        verbose_name_plural = "FAQs"

    def __str__(self):
        return self.question


class PlatformSetting(models.Model):
    key = models.CharField(max_length=60, unique=True)
    value = models.TextField(blank=True)
    description = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Platform Setting"
        verbose_name_plural = "Platform Settings"

    def __str__(self):
        return f"{self.key}: {self.value}"

    @classmethod
    def get_setting(cls, key, default=""):
        obj = cls.objects.filter(key=key).first()
        return obj.value if obj else default

    @classmethod
    def set_setting(cls, key, value, description=""):
        obj, _ = cls.objects.update_or_create(
            key=key,
            defaults={"value": str(value), "description": description},
        )
        return obj


class BuyerModuleConfig(models.Model):
    class MinPlan(models.TextChoices):
        FREE = "FREE", "Free Tier"
        PRO = "PRO", "Pro Tier"
        ENTERPRISE = "ENTERPRISE", "Enterprise Tier"

    module_key = models.CharField(max_length=60, unique=True)
    display_name = models.CharField(max_length=100)
    nav_label = models.CharField(max_length=100, blank=True)
    url_name = models.CharField(max_length=100)
    icon_class = models.CharField(max_length=50, default="fa-circle")
    order = models.PositiveIntegerField(default=0)
    is_enabled = models.BooleanField(default=True)
    description = models.CharField(max_length=255, blank=True)
    requires_credits = models.BooleanField(default=False)
    min_plan = models.CharField(
        max_length=20,
        choices=MinPlan.choices,
        default=MinPlan.FREE,
    )
    is_visible_on_sidebar = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Sales Module Configuration"
        verbose_name_plural = "Sales Module Configurations"

    def __str__(self):
        status = "Active" if self.is_enabled else "Disabled"
        return f"{self.display_name} ({self.module_key}) [{status}]"

    @classmethod
    def get_active_modules(cls):
        cls.ensure_defaults()
        return cls.objects.filter(is_enabled=True).order_by("order", "id")

    @classmethod
    def is_module_enabled(cls, key):
        cls.ensure_defaults()
        mod = cls.objects.filter(module_key=key).first()
        return mod.is_enabled if mod else True

    @classmethod
    def ensure_defaults(cls):
        defaults = [
            {
                "module_key": "my_requirements",
                "display_name": "Sourcing Requirements",
                "nav_label": "Requirements",
                "url_name": "requirements-list",
                "icon_class": "fa-clipboard-list",
                "order": 1,
                "is_enabled": True,
                "description": "Post, manage, and edit company procurement requirements.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
            {
                "module_key": "find_suppliers",
                "display_name": "Find Customers",
                "nav_label": "Discovery",
                "url_name": "find-suppliers",
                "icon_class": "fa-magnifying-glass-chart",
                "order": 2,
                "is_enabled": True,
                "description": "AI-powered web scraping and live matching engine.",
                "requires_credits": True,
                "min_plan": "FREE",
            },
            {
                "module_key": "saved_suppliers",
                "display_name": "Saved Customers",
                "nav_label": "Saved Pipeline",
                "url_name": "saved-suppliers",
                "icon_class": "fa-bookmark",
                "order": 3,
                "is_enabled": True,
                "description": "Requirement-wise saved candidate pipeline and deal stages.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
            {
                "module_key": "inquiries",
                "display_name": "Inquiries / RFQs",
                "nav_label": "Inquiries",
                "url_name": "inquiries-list",
                "icon_class": "fa-paper-plane",
                "order": 4,
                "is_enabled": True,
                "description": "Official quotation requests, message threads, and quotes.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
            {
                "module_key": "price_comparison",
                "display_name": "Price Comparison",
                "nav_label": "Comparison",
                "url_name": "price-comparison",
                "icon_class": "fa-chart-line",
                "order": 5,
                "is_enabled": True,
                "description": "Side-by-side comparison of supplier quotes and delivery terms.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
            {
                "module_key": "export_reports",
                "display_name": "Export Reports",
                "nav_label": "Reports",
                "url_name": "export-reports",
                "icon_class": "fa-download",
                "order": 6,
                "is_enabled": True,
                "description": "Generate authorized CSV/Excel dossiers and summaries.",
                "requires_credits": False,
                "min_plan": "PRO",
            },
            {
                "module_key": "subscription_credits",
                "display_name": "Subscription & Credits",
                "nav_label": "Credits",
                "url_name": "subscription-billing-web",
                "icon_class": "fa-credit-card",
                "order": 7,
                "is_enabled": True,
                "description": "View active plan, top-up AI search credits, and upgrade tier.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
            {
                "module_key": "invoices_billing",
                "display_name": "Invoices & Billing",
                "nav_label": "Invoices",
                "url_name": "company-invoices",
                "icon_class": "fa-file-invoice",
                "order": 8,
                "is_enabled": True,
                "description": "Tax invoices, ledger statements, and billing receipts.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
            {
                "module_key": "help_support",
                "display_name": "Help & Support",
                "nav_label": "Support",
                "url_name": "help-support",
                "icon_class": "fa-headset",
                "order": 9,
                "is_enabled": True,
                "description": "Platform FAQ knowledgebase and support ticketing.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
        ]
        for item in defaults:
            cls.objects.get_or_create(module_key=item["module_key"], defaults=item)


class BuyerDashboardLayout(models.Model):
    show_kpi_requirements = models.BooleanField(default=True)
    show_kpi_searches = models.BooleanField(default=True)
    show_kpi_suppliers_discovered = models.BooleanField(default=True)
    show_kpi_saved_suppliers = models.BooleanField(default=True)
    show_kpi_inquiries_sent = models.BooleanField(default=True)
    show_kpi_credits = models.BooleanField(default=True)
    show_quick_actions = models.BooleanField(default=True)
    show_recent_searches = models.BooleanField(default=True)
    show_recent_activity = models.BooleanField(default=True)
    show_top_suppliers = models.BooleanField(default=True)
    show_subscription_widget = models.BooleanField(default=True)
    kpi_refresh_rate = models.IntegerField(default=60)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Sales Dashboard Layout"
        verbose_name_plural = "Sales Dashboard Layouts"

    @classmethod
    def get_layout(cls):
        obj, _ = cls.objects.get_or_create(id=1)
        return obj


class SellerModuleConfig(models.Model):
    class MinPlan(models.TextChoices):
        FREE = "FREE", "Free Tier"
        PRO = "PRO", "Pro Tier"
        ENTERPRISE = "ENTERPRISE", "Enterprise Tier"

    module_key = models.CharField(max_length=60, unique=True)
    display_name = models.CharField(max_length=100)
    nav_label = models.CharField(max_length=100, blank=True)
    url_name = models.CharField(max_length=100)
    icon_class = models.CharField(max_length=50, default="fa-circle")
    order = models.PositiveIntegerField(default=0)
    is_enabled = models.BooleanField(default=True)
    description = models.CharField(max_length=255, blank=True)
    requires_credits = models.BooleanField(default=False)
    min_plan = models.CharField(
        max_length=20,
        choices=MinPlan.choices,
        default=MinPlan.FREE,
    )
    is_visible_on_sidebar = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Vendor Module Configuration"
        verbose_name_plural = "Vendor Module Configurations"

    def __str__(self):
        status = "Active" if self.is_enabled else "Disabled"
        return f"{self.display_name} ({self.module_key}) [{status}]"

    @classmethod
    def get_active_modules(cls):
        cls.ensure_defaults()
        return cls.objects.filter(is_enabled=True).order_by("order", "id")

    @classmethod
    def is_module_enabled(cls, key):
        cls.ensure_defaults()
        mod = cls.objects.filter(module_key=key).first()
        return mod.is_enabled if mod else True

    @classmethod
    def ensure_defaults(cls):
        defaults = [
            {
                "module_key": "my_products",
                "display_name": "Products Catalog",
                "nav_label": "Products",
                "url_name": "products-list",
                "icon_class": "fa-box-open",
                "order": 1,
                "is_enabled": True,
                "description": "Manage catalog products, availability, and specifications.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
            {
                "module_key": "find_buyers",
                "display_name": "Find Vendors (AI)",
                "nav_label": "Discovery",
                "url_name": "find-buyers",
                "icon_class": "fa-crosshairs",
                "order": 2,
                "is_enabled": True,
                "description": "AI-powered web scraper for corporate buyers and distributor leads.",
                "requires_credits": True,
                "min_plan": "FREE",
            },
            {
                "module_key": "buyer_leads",
                "display_name": "Vendor List",
                "nav_label": "Vendor List",
                "url_name": "leads-list",
                "icon_class": "fa-users-viewfinder",
                "order": 3,
                "is_enabled": True,
                "description": "Browse discovered corporate buyer prospects and verified profiles.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
            {
                "module_key": "saved_buyers",
                "display_name": "Prequalified Vendors",
                "nav_label": "Pipeline",
                "url_name": "saved-leads",
                "icon_class": "fa-bookmark",
                "order": 4,
                "is_enabled": True,
                "description": "Qualified sales lead pipeline with deal stage velocity tracking.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
            {
                "module_key": "seller_inquiries",
                "display_name": "Buyer Inquiries",
                "nav_label": "Inquiries",
                "url_name": "sales-inquiries-list",
                "icon_class": "fa-paper-plane",
                "order": 5,
                "is_enabled": True,
                "description": "Commercial proposals, RFQ responses, and quote discussions.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
            {
                "module_key": "seller_export_reports",
                "display_name": "Export Reports",
                "nav_label": "Reports",
                "url_name": "sales-export-reports",
                "icon_class": "fa-download",
                "order": 6,
                "is_enabled": True,
                "description": "Generate authorized sales lead dossiers and inquiry summaries.",
                "requires_credits": False,
                "min_plan": "PRO",
            },
            {
                "module_key": "seller_roles",
                "display_name": "Roles & Responsibilities",
                "nav_label": "Roles",
                "url_name": "seller-roles-web",
                "icon_class": "fa-user-shield",
                "order": 7,
                "is_enabled": True,
                "description": "Configure organizational sales roles and custom permissions.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
            {
                "module_key": "seller_subscription_credits",
                "display_name": "Subscription & Credits",
                "nav_label": "Credits",
                "url_name": "subscription-billing-web",
                "icon_class": "fa-credit-card",
                "order": 8,
                "is_enabled": True,
                "description": "View active plan, top-up AI search credits, and upgrade tier.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
            {
                "module_key": "seller_invoices_billing",
                "display_name": "Invoices & Billing",
                "nav_label": "Invoices",
                "url_name": "company-invoices",
                "icon_class": "fa-file-invoice",
                "order": 9,
                "is_enabled": True,
                "description": "Tax invoices, ledger statements, and billing receipts.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
            {
                "module_key": "seller_help_support",
                "display_name": "Help & Support",
                "nav_label": "Support",
                "url_name": "help-support",
                "icon_class": "fa-headset",
                "order": 10,
                "is_enabled": True,
                "description": "Platform FAQ knowledgebase and support ticketing.",
                "requires_credits": False,
                "min_plan": "FREE",
            },
        ]
        for item in defaults:
            cls.objects.get_or_create(module_key=item["module_key"], defaults=item)


class SellerDashboardLayout(models.Model):
    show_kpi_products = models.BooleanField(default=True)
    show_kpi_searches = models.BooleanField(default=True)
    show_kpi_leads_discovered = models.BooleanField(default=True)
    show_kpi_saved_leads = models.BooleanField(default=True)
    show_kpi_inquiries_received = models.BooleanField(default=True)
    show_kpi_credits = models.BooleanField(default=True)
    show_quick_actions = models.BooleanField(default=True)
    show_recent_searches = models.BooleanField(default=True)
    show_recent_activity = models.BooleanField(default=True)
    show_top_leads = models.BooleanField(default=True)
    show_subscription_widget = models.BooleanField(default=True)
    kpi_refresh_rate = models.IntegerField(default=60)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vendor Dashboard Layout"
        verbose_name_plural = "Vendor Dashboard Layouts"

    @classmethod
    def get_layout(cls):
        obj, _ = cls.objects.get_or_create(id=1)
        return obj


class BothDashboardLayout(models.Model):
    default_active_view = models.CharField(
        max_length=20,
        choices=[
            ("combined", "Combined Overview"),
            ("buyer", "Sales Workspace"),
            ("seller", "Vendor Workspace"),
        ],
        default="combined",
    )
    show_view_switcher = models.BooleanField(default=True)
    show_combined_metrics = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Both (Sales & Vendor) Dashboard Layout"
        verbose_name_plural = "Both (Sales & Vendor) Dashboard Layouts"

    @classmethod
    def get_layout(cls):
        obj, _ = cls.objects.get_or_create(id=1)
        return obj


def is_platform_module_enabled(module_code, company_type=None):
    """
    Evaluates whether a module is currently enabled by Platform Super Admin.
    Maps common aliases and respects company type context.
    """
    if not module_code:
        return True

    code = str(module_code).strip().lower()

    BUYER_MAP = {
        "requirements": "my_requirements",
        "procurement": "my_requirements",
        "my_requirements": "my_requirements",
        "find_suppliers": "find_suppliers",
        "supplier_search": "find_suppliers",
        "suppliers": "find_suppliers",
        "customers": "saved_suppliers",
        "customer_list": "find_suppliers",
        "saved_suppliers": "saved_suppliers",
        "inquiries": "inquiries",
        "rfq": "inquiries",
        "price_comparison": "price_comparison",
        "export_reports": "export_reports",
        "billing": "subscription_credits",
        "subscription": "subscription_credits",
        "subscription_credits": "subscription_credits",
        "invoices": "invoices_billing",
        "invoices_billing": "invoices_billing",
        "credit_ledger": "subscription_credits",
        "help": "help_support",
        "help_support": "help_support",
    }

    SELLER_MAP = {
        "products": "my_products",
        "catalog": "my_products",
        "my_products": "my_products",
        "find_buyers": "find_buyers",
        "buyer_discovery": "find_buyers",
        "leads": "buyer_leads",
        "buyer_leads": "buyer_leads",
        "vendor_list": "buyer_leads",
        "saved_leads": "saved_buyers",
        "saved_buyers": "saved_buyers",
        "inquiries": "seller_inquiries",
        "seller_inquiries": "seller_inquiries",
        "export_reports": "seller_export_reports",
        "seller_export_reports": "seller_export_reports",
        "roles": "seller_roles",
        "seller_roles": "seller_roles",
        "matching_parameters": "my_products",
        "billing": "seller_subscription_credits",
        "seller_subscription_credits": "seller_subscription_credits",
        "invoices": "seller_invoices_billing",
        "seller_invoices_billing": "seller_invoices_billing",
        "help": "seller_help_support",
        "seller_help_support": "seller_help_support",
    }

    if company_type == "BUYER":
        target_key = BUYER_MAP.get(code, code)
        return BuyerModuleConfig.is_module_enabled(target_key)

    if company_type == "SELLER":
        target_key = SELLER_MAP.get(code, code)
        return SellerModuleConfig.is_module_enabled(target_key)

    # For BOTH or unspecified company_type:
    if code in BUYER_MAP and code not in SELLER_MAP:
        return BuyerModuleConfig.is_module_enabled(BUYER_MAP[code])
    if code in SELLER_MAP and code not in BUYER_MAP:
        return SellerModuleConfig.is_module_enabled(SELLER_MAP[code])

    # If ambiguous (e.g. 'inquiries', 'export_reports', 'billing'):
    b_key = BUYER_MAP.get(code)
    s_key = SELLER_MAP.get(code)
    b_enabled = BuyerModuleConfig.is_module_enabled(b_key) if b_key else True
    s_enabled = SellerModuleConfig.is_module_enabled(s_key) if s_key else True
    return b_enabled or s_enabled





