import logging
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.notifications.models import DeviceToken, PushDeliveryLog

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Prunes inactive browser push device tokens and historical PushDeliveryLog records older than a threshold (default: 90 days)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Age threshold in days for pruning inactive tokens and historical logs (default: 90).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the cleanup without modifying or deleting database records.",
        )

    def handle(self, *args, **options):
        days = options.get("days", 90)
        dry_run = options.get("dry_run", False)

        cutoff = timezone.now() - timedelta(days=days)
        self.stdout.write(
            self.style.NOTICE(
                f"Starting Push Token & Telemetry Pruning (Cutoff: {cutoff.strftime('%Y-%m-%d %H:%M:%S')}, Threshold: {days} days)..."
            )
        )

        # 1. Inactive Device Tokens older than cutoff
        inactive_tokens_qs = DeviceToken.objects.filter(
            is_active=False,
            last_seen_at__lt=cutoff,
        )
        inactive_tokens_count = inactive_tokens_qs.count()

        # 2. Historical PushDeliveryLogs older than cutoff
        old_logs_qs = PushDeliveryLog.objects.filter(
            created_at__lt=cutoff,
        )
        old_logs_count = old_logs_qs.count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY-RUN] Would prune {inactive_tokens_count} inactive DeviceToken records."
                )
            )
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY-RUN] Would prune {old_logs_count} PushDeliveryLog records."
                )
            )
            self.stdout.write(self.style.SUCCESS("[DRY-RUN] Pruning simulation completed. Database unchanged."))
            return

        # Perform actual deletion
        deleted_tokens, _ = inactive_tokens_qs.delete()
        deleted_logs, _ = old_logs_qs.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"[SUCCESS] Pruned {deleted_tokens} stale inactive DeviceToken records."
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"[SUCCESS] Pruned {deleted_logs} historical PushDeliveryLog records."
            )
        )
