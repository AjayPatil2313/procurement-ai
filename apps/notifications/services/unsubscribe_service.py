import logging
from django.conf import settings
from django.core import signing
from django.contrib.auth import get_user_model
from apps.notifications.models import NotificationPreference

logger = logging.getLogger(__name__)

UNSUBSCRIBE_SALT = "tradeconnect-email-unsubscribe-v1"


def generate_unsubscribe_token(user_id, category="ALL"):
    """
    Generates a secure, cryptographically signed token for 1-click unsubscribe.
    Payload includes user_id, category, and timestamp.
    """
    signer = signing.TimestampSigner(salt=UNSUBSCRIBE_SALT)
    payload = f"{user_id}:{category}"
    return signer.sign(payload)


def verify_unsubscribe_token(token, max_age_days=60):
    """
    Verifies the unsubscribe token. Returns (user, category, is_valid, error_msg).
    """
    if not token:
        return None, None, False, "Missing token."

    signer = signing.TimestampSigner(salt=UNSUBSCRIBE_SALT)
    try:
        max_age_seconds = max_age_days * 86400
        unsigned = signer.unsign(token, max_age=max_age_seconds)
        parts = unsigned.split(":", 1)
        user_id = int(parts[0])
        category = parts[1] if len(parts) > 1 else "ALL"

        User = get_user_model()
        user = User.objects.filter(id=user_id, is_active=True).first()
        if not user:
            return None, None, False, "User account not found or inactive."

        return user, category, True, ""
    except signing.SignatureExpired:
        return None, None, False, "This unsubscribe link has expired."
    except (signing.BadSignature, ValueError) as exc:
        logger.warning(f"Invalid unsubscribe token signature: {exc}")
        return None, None, False, "Invalid unsubscribe security signature."


def process_user_unsubscribe(user, category, company=None):
    """
    Safely unsubscribes the user from the specified notification category.
    Mandatory categories (SECURITY, BILLING) remain protected per compliance.
    Returns (success: bool, message: str).
    """
    if not user:
        return False, "User is required."

    # Cannot unsubscribe from security alerts
    if category in (NotificationPreference.Category.SECURITY,):
        return False, "Account and security alerts cannot be disabled for enterprise compliance."

    if category == "ALL":
        # Disable all non-mandatory categories
        non_mandatory = [
            c for c, _ in NotificationPreference.Category.choices
            if c not in (NotificationPreference.Category.SECURITY, NotificationPreference.Category.BILLING)
        ]
        for cat in non_mandatory:
            pref, _ = NotificationPreference.objects.get_or_create(
                user=user,
                company=company,
                category=cat,
            )
            pref.email_enabled = False
            pref.save(update_fields=["email_enabled"])
        return True, "Successfully unsubscribed from all optional email communications."

    # Single category
    valid_categories = dict(NotificationPreference.Category.choices)
    if category not in valid_categories:
        # Generic match or fallback
        matched = None
        for cat_key in valid_categories:
            if cat_key.lower() in category.lower() or category.lower() in cat_key.lower():
                matched = cat_key
                break
        category = matched or NotificationPreference.Category.MARKETING

    pref, _ = NotificationPreference.objects.get_or_create(
        user=user,
        company=company,
        category=category,
    )
    pref.email_enabled = False
    pref.save(update_fields=["email_enabled"])

    cat_label = valid_categories.get(category, category)
    return True, f"Successfully unsubscribed from {cat_label} emails."
