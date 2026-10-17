"""
Dynamic Notification Engine & Dispatcher
AI Procurement & Sales B2B Platform

Coordinates In-App Notifications and Firebase Web Push notifications across
all 48 business events with strict channel separation from Email.
"""

import logging
from django.contrib.auth import get_user_model
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
from apps.notifications.services.notification_engine import (
    notify_event,
    resolve_event_recipients,
)

logger = logging.getLogger(__name__)
User = get_user_model()


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
    Backward-compatible dynamic dispatch wrapper.
    Delegates directly to the authoritative central engine `notify_event()`.
    """
    res = notify_event(
        event=event_key,
        company=company,
        data=context or {},
        actor=actor,
        custom_title=custom_title,
        custom_message=custom_message,
        custom_url=custom_url,
        dedupe_key=deduplication_key,
        async_push=False,  # Keep synchronous for backward compatibility in existing tests
    )
    return {
        "success": res.get("success", False),
        "event": res.get("event", event_key),
        "event_key": res.get("event", event_key),
        "recipients_count": res.get("recipients_count", 0),
        "in_app_created": res.get("in_app_created", 0),
        "push_dispatched": res.get("push_dispatched", 0),
        "in_app_ids": res.get("in_app_ids", []),
        "suppressed": res.get("suppressed", False),
    }
