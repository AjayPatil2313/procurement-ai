import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape

logger = logging.getLogger(__name__)


def send_inquiry_email(inquiry, user=None):
    """
    Dispatches an official B2B RFQ / Inquiry email to the external supplier.
    Supports both plaintext and rich HTML formatting.
    Sets Reply-To to the active user's business email.
    """
    subject = inquiry.subject
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@procurement-ai.com")
    to_email = inquiry.sent_to_email

    reply_to = []
    if user and getattr(user, "email", None):
        reply_to.append(user.email)
    elif inquiry.company and getattr(inquiry.company, "email", None):
        reply_to.append(inquiry.company.email)

    company_name = inquiry.company.name if inquiry.company else "Procurement Buyer"
    product_title = inquiry.search_result.product_title if inquiry.search_result else "Commercial Requirement"
    sender_name = user.get_full_name() or user.email if user else company_name

    # Plain text version
    text_content = f"""
Official Request for Quotation (RFQ)
From: {sender_name} ({company_name})
Target Item: {product_title}

------------------------------------------------------------
COMMERCIAL MESSAGE & SPECIFICATIONS:
------------------------------------------------------------
{inquiry.message}

------------------------------------------------------------
Please reply directly to this email to submit your quotation,
lead times, and payment terms.

Sent via AI Procurement Platform on behalf of {company_name}.
"""

    # HTML formatted version
    formatted_message = escape(inquiry.message).replace("\n", "<br>")
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b; }}
  .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; overflow: hidden; }}
  .header {{ background: #1e40af; color: #ffffff; padding: 24px 28px; }}
  .badge {{ background: #dbeafe; color: #1e40af; font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 4px 10px; border-radius: 6px; display: inline-block; margin-bottom: 8px; }}
  .content {{ padding: 28px; }}
  .box {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin-top: 16px; font-size: 14px; line-height: 1.6; color: #334155; }}
  .meta-table {{ width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 13px; }}
  .meta-table td {{ padding: 8px 0; border-bottom: 1px solid #f1f5f9; }}
  .meta-table td.label {{ color: #64748b; font-weight: 600; width: 35%; }}
  .meta-table td.value {{ color: #0f172a; font-weight: 700; }}
  .footer {{ background: #f8fafc; border-top: 1px solid #e2e8f0; padding: 20px 28px; font-size: 12px; color: #64748b; text-align: center; }}
</style>
</head>
<body>
  <div class="container">
    <div class="header">
      <span class="badge">Official RFQ</span>
      <h2 style="margin: 0; font-size: 20px; font-weight: 800;">Request for Quotation</h2>
      <p style="margin: 4px 0 0 0; font-size: 13px; opacity: 0.9;">Initiated by {company_name}</p>
    </div>
    <div class="content">
      <table class="meta-table">
        <tr>
          <td class="label">Procurement Item:</td>
          <td class="value">{product_title}</td>
        </tr>
        <tr>
          <td class="label">Buyer Organization:</td>
          <td class="value">{company_name}</td>
        </tr>
        <tr>
          <td class="label">Authorized Contact:</td>
          <td class="value">{sender_name}</td>
        </tr>
      </table>

      <h4 style="margin: 24px 0 8px 0; font-size: 14px; color: #0f172a;">Commercial Inquiry & Terms:</h4>
      <div class="box">
        {formatted_message}
      </div>

      <p style="margin-top: 20px; font-size: 13px; color: #475569;">
        Please reply directly to this email with your formal quotation, delivery schedules, and payment terms.
      </p>
    </div>
    <div class="footer">
      Dispatched via <strong>AI Procurement & Sales Platform</strong> on behalf of {company_name}.
    </div>
  </div>
</body>
</html>
"""

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content.strip(),
            from_email=from_email,
            to=[to_email],
            reply_to=reply_to,
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)
        logger.info(f"Inquiry #{inquiry.id} successfully sent to {to_email}")
        return True, "Email successfully sent to supplier."
    except Exception as exc:
        logger.warning(f"Failed to dispatch email for inquiry #{inquiry.id} to {to_email}: {exc}")
        return False, str(exc)
