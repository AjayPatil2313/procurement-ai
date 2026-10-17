import json
import logging
import os
from django.conf import settings
from django.utils import timezone
from apps.notifications.models import DeviceToken, NotificationPreference

logger = logging.getLogger(__name__)

_FIREBASE_APP_INITIALIZED = False


def get_firebase_app():
    """
    Lazily initializes the Firebase Admin SDK if valid credentials are provided.
    Returns the Firebase App instance or None if not configured.
    """
    global _FIREBASE_APP_INITIALIZED
    import firebase_admin
    from firebase_admin import credentials

    if firebase_admin._apps:
        return firebase_admin.get_app()

    cred = None
    cred_path = getattr(settings, "FIREBASE_CREDENTIALS_PATH", "")
    cred_json_str = getattr(settings, "FIREBASE_SERVICE_ACCOUNT_JSON", "")

    try:
        if cred_path and os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            logger.info(f"Loaded Firebase credentials from file: {cred_path}")
        elif cred_json_str:
            cred_dict = json.loads(cred_json_str) if isinstance(cred_json_str, str) else cred_json_str
            cred = credentials.Certificate(cred_dict)
            logger.info("Loaded Firebase credentials from environment JSON.")
        elif getattr(settings, "FIREBASE_PROJECT_ID", ""):
            # Attempt default application credentials
            try:
                cred = credentials.ApplicationDefault()
            except Exception:
                cred = None
    except Exception as exc:
        logger.warning(f"Failed to load Firebase credentials: {exc}")
        return None

    if not cred:
        return None

    try:
        options = {}
        if getattr(settings, "FIREBASE_PROJECT_ID", ""):
            options["projectId"] = settings.FIREBASE_PROJECT_ID
        app = firebase_admin.initialize_app(cred, options=options or None)
        _FIREBASE_APP_INITIALIZED = True
        logger.info("Firebase Admin SDK successfully initialized.")
        return app
    except Exception as exc:
        logger.warning(f"Error initializing Firebase Admin SDK: {exc}")
        return None


def is_firebase_configured():
    """Returns True if Firebase credentials or project settings are present."""
    return bool(
        getattr(settings, "FIREBASE_CREDENTIALS_PATH", "")
        or getattr(settings, "FIREBASE_SERVICE_ACCOUNT_JSON", "")
        or getattr(settings, "FIREBASE_PROJECT_ID", "")
    )


def get_firebase_web_config():
    """
    Returns public web push configuration for browser JavaScript SDK initialization.
    Never exposes private service account keys.
    """
    return {
        "enabled": is_firebase_configured(),
        "apiKey": getattr(settings, "FIREBASE_API_KEY", ""),
        "authDomain": getattr(settings, "FIREBASE_AUTH_DOMAIN", ""),
        "projectId": getattr(settings, "FIREBASE_PROJECT_ID", ""),
        "storageBucket": getattr(settings, "FIREBASE_STORAGE_BUCKET", ""),
        "messagingSenderId": getattr(settings, "FIREBASE_MESSAGING_SENDER_ID", ""),
        "appId": getattr(settings, "FIREBASE_APP_ID", ""),
        "measurementId": getattr(settings, "FIREBASE_MEASUREMENT_ID", ""),
        "vapidPublicKey": getattr(settings, "FIREBASE_VAPID_PUBLIC_KEY", ""),
    }


def register_device_token(user, token, device_type="OTHER", device_name="", user_agent=""):
    """
    Registers or updates an FCM web push token for a given authenticated user.
    Associates token with user's company membership for tenant isolation.
    """
    if not token or not token.strip():
        raise ValueError("Device token cannot be empty.")

    clean_token = token.strip()
    company = getattr(user, "company", None)
    if not company and hasattr(user, "company_membership") and user.company_membership:
        company = user.company_membership.company

    device_token, created = DeviceToken.objects.update_or_create(
        token=clean_token,
        defaults={
            "user": user,
            "company": company,
            "device_type": device_type or DeviceToken.DeviceType.OTHER,
            "device_name": (device_name or "Web Browser")[:150],
            "user_agent": user_agent or "",
            "is_active": True,
        },
    )
    if not created and not device_token.is_active:
        device_token.is_active = True
        device_token.save(update_fields=["is_active", "last_seen_at"])

    return device_token


def unregister_device_token(user, token):
    """
    Deactivates an FCM token for a given user (upon logout or permission revoke).
    """
    if not token:
        return False
    updated = DeviceToken.objects.filter(user=user, token=token.strip()).update(
        is_active=False
    )
    return updated > 0


def send_fcm_multicast(tokens, title, body, link="", icon="", badge="", data=None, urgency="normal"):
    """
    Sends Web Push notifications to a list of registration tokens using FCM HTTP v1.
    Tracks exact per-device outcome in device_results.
    Automatically identifies invalid or unregistered tokens and marks them is_active=False.
    """
    if not tokens:
        return {
            "success": False,
            "sent_count": 0,
            "failed_count": 0,
            "errors": ["No tokens provided"],
            "device_results": {},
        }

    app = get_firebase_app()
    if not app:
        logger.info("Firebase Admin SDK is not initialized. Skipping push dispatch.")
        fail_map = {
            t: {
                "success": False,
                "message_id": "",
                "error_code": "FIREBASE_NOT_CONFIGURED",
                "error_message": "Firebase Admin SDK is not configured",
            }
            for t in tokens
        }
        return {
            "success": False,
            "sent_count": 0,
            "failed_count": len(tokens),
            "errors": ["Firebase not configured"],
            "device_results": fail_map,
        }

    from firebase_admin import messaging

    # Ensure link is relative root-based to avoid ERR_CONNECTION_REFUSED
    clean_url = link or "/dashboard/"
    if clean_url.startswith("http://") or clean_url.startswith("https://"):
        try:
            from urllib.parse import urlparse
            parsed = urlparse(clean_url)
            clean_url = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        except Exception:
            clean_url = "/dashboard/"

    custom_data = {
        "title": title[:250],
        "body": body[:500],
        "click_action": clean_url,
        "url": clean_url,
        **(data or {}),
    }
    # Firebase messaging data values must all be strings
    sanitized_data = {str(k): str(v) for k, v in custom_data.items()}

    fcm_options = None
    if clean_url and clean_url.startswith("https://"):
        fcm_options = messaging.WebpushFCMOptions(link=clean_url)

    # Configure WebPush headers including Urgency (high vs normal)
    webpush_headers = {
        "Urgency": "high" if urgency == "high" else "normal"
    }

    webpush_kwargs = {
        "headers": webpush_headers,
        "notification": messaging.WebpushNotification(
            title=title[:250],
            body=body[:500],
            icon=icon or "/static/images/logo.png",
            badge=badge or "/static/images/favicon.ico",
        ),
        "data": sanitized_data,
    }
    if fcm_options:
        webpush_kwargs["fcm_options"] = fcm_options

    webpush_config = messaging.WebpushConfig(**webpush_kwargs)

    message = messaging.MulticastMessage(
        tokens=list(tokens),
        data=sanitized_data,
        webpush=webpush_config,
    )

    device_results = {}
    try:
        response = messaging.send_each_for_multicast(message)
    except Exception as exc:
        logger.error(f"FCM multicast dispatch failed with exception: {exc}")
        for t in tokens:
            device_results[t] = {
                "success": False,
                "message_id": "",
                "error_code": "FCM_DISPATCH_FAIL",
                "error_message": str(exc),
            }
        return {
            "success": False,
            "sent_count": 0,
            "failed_count": len(tokens),
            "errors": [str(exc)],
            "device_results": device_results,
        }

    stale_tokens = []
    errors = []
    for idx, resp in enumerate(response.responses):
        t = tokens[idx]
        if resp.success:
            device_results[t] = {
                "success": True,
                "message_id": getattr(resp, "message_id", "") or "",
                "error_code": "",
                "error_message": "",
            }
        else:
            err = resp.exception
            err_str = str(err)
            errors.append(err_str)

            err_code = "FCM_DISPATCH_FAIL"
            if isinstance(err, messaging.UnregisteredError) or "unregistered" in err_str.lower():
                err_code = "UNREGISTERED"
            elif isinstance(err, messaging.SenderIdMismatchError):
                err_code = "SENDER_ID_MISMATCH"
            elif isinstance(err, messaging.QuotaExceededError):
                err_code = "QUOTA_EXCEEDED"
            elif "not found" in err_str.lower():
                err_code = "TOKEN_NOT_FOUND"

            device_results[t] = {
                "success": False,
                "message_id": "",
                "error_code": err_code,
                "error_message": err_str,
            }

            # Mark stale tokens for deactivation
            if err_code in ["UNREGISTERED", "TOKEN_NOT_FOUND", "SENDER_ID_MISMATCH"]:
                stale_tokens.append(t)

    if stale_tokens:
        logger.info(f"Deactivating {len(stale_tokens)} stale FCM tokens.")
        DeviceToken.objects.filter(token__in=stale_tokens).update(is_active=False)

    return {
        "success": response.success_count > 0,
        "sent_count": response.success_count,
        "failed_count": response.failure_count,
        "errors": errors,
        "device_results": device_results,
    }


def send_user_push(user_or_users, title, body, link="", icon="", badge="", data=None, category=None, priority=None):
    """
    Central push dispatch function:
    - Resolves active tokens for user(s).
    - Checks user preferences (push_enabled).
    - Checks company suspension (skips suspended tenants).
    - Configures urgency header based on notification priority.
    - Calls send_fcm_multicast.
    """
    if not isinstance(user_or_users, (list, tuple, set)):
        users = [user_or_users]
    else:
        users = list(user_or_users)

    active_tokens = []
    for u in users:
        if not u or not getattr(u, "is_active", True):
            continue

        # Enforce tenant isolation & governance: if company is suspended, skip push
        u_company = getattr(u, "company", None)
        if not u_company and hasattr(u, "company_membership") and u.company_membership:
            u_company = u.company_membership.company
        if u_company and not getattr(u_company, "is_active", True):
            logger.info(f"Skipping push for user {u.email}: Company '{u_company.name}' is suspended.")
            continue

        # Check notification preference if category provided
        if category and not NotificationPreference.is_push_permitted(u, category):
            logger.info(f"Skipping push for user {u.email}: Push disabled for category '{category}'.")
            continue

        user_tokens = list(
            DeviceToken.objects.filter(user=u, is_active=True).values_list("token", flat=True)
        )
        active_tokens.extend(user_tokens)

    if not active_tokens:
        return {
            "success": True,
            "sent_count": 0,
            "failed_count": 0,
            "message": "No active devices",
            "device_results": {},
        }

    # High urgency for critical or warning notifications
    urgency = "high" if priority in ["CRITICAL", "WARNING"] or (data and str(data.get("priority", "")).upper() in ["CRITICAL", "WARNING"]) else "normal"

    return send_fcm_multicast(
        tokens=active_tokens,
        title=title,
        body=body,
        link=link,
        icon=icon,
        badge=badge,
        data=data,
        urgency=urgency,
    )
