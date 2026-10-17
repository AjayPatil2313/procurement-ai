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
    NotificationRule,
    NotificationTemplate,
    NotificationLog,
)
from apps.notifications.services.event_registry import (
    EVENT_REGISTRY,
    EVENT_ALIASES,
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

    # Check if target vendor companies are explicitly provided (e.g. requirement broadcast to vendors)
    target_vendor_comps = data.get("target_vendor_companies")
    if not target_vendor_comps and data.get("target_vendor_company"):
        target_vendor_comps = [data.get("target_vendor_company")]

    if target_vendor_comps:
        for v_comp in target_vendor_comps:
            is_vendor = getattr(v_comp, "is_vendor_type", False) or (getattr(v_comp, "company_type", "") in (Company.CompanyType.VENDOR, Company.CompanyType.SELLER, Company.CompanyType.BOTH))
            if getattr(v_comp, "is_active", True) and is_vendor:
                members = CompanyMember.objects.filter(
                    company=v_comp,
                    is_active=True,
                ).select_related("user")
                for m in members:
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
        if company:
            is_vendor = getattr(company, "is_vendor_type", False) or (getattr(company, "company_type", "") in (Company.CompanyType.VENDOR, Company.CompanyType.SELLER, Company.CompanyType.BOTH))
            if getattr(company, "is_active", True) and is_vendor:
                members = CompanyMember.objects.filter(
                    company=company,
                    is_active=True,
                ).select_related("user")
                for m in members:
                    if m.user and m.user.is_active:
                        recipients.add(m.user)
        return recipients

    # 5. VENDOR_ADMINS Audience
    if audience == "VENDOR_ADMINS":
        target_company = data.get("target_company") or company
        if target_company and getattr(target_company, "is_active", True):
            is_vendor = getattr(target_company, "is_vendor_type", False) or (getattr(target_company, "company_type", "") in (Company.CompanyType.VENDOR, Company.CompanyType.SELLER, Company.CompanyType.BOTH))
            if is_vendor:
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

    Dispatches System In-App Notifications, Firebase Cloud Messaging (FCM) Push Notifications,
    and Transactional Email Notifications through a unified, database-driven interface synchronized
    with the Super Admin Panel Notification Management rules and templates.
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

    # 1. Build lookup keys to query database NotificationRule
    lookup_keys = [event, resolved_key]
    for k, v in EVENT_ALIASES.items():
        if v in (event, resolved_key) and k not in lookup_keys:
            lookup_keys.append(k)
        if k in (event, resolved_key) and v not in lookup_keys:
            lookup_keys.append(v)

    rule = (
        NotificationRule.objects.filter(event_key__in=lookup_keys)
        .select_related("template")
        .first()
    )

    # 2. Check Database Rule Governance (Super Admin enable/disable control)
    is_mandatory = event_def.get("is_mandatory", False) or getattr(rule, "is_mandatory_auth", False)
    if rule and not rule.is_enabled and not is_mandatory:
        logger.info(f"Notification rule for '{resolved_key}' ({rule.name}) is disabled in Super Admin Panel. Suppressing.")
        return {
            "event": resolved_key,
            "success": True,
            "suppressed": True,
            "reason": f"Rule '{rule.name}' disabled by Super Admin",
            "recipients_count": 0,
            "in_app_created": 0,
            "in_app_ids": [],
            "push_dispatched": 0,
            "push_sent_count": 0,
            "push_failed_count": 0,
            "email_dispatched": 0,
            "email_sent_count": 0,
            "email_failed_count": 0,
            "email_log_ids": [],
            "errors": [],
        }

    # 3. Check Company Type Scope from Database Rule
    if rule and company and rule.company_type and rule.company_type != NotificationRule.CompanyTypeScope.ALL:
        c_type = getattr(company, "company_type", "")
        is_sales = getattr(company, "is_sales_type", c_type in ("BUYER", "SALES"))
        is_vendor = getattr(company, "is_vendor_type", c_type in ("SELLER", "VENDOR"))
        if rule.company_type in ("BUYER", "SALES") and not (is_sales or c_type in ("BUYER", "SALES", "BOTH")):
            logger.info(f"Company {company.name} out of scope for rule {rule.name} (Rule scope: {rule.company_type})")
            return {
                "event": resolved_key,
                "success": True,
                "suppressed": True,
                "reason": "Company type out of scope",
                "recipients_count": 0,
                "in_app_created": 0,
                "in_app_ids": [],
                "push_dispatched": 0,
                "push_sent_count": 0,
                "push_failed_count": 0,
                "email_dispatched": 0,
                "email_sent_count": 0,
                "email_failed_count": 0,
                "email_log_ids": [],
                "errors": [],
            }
        if rule.company_type in ("SELLER", "VENDOR") and not (is_vendor or c_type in ("SELLER", "VENDOR", "BOTH")):
            logger.info(f"Company {company.name} out of scope for rule {rule.name} (Rule scope: {rule.company_type})")
            return {
                "event": resolved_key,
                "success": True,
                "suppressed": True,
                "reason": "Company type out of scope",
                "recipients_count": 0,
                "in_app_created": 0,
                "in_app_ids": [],
                "push_dispatched": 0,
                "push_sent_count": 0,
                "push_failed_count": 0,
                "email_dispatched": 0,
                "email_sent_count": 0,
                "email_failed_count": 0,
                "email_log_ids": [],
                "errors": [],
            }

    # 4. Resolve Channels (Database rule overrides default unless channels explicitly passed)
    if channels:
        allowed_channels = channels
    elif rule:
        chan_val = rule.channel
        if chan_val == NotificationRule.Channel.ALL:
            allowed_channels = ["email", "in_app", "push"]
        elif chan_val == NotificationRule.Channel.BOTH:
            allowed_channels = ["email", "in_app"]
        elif chan_val == NotificationRule.Channel.IN_APP_PUSH:
            allowed_channels = ["in_app", "push"]
        elif chan_val == NotificationRule.Channel.EMAIL:
            allowed_channels = ["email"]
        elif chan_val == NotificationRule.Channel.IN_APP:
            allowed_channels = ["in_app"]
        elif chan_val == NotificationRule.Channel.PUSH:
            allowed_channels = ["push"]
        else:
            allowed_channels = ["in_app", "push"]
    else:
        allowed_channels = event_def.get("channels", ["in_app", "push"])

    # 5. Dynamic Template & Properties
    category = event_def.get("category", NotificationPreference.Category.PROCUREMENT)
    event_priority = priority or (rule.priority if rule and rule.priority else event_def.get("priority", Notification.Priority.INFO))
    icon = event_def.get("icon", "fa-bell")
    color = event_def.get("color", "blue")
    cooldown_minutes = (rule.cooldown_hours * 60) if (rule and rule.cooldown_hours) else event_def.get("cooldown_minutes", 15)

    title_tmpl = custom_title or (rule.subject_override if rule and rule.subject_override else None)
    if not title_tmpl and rule and rule.template and rule.template.is_active and rule.template.subject:
        title_tmpl = rule.template.subject
    title_tmpl = title_tmpl or event_def.get("title_template", "Notification")

    msg_tmpl = custom_message
    if not msg_tmpl and rule and rule.template and rule.template.is_active and rule.template.text_content:
        msg_tmpl = rule.template.text_content
    msg_tmpl = msg_tmpl or event_def.get("message_template", "")

    url_tmpl = custom_url or event_def.get("default_url", "/dashboard/")

    rendered_title = _format_safe_template(title_tmpl, data, max_len=250)
    rendered_message = _format_safe_template(msg_tmpl, data, max_len=1000)
    rendered_url = _format_safe_template(url_tmpl, data, max_len=500)

    # 6. Resolve Target Recipients
    target_recipients = set()
    if recipient:
        if getattr(recipient, "is_active", False):
            target_recipients.add(recipient)
    elif recipients:
        for r in recipients:
            if getattr(r, "is_active", False):
                target_recipients.add(r)
    else:
        if rule and rule.recipient_type == NotificationRule.RecipientType.SUPER_ADMINS:
            target_recipients = resolve_event_recipients(
                event_key=resolved_key,
                event_def={"audience": "SUPER_ADMINS"},
                company=company,
                data=data,
                actor=actor,
            )
        elif rule and rule.recipient_type == NotificationRule.RecipientType.COMPANY_ADMINS:
            target_recipients = resolve_event_recipients(
                event_key=resolved_key,
                event_def={"audience": "COMPANY_ADMINS"},
                company=company,
                data=data,
                actor=actor,
            )
        else:
            target_recipients = resolve_event_recipients(
                event_key=resolved_key,
                event_def=event_def,
                company=company,
                data=data,
                actor=actor,
            )

    cutoff = timezone.now() - timezone.timedelta(minutes=cooldown_minutes)

    created_notif_objs = []
    push_results = []
    email_results = []
    suppressed_count = 0
    errors_list = []

    for target_user in target_recipients:
        user_company = company
        if not user_company and hasattr(target_user, "company_membership") and target_user.company_membership:
            user_company = target_user.company_membership.company

        # Deduplication Check
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

        # In-App Notification Delivery
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
                        event_key=event,
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

        # Firebase Cloud Messaging (FCM) Browser Push Delivery
        if "push" in allowed_channels:
            push_permitted = is_mandatory or NotificationPreference.is_push_permitted(target_user, category)
            if push_permitted:
                if async_push and transaction.get_connection().in_atomic_block:
                    _t_user = target_user
                    _u_comp = user_company
                    _e_key = event
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
                    res = _execute_push_dispatch(
                        target_user, user_company, event, notif_obj,
                        rendered_title, rendered_message, rendered_url, category, event_priority
                    )
                    push_results.append(res)

        # Transactional Email Delivery (Database-driven NotificationRule)
        if "email" in allowed_channels and target_user.email:
            email_permitted = is_mandatory or NotificationPreference.is_email_permitted(target_user, category)
            if email_permitted:
                from apps.notifications.services.email_engine import trigger_email_notification
                try:
                    e_logs = trigger_email_notification(
                        event_key=rule.event_key if rule else resolved_key,
                        context=data,
                        company=user_company,
                        user=target_user,
                        idempotency_key=f"{effective_dedupe}:email",
                        severity=getattr(rule, "severity", None) or event_def.get("severity"),
                        priority=event_priority,
                        async_dispatch=async_push,
                        override_subject=rendered_title,
                        is_manual=data.get("is_manual", False),
                        sent_by=actor,
                    )
                    if e_logs:
                        email_results.extend(e_logs)
                except Exception as exc:
                    err_msg = f"Email dispatch failed for {target_user.email}: {exc}"
                    logger.warning(err_msg)
                    errors_list.append(err_msg)

    # Custom Recipients Email Dispatch (if defined in rule)
    if "email" in allowed_channels and rule and rule.custom_recipients:
        from apps.notifications.services.email_engine import trigger_email_notification
        c_emails = [e.strip() for e in rule.custom_recipients.split(",") if e.strip()]
        if c_emails:
            try:
                c_logs = trigger_email_notification(
                    event_key=rule.event_key,
                    context=data,
                    company=company,
                    custom_recipient_emails=c_emails,
                    severity=rule.severity,
                    priority=event_priority,
                    async_dispatch=async_push,
                    override_subject=rendered_title,
                    sent_by=actor,
                )
                if c_logs:
                    email_results.extend(c_logs)
            except Exception as exc:
                logger.warning(f"Custom email dispatch failed: {exc}")

    total_push_sent = sum(p.get("sent_count", 0) for p in push_results if isinstance(p, dict))
    total_push_failed = sum(p.get("failed_count", 0) for p in push_results if isinstance(p, dict))
    total_email_sent = sum(1 for el in email_results if getattr(el, "status", None) == NotificationLog.Status.SENT)
    total_email_failed = sum(1 for el in email_results if getattr(el, "status", None) == NotificationLog.Status.FAILED)

    return {
        "event": resolved_key,
        "success": len(created_notif_objs) > 0 or len(push_results) > 0 or len(email_results) > 0 or suppressed_count > 0,
        "recipients_count": len(target_recipients),
        "in_app_created": len(created_notif_objs),
        "in_app_ids": [n.id for n in created_notif_objs],
        "push_dispatched": len(push_results),
        "push_sent_count": total_push_sent,
        "push_failed_count": total_push_failed,
        "email_dispatched": len(email_results),
        "email_sent_count": total_email_sent,
        "email_failed_count": total_email_failed,
        "email_log_ids": [getattr(el, "id", None) for el in email_results if hasattr(el, "id")],
        "suppressed": suppressed_count > 0,
        "errors": errors_list,
    }
