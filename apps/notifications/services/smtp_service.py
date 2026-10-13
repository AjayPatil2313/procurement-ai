import logging
from django.conf import settings
from django.core.mail import get_connection, EmailMultiAlternatives
from apps.notifications.models import SMTPSetting

logger = logging.getLogger(__name__)


def get_active_email_connection():
    """
    Returns a configured email connection based on the active SMTPSetting in the DB,
    or falls back to Django's settings-configured backend.
    """
    active_smtp = SMTPSetting.get_active_config()
    if active_smtp:
        if active_smtp.backend_type == SMTPSetting.BackendType.CONSOLE:
            return get_connection("django.core.mail.backends.console.EmailBackend"), active_smtp
        elif active_smtp.backend_type == SMTPSetting.BackendType.LOCMEM:
            return get_connection("django.core.mail.backends.locmem.EmailBackend"), active_smtp
        else:
            conn = get_connection(
                backend="django.core.mail.backends.smtp.EmailBackend",
                host=active_smtp.host,
                port=active_smtp.port,
                username=active_smtp.username,
                password=active_smtp.get_password(),
                use_tls=active_smtp.use_tls,
                use_ssl=active_smtp.use_ssl,
                timeout=active_smtp.timeout,
            )
            return conn, active_smtp

    # Fallback to standard Django connection from settings.py
    return get_connection(), None


def get_default_from_email():
    """
    Returns formatted 'Name <email>' string from DB setting or settings.py.
    """
    active_smtp = SMTPSetting.get_active_config()
    if active_smtp:
        name = active_smtp.sender_name.strip()
        email = active_smtp.sender_email.strip()
        return f"{name} <{email}>" if name else email
    return getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@procurement-ai.com")


def test_smtp_connection(recipient_email, subject="Test Email — AI Procurement & Sales Platform"):
    """
    Attempts to send a test email using the currently configured SMTP backend.
    Returns (success: bool, message: str).
    """
    try:
        connection, active_config = get_active_email_connection()
        from_email = get_default_from_email()

        html_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 12px;">
            <div style="background-color: #0f172a; padding: 16px; border-radius: 8px; text-align: center; color: #ffffff;">
                <h2 style="margin: 0; font-size: 18px;">AI Procurement & Sales Platform</h2>
                <p style="margin: 4px 0 0; font-size: 12px; color: #94a3b8;">Super Admin SMTP Verification</p>
            </div>
            <div style="padding: 24px 8px;">
                <h3 style="color: #059669; margin-top: 0;">✓ SMTP Configuration Successful</h3>
                <p style="font-size: 13px; color: #334155; line-height: 1.6;">
                    This is a verification test email dispatched from your platform's Super Admin control center.
                    Your outgoing mail server credentials and connection handshake are operating normally.
                </p>
                <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; font-size: 12px; color: #475569; font-family: monospace;">
                    <strong>Sender:</strong> {from_email}<br>
                    <strong>Recipient:</strong> {recipient_email}<br>
                    <strong>Backend:</strong> {getattr(active_config, 'backend_type', 'Default Settings Backend')}<br>
                    <strong>Host:</strong> {getattr(active_config, 'host', getattr(settings, 'EMAIL_HOST', 'Default'))}
                </div>
            </div>
            <div style="border-top: 1px solid #e2e8f0; padding-top: 12px; text-align: center; font-size: 11px; color: #94a3b8;">
                B2B AI Procurement & Sales Platform &bull; Automated System Verification
            </div>
        </div>
        """
        text_body = f"AI Procurement & Sales Platform - SMTP Test Email.\nSender: {from_email}\nRecipient: {recipient_email}\nYour email configuration is working correctly."

        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=from_email,
            to=[recipient_email],
            connection=connection,
        )
        msg.attach_alternative(html_body, "text/html")
        sent = msg.send(fail_silently=False)

        if sent:
            return True, f"Verification email successfully dispatched to {recipient_email}."
        else:
            return False, "Email backend accepted command but returned 0 messages sent."
    except Exception as exc:
        logger.exception("SMTP test failed: %s", exc)
        return False, str(exc)
