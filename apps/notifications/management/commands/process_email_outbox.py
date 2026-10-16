import logging
from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.utils import timezone
from apps.notifications.models import NotificationLog, EmailDeliveryAttempt
from apps.notifications.services.smtp_service import (
    get_active_email_connection,
    get_default_from_email,
)
from apps.notifications.services.email_engine import ensure_absolute_url
from apps.notifications.services.unsubscribe_service import generate_unsubscribe_token

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Processes queued email outbox records, pooling SMTP connections and "
        "enforcing exponential backoff retries (RFC 8058 compliant)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Maximum number of queued emails to process in a single batch (default: 50)",
        )
        parser.add_argument(
            "--max-retries",
            type=int,
            default=3,
            help="Maximum delivery attempts before permanent failure (default: 3)",
        )
        parser.add_argument(
            "--include-failed",
            action="store_true",
            help="Also process FAILED emails that have not yet exceeded max retries",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate outbox processing without opening SMTP connection or sending emails",
        )
        parser.add_argument(
            "--recover-stale",
            action="store_true",
            default=True,
            help="Automatically recover logs stuck in SENDING status for > 10 minutes (default: True)",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        max_retries = options["max_retries"]
        include_failed = options["include_failed"]
        dry_run = options["dry_run"]
        recover_stale = options["recover_stale"]

        now = timezone.now()

        # Self-healing: Recover logs stranded in SENDING state due to process restarts or timeouts
        if recover_stale:
            stale_threshold = now - timezone.timedelta(minutes=10)
            stale_sending_logs = NotificationLog.objects.filter(
                status=NotificationLog.Status.SENDING,
                updated_at__lte=stale_threshold,
            )
            stale_count = stale_sending_logs.count()
            if stale_count > 0:
                self.stdout.write(
                    self.style.WARNING(
                        f"Found {stale_count} stalled 'SENDING' logs older than 10 mins. Recovering to QUEUED..."
                    )
                )
                if not dry_run:
                    for s_log in stale_sending_logs:
                        if s_log.attempts_count < max_retries:
                            s_log.status = NotificationLog.Status.QUEUED
                            s_log.last_error = f"Recovered from stalled SENDING state at {now.strftime('%Y-%m-%d %H:%M:%S')}"
                            s_log.save(update_fields=["status", "last_error"])
                        else:
                            s_log.status = NotificationLog.Status.FAILED
                            s_log.last_error = "Exhausted retries; worker stalled during transmission."
                            s_log.save(update_fields=["status", "last_error"])

        status_filter = [NotificationLog.Status.QUEUED]
        if include_failed:
            status_filter.append(NotificationLog.Status.FAILED)

        # Query eligible queued logs respecting exponential backoff retry_after
        eligible_logs = (
            NotificationLog.objects.filter(
                status__in=status_filter,
                attempts_count__lt=max_retries,
            )
            .filter(Q(retry_after__isnull=True) | Q(retry_after__lte=now))
            .select_related("template", "company", "user")
            .order_by("created_at")[:batch_size]
        )

        total_queued = eligible_logs.count()
        if total_queued == 0:
            self.stdout.write(self.style.SUCCESS("No pending emails eligible for dispatch in outbox."))
            return

        self.stdout.write(f"Found {total_queued} outbox emails eligible for processing.")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: No actual emails will be dispatched."))
            for log in eligible_logs:
                self.stdout.write(f"  [DRY RUN] Would send #{log.id} ({log.event_key}) to {log.recipient_email}")
            return

        connection, active_config = get_active_email_connection()
        from_email = get_default_from_email()

        sent_count = 0
        failed_count = 0

        # Process each eligible log
        for log in eligible_logs:
            attempt_number = log.attempts_count + 1
            log.status = NotificationLog.Status.SENDING
            log.save(update_fields=["status"])

            try:
                msg = EmailMultiAlternatives(
                    subject=log.subject,
                    body=log.text_body,
                    from_email=from_email,
                    to=[log.recipient_email],
                    connection=connection,
                )
                msg.attach_alternative(log.html_body, "text/html")

                # RFC 8058 Deliverability Headers
                cat_key = log.template.category if log.template else "SYSTEM"
                unsub_token = generate_unsubscribe_token(log.user_id, cat_key) if log.user_id else ""
                unsub_url = (
                    ensure_absolute_url(f"/notifications/unsubscribe/?token={unsub_token}")
                    if unsub_token
                    else ensure_absolute_url("/company/notification-preferences/")
                )

                msg.extra_headers["List-Unsubscribe"] = f"<{unsub_url}>"
                msg.extra_headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
                msg.extra_headers["X-Entity-Ref-ID"] = f"TC-NOTIF-{log.id}"

                result = msg.send(fail_silently=False)
                if result > 0:
                    log.status = NotificationLog.Status.SENT
                    log.sent_at = timezone.now()
                    log.attempts_count = attempt_number
                    log.last_error = ""
                    log.retry_after = None
                    log.save(update_fields=["status", "sent_at", "attempts_count", "last_error", "retry_after"])

                    EmailDeliveryAttempt.objects.create(
                        log=log,
                        attempt_number=attempt_number,
                        status=EmailDeliveryAttempt.Status.SUCCESS,
                    )
                    sent_count += 1
                    self.stdout.write(self.style.SUCCESS(f"  [OK] Dispatched #{log.id} to {log.recipient_email}"))
                else:
                    raise RuntimeError("SMTP backend delivered 0 messages.")

            except Exception as exc:
                err_msg = str(exc)
                failed_count += 1
                log.attempts_count = attempt_number
                log.last_error = err_msg

                if attempt_number >= max_retries:
                    log.status = NotificationLog.Status.FAILED
                    log.retry_after = None
                else:
                    log.status = NotificationLog.Status.QUEUED
                    # Exponential backoff (2, 4, 8 minutes)
                    log.retry_after = timezone.now() + timezone.timedelta(minutes=2 ** attempt_number)

                log.save(update_fields=["status", "attempts_count", "last_error", "retry_after"])

                EmailDeliveryAttempt.objects.create(
                    log=log,
                    attempt_number=attempt_number,
                    status=EmailDeliveryAttempt.Status.FAILED,
                    error_message=err_msg,
                )
                self.stdout.write(
                    self.style.ERROR(f"  [FAILED] Failed #{log.id} to {log.recipient_email} (attempt {attempt_number}): {err_msg}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nOutbox processing complete. Total processed: {total_queued} (Sent: {sent_count}, Failed: {failed_count})"
            )
        )
