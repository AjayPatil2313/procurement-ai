import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


class TransactionalEmailService:
    """
    Enterprise Transactional Email Engine:
    Handles multi-tenant RFQs, quote updates, low credit alerts,
    subscription expiration reminders, and PDF invoice delivery.
    """

    @staticmethod
    def _send_html_email(subject: str, recipient_list: list, html_content: str, attachments: list = None) -> bool:
        """
        Internal safe email dispatcher with HTML + text fallback and attachment support.
        Fails gracefully without raising exceptions to ensure transaction safety.
        """
        if not recipient_list:
            return False

        # Filter out empty or invalid addresses
        valid_recipients = [r.strip() for r in recipient_list if r and "@" in r and not r.endswith("@example.com")]
        if not valid_recipients:
            logger.info("No valid email recipients for subject: '%s'. Skipping delivery.", subject)
            return False

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "Procurement AI <noreply@procurement-ai.com>")
        text_content = strip_tags(html_content)

        try:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=from_email,
                to=valid_recipients,
            )
            msg.attach_alternative(html_content, "text/html")

            if attachments:
                for att in attachments:
                    # att tuple: (filename, content, mimetype)
                    if isinstance(att, tuple) and len(att) == 3:
                        msg.attach(att[0], att[1], att[2])

            sent_count = msg.send(fail_silently=True)
            logger.info("Dispatched transactional email '%s' to %s (sent=%s)", subject, valid_recipients, sent_count)
            return bool(sent_count)
        except Exception as e:
            logger.warning("Transactional email delivery failed for '%s': %s", subject, e)
            return False

    @classmethod
    def send_rfq_email(cls, inquiry) -> bool:
        """
        Sends an official Request For Quotation (RFQ) to the target supplier.
        """
        buyer_company = inquiry.company
        supplier_email = inquiry.sent_to_email
        target_item = inquiry.search_result.product_title if inquiry.search_result else "Commercial Sourcing Requisition"

        subject = f"[RFQ] Requisition for {target_item} from {buyer_company.name}"

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 620px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; color: #334155;">
            <div style="background-color: #0f172a; padding: 24px; color: #ffffff; text-align: center;">
                <h1 style="margin: 0; font-size: 20px; font-weight: bold; letter-spacing: 0.5px;">PROCUREMENT AI</h1>
                <p style="margin: 4px 0 0; font-size: 13px; color: #94a3b8;">Commercial Request For Quotation (RFQ)</p>
            </div>
            <div style="padding: 24px; background-color: #ffffff;">
                <p style="font-size: 15px; color: #0f172a; margin-top: 0;"><b>Dear Commercial Sales Team,</b></p>
                <p style="font-size: 14px; line-height: 1.6;">
                    <b>{buyer_company.name}</b> has identified your enterprise through Procurement AI's verified vendor network
                    and is requesting your best formal commercial proposal for the following requisition:
                </p>

                <div style="background-color: #f8fafc; border-left: 4px solid #2563eb; padding: 14px 18px; margin: 20px 0; border-radius: 4px;">
                    <p style="margin: 0 0 6px; font-size: 14px;"><b>Subject:</b> {inquiry.subject}</p>
                    <p style="margin: 0 0 6px; font-size: 14px;"><b>Target Item:</b> {target_item}</p>
                    <p style="margin: 0; font-size: 14px; color: #475569;"><b>Requisition Details:</b><br/>{inquiry.message.replace(chr(10), '<br/>')}</p>
                </div>

                <div style="background-color: #f1f5f9; padding: 12px 16px; border-radius: 6px; font-size: 13px; color: #475569;">
                    <b>Buyer Organization:</b> {buyer_company.name}<br/>
                    <b>Location:</b> {buyer_company.city or ''}, {buyer_company.country or 'India'}<br/>
                    <b>Contact Email:</b> {buyer_company.email or 'N/A'}<br/>
                    <b>Phone:</b> {buyer_company.phone or 'N/A'}
                </div>

                <p style="font-size: 13px; line-height: 1.5; margin-top: 20px; color: #64748b;">
                    Please reply directly to this email or send your quotation and lead times to <b>{buyer_company.email or 'the buyer organization'}</b>.
                </p>
            </div>
            <div style="background-color: #f8fafc; padding: 14px; text-align: center; font-size: 12px; color: #94a3b8; border-top: 1px solid #e2e8f0;">
                Sent securely via Procurement AI Automated Enterprise Sourcing.
            </div>
        </div>
        """

        attachments = []
        if inquiry.attachment:
            try:
                att_name = inquiry.get_attachment_filename() or "RFQ_Specification.pdf"
                inquiry.attachment.seek(0)
                attachments.append((att_name, inquiry.attachment.read(), "application/octet-stream"))
            except Exception as e:
                logger.warning("Could not read inquiry attachment: %s", e)

        return cls._send_html_email(subject, [supplier_email], html_content, attachments)

    @classmethod
    def _get_admin_emails(cls, company) -> list:
        recipients = []
        if company:
            if hasattr(company, "members"):
                for m in company.members.filter(is_active=True):
                    if m.user and m.user.email and str(m.role).upper() in ("ADMIN", "COMPANY_ADMIN"):
                        recipients.append(m.user.email)
            if not recipients and company.email:
                recipients.append(company.email)
        return list(set(recipients))

    @classmethod
    def send_quote_received_email(cls, inquiry) -> bool:
        """
        Alerts buyer administrator and team members that a supplier submitted a quote.
        """
        buyer_company = inquiry.company
        supplier_name = inquiry.search_result.external_company.name if inquiry.search_result else "Supplier"
        target_item = inquiry.search_result.product_title if inquiry.search_result else "Requested Item"

        # Notify company admins
        recipients = cls._get_admin_emails(buyer_company)

        subject = f"[Quote Received] {supplier_name} quoted on {target_item}"

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; color: #334155;">
            <div style="background-color: #15803d; padding: 20px; color: #ffffff; text-align: center;">
                <h2 style="margin: 0; font-size: 18px;">Commercial Quotation Received</h2>
            </div>
            <div style="padding: 22px; background-color: #ffffff;">
                <p style="font-size: 14px;">Great news! <b>{supplier_name}</b> has provided commercial terms for your inquiry on <b>{target_item}</b>.</p>

                <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; padding: 14px; border-radius: 6px; margin: 16px 0;">
                    <p style="margin: 0 0 6px; font-size: 16px; color: #166534;"><b>Quoted Price:</b> {inquiry.quoted_currency} {inquiry.quoted_price}</p>
                    <p style="margin: 0 0 6px; font-size: 13px;"><b>Delivery Terms:</b> {inquiry.delivery_terms or 'Standard Delivery'}</p>
                    <p style="margin: 0; font-size: 13px;"><b>Status:</b> {inquiry.get_status_display()}</p>
                </div>

                <p style="font-size: 13px; color: #64748b;">
                    You can review this quote, compare it with competitor offers, or message the supplier directly on the platform.
                </p>
            </div>
            <div style="background-color: #f8fafc; padding: 12px; text-align: center; font-size: 11px; color: #94a3b8;">
                Procurement AI CRM Notifications
            </div>
        </div>
        """
        return cls._send_html_email(subject, recipients, html_content)

    @classmethod
    def send_low_credit_alert_email(cls, company, available_credits: int) -> bool:
        """
        Alerts Company Admins when available credit balance is critically low.
        """
        recipients = cls._get_admin_emails(company)

        subject = f"[Action Required] Low Credit Balance Alert for {company.name}"

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #fed7aa; border-radius: 8px; overflow: hidden; color: #334155;">
            <div style="background-color: #ea580c; padding: 20px; color: #ffffff; text-align: center;">
                <h2 style="margin: 0; font-size: 18px;">⚠️ Credit Balance Running Low</h2>
            </div>
            <div style="padding: 22px; background-color: #ffffff;">
                <p style="font-size: 14px;">Hello Admin,</p>
                <p style="font-size: 14px; line-height: 1.5;">
                    Your company <b>{company.name}</b> has only <b>{available_credits} search/AI credits</b> remaining in your wallet.
                    Once credits are depleted, autonomous vendor discovery and report exports will be temporarily paused.
                </p>

                <div style="text-align: center; margin: 24px 0;">
                    <a href="/company/billing/" style="background-color: #2563eb; color: #ffffff; padding: 10px 22px; border-radius: 6px; text-decoration: none; font-weight: bold; font-size: 14px; display: inline-block;">
                        Top Up Credits Now
                    </a>
                </div>

                <p style="font-size: 12px; color: #64748b;">
                    Choose from 100, 500, or 1000 credit top-up packages, or upgrade your monthly tier for increased allocations.
                </p>
            </div>
        </div>
        """
        return cls._send_html_email(subject, recipients, html_content)

    @classmethod
    def send_subscription_expiry_reminder_email(cls, company, subscription, days_left: int) -> bool:
        """
        Sends advance subscription renewal warning email to Company Admins.
        """
        recipients = cls._get_admin_emails(company)

        subject = f"[Reminder] Subscription for {company.name} expires in {days_left} day{'s' if days_left > 1 else ''}"

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #fed7aa; border-radius: 8px; overflow: hidden; color: #334155;">
            <div style="background-color: #f59e0b; padding: 20px; color: #ffffff; text-align: center;">
                <h2 style="margin: 0; font-size: 18px;">Subscription Renewal Reminder</h2>
            </div>
            <div style="padding: 22px; background-color: #ffffff;">
                <p style="font-size: 14px;">Hello Admin,</p>
                <p style="font-size: 14px; line-height: 1.5;">
                    Your <b>{subscription.get_plan_display()}</b> plan for <b>{company.name}</b> is scheduled to renew or expire on <b>{subscription.valid_till}</b> (in {days_left} days).
                </p>

                <div style="text-align: center; margin: 24px 0;">
                    <a href="/company/billing/" style="background-color: #2563eb; color: #ffffff; padding: 10px 22px; border-radius: 6px; text-decoration: none; font-weight: bold; font-size: 14px; display: inline-block;">
                        Renew Subscription
                    </a>
                </div>
            </div>
        </div>
        """
        return cls._send_html_email(subject, recipients, html_content)

    @classmethod
    def send_payment_invoice_email(cls, invoice, pdf_bytes: bytes = None) -> bool:
        """
        Emails official Tax Invoice directly to the Company Admin upon payment completion.
        Attaches vector PDF invoice if provided or generates it dynamically.
        """
        company = invoice.company
        recipients = cls._get_admin_emails(company)
        if not recipients and company.email:
            recipients = [company.email]

        subject = f"Tax Invoice {invoice.invoice_number} from Procurement AI (₹{invoice.total_amount})"

        if not pdf_bytes:
            from apps.billing.services.pdf_generator import PDFReportGenerator
            try:
                pdf_bytes = PDFReportGenerator.generate_tax_invoice_pdf(invoice)
            except Exception as e:
                logger.warning("Could not generate PDF for invoice email: %s", e)

        attachments = []
        if pdf_bytes:
            attachments.append((f"{invoice.invoice_number}.pdf", pdf_bytes, "application/pdf"))

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; color: #334155;">
            <div style="background-color: #0f172a; padding: 20px; color: #ffffff; text-align: center;">
                <h2 style="margin: 0; font-size: 18px;">Payment Received & Invoice Generated</h2>
            </div>
            <div style="padding: 22px; background-color: #ffffff;">
                <p style="font-size: 14px;">Dear {company.name} Finance Team,</p>
                <p style="font-size: 14px; line-height: 1.5;">
                    Thank you for your business. We have successfully processed your payment and updated your company credit balance.
                </p>

                <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 14px; border-radius: 6px; margin: 18px 0;">
                    <p style="margin: 0 0 6px; font-size: 13px;"><b>Invoice Number:</b> {invoice.invoice_number}</p>
                    <p style="margin: 0 0 6px; font-size: 13px;"><b>Billing Period:</b> {invoice.billing_period_start} to {invoice.billing_period_end}</p>
                    <p style="margin: 0 0 6px; font-size: 13px;"><b>Subtotal:</b> {invoice.currency} {invoice.subtotal}</p>
                    <p style="margin: 0 0 6px; font-size: 13px;"><b>18% GST (SAC 998313):</b> {invoice.currency} {invoice.tax}</p>
                    <p style="margin: 0; font-size: 15px; color: #0f172a;"><b>Total Paid:</b> {invoice.currency} {invoice.total_amount}</p>
                </div>

                <p style="font-size: 13px; color: #64748b;">
                    Your official GST tax invoice is attached as a PDF to this email. You can also view or download it anytime from your Billing Dashboard.
                </p>
            </div>
        </div>
        """
        return cls._send_html_email(subject, recipients, html_content, attachments)
