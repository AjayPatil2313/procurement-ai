import logging
from django.urls import reverse
from apps.dashboard.models import Notification
from apps.companies.models import CompanyMember

logger = logging.getLogger(__name__)


def create_notification(
    user=None,
    company=None,
    notification_type="system",
    title="",
    message="",
    link="",
    icon="fa-bell",
    color="blue",
    notify_company_members=False,
):
    """
    Creates real-time notifications for the given user, or for all active members of the company.
    """
    recipients = set()
    if user:
        recipients.add(user)

    if notify_company_members and company:
        active_members = CompanyMember.objects.filter(
            company=company, is_active=True
        ).select_related("user")
        for m in active_members:
            if m.user:
                recipients.add(m.user)

    created_notifications = []
    for recipient in recipients:
        try:
            notif = Notification.objects.create(
                user=recipient,
                company=company,
                notification_type=notification_type,
                title=title,
                message=message,
                link=link,
                icon=icon,
                color=color,
            )
            created_notifications.append(notif)
        except Exception as exc:
            logger.warning(f"Failed to create notification for {recipient}: {exc}")

    return created_notifications


def notify_proposal_sent(inquiry, user=None):
    """Triggered when a Seller sends a Proposal or a Buyer dispatches an RFQ."""
    company = inquiry.company
    is_seller = getattr(company, "company_type", "") == "seller" or (
        inquiry.search_result and inquiry.search_result.result_type == "lead"
    )
    product_title = inquiry.search_result.product_title if inquiry.search_result else "Commercial Requirement"
    target_company = (
        inquiry.search_result.external_company.name
        if inquiry.search_result and inquiry.search_result.external_company
        else inquiry.sent_to_email
    )

    if is_seller:
        title = f"Commercial Proposal Dispatched: {product_title}"
        msg = f"Proposal sent to {target_company} ({inquiry.sent_to_email}) with quoted terms."
        link = reverse("sales-inquiry-detail", kwargs={"pk": inquiry.id})
        icon = "fa-paper-plane"
        color = "orange"
    else:
        title = f"Official RFQ Dispatched: {product_title}"
        msg = f"RFQ dispatched to supplier {target_company} ({inquiry.sent_to_email})."
        link = reverse("inquiry-detail", kwargs={"pk": inquiry.id})
        icon = "fa-paper-plane"
        color = "blue"

    return create_notification(
        user=user,
        company=company,
        notification_type=Notification.NotificationType.PROPOSAL_SENT,
        title=title,
        message=msg,
        link=link,
        icon=icon,
        color=color,
        notify_company_members=True,
    )


def notify_quote_recorded(inquiry, user=None):
    """Triggered when a quote is recorded for an inquiry."""
    company = inquiry.company
    is_seller = getattr(company, "company_type", "") == "seller"
    product_title = inquiry.search_result.product_title if inquiry.search_result else "Item"
    target_company = (
        inquiry.search_result.external_company.name
        if inquiry.search_result and inquiry.search_result.external_company
        else "Vendor"
    )

    currency = inquiry.quoted_currency or "INR"
    price_str = f"{currency} {inquiry.quoted_price:,.2f}" if inquiry.quoted_price else "Recorded"

    title = f"Commercial Quotation Logged: {price_str}"
    msg = f"Quote terms updated for {product_title} from {target_company}."
    link_name = "sales-inquiry-detail" if is_seller else "inquiry-detail"
    link = reverse(link_name, kwargs={"pk": inquiry.id})

    return create_notification(
        user=user,
        company=company,
        notification_type=Notification.NotificationType.QUOTE_RECEIVED,
        title=title,
        message=msg,
        link=link,
        icon="fa-calculator",
        color="emerald",
        notify_company_members=True,
    )


def notify_status_changed(inquiry, old_status_display, user=None):
    """Triggered when inquiry status transitions (e.g. Won, Lost, In Discussion)."""
    company = inquiry.company
    is_seller = getattr(company, "company_type", "") == "seller"
    target_company = (
        inquiry.search_result.external_company.name
        if inquiry.search_result and inquiry.search_result.external_company
        else "Counterparty"
    )

    link_name = "sales-inquiry-detail" if is_seller else "inquiry-detail"
    link = reverse(link_name, kwargs={"pk": inquiry.id})

    if inquiry.status == "won":
        title = f"Deal Won! 🏆 ({target_company})"
        msg = f"Inquiry #{inquiry.id} was successfully accepted and marked as WON!"
        notif_type = Notification.NotificationType.DEAL_WON
        icon = "fa-trophy"
        color = "emerald"
    elif inquiry.status == "lost":
        title = f"Deal Closed / Lost ({target_company})"
        msg = f"Inquiry #{inquiry.id} transitioned to LOST / Closed."
        notif_type = Notification.NotificationType.DEAL_LOST
        icon = "fa-circle-xmark"
        color = "rose"
    else:
        title = f"Status Updated: {inquiry.get_status_display()}"
        msg = f"Inquiry #{inquiry.id} changed from '{old_status_display}' to '{inquiry.get_status_display()}'."
        notif_type = Notification.NotificationType.STATUS_CHANGED
        icon = "fa-arrows-rotate"
        color = "blue"

    return create_notification(
        user=user,
        company=company,
        notification_type=notif_type,
        title=title,
        message=msg,
        link=link,
        icon=icon,
        color=color,
        notify_company_members=True,
    )


def notify_message_logged(inquiry, inquiry_msg, user=None):
    """Triggered when a follow-up or reply message is added to an inquiry thread."""
    company = inquiry.company
    is_seller = getattr(company, "company_type", "") == "seller"
    target_company = (
        inquiry.search_result.external_company.name
        if inquiry.search_result and inquiry.search_result.external_company
        else "Contact"
    )

    link_name = "sales-inquiry-detail" if is_seller else "inquiry-detail"
    link = reverse(link_name, kwargs={"pk": inquiry.id})

    sender = inquiry_msg.sender_name or (user.get_full_name() if user else "Team Member")
    body_snippet = (inquiry_msg.body[:90] + "...") if len(inquiry_msg.body) > 90 else inquiry_msg.body

    title = f"New Message: {inquiry.subject[:40]}"
    msg = f"{sender}: {body_snippet}"

    return create_notification(
        user=user,
        company=company,
        notification_type=Notification.NotificationType.INQUIRY_REPLY,
        title=title,
        message=msg,
        link=link,
        icon="fa-comments",
        color="purple",
        notify_company_members=True,
    )


def notify_support_ticket(ticket, event_type="created", user=None):
    """Triggered when a support ticket is created or updated."""
    company = ticket.company
    link = reverse("help-ticket-detail", kwargs={"pk": ticket.id})

    if event_type == "created":
        title = f"Ticket Filed: {ticket.ticket_number}"
        msg = f"Support ticket '{ticket.subject}' has been submitted ({ticket.get_priority_display()} priority)."
        icon = "fa-ticket"
        color = "blue"
    elif event_type == "resolved":
        title = f"Ticket Resolved: {ticket.ticket_number}"
        msg = f"Your support ticket '{ticket.subject}' has been marked as Resolved."
        icon = "fa-circle-check"
        color = "emerald"
    else:
        title = f"Ticket Update: {ticket.ticket_number}"
        msg = f"Status updated to {ticket.get_status_display()} for '{ticket.subject}'."
        icon = "fa-headset"
        color = "purple"

    return create_notification(
        user=user or ticket.user,
        company=company,
        notification_type=Notification.NotificationType.TICKET_UPDATE,
        title=title,
        message=msg,
        link=link,
        icon=icon,
        color=color,
        notify_company_members=False,
    )
