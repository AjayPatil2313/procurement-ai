import logging
import mimetypes
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape
from apps.ai_search.models import SearchResult

logger = logging.getLogger(__name__)


def send_inquiry_email(inquiry, user=None, custom_body=None, attachment_file=None):
    """
    Dispatches a real B2B Inquiry / Proposal email to the recipient (buyer or supplier).
    Supports:
      1. Seller mode: Commercial Proposal & Quotation Offer sent to a Buyer Lead.
      2. Buyer mode: Official Request for Quotation (RFQ) sent to a Supplier.
      3. Physical Document Attachments: Quotation PDF, Technical Specs, PO, etc.
    Includes rich responsive HTML and plain text fallback.
    Sets Reply-To to the active user's or company's business email.
    """
    subject = inquiry.subject
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@procurement-ai.com")
    to_email = inquiry.sent_to_email

    if not to_email:
        logger.warning(f"Inquiry #{inquiry.id} has no recipient email address.")
        return False, "No recipient email address provided."

    reply_to = []
    if user and getattr(user, "email", None):
        reply_to.append(user.email)
    elif inquiry.company and getattr(inquiry.company, "email", None):
        reply_to.append(inquiry.company.email)

    company_name = inquiry.company.name if inquiry.company else "ProcureAI Enterprise"
    product_title = inquiry.search_result.product_title if inquiry.search_result else "Commercial Requirement"
    sender_name = (user.get_full_name() or user.email) if user else company_name
    active_message = custom_body or inquiry.message or ""

    # Determine whether this is a Seller proposal or Buyer RFQ
    is_seller = False
    if inquiry.search_result and inquiry.search_result.result_type == SearchResult.ResultType.LEAD:
        is_seller = True
    elif inquiry.company and getattr(inquiry.company, "company_type", "") == "seller":
        is_seller = True

    # Commercial specifics
    has_price = bool(inquiry.quoted_price and inquiry.quoted_price > 0)
    currency = inquiry.quoted_currency or "INR"
    price_display = f"{currency} {inquiry.quoted_price:,.2f}" if has_price else None
    delivery_terms = inquiry.delivery_terms.strip() if inquiry.delivery_terms else None

    # Handle attachments
    attached_content = None
    attached_filename = None
    attached_mimetype = None

    if attachment_file:
        try:
            if hasattr(attachment_file, "read"):
                if hasattr(attachment_file, "seek"):
                    attachment_file.seek(0)
                attached_content = attachment_file.read()
                if hasattr(attachment_file, "seek"):
                    attachment_file.seek(0)
                raw_name = getattr(attachment_file, "name", "document.pdf")
                attached_filename = raw_name.replace("\\", "/").split("/")[-1]
                attached_mimetype = (
                    getattr(attachment_file, "content_type", None)
                    or mimetypes.guess_type(attached_filename)[0]
                    or "application/octet-stream"
                )
        except Exception as exc:
            logger.warning(f"Error preparing provided attachment for inquiry #{inquiry.id}: {exc}")
    elif inquiry.attachment:
        try:
            if inquiry.attachment.storage.exists(inquiry.attachment.name):
                with inquiry.attachment.open("rb") as f:
                    attached_content = f.read()
                raw_name = inquiry.attachment_name or inquiry.attachment.name
                attached_filename = raw_name.replace("\\", "/").split("/")[-1]
                attached_mimetype = mimetypes.guess_type(attached_filename)[0] or "application/octet-stream"
        except Exception as exc:
            logger.warning(f"Error reading saved attachment from inquiry #{inquiry.id}: {exc}")

    # Plain text version
    if is_seller:
        header_text = f"Official Commercial Proposal & Quotation Offer\nFrom Seller: {sender_name} ({company_name})\nOffered Product: {product_title}"
        if price_display:
            header_text += f"\nQuoted Commercial Price: {price_display}"
        if delivery_terms:
            header_text += f"\nDelivery & Logistics Terms: {delivery_terms}"
        cta_text = f"Please reply directly to this email to accept or discuss terms with {company_name}."
    else:
        header_text = f"Official Request for Quotation (RFQ)\nFrom Buyer: {sender_name} ({company_name})\nTarget Procurement Item: {product_title}"
        if delivery_terms:
            header_text += f"\nRequired Delivery Terms: {delivery_terms}"
        cta_text = "Please reply directly to this email to submit your quotation, lead times, and payment terms."

    attachment_text_snippet = ""
    if attached_filename:
        attachment_text_snippet = f"\n------------------------------------------------------------\nATTACHED COMMERCIAL DOCUMENT:\n• File: {attached_filename}\n(Please find the document attached to this email message.)\n"

    text_content = f"""
{header_text}

------------------------------------------------------------
COMMERCIAL MESSAGE & SPECIFICATIONS:
------------------------------------------------------------
{active_message}
{attachment_text_snippet}
------------------------------------------------------------
{cta_text}

Sent via AI Procurement & Sales Platform on behalf of {company_name}.
"""

    # HTML formatted version
    formatted_message = escape(active_message).replace("\n", "<br>")

    # Colors and branding based on Seller vs Buyer
    if is_seller:
        primary_color = "#ea580c"
        badge_bg = "#ffedd5"
        badge_text = "#9a3412"
        badge_label = "Commercial Proposal"
        title_text = "Commercial Proposal & Quotation"
        role_label = "Seller Organization:"
        rep_label = "Sales Representative:"
        cta_html = f"Please reply directly to this email to accept this proposal or discuss pricing and delivery terms with <strong>{escape(company_name)}</strong>."
    else:
        primary_color = "#1e40af"
        badge_bg = "#dbeafe"
        badge_text = "#1e40af"
        badge_label = "Official RFQ"
        title_text = "Request for Quotation (RFQ)"
        role_label = "Buyer Organization:"
        rep_label = "Procurement Officer:"
        cta_html = "Please reply directly to this email with your formal quotation, turnaround schedule, and payment terms."

    attachment_html_snippet = ""
    if attached_filename:
        attachment_html_snippet = f"""
      <div style="margin-top: 20px; padding: 14px 18px; background: #f8fafc; border: 1.5px dashed #cbd5e1; border-radius: 12px; display: flex; align-items: center; gap: 12px;">
        <div style="font-size: 24px; line-height: 1;">📎</div>
        <div>
          <div style="font-size: 11px; font-weight: 800; text-transform: uppercase; color: #64748b; letter-spacing: 0.5px;">Attached Commercial Document</div>
          <div style="font-size: 13px; font-weight: 700; color: #0f172a; margin-top: 2px;">{escape(attached_filename)}</div>
          <div style="font-size: 11px; color: #94a3b8; margin-top: 2px;">Delivered as a physical attachment with this email dispatch.</div>
        </div>
      </div>
"""

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b; }}
  .container {{ max-width: 620px; margin: 0 auto; background: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); }}
  .header {{ background: {primary_color}; color: #ffffff; padding: 28px 32px; }}
  .badge {{ background: {badge_bg}; color: {badge_text}; font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px; padding: 4px 10px; border-radius: 6px; display: inline-block; margin-bottom: 8px; }}
  .content {{ padding: 32px; }}
  .box {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin-top: 16px; font-size: 14px; line-height: 1.6; color: #334155; }}
  .meta-table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }}
  .meta-table td {{ padding: 10px 0; border-bottom: 1px solid #f1f5f9; }}
  .meta-table td.label {{ color: #64748b; font-weight: 600; width: 38%; }}
  .meta-table td.value {{ color: #0f172a; font-weight: 700; }}
  .highlight-price {{ color: #16a34a; font-size: 15px; font-weight: 800; }}
  .footer {{ background: #f8fafc; border-top: 1px solid #e2e8f0; padding: 20px 32px; font-size: 12px; color: #64748b; text-align: center; }}
</style>
</head>
<body>
  <div class="container">
    <div class="header">
      <span class="badge">{badge_label}</span>
      <h2 style="margin: 0; font-size: 22px; font-weight: 800;">{title_text}</h2>
      <p style="margin: 6px 0 0 0; font-size: 13px; opacity: 0.95;">Initiated on behalf of {escape(company_name)}</p>
    </div>
    <div class="content">
      <table class="meta-table">
        <tr>
          <td class="label">Product / Scope:</td>
          <td class="value">{escape(product_title)}</td>
        </tr>
        <tr>
          <td class="label">{role_label}</td>
          <td class="value">{escape(company_name)}</td>
        </tr>
        <tr>
          <td class="label">{rep_label}</td>
          <td class="value">{escape(sender_name)}</td>
        </tr>
        {"<tr><td class='label'>Offered Price:</td><td class='value highlight-price'>" + escape(price_display) + "</td></tr>" if price_display else ""}
        {"<tr><td class='label'>Delivery Terms:</td><td class='value'>" + escape(delivery_terms) + "</td></tr>" if delivery_terms else ""}
      </table>

      <h4 style="margin: 24px 0 8px 0; font-size: 14px; font-weight: 800; color: #0f172a;">Commercial Scope & Terms:</h4>
      <div class="box">
        {formatted_message}
      </div>

      {attachment_html_snippet}

      <p style="margin-top: 24px; font-size: 13px; color: #475569; line-height: 1.5;">
        {cta_html}
      </p>
    </div>
    <div class="footer">
      Dispatched via <strong>AI Procurement & Sales Platform</strong> &bull; Secured Enterprise B2B Engine
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

        if attached_content and attached_filename:
            msg.attach(attached_filename, attached_content, attached_mimetype)

        msg.send(fail_silently=False)
        logger.info(f"Inquiry #{inquiry.id} successfully sent to {to_email} via configured email backend (attachment: {attached_filename})")
        return True, "Email successfully dispatched to recipient."
    except Exception as exc:
        logger.warning(f"Failed to dispatch email for inquiry #{inquiry.id} to {to_email}: {exc}")
        return False, str(exc)


def send_inquiry_followup_email(inquiry_message, user=None):
    """
    Dispatches an official follow-up message email in an existing inquiry conversation thread.
    Includes any attached document.
    """
    inquiry = inquiry_message.inquiry
    subject = inquiry_message.subject or f"Re: {inquiry.subject}"
    return send_inquiry_email(
        inquiry,
        user=user,
        custom_body=inquiry_message.body,
        attachment_file=inquiry_message.attachment if inquiry_message.attachment else None,
    )
