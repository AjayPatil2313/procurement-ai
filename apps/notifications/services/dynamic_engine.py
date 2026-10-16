import logging
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from apps.companies.models import Company, CompanyMember
from apps.dashboard.models import Notification
from apps.notifications.models import (
    DeviceToken,
    NotificationPreference,
    PushDeliveryLog,
)

logger = logging.getLogger(__name__)
User = get_user_model()


# ============================================================
# CENTRALIZED DYNAMIC EVENT REGISTRY
# ============================================================
EVENT_REGISTRY = {
    # 1. Customer Requirement Created -> Relevant Vendors notified
    "requirement.created": {
        "category": NotificationPreference.Category.SALES,
        "priority": Notification.Priority.INFO,
        "icon": "fa-bullhorn",
        "color": "blue",
        "default_title": "New Customer Requirement: {item_name}",
        "default_message": "Customer {company_name} is sourcing {quantity} {unit} of '{item_name}'. Review specifications and submit quote.",
        "default_url": "/sourcing/requirements/{id}/",
        "audience": "VENDORS",
    },
    # 2. Matching Engine Finds Vendors -> Customer notified
    "vendor_match.found": {
        "category": NotificationPreference.Category.PROCUREMENT,
        "priority": Notification.Priority.SUCCESS,
        "icon": "fa-handshake",
        "color": "emerald",
        "default_title": "AI Vendor Matches Discovered: {item_name}",
        "default_message": "Our AI matching engine discovered {match_count} verified vendors for '{item_name}'. Review supplier responses.",
        "default_url": "/sourcing/requirements/{id}/",
        "audience": "CUSTOMER",
    },
    # 3. New Inquiry Submitted -> Target Vendor notified
    "inquiry.created": {
        "category": NotificationPreference.Category.SALES,
        "priority": Notification.Priority.INFO,
        "icon": "fa-envelope",
        "color": "blue",
        "default_title": "New Inquiry: {subject}",
        "default_message": "Customer {customer_name} submitted a commercial quotation request for '{item_name}'.",
        "default_url": "/leads/inquiries/{id}/",
        "audience": "VENDOR_ADMINS",
    },
    # 4. Inquiry Status Changed -> Participants notified
    "inquiry.status_changed": {
        "category": NotificationPreference.Category.SALES,
        "priority": Notification.Priority.INFO,
        "icon": "fa-arrow-progress",
        "color": "indigo",
        "default_title": "Inquiry Updated: {subject}",
        "default_message": "Status updated to '{status_display}' by {acting_party}.",
        "default_url": "/leads/inquiries/{id}/",
        "audience": "INQUIRY_PARTICIPANTS",
    },
    # 5. Catalog Price Changed -> Customers notified
    "price.changed": {
        "category": NotificationPreference.Category.PROCUREMENT,
        "priority": Notification.Priority.INFO,
        "icon": "fa-tag",
        "color": "amber",
        "default_title": "Price Update: {product_title}",
        "default_message": "Vendor {vendor_name} updated pricing for '{product_title}'.",
        "default_url": "/catalog/products/{id}/",
        "audience": "CUSTOMERS",
    },
    # 6. Team Member Added -> Company Admin & New Member notified
    "company.user_added": {
        "category": NotificationPreference.Category.SECURITY,
        "priority": Notification.Priority.INFO,
        "icon": "fa-user-plus",
        "color": "emerald",
        "default_title": "New Team Member Added",
        "default_message": "{member_name} ({role}) has joined {company_name}.",
        "default_url": "/company/members/",
        "audience": "COMPANY_ADMINS",
    },
    # 7. Verification Decision -> Company Admin notified
    "company.verification_updated": {
        "category": NotificationPreference.Category.SECURITY,
        "priority": Notification.Priority.WARNING,
        "icon": "fa-building-circle-check",
        "color": "purple",
        "default_title": "Company Verification: {status_display}",
        "default_message": "Company verification status for {company_name} is now '{status_display}'.",
        "default_url": "/company/profile/",
        "audience": "COMPANY_ADMINS",
    },
    # 8. Low AI Credits -> Company Admin & Billing Contacts notified
    "credits.low": {
        "category": NotificationPreference.Category.BILLING,
        "priority": Notification.Priority.WARNING,
        "icon": "fa-battery-quarter",
        "color": "rose",
        "default_title": "Low AI Credits Warning",
        "default_message": "Your credit balance ({balance}) has fallen below threshold. Recharge your wallet to avoid disruption.",
        "default_url": "/billing/wallet/",
        "audience": "COMPANY_ADMINS",
    },
    # 9. Subscription Expiring -> Company Admin notified
    "subscription.expiring": {
        "category": NotificationPreference.Category.BILLING,
        "priority": Notification.Priority.WARNING,
        "icon": "fa-calendar-clock",
        "color": "rose",
        "default_title": "Subscription Expiring Soon",
        "default_message": "Your {plan_name} subscription expires in {days_left} days. Renew now to preserve access.",
        "default_url": "/billing/subscription/",
        "audience": "COMPANY_ADMINS",
    },
    # 10. Company Registered -> Super Admin notified
    "company.registered": {
        "category": NotificationPreference.Category.SECURITY,
        "priority": Notification.Priority.INFO,
        "icon": "fa-shield-halved",
        "color": "teal",
        "default_title": "New Company Registered: {company_name}",
        "default_message": "Company '{company_name}' ({company_type}) has registered on the platform. Review pending verification.",
        "default_url": "/admin-panel/companies/{id}/",
        "audience": "SUPER_ADMINS",
    },
}


# ============================================================
# RECIPIENT RESOLVER & RBAC AUTHORIZATION
# ============================================================
def resolve_event_recipients(event_key, context, company=None, actor=None):
    """
    Resolves eligible recipient users for a business event according to:
    - User roles (Company Admin, Company User, Super Admin)
    - Company types (Customer/Sales, Vendor, Customer + Vendor)
    - Strict company data isolation
    """
    recipients = set()
    event_info = EVENT_REGISTRY.get(event_key, {})
    audience = event_info.get("audience", "")

    if audience == "SUPER_ADMINS":
        # Platform-level alerts targeted strictly to Super Admins
        super_admins = User.objects.filter(is_superuser=True, is_active=True)
        recipients.update(super_admins)
        return recipients

    if audience == "COMPANY_ADMINS" and company:
        # Target active company admins for the specific tenant
        admin_members = CompanyMember.objects.filter(
            company=company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        ).select_related("user")
        for m in admin_members:
            if m.user and m.user.is_active:
                recipients.add(m.user)
        return recipients

    if audience == "CUSTOMER":
        # Target customer owner or customer company admins
        created_by_user = context.get("created_by") or actor
        if created_by_user and getattr(created_by_user, "is_active", False):
            recipients.add(created_by_user)
        if company:
            admin_members = CompanyMember.objects.filter(
                company=company,
                role=CompanyMember.Role.ADMIN,
                is_active=True,
            ).select_related("user")
            for m in admin_members:
                if m.user and m.user.is_active:
                    recipients.add(m.user)
        return recipients

    if audience == "VENDORS":
        # Target vendor companies selling relevant categories (VENDOR or BOTH)
        vendor_companies = context.get("target_vendor_companies")
        if not vendor_companies and context.get("target_vendor_company"):
            vendor_companies = [context.get("target_vendor_company")]

        if vendor_companies:
            for v_comp in vendor_companies:
                # Ensure company type is VENDOR or BOTH (Customer + Vendor)
                if v_comp.company_type in (Company.CompanyType.VENDOR, Company.CompanyType.BOTH):
                    members = CompanyMember.objects.filter(
                        company=v_comp,
                        is_active=True,
                    ).select_related("user")
                    for m in members:
                        if m.user and m.user.is_active:
                            recipients.add(m.user)
        return recipients

    if audience == "VENDOR_ADMINS":
        # Target vendor company admins
        target_company = context.get("target_company") or company
        if target_company and target_company.company_type in (Company.CompanyType.VENDOR, Company.CompanyType.BOTH):
            admins = CompanyMember.objects.filter(
                company=target_company,
                role=CompanyMember.Role.ADMIN,
                is_active=True,
            ).select_related("user")
            for m in admins:
                if m.user and m.user.is_active:
                    recipients.add(m.user)
        return recipients

    if audience == "INQUIRY_PARTICIPANTS":
        target_user = context.get("target_user")
        if target_user and getattr(target_user, "is_active", False):
            recipients.add(target_user)
        return recipients

    # Fallback: Explicit recipient passed in context
    explicit_user = context.get("user") or context.get("recipient")
    if explicit_user and getattr(explicit_user, "is_active", False):
        recipients.add(explicit_user)

    return recipients


# ============================================================
# DYNAMIC DISPATCH ENGINE
# ============================================================
def dispatch_dynamic_notification(
    event_key,
    context,
    company=None,
    actor=None,
    custom_title="",
    custom_message="",
    custom_url="",
    deduplication_key="",
):
    """
    Dispatches dynamic In-App and Firebase Browser Push notifications
    for a confirmed business event with strict channel separation from email.
    
    1. Resolves authorized recipients according to RBAC and company isolation.
    2. Evaluates deduplication key to suppress spam bursts.
    3. Checks independent In-App and Push user preferences.
    4. Creates In-App Notification records (dashboard.Notification).
    """
    event_info = EVENT_REGISTRY.get(event_key, {})
    category = event_info.get("category") or NotificationPreference.Category.PROCUREMENT
    priority = event_info.get("priority", Notification.Priority.INFO)
    icon = event_info.get("icon", "fa-bell")
    color = event_info.get("color", "blue")

    # Render dynamic title, message, and URL
    title_template = custom_title or event_info.get("default_title", "Platform Notification")
    message_template = custom_message or event_info.get("default_message", "")
    url_template = custom_url or event_info.get("default_url", "/dashboard/")

    try:
        rendered_title = title_template.format(**context)[:250]
    except Exception:
        rendered_title = title_template[:250]

    try:
        rendered_message = message_template.format(**context)[:1000]
    except Exception:
        rendered_message = message_template[:1000]

    try:
        rendered_url = url_template.format(**context)[:500]
    except Exception:
        rendered_url = url_template[:500]

    recipients = resolve_event_recipients(event_key, context, company=company, actor=actor)
    created_in_app = []
    push_results = []

    cutoff_15m = timezone.now() - timezone.timedelta(minutes=15)

    for recipient in recipients:
        user_company = company
        if not user_company and hasattr(recipient, "company_membership") and recipient.company_membership:
            user_company = recipient.company_membership.company

        # Deduplication check
        effective_dedup_key = deduplication_key or f"{event_key}:{context.get('id', '')}:{recipient.id}"
        if effective_dedup_key:
            duplicate_exists = Notification.objects.filter(
                user=recipient,
                deduplication_key=effective_dedup_key,
                created_at__gte=cutoff_15m,
            ).exists()
            if duplicate_exists:
                logger.info(f"Notification suppressed for {recipient.email} by deduplication_key: {effective_dedup_key}")
                continue

        # 1. IN-APP CHANNEL
        in_app_allowed = NotificationPreference.is_in_app_permitted(recipient, category)
        notif_obj = None
        if in_app_allowed:
            notif_obj = Notification.objects.create(
                user=recipient,
                company=user_company,
                notification_type=Notification.NotificationType.SYSTEM,
                priority=priority,
                event_key=event_key,
                deduplication_key=effective_dedup_key,
                title=rendered_title,
                message=rendered_message,
                link=rendered_url,
                icon=icon,
                color=color,
                is_read=False,
            )
            created_in_app.append(notif_obj)

        # 2. FIREBASE BROWSER PUSH CHANNEL (Strictly separate from Email)
        push_allowed = NotificationPreference.is_push_permitted(recipient, category)
        if push_allowed:
            active_devices = DeviceToken.objects.filter(user=recipient, is_active=True)
            if active_devices.exists():
                from apps.notifications.services.fcm_service import send_user_push
                push_res = send_user_push(
                    user_or_users=recipient,
                    title=rendered_title,
                    body=rendered_message[:500],
                    link=rendered_url,
                    category=category,
                )
                push_results.append(push_res)

                # Record PushDeliveryLog for audit and telemetry
                for device in active_devices:
                    status = (
                        PushDeliveryLog.Status.SUCCESS
                        if push_res.get("success")
                        else PushDeliveryLog.Status.FAILED
                    )
                    error_msg = "; ".join(push_res.get("errors", [])) if not push_res.get("success") else ""
                    PushDeliveryLog.objects.create(
                        notification=notif_obj,
                        device=device,
                        user=recipient,
                        company=user_company,
                        event_key=event_key,
                        status=status,
                        attempt_count=1,
                        error_code="FCM_DISPATCH_FAIL" if error_msg else "",
                        error_message=error_msg,
                        sent_at=timezone.now() if status == PushDeliveryLog.Status.SUCCESS else None,
                    )

    return {
        "event_key": event_key,
        "recipients_count": len(recipients),
        "in_app_created": len(created_in_app),
        "push_dispatched": len(push_results),
    }
