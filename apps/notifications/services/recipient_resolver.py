import logging
from django.contrib.auth import get_user_model
from apps.companies.models import CompanyMember
from apps.notifications.models import NotificationRule, NotificationPreference

logger = logging.getLogger(__name__)
User = get_user_model()


def resolve_rule_recipients(rule, company=None, user=None, context=None):
    """
    Resolves a list of recipient dictionaries:
    [
        {"email": "...", "name": "...", "user": UserInstance or None},
        ...
    ]
    Enforces tenant boundaries and respects user notification preferences.
    """
    recipients_map = {}  # email -> dict
    rtype = rule.recipient_type

    def add_recipient(email, name="", user_obj=None):
        if not email:
            return
        clean_email = email.strip().lower()
        if "@" not in clean_email:
            return

        # Check notification preferences if user_obj exists
        if user_obj:
            pref_category = get_preference_category_from_module(rule.module)
            if pref_category and not NotificationPreference.is_email_permitted(user_obj, pref_category):
                logger.info(f"Email skipped for {clean_email} due to user preference ({pref_category})")
                return

        if clean_email not in recipients_map:
            recipients_map[clean_email] = {
                "email": clean_email,
                "name": name or (user_obj.get_full_name() if user_obj else clean_email),
                "user": user_obj,
            }

    # 1. ACTING USER
    if rtype == NotificationRule.RecipientType.ACTING_USER and user:
        add_recipient(user.email, user.get_full_name() or user.email, user)

    # 2. COMPANY ADMINS
    elif rtype == NotificationRule.RecipientType.COMPANY_ADMINS and company:
        admins = CompanyMember.objects.filter(
            company=company, role="ADMIN", is_active=True
        ).select_related("user")
        for m in admins:
            if m.user and m.user.email and m.user.is_active:
                add_recipient(m.user.email, m.user.get_full_name(), m.user)
        # Fallback to company profile email if no admins found
        if not recipients_map and getattr(company, "email", None):
            add_recipient(company.email, company.name, None)

    # 3. ALL MEMBERS
    elif rtype == NotificationRule.RecipientType.ALL_MEMBERS and company:
        members = CompanyMember.objects.filter(
            company=company, is_active=True
        ).select_related("user")
        for m in members:
            if m.user and m.user.email and m.user.is_active:
                add_recipient(m.user.email, m.user.get_full_name(), m.user)

    # 4. BILLING CONTACTS
    elif rtype == NotificationRule.RecipientType.BILLING_CONTACTS:
        if company:
            # First add company billing email
            if getattr(company, "email", None):
                add_recipient(company.email, company.name, None)
            # Add company admins
            admins = CompanyMember.objects.filter(
                company=company, role="ADMIN", is_active=True
            ).select_related("user")
            for m in admins:
                if m.user and m.user.email and m.user.is_active:
                    add_recipient(m.user.email, m.user.get_full_name(), m.user)
        elif user:
            add_recipient(user.email, user.get_full_name(), user)

    # 5. SUPER ADMINS
    elif rtype == NotificationRule.RecipientType.SUPER_ADMINS:
        super_admins = User.objects.filter(is_superuser=True, is_active=True)
        active_smtp = None
        for sa in super_admins:
            target_email = sa.email.strip().lower() if sa.email else ""
            if not target_email:
                continue
            # If superuser email has an unroutable placeholder domain (e.g. procurement.ai, example.com),
            # route to active SMTP sender or administrator email to avoid external mail delivery bounce.
            if any(target_email.endswith(f"@{dom}") for dom in ["procurement.ai", "example.com", "testcorp.com"]):
                if active_smtp is None:
                    from apps.notifications.models import SMTPSetting
                    active_smtp = SMTPSetting.get_active_config()
                if active_smtp and active_smtp.sender_email and "@" in active_smtp.sender_email:
                    fallback_email = active_smtp.sender_email.strip().lower()
                    if not any(fallback_email.endswith(f"@{dom}") for dom in ["procurement.ai", "example.com"]):
                        add_recipient(fallback_email, sa.get_full_name() or "Platform Super Admin", sa)
                        continue
            add_recipient(target_email, sa.get_full_name() or "Platform Super Admin", sa)

    # 6. CUSTOM EMAILS
    elif rtype == NotificationRule.RecipientType.CUSTOM:
        if rule.custom_recipients:
            for raw in rule.custom_recipients.replace(";", ",").split(","):
                email = raw.strip()
                if email:
                    add_recipient(email, email, None)

    # Context override: If context specifies 'extra_recipients'
    if context and isinstance(context, dict):
        extra = context.get("extra_recipients", [])
        if isinstance(extra, list):
            for e in extra:
                if isinstance(e, str):
                    add_recipient(e, e, None)
                elif isinstance(e, dict) and "email" in e:
                    add_recipient(e["email"], e.get("name", ""), None)

    return list(recipients_map.values())


def get_preference_category_from_module(module):
    """Maps NotificationRule module to NotificationPreference Category."""
    mapping = {
        NotificationRule.ModuleType.PROCUREMENT: NotificationPreference.Category.PROCUREMENT,
        NotificationRule.ModuleType.SALES: NotificationPreference.Category.SALES,
        NotificationRule.ModuleType.AI_SEARCH: NotificationPreference.Category.AI_SEARCH,
        NotificationRule.ModuleType.BILLING: NotificationPreference.Category.BILLING,
        NotificationRule.ModuleType.AUTH: NotificationPreference.Category.SECURITY,
        NotificationRule.ModuleType.ADMIN: None,
        NotificationRule.ModuleType.PLATFORM: None,
    }
    return mapping.get(module)
