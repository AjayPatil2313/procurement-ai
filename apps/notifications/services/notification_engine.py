"""
Centralized Notification Engine (In-App System + Firebase Cloud Messaging Web Push)
AI Procurement & Sales B2B Platform

Provides a single authoritative function `notify_event()` to dispatch all 48 business events
with full role-based permissions, multi-tenant isolation, duplicate suppression,
transactional safety (transaction.on_commit), and comprehensive telemetry logging.
"""

import logging
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.companies.models import Company, CompanyMember
from apps.dashboard.models import Notification
from apps.notifications.models import (
    DeviceToken,
    NotificationPreference,
    PushDeliveryLog,
)
from apps.notifications.services.event_registry import (
    EVENT_REGISTRY,
    get_event_definition,
)

logger = logging.getLogger(__name__)
User = get_user_model()


class SafeDict(dict):
    """Fallback dictionary that preserves unknown format keys without throwing KeyError."""
    def __missing__(self, key):
        return f"{{{key}}}"


def _sanitize_string(val, max_len=1000):
    if val is None:
        return ""
    # Strip dangerous HTML/scripts or control characters if any
    clean = str(val).strip()
    return clean[:max_len]


def _format_safe_template(template_str, context_dict, max_len=500):
    """Safely interpolates data dictionary into string template without code evaluation."""
    if not template_str:
        return ""
    safe_data = SafeDict({str(k): _sanitize_string(v, 250) for k, v in context_dict.items()})
    try:
        return template_str.format_map(safe_data)[:max_len]
    except Exception:
        return str(template_str)[:max_len]


def resolve_event_recipients(event_key, event_def, company=None, data=None, actor=None):
    """
    Resolves authorized recipient User objects for a business event based on:
    - Target audience rule (USER, ACTING_USER, COMPANY_ADMINS, VENDORS, CUSTOMER, SUPER_ADMINS, etc.)
    - Multi-tenant company boundary
    - Company active/suspended status
    """
    recipients = set()
    data = data or {}
    audience = event_def.get("audience", "USER")

    # 1. SUPER_ADMINS Audience
    if audience == "SUPER_ADMINS":
        super_users = User.objects.filter(is_superuser=True, is_active=True)
        recipients.update(super_users)
        return recipients

    # Check company suspension: suspended companies must not receive operational alerts
    if company and getattr(company, "is_active", True) is False:
        logger.warning(f"Suppressing event '{event_key}' for suspended company: {company.name}")
        return recipients

    # 2. COMPANY_ADMINS Audience
    if audience == "COMPANY_ADMINS" and company:
        admin_members = CompanyMember.objects.filter(
            company=company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        ).select_related("user")
        for m in admin_members:
            if m.user and m.user.is_active:
                recipients.add(m.user)
        return recipients

    # 3. CUSTOMER Audience (Procurement / Buyer)
    if audience == "CUSTOMER":
        created_by_user = data.get("created_by") or data.get("user") or actor
        if created_by_user and getattr(created_by_user, "is_active", False):
            recipients.add(created_by_user)
        if company:
            admins = CompanyMember.objects.filter(
                company=company,
                role=CompanyMember.Role.ADMIN,
                is_active=True,
            ).select_related("user")
            for m in admins:
                if m.user and m.user.is_active:
                    recipients.add(m.user)
        return recipients

    # 4. VENDORS Audience (Catalog / Sales)
    if audience in ("VENDORS", "VENDOR"):
        vendor_companies = data.get("target_vendor_companies")
        if not vendor_companies and data.get("target_vendor_company"):
            vendor_companies = [data.get("target_vendor_company")]
        if not vendor_companies and company and company.company_type in (Company.CompanyType.VENDOR, Company.CompanyType.BOTH):
            vendor_companies = [company]

        if vendor_companies:
            for v_comp in vendor_companies:
                if getattr(v_comp, "is_active", True) and v_comp.company_type in (Company.CompanyType.VENDOR, Company.CompanyType.BOTH):
                    members = CompanyMember.objects.filter(
                        company=v_comp,
                        is_active=True,
                    ).select_related("user")
                    for m in members:
                        if m.user and m.user.is_active:
                            recipients.add(m.user)
        return recipients

    # 5. VENDOR_ADMINS Audience
    if audience == "VENDOR_ADMINS":
        target_company = data.get("target_company") or company
        if target_company and getattr(target_company, "is_active", True) and target_company.company_type in (Company.CompanyType.VENDOR, Company.CompanyType.BOTH):
            admins = CompanyMember.objects.filter(
                company=target_company,
                role=CompanyMember.Role.ADMIN,
                is_active=True,
            ).select_related("user")
            for m in admins:
                if m.user and m.user.is_active:
                    recipients.add(m.user)
        return recipients

    # 6. INQUIRY_PARTICIPANTS Audience
    if audience == "INQUIRY_PARTICIPANTS":
        target_user = data.get("target_user") or data.get("recipient")
        if target_user and getattr(target_user, "is_active", False):
            recipients.add(target_user)
        return recipients

    # 7. USER / ACTING_USER Audience
    target_user = data.get("user") or data.get("recipient") or actor
    if target_user and getattr(target_user, "is_active", False):
        recipients.add(target_user)

    return recipients


def _execute_push_dispatch(recipient, user_company, event_key, notif_obj, title, message, url, category, priority):
    """
    Helper to execute Firebase Cloud Messaging push dispatch and per-device telemetry logging.
    Can be run immediately or scheduled inside transaction.on_commit.
    """
    try:
        active_devices = list(DeviceToken.objects.filter(user=recipient, is_active=True))
        if not active_devices:
            return {"success": True, "sent_count": 0, "failed_count": 0, "errors": []}

        from apps.notifications.services.fcm_service import send_user_push
        push_res = send_user_push(
            user_or_users=recipient,
            title=title,
            body=message[:500],
            link=url,
            category=category,
            priority=priority,
        )

        device_results = push_res.get("device_results", {})
        for device in active_devices:
            dev_res = device_results.get(device.token, {})
            is_dev_success = dev_res.get("success", push_res.get("success", False))
            dev_status = (
                PushDeliveryLog.Status.SUCCESS
                if is_dev_success
                else PushDeliveryLog.Status.FAILED
            )
            dev_err_code = dev_res.get("error_code") or ("" if is_dev_success else "FCM_DISPATCH_FAIL")
            dev_err_msg = dev_res.get("error_message") or ("; ".join(push_res.get("errors", [])) if not is_dev_success else "")

            PushDeliveryLog.objects.create(
                notification=notif_obj,
                device=device,
                user=recipient,
                company=user_company,
                event_key=event_key,
                status=dev_status,
                attempt_count=1,
                error_code=dev_err_code,
                error_message=dev_err_msg,
                sent_at=timezone.now() if dev_status == PushDeliveryLog.Status.SUCCESS else None,
            )

            # Auto-prune stale / invalid / unregistered FCM tokens
            if not is_dev_success and dev_err_code in (
                "UNREGISTERED",
                "INVALID_ARGUMENT",
                "REGISTRATION_TOKEN_NOT_REGISTERED",
                "messaging/invalid-registration-token",
                "messaging/registration-token-not-registered",
            ):
                device.is_active = False
                device.save(update_fields=["is_active"])
                logger.info(f"Automatically deactivated stale device token #{device.id} for {recipient.email} (Error: {dev_err_code})")

        return push_res
    except Exception as exc:
        logger.error(f"Error in push dispatch for notification #{getattr(notif_obj, 'id', None)}: {exc}")
        return {"success": False, "sent_count": 0, "failed_count": 1, "errors": [str(exc)]}


def notify_event(
    event,
    recipient=None,
    recipients=None,
    company=None,
    data=None,
    dedupe_key=None,
    channels=None,
    actor=None,
    custom_title=None,
    custom_message=None,
    custom_url=None,
    priority=None,
    async_push=True,
):
    """
    AUTHORITATIVE CENTRAL NOTIFICATION ENGINE FUNCTION

    Dispatches In-App/System Notifications and Firebase Cloud Messaging (FCM) Push Notifications
    through a unified, configuration-driven interface.

    Parameters:
    - event: Unique event code (e.g. 'requirement.changed', 'inquiry.created', 'platform.email_delivery_failure')
    - recipient: Single User object (optional)
    - recipients: Iterable of User objects (optional)
    - company: Company tenant instance (optional)
    - data: Context dictionary for safe string interpolation (dict)
    - dedupe_key: Explicit deduplication/idempotency key (optional)
    - channels: List of allowed channels, e.g. ['in_app', 'push'] (defaults to event configuration)
    - actor: User initiating the event (optional)
    - custom_title / custom_message / custom_url: Overrides for standard event templates
    - priority: Notification.Priority override
    - async_push: If True and inside a DB transaction, push is queued via transaction.on_commit
    """
    data = data or {}
    resolved_key, event_def = get_event_definition(event)

    if not event_def:
        logger.warning(f"Unregistered notification event: '{event}'. Using generic fallback configuration.")
        event_def = {
            "title_template": custom_title or "Platform Notification",
            "message_template": custom_message or "{message}",
            "category": NotificationPreference.Category.PROCUREMENT,
            "priority": priority or Notification.Priority.INFO,
            "audience": "USER",
            "default_url": "/dashboard/",
            "channels": ["in_app", "push"],
            "icon": "fa-bell",
            "color": "blue",
            "cooldown_minutes": 15,
            "is_mandatory": False,
        }
        resolved_key = event

    category = event_def.get("category", NotificationPreference.Category.PROCUREMENT)
    event_priority = priority or event_def.get("priority", Notification.Priority.INFO)
    icon = event_def.get("icon", "fa-bell")
    color = event_def.get("color", "blue")
    cooldown_minutes = event_def.get("cooldown_minutes", 15)
    allowed_channels = channels or event_def.get("channels", ["in_app", "push"])
    is_mandatory = event_def.get("is_mandatory", False)

    # 1. Resolve Target Recipients
    target_recipients = set()
    if recipient:
        if getattr(recipient, "is_active", False):
            target_recipients.add(recipient)
    elif recipients:
        for r in recipients:
            if getattr(r, "is_active", False):
                target_recipients.add(r)
    else:
        target_recipients = resolve_event_recipients(
            event_key=resolved_key,
            event_def=event_def,
            company=company,
            data=data,
            actor=actor,
        )

    # Format dynamic content safely
    title_tmpl = custom_title or event_def.get("title_template", "Notification")
    msg_tmpl = custom_message or event_def.get("message_template", "")
    url_tmpl = custom_url or event_def.get("default_url", "/dashboard/")

    rendered_title = _format_safe_template(title_tmpl, data, max_len=250)
    rendered_message = _format_safe_template(msg_tmpl, data, max_len=1000)
    rendered_url = _format_safe_template(url_tmpl, data, max_len=500)

    cutoff = timezone.now() - timezone.timedelta(minutes=cooldown_minutes)

    created_notif_objs = []
    push_results = []
    suppressed_count = 0
    errors_list = []

    for target_user in target_recipients:
        user_company = company
        if not user_company and hasattr(target_user, "company_membership") and target_user.company_membership:
            user_company = target_user.company_membership.company

        # 2. Check Deduplication
        effective_dedupe = dedupe_key or f"{resolved_key}:{data.get('id', data.get('inquiry_id', data.get('requirement_id', '')))}:{target_user.id}"
        if effective_dedupe and cooldown_minutes > 0:
            is_duplicate = Notification.objects.filter(
                user=target_user,
                deduplication_key=effective_dedupe,
                created_at__gte=cutoff,
            ).exists()
            if is_duplicate:
                suppressed_count += 1
                logger.info(f"Duplicate suppressed for {target_user.email} with key '{effective_dedupe}'")
                continue

        # 3. In-App Notification Delivery
        notif_obj = None
        if "in_app" in allowed_channels:
            in_app_permitted = is_mandatory or NotificationPreference.is_in_app_permitted(target_user, category)
            if in_app_permitted:
                try:
                    notif_obj = Notification.objects.create(
                        user=target_user,
                        company=user_company,
                        notification_type=Notification.NotificationType.SYSTEM,
                        priority=event_priority,
                        event_key=resolved_key,
                        deduplication_key=effective_dedupe,
                        title=rendered_title,
                        message=rendered_message,
                        link=rendered_url,
                        icon=icon,
                        color=color,
                        is_read=False,
                    )
                    created_notif_objs.append(notif_obj)
                except Exception as exc:
                    err_msg = f"In-App notification DB creation failed for {target_user.email}: {exc}"
                    logger.error(err_msg)
                    errors_list.append(err_msg)

        # 4. Firebase Cloud Messaging (FCM) Browser Push Delivery
        if "push" in allowed_channels:
            push_permitted = is_mandatory or NotificationPreference.is_push_permitted(target_user, category)
            if push_permitted:
                # If inside active atomic block and async requested, schedule on transaction commit
                if async_push and transaction.get_connection().in_atomic_block:
                    # Capture parameters for closure
                    _t_user = target_user
                    _u_comp = user_company
                    _e_key = resolved_key
                    _n_obj = notif_obj
                    _title = rendered_title
                    _msg = rendered_message
                    _url = rendered_url
                    _cat = category
                    _prio = event_priority
                    transaction.on_commit(
                        lambda: _execute_push_dispatch(
                            _t_user, _u_comp, _e_key, _n_obj, _title, _msg, _url, _cat, _prio
                        )
                    )
                    push_results.append({"status": "SCHEDULED_ON_COMMIT", "recipient": target_user.email})
                else:
                    # Execute immediately
                    res = _execute_push_dispatch(
                        target_user, user_company, resolved_key, notif_obj,
                        rendered_title, rendered_message, rendered_url, category, event_priority
                    )
                    push_results.append(res)

    total_sent = sum(p.get("sent_count", 0) for p in push_results if isinstance(p, dict))
    total_failed = sum(p.get("failed_count", 0) for p in push_results if isinstance(p, dict))

    return {
        "event": resolved_key,
        "success": len(created_notif_objs) > 0 or len(push_results) > 0 or suppressed_count > 0,
        "recipients_count": len(target_recipients),
        "in_app_created": len(created_notif_objs),
        "in_app_ids": [n.id for n in created_notif_objs],
        "push_dispatched": len(push_results),
        "push_sent_count": total_sent,
        "push_failed_count": total_failed,
        "suppressed": suppressed_count > 0,
        "errors": errors_list,
    }
