import logging
from django.urls import reverse
from apps.dashboard.models import Notification
from apps.companies.models import CompanyMember

from django.utils import timezone
from django.db.models import Q
from apps.notifications.services.notification_engine import notify_event

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
    event_key="",
    priority=None,
):
    """
    Creates real-time notifications for the given user, or for all active members of the company.
    Includes duplicate suppression within 15-minute window for identical user alerts.
    Automatically dispatches Firebase Web Push notification to recipients with active devices.
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
    now = timezone.now()
    cutoff = now - timezone.timedelta(minutes=15)

    for recipient in recipients:
        try:
            # Check for existing duplicate notification within 15 mins
            dup_q = Q(user=recipient) & Q(created_at__gte=cutoff)
            if title:
                dup_q &= Q(title=title)
            if link:
                dup_q &= Q(link=link)
            if event_key:
                dup_q &= (Q(event_key=event_key) | Q(event_key=""))

            existing = Notification.objects.filter(dup_q).first()
            if existing:
                created_notifications.append(existing)
                continue

            notif = Notification.objects.create(
                user=recipient,
                company=company,
                notification_type=notification_type,
                title=title,
                message=message,
                link=link,
                icon=icon,
                color=color,
                event_key=event_key,
                priority=priority or Notification.Priority.INFO,
            )
            created_notifications.append(notif)

            # Trigger Firebase Web Push dispatch if user has active registered devices
            try:
                from apps.notifications.models import DeviceToken, PushDeliveryLog
                from apps.notifications.services.fcm_service import send_user_push

                active_devices = list(DeviceToken.objects.filter(user=recipient, is_active=True))
                if active_devices:
                    push_res = send_user_push(
                        user_or_users=recipient,
                        title=title,
                        body=message[:500] if message else title,
                        link=link,
                        priority=priority or Notification.Priority.INFO,
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

                        user_company = company
                        if not user_company and hasattr(recipient, "company_membership") and recipient.company_membership:
                            user_company = recipient.company_membership.company

                        PushDeliveryLog.objects.create(
                            notification=notif,
                            device=device,
                            user=recipient,
                            company=user_company,
                            event_key=event_key or "system.notification",
                            status=dev_status,
                            attempt_count=1,
                            error_code=dev_err_code,
                            error_message=dev_err_msg,
                            sent_at=timezone.now() if dev_status == PushDeliveryLog.Status.SUCCESS else None,
                        )
            except Exception as push_err:
                logger.warning(f"Failed to dispatch FCM push for notification {notif.id}: {push_err}")

        except Exception as exc:
            logger.warning(f"Failed to create notification for {recipient}: {exc}")

    return created_notifications


def notify_proposal_sent(inquiry, user=None):
    """Triggered when a Seller sends a Proposal or a Buyer dispatches an RFQ."""
    company = inquiry.company
    is_seller = getattr(company, "is_vendor_type", False) or getattr(company, "company_type", "").lower() in ["seller", "vendor"] or (
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

    event_key = "seller.inquiry_reply" if is_seller else "buyer.rfq_dispatched"
    notifs = create_notification(
        user=user,
        company=company,
        notification_type=Notification.NotificationType.PROPOSAL_SENT,
        title=title,
        message=msg,
        link=link,
        icon=icon,
        color=color,
        notify_company_members=True,
        event_key=event_key,
    )

    try:
        from apps.notifications.services.email_engine import trigger_email_notification
        trigger_email_notification(
            event_key=event_key,
            company=company,
            user=user,
            context={
                "inquiry_id": str(inquiry.id),
                "supplier_name": target_company,
                "product_name": product_title,
                "buyer_company": company.name if is_seller else target_company,
                "quantity": str(getattr(inquiry, "quantity", "1")),
                "cta_url": link,
                "details": {
                    "Inquiry #": str(inquiry.id),
                    "Product / Requirement": product_title,
                    "Recipient": f"{target_company} ({inquiry.sent_to_email})",
                },
            },
            idempotency_key=f"inq_prop_{inquiry.id}_{inquiry.updated_at.timestamp() if hasattr(inquiry, 'updated_at') else 1}",
        )
    except Exception as exc:
        logger.warning(f"Failed to trigger email notification for proposal: {exc}")

    return notifs


def notify_quote_recorded(inquiry, user=None):
    """Triggered when a quote is recorded for an inquiry."""
    company = inquiry.company
    is_seller = getattr(company, "is_vendor_type", False) or getattr(company, "company_type", "").lower() in ["seller", "vendor"]
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

    event_key = "buyer.quote_received"
    notifs = create_notification(
        user=user,
        company=company,
        notification_type=Notification.NotificationType.QUOTE_RECEIVED,
        title=title,
        message=msg,
        link=link,
        icon="fa-calculator",
        color="emerald",
        notify_company_members=True,
        event_key=event_key,
    )

    try:
        from apps.notifications.services.email_engine import trigger_email_notification
        trigger_email_notification(
            event_key="buyer.quote_received",
            company=company,
            user=user,
            context={
                "inquiry_id": str(inquiry.id),
                "supplier_name": target_company,
                "product_name": product_title,
                "quoted_price": price_str,
                "moq": str(getattr(inquiry, "minimum_order_quantity", "1")),
                "cta_url": link,
                "details": {
                    "Supplier": target_company,
                    "Item": product_title,
                    "Commercial Quote": price_str,
                },
            },
            idempotency_key=f"inq_quote_{inquiry.id}_{inquiry.updated_at.timestamp() if hasattr(inquiry, 'updated_at') else 1}",
        )
    except Exception as exc:
        logger.warning(f"Failed to trigger quote email: {exc}")

    try:
        from apps.notifications.services.notification_engine import notify_event
        notify_event(
            event="quotation.status_changed",
            company=company,
            data={"status_display": "Recorded", "price": price_str},
            dedupe_key=f"quote_rec_{inquiry.id}_{price_str}",
        )
    except Exception:
        pass

    return notifs


def notify_status_changed(inquiry, old_status_display, user=None):
    """Triggered when inquiry status transitions (e.g. Won, Lost, In Discussion)."""
    company = inquiry.company
    is_seller = getattr(company, "is_vendor_type", False) or getattr(company, "company_type", "").lower() in ["seller", "vendor"]
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

    event_key = "seller.deal_status_changed" if is_seller else "buyer.deal_status_changed"
    notifs = create_notification(
        user=user,
        company=company,
        notification_type=notif_type,
        title=title,
        message=msg,
        link=link,
        icon=icon,
        color=color,
        notify_company_members=True,
        event_key=event_key,
    )

    try:
        from apps.notifications.services.email_engine import trigger_email_notification
        trigger_email_notification(
            event_key=event_key,
            company=company,
            user=user,
            context={
                "inquiry_id": str(inquiry.id),
                "counterparty_name": target_company,
                "status_label": inquiry.get_status_display(),
                "remarks": msg,
                "cta_url": link,
                "details": {
                    "Inquiry #": str(inquiry.id),
                    "Counterparty": target_company,
                    "Current Stage": inquiry.get_status_display(),
                },
            },
            idempotency_key=f"inq_status_{inquiry.id}_{inquiry.status}",
        )
    except Exception as exc:
        logger.warning(f"Failed to trigger status change email: {exc}")

    try:
        from apps.notifications.services.notification_engine import notify_event
        notify_event(
            event="quotation.status_changed",
            company=company,
            data={"status_display": inquiry.get_status_display()},
            dedupe_key=f"inq_status_chg_{inquiry.id}_{inquiry.status}",
        )
    except Exception:
        pass

    return notifs


def notify_message_logged(inquiry, inquiry_msg, user=None):
    """Triggered when a follow-up or reply message is added to an inquiry thread."""
    company = inquiry.company
    is_seller = getattr(company, "is_vendor_type", False) or getattr(company, "company_type", "").lower() in ["seller", "vendor"]
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

    try:
        from apps.notifications.services.notification_engine import notify_event
        msg_id_str = str(inquiry_msg.id) if hasattr(inquiry_msg, 'id') and inquiry_msg.id else str(inquiry.id)
        notify_event(
            event="inquiry.message_received",
            company=company,
            data={"inquiry_id": str(inquiry.id), "sender_name": sender},
            dedupe_key=f"inq_msg_{msg_id_str}",
        )
    except Exception:
        pass

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
    """
    Triggered when a support ticket is created or updated.
    Notifies the ticket owner, active Super Admins, and triggers branded email notifications.
    """
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

    # 1. In-app notification to ticket owner
    notifs = create_notification(
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

    # 2. In-app notification to all active Super Admins when a new ticket is filed
    if event_type == "created":
        try:
            from apps.accounts.models import User
            from django.db.models import Q
            super_admins = User.objects.filter(Q(is_superuser=True) | Q(is_staff=True), is_active=True)
            for sa in super_admins:
                if sa != (user or ticket.user):
                    create_notification(
                        user=sa,
                        company=None,
                        notification_type=Notification.NotificationType.TICKET_UPDATE,
                        title=f"Support Ticket #{ticket.ticket_number} Received",
                        message=f"[{ticket.company.name if ticket.company else 'Customer'}] {ticket.subject} ({ticket.get_priority_display()} priority)",
                        link=reverse("admin-panel-support"),
                        icon="fa-headset",
                        color="rose" if ticket.priority == "URGENT" else "blue",
                    )
        except Exception as exc:
            logger.warning(f"Failed to create superadmin notifications for ticket: {exc}")

    # 3. Automated Branded Email Alerts
    try:
        from apps.notifications.services.email_engine import trigger_email_notification
        if event_type == "created":
            # Alert Super Admins / Platform
            trigger_email_notification(
                event_key="admin.ticket_filed",
                company=company,
                user=user or ticket.user,
                context={
                    "ticket_number": ticket.ticket_number,
                    "ticket_subject": ticket.subject,
                    "priority": ticket.get_priority_display(),
                    "category": ticket.get_category_display(),
                    "company_name": ticket.company.name if ticket.company else "Direct Account",
                    "contact_email": ticket.contact_email,
                    "contact_phone": ticket.contact_phone or "N/A",
                    "description": ticket.description,
                    "cta_url": reverse("admin-panel-support"),
                    "cta_text": "Open Support Desk",
                    "summary": f"Inbound support ticket #{ticket.ticket_number} logged under {ticket.get_category_display()}.",
                    "details": {
                        "Ticket #": ticket.ticket_number,
                        "Company": ticket.company.name if ticket.company else "Direct Account",
                        "Category": ticket.get_category_display(),
                        "Priority": ticket.get_priority_display(),
                        "Contact Email": ticket.contact_email,
                        "Subject": ticket.subject,
                    },
                },
                idempotency_key=f"tkt_create_admin_{ticket.id}",
            )
            # Confirmation to Ticket Creator
            trigger_email_notification(
                event_key="company.ticket_created",
                company=company,
                user=ticket.user,
                context={
                    "ticket_number": ticket.ticket_number,
                    "ticket_subject": ticket.subject,
                    "priority": ticket.get_priority_display(),
                    "category": ticket.get_category_display(),
                    "status": ticket.get_status_display(),
                    "contact_email": ticket.contact_email,
                    "cta_url": reverse("help-support"),
                    "cta_text": "View Support Ticket",
                    "summary": f"Your support ticket #{ticket.ticket_number} has been received and logged.",
                    "details": {
                        "Ticket #": ticket.ticket_number,
                        "Subject": ticket.subject,
                        "Category": ticket.get_category_display(),
                        "Priority": ticket.get_priority_display(),
                        "Status": ticket.get_status_display(),
                    },
                },
                idempotency_key=f"tkt_create_user_{ticket.id}",
            )
        else:
            # Ticket Status / Resolution Update to Creator
            event_key = "company.ticket_resolved" if event_type == "resolved" else "company.ticket_updated"
            trigger_email_notification(
                event_key=event_key,
                company=company,
                user=ticket.user,
                context={
                    "ticket_number": ticket.ticket_number,
                    "ticket_subject": ticket.subject,
                    "status": ticket.get_status_display(),
                    "resolution": ticket.resolution or "Updated by platform operations team.",
                    "cta_url": reverse("help-support"),
                    "cta_text": "View Support Ticket",
                    "summary": f"Your support ticket #{ticket.ticket_number} status has been updated to {ticket.get_status_display()}.",
                    "details": {
                        "Ticket #": ticket.ticket_number,
                        "Subject": ticket.subject,
                        "Current Status": ticket.get_status_display(),
                        "Resolution Notes": ticket.resolution or "In progress",
                    },
                },
                idempotency_key=f"tkt_status_{ticket.id}_{ticket.status}",
            )
    except Exception as exc:
        logger.warning(f"Failed to dispatch email notification for support ticket: {exc}")

    return notifs
