import logging
from django.core.management.base import BaseCommand
from apps.billing.services.wallet import CreditWalletService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Monitors company subscription expirations, transitions past_due/expired status, and dispatches renewal notifications."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Executing subscription lifecycle check..."))
        summary = CreditWalletService.process_subscription_lifecycle()
        self.stdout.write(
            self.style.SUCCESS(
                f"Subscription processing complete: "
                f"{summary['expired']} expired, "
                f"{summary['past_due']} past due, "
                f"{summary['advance_notified']} advance alerts dispatched."
            )
        )
