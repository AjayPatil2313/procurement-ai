import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.billing.models import (
    SubscriptionPlan,
    Subscription,
    CompanyCreditWallet,
    FeatureCreditCost,
    CreditTransaction,
    UsageRecord,
    CompanyWallet,
    WalletTransaction,
    ServicePricing,
    Payment,
    Invoice,
)
from apps.dashboard.models import Notification
from apps.dashboard.services.notification_service import create_notification
from django.core.cache import cache

logger = logging.getLogger(__name__)


class CreditWalletService:
    """
    Central service for company credit wallet operations, atomic reservations,
    deductions, refunds, rate-card queries, and subscription reloads.
    """

    @classmethod
    def ensure_default_plans_and_costs(cls):
        """
        Seeds standard default plans and feature credit costs if none exist.
        """
        # 1. Default Plans
        if not SubscriptionPlan.objects.exists():
            plans = [
                {
                    "code": "free",
                    "name": "Free Starter",
                    "description": "Essential procurement search tools with 50 monthly search credits.",
                    "billing_cycle": SubscriptionPlan.BillingCycle.MONTHLY,
                    "price": Decimal("0.00"),
                    "currency": "INR",
                    "included_credits": 50,
                    "max_team_members": 2,
                    "max_products": 10,
                    "max_saved_vendors": 25,
                    "search_limit_monthly": 50,
                    "website_extraction_limit": 50,
                    "ai_matching_limit": 50,
                    "feature_access": {"global_suppliers": False, "export_pdf": False, "api_access": False},
                    "order": 1,
                    "is_public": True,
                },
                {
                    "code": "pro",
                    "name": "Pro Growth",
                    "description": "Designed for active procurement teams with 500 AI search credits, global exporters and exports.",
                    "billing_cycle": SubscriptionPlan.BillingCycle.MONTHLY,
                    "price": Decimal("14999.00"),
                    "currency": "INR",
                    "included_credits": 500,
                    "max_team_members": 10,
                    "max_products": 100,
                    "max_saved_vendors": 500,
                    "search_limit_monthly": 500,
                    "website_extraction_limit": 500,
                    "ai_matching_limit": 500,
                    "feature_access": {"global_suppliers": True, "export_pdf": True, "cheaper_options": True, "api_access": False},
                    "order": 2,
                    "is_public": True,
                },
                {
                    "code": "enterprise",
                    "name": "Enterprise Suite",
                    "description": "High-volume AI procurement engine with 2,500 credits, custom crawler integrations, and priority SLA.",
                    "billing_cycle": SubscriptionPlan.BillingCycle.MONTHLY,
                    "price": Decimal("49999.00"),
                    "currency": "INR",
                    "included_credits": 2500,
                    "max_team_members": 50,
                    "max_products": 1000,
                    "max_saved_vendors": 5000,
                    "search_limit_monthly": 2500,
                    "website_extraction_limit": 2500,
                    "ai_matching_limit": 2500,
                    "feature_access": {"global_suppliers": True, "export_pdf": True, "cheaper_options": True, "api_access": True, "dedicated_manager": True},
                    "order": 3,
                    "is_public": True,
                },
            ]
            for p in plans:
                SubscriptionPlan.objects.get_or_create(code=p["code"], defaults=p)

        # 2. Default Feature Credit Costs
        if not FeatureCreditCost.objects.exists():
            feature_costs = [
                {
                    "feature_code": "vendor_discovery",
                    "display_name": "AI Vendor Discovery / Buyer Search",
                    "credit_cost": 5,
                    "charging_unit": FeatureCreditCost.ChargingUnit.PER_JOB,
                    "description": "Executes multi-source supplier crawler and candidate scraping per search job.",
                },
                {
                    "feature_code": "website_extraction",
                    "display_name": "Website & Catalog Data Extraction",
                    "credit_cost": 2,
                    "charging_unit": FeatureCreditCost.ChargingUnit.PER_WEBSITE,
                    "description": "Deep extracts manufacturing facilities, products, and contact data from supplier domain.",
                },
                {
                    "feature_code": "ai_matching",
                    "display_name": "AI Company Parameter Matching",
                    "credit_cost": 1,
                    "charging_unit": FeatureCreditCost.ChargingUnit.PER_COMPANY,
                    "description": "Calculates technical criteria fit, score, and commercial alignment per matched company.",
                },
                {
                    "feature_code": "data_enrichment",
                    "display_name": "Commercial Intelligence Dossier",
                    "credit_cost": 3,
                    "charging_unit": FeatureCreditCost.ChargingUnit.PER_COMPANY,
                    "description": "Enriches company dossier with verified contacts, tax identifiers, and credit risk.",
                },
                {
                    "feature_code": "export_reports",
                    "display_name": "Deep Intelligence Report Export",
                    "credit_cost": 2,
                    "charging_unit": FeatureCreditCost.ChargingUnit.PER_OPERATION,
                    "description": "Compiles executive PDF / Excel audit dossier for offline procurement review.",
                },
            ]
            for fc in feature_costs:
                FeatureCreditCost.objects.get_or_create(feature_code=fc["feature_code"], defaults=fc)

    @classmethod
    def get_or_create_wallet(cls, company):
        """
        Retrieves or initializes the company credit wallet, synchronizing
        from legacy Subscription if necessary.
        """
        if not company:
            return None

        wallet, created = CompanyCreditWallet.objects.get_or_create(company=company)
        if created:
            # Sync starting credits from legacy subscription if present
            sub = getattr(company, "subscription", None)
            if sub and sub.credits_total > 0:
                wallet.subscription_credits = max(0, sub.credits_remaining)
                wallet.credits_consumed = sub.credits_used
                wallet.save(update_fields=["subscription_credits", "credits_consumed", "updated_at"])
            else:
                wallet.subscription_credits = 50  # Default starter quota
                wallet.save(update_fields=["subscription_credits", "updated_at"])
        return wallet

    @classmethod
    def get_feature_cost(cls, feature_code):
        """
        Looks up the active credit cost for an AI feature code with 5-minute caching.
        """
        cache_key = f"feat_cost:{feature_code}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        cost = 1
        try:
            rule = FeatureCreditCost.objects.filter(feature_code=feature_code, is_active=True).first()
            if rule:
                cost = rule.credit_cost
        except Exception:
            pass

        cache.set(cache_key, cost, timeout=300)
        return cost

    @classmethod
    def check_balance(cls, company, feature_code, quantity=1):
        """
        Checks whether the company's wallet has enough available credits.
        Returns (has_sufficient: bool, total_available: int, required_credits: int).
        """
        wallet = cls.get_or_create_wallet(company)
        if not wallet:
            return False, 0, 0

        unit_cost = cls.get_feature_cost(feature_code)
        required_credits = unit_cost * max(1, quantity)
        available = wallet.total_available
        return (available >= required_credits), available, required_credits

    @classmethod
    def reserve_credits(cls, company, user, feature_code, quantity=1, amount=None, search_job=None, idempotency_key=None):
        """
        Atomically reserves credits for an operation before running the AI job.
        Prevents race conditions and double-deductions.
        Returns (success: bool, message: str, usage_record: UsageRecord or None).
        """
        if amount is not None:
            quantity = amount
        cls.ensure_default_plans_and_costs()

        if idempotency_key:
            existing = UsageRecord.objects.filter(idempotency_key=idempotency_key).first()
            if existing:
                if existing.status == UsageRecord.ProcessingStatus.COMPLETED:
                    return True, "Operation already completed (idempotent)", existing
                elif existing.status == UsageRecord.ProcessingStatus.RESERVED:
                    return True, "Operation already reserved", existing

        unit_cost = cls.get_feature_cost(feature_code)
        needed_credits = unit_cost * max(1, quantity)

        with transaction.atomic():
            wallet = CompanyCreditWallet.objects.select_for_update().filter(company=company).first()
            if not wallet:
                wallet = cls.get_or_create_wallet(company)
                wallet = CompanyCreditWallet.objects.select_for_update().filter(company=company).first()

            if wallet.total_available < needed_credits:
                # Trigger low credit alert if balance is critically exhausted
                cls._trigger_low_credit_notification(company, wallet)
                return False, f"Insufficient AI credits. Required: {needed_credits}, Available: {wallet.total_available}.", None

            # Reserve credits
            wallet.reserved_credits += needed_credits
            wallet.save(update_fields=["reserved_credits", "updated_at"])

            usage_record = UsageRecord.objects.create(
                company=company,
                user=user,
                feature_code=feature_code,
                search_job=search_job,
                usage_quantity=quantity,
                credits_charged=needed_credits,
                status=UsageRecord.ProcessingStatus.RESERVED,
                idempotency_key=idempotency_key or "",
                started_at=timezone.now(),
            )

        return True, "Credits reserved successfully.", usage_record

    @classmethod
    def commit_usage(cls, usage_record, actual_quantity=None):
        """
        Commits a reserved operation on completion:
        1. Releases reservation
        2. Deducts credits from subscription, then purchased, then promotional buckets
        3. Increments credits_consumed
        4. Logs CreditTransaction ledger row
        5. Synchronizes legacy subscription model
        """
        if not usage_record:
            return None

        with transaction.atomic():
            wallet = CompanyCreditWallet.objects.select_for_update().filter(company=usage_record.company).first()
            if not wallet:
                wallet = cls.get_or_create_wallet(usage_record.company)

            # Determine final credits to charge
            credits_to_charge = usage_record.credits_charged
            if actual_quantity is not None:
                unit_cost = cls.get_feature_cost(usage_record.feature_code)
                credits_to_charge = unit_cost * max(1, actual_quantity)

            # Release reserved credits
            wallet.reserved_credits = max(0, wallet.reserved_credits - usage_record.credits_charged)

            # Deduct from buckets
            remaining_to_deduct = credits_to_charge

            if wallet.subscription_credits >= remaining_to_deduct:
                wallet.subscription_credits -= remaining_to_deduct
                remaining_to_deduct = 0
            else:
                remaining_to_deduct -= wallet.subscription_credits
                wallet.subscription_credits = 0

            if remaining_to_deduct > 0:
                if wallet.purchased_credits >= remaining_to_deduct:
                    wallet.purchased_credits -= remaining_to_deduct
                    remaining_to_deduct = 0
                else:
                    remaining_to_deduct -= wallet.purchased_credits
                    wallet.purchased_credits = 0

            if remaining_to_deduct > 0:
                wallet.promotional_credits = max(0, wallet.promotional_credits - remaining_to_deduct)

            wallet.credits_consumed += credits_to_charge
            wallet.save(update_fields=[
                "reserved_credits",
                "subscription_credits",
                "purchased_credits",
                "promotional_credits",
                "credits_consumed",
                "updated_at",
            ])

            # Record Ledger Transaction
            feature_name = usage_record.feature_code.replace("_", " ").title()
            tx = CreditTransaction.objects.create(
                company=usage_record.company,
                user=usage_record.user,
                wallet=wallet,
                transaction_type=CreditTransaction.TransactionType.DEDUCTION,
                credits=-credits_to_charge,
                balance_after=wallet.total_available,
                feature_code=usage_record.feature_code,
                search_job=usage_record.search_job,
                idempotency_key=usage_record.idempotency_key or "",
                status=CreditTransaction.TransactionStatus.COMPLETED,
                notes=f"{feature_name} ({credits_to_charge} credits)",
            )

            # Update Usage Record
            usage_record.status = UsageRecord.ProcessingStatus.COMPLETED
            usage_record.credits_charged = credits_to_charge
            usage_record.completed_at = timezone.now()
            usage_record.save(update_fields=["status", "credits_charged", "completed_at"])

            wallet.sync_legacy_subscription()

            # Check low balance notification
            cls._trigger_low_credit_notification(usage_record.company, wallet)

        return tx

    @classmethod
    def refund_or_release(cls, usage_record, reason="Operation failed"):
        """
        Releases reserved credits on failure or refunds already committed credits.
        """
        if not usage_record:
            return

        with transaction.atomic():
            wallet = CompanyCreditWallet.objects.select_for_update().filter(company=usage_record.company).first()
            if not wallet:
                wallet = cls.get_or_create_wallet(usage_record.company)

            if usage_record.status == UsageRecord.ProcessingStatus.RESERVED:
                # Simply release the hold
                wallet.reserved_credits = max(0, wallet.reserved_credits - usage_record.credits_charged)
                wallet.save(update_fields=["reserved_credits", "updated_at"])

                usage_record.status = UsageRecord.ProcessingStatus.FAILED
                usage_record.failure_reason = reason
                usage_record.completed_at = timezone.now()
                usage_record.save(update_fields=["status", "failure_reason", "completed_at"])

                tx = CreditTransaction.objects.create(
                    company=usage_record.company,
                    user=usage_record.user,
                    wallet=wallet,
                    transaction_type=CreditTransaction.TransactionType.REFUND,
                    credits=0,
                    balance_after=wallet.total_available,
                    feature_code=usage_record.feature_code,
                    search_job=usage_record.search_job,
                    notes=f"Hold Released: {reason[:60]}",
                    status=CreditTransaction.TransactionStatus.COMPLETED,
                )
                wallet.sync_legacy_subscription()
                return tx

            elif usage_record.status == UsageRecord.ProcessingStatus.COMPLETED:
                # Refund back to purchased or subscription bucket
                wallet.subscription_credits += usage_record.credits_charged
                wallet.credits_consumed = max(0, wallet.credits_consumed - usage_record.credits_charged)
                wallet.save(update_fields=["subscription_credits", "credits_consumed", "updated_at"])

                tx = CreditTransaction.objects.create(
                    company=usage_record.company,
                    user=usage_record.user,
                    wallet=wallet,
                    transaction_type=CreditTransaction.TransactionType.REFUND,
                    credits=usage_record.credits_charged,
                    balance_after=wallet.total_available,
                    feature_code=usage_record.feature_code,
                    search_job=usage_record.search_job,
                    notes=f"Refund: {reason[:60]}",
                    status=CreditTransaction.TransactionStatus.COMPLETED,
                )

                usage_record.status = UsageRecord.ProcessingStatus.REFUNDED
                usage_record.failure_reason = reason
                usage_record.save(update_fields=["status", "failure_reason"])
                wallet.sync_legacy_subscription()
                return tx

    @classmethod
    def allocate_plan_credits(cls, company, plan_tier, is_renewal=False):
        """
        Allocates included credits when a company subscribes, upgrades, or renews.
        Preserves purchased credits while refreshing subscription credits.
        """
        cls.ensure_default_plans_and_costs()
        wallet = cls.get_or_create_wallet(company)

        with transaction.atomic():
            wallet = CompanyCreditWallet.objects.select_for_update().filter(company=company).first()
            sub, _ = Subscription.objects.get_or_create(company=company)

            sub.plan_tier = plan_tier
            sub.plan = plan_tier.code
            sub.status = Subscription.Status.ACTIVE
            sub.start_date = timezone.now()
            sub.renewal_date = timezone.now() + timezone.timedelta(days=30 if plan_tier.billing_cycle == "monthly" else 365)
            sub.valid_till = sub.renewal_date.date()
            sub.save()

            old_sub_credits = wallet.subscription_credits
            wallet.subscription_credits = plan_tier.included_credits
            wallet.last_reloaded_at = timezone.now()
            wallet.save(update_fields=["subscription_credits", "last_reloaded_at", "updated_at"])

            CreditTransaction.objects.create(
                company=company,
                wallet=wallet,
                transaction_type=CreditTransaction.TransactionType.ALLOCATION,
                credits=plan_tier.included_credits,
                balance_after=wallet.total_available,
                notes=f"{plan_tier.name} {'Renewal' if is_renewal else 'Activation'} Credits",
                status=CreditTransaction.TransactionStatus.COMPLETED,
            )

            wallet.sync_legacy_subscription()

            # Create notification
            for member in company.members.filter(is_active=True):
                create_notification(
                    user=member.user,
                    company=company,
                    notification_type=Notification.NotificationType.PLAN_UPGRADED,
                    title=f"Plan Activated: {plan_tier.name}",
                    message=f"Your subscription is now active on {plan_tier.name}. {plan_tier.included_credits} credits added.",
                    link="/company/billing/",
                    icon="fa-crown",
                    color="amber",
                )

    @classmethod
    def adjust_credits_admin(cls, company, delta, reason="Super Admin Adjustment", admin_user=None):
        """
        Super Admin manual top-up or deduction.
        """
        wallet = cls.get_or_create_wallet(company)

        with transaction.atomic():
            wallet = CompanyCreditWallet.objects.select_for_update().filter(company=company).first()

            if delta >= 0:
                wallet.purchased_credits += delta
            else:
                to_deduct = abs(delta)
                if wallet.subscription_credits >= to_deduct:
                    wallet.subscription_credits -= to_deduct
                else:
                    to_deduct -= wallet.subscription_credits
                    wallet.subscription_credits = 0
                    wallet.purchased_credits = max(0, wallet.purchased_credits - to_deduct)

            wallet.save(update_fields=["subscription_credits", "purchased_credits", "updated_at"])

            tx = CreditTransaction.objects.create(
                company=company,
                user=admin_user,
                wallet=wallet,
                transaction_type=CreditTransaction.TransactionType.CREDIT if delta >= 0 else CreditTransaction.TransactionType.DEBIT,
                credits=delta,
                balance_after=wallet.total_available,
                notes=reason[:250],
                created_by=admin_user,
                status=CreditTransaction.TransactionStatus.COMPLETED,
            )

            wallet.sync_legacy_subscription()

        return tx

    @classmethod
    def calculate_gst_breakdown(cls, subtotal_amount, tax_rate=Decimal("0.18")):
        """
        Calculates Indian SaaS 18% GST (CGST 9% + SGST 9% or IGST 18%), returning
        (subtotal: Decimal, tax: Decimal, total: Decimal).
        """
        subtotal = Decimal(str(subtotal_amount)).quantize(Decimal("0.01"))
        tax = (subtotal * tax_rate).quantize(Decimal("0.01"))
        total = subtotal + tax
        return subtotal, tax, total

    @classmethod
    def process_subscription_lifecycle(cls, now=None):
        """
        Monitors subscription expiration, sets past_due/expired status, and triggers
        advance renewal notifications to Company Admins (7 days and 1 day prior).
        """
        now = now or timezone.now()
        today = now.date()
        summary = {
            "expired": 0,
            "past_due": 0,
            "advance_notified": 0,
        }

        # 1. Subscriptions due for renewal or past valid_till date
        active_subs = Subscription.objects.filter(
            status__in=[Subscription.Status.ACTIVE, Subscription.Status.TRIAL, Subscription.Status.PAST_DUE],
            valid_till__lte=today,
        )
        for sub in active_subs:
            tier = sub.plan_tier or SubscriptionPlan.objects.filter(code=sub.plan).first()
            if sub.auto_renew and tier and tier.code != "free" and tier.price > Decimal("0.00"):
                # Attempt Auto-Pay via Company Wallet for paid plans
                success, msg, _ = CompanyWalletService.execute_auto_pay_renewal(sub.company, sub)
                if success:
                    summary["auto_pay_renewed"] = summary.get("auto_pay_renewed", 0) + 1
                else:
                    # Auto-Pay cancelled due to insufficient balance
                    summary["auto_pay_cancelled"] = summary.get("auto_pay_cancelled", 0) + 1
                    summary["expired"] += 1
            else:
                sub.status = Subscription.Status.EXPIRED
                sub.save(update_fields=["status", "updated_at"])
                summary["expired"] += 1

                # Broadcast alert to Company Admins
                for member in sub.company.members.filter(is_active=True, role="ADMIN"):
                    create_notification(
                        user=member.user,
                        company=sub.company,
                        notification_type=Notification.NotificationType.SYSTEM,
                        title=f"Subscription {sub.get_status_display()}: Action Required",
                        message=f"Your subscription expired on {sub.valid_till}. Please renew now to maintain uninterrupted AI discovery operations.",
                        link="/company/billing/",
                        icon="fa-clock-rotate-left",
                        color="rose",
                    )

        # 2. Advance renewal alerts: 7 days, 5 days, 3 days, and 1 day prior
        for days_ahead in [7, 5, 3, 1]:
            target_date = today + timezone.timedelta(days=days_ahead)
            expiring_soon = Subscription.objects.filter(
                status=Subscription.Status.ACTIVE,
                valid_till=target_date,
            )
            for sub in expiring_soon:
                recent_alert = Notification.objects.filter(
                    company=sub.company,
                    title__icontains=f"{days_ahead} Day",
                    created_at__gte=now - timezone.timedelta(hours=20),
                ).exists()
                if not recent_alert:
                    summary["advance_notified"] += 1
                    if days_ahead == 5:
                        sub.expiry_warning_5d_sent = True
                        sub.save(update_fields=["expiry_warning_5d_sent", "updated_at"])

                    tier = sub.plan_tier or SubscriptionPlan.objects.filter(code=sub.plan).first()
                    wallet = CompanyWalletService.get_or_create_wallet(sub.company)

                    if sub.auto_renew and tier and tier.code != "free" and tier.price > Decimal("0.00"):
                        _, _, total_needed = CreditWalletService.calculate_gst_breakdown(tier.price)
                        if wallet.available_balance < total_needed:
                            # INSUFFICIENT WALLET BALANCE: Alert admin to recharge before expiry date
                            deficit = total_needed - wallet.available_balance
                            for member in sub.company.members.filter(is_active=True, role="ADMIN"):
                                create_notification(
                                    user=member.user,
                                    company=sub.company,
                                    notification_type=Notification.NotificationType.SYSTEM,
                                    title=f"Recharge Wallet for Auto-Pay: {days_ahead} Day{'s' if days_ahead > 1 else ''} Left (Due {sub.valid_till})",
                                    message=(
                                        f"Your {tier.name} plan will Auto-Pay on {sub.valid_till} for ₹{total_needed:.2f} (incl. 18% GST). "
                                        f"Your wallet balance is currently ₹{wallet.available_balance:.2f} (Shortfall: ₹{deficit:.2f}). "
                                        f"Please recharge your company wallet before {sub.valid_till} or Auto-Pay will be cancelled."
                                    ),
                                    link="/company/billing/",
                                    icon="fa-wallet",
                                    color="amber",
                                )
                        else:
                            # SUFFICIENT WALLET BALANCE
                            for member in sub.company.members.filter(is_active=True, role="ADMIN"):
                                create_notification(
                                    user=member.user,
                                    company=sub.company,
                                    notification_type=Notification.NotificationType.SYSTEM,
                                    title=f"Auto-Pay Scheduled in {days_ahead} Day{'s' if days_ahead > 1 else ''}",
                                    message=(
                                        f"Your {tier.name} plan will automatically renew on {sub.valid_till}. "
                                        f"Wallet balance (₹{wallet.available_balance:.2f}) is sufficient for ₹{total_needed:.2f} renewal."
                                    ),
                                    link="/company/billing/",
                                    icon="fa-arrows-rotate",
                                    color="emerald",
                                )
                    else:
                        auto_pay_text = "Auto-Pay is ACTIVE." if sub.auto_renew else "Auto-Pay is OFF. Please renew manually."
                        for member in sub.company.members.filter(is_active=True, role="ADMIN"):
                            create_notification(
                                user=member.user,
                                company=sub.company,
                                notification_type=Notification.NotificationType.SYSTEM,
                                title=f"Subscription Expires in {days_ahead} Day{'s' if days_ahead > 1 else ''}",
                                message=f"Your {sub.get_plan_display()} plan will expire on {sub.valid_till}. {auto_pay_text}",
                                link="/company/billing/",
                                icon="fa-calendar-days",
                                color="amber",
                            )

                    try:
                        from apps.billing.services.transactional_email import TransactionalEmailService
                        TransactionalEmailService.send_subscription_expiry_reminder_email(
                            company=sub.company,
                            subscription=sub,
                            days_left=days_ahead,
                        )
                    except Exception:
                        pass

        return summary

    @classmethod
    def check_and_notify_5day_expiry(cls, company):
        """
        Proactively checks if company's subscription expires within 5 days.
        If Auto-Pay is active and wallet balance is insufficient, urges Company Admins
        to recharge before the expiry date to prevent Auto-Pay cancellation.
        """
        sub = getattr(company, "subscription", None)
        if not sub or sub.status != Subscription.Status.ACTIVE or not sub.valid_till:
            return False

        today = timezone.now().date()
        valid_till_date = sub.valid_till
        if hasattr(valid_till_date, "date") and callable(valid_till_date.date):
            valid_till_date = valid_till_date.date()
        days_left = (valid_till_date - today).days

        # Trigger if 1 to 5 days left and not already marked
        if 1 <= days_left <= 5 and not sub.expiry_warning_5d_sent:
            recent_alert = Notification.objects.filter(
                company=company,
                title__icontains="Recharge Wallet Before" if sub.auto_renew else "Expiry Notice",
                created_at__gte=timezone.now() - timezone.timedelta(days=2),
            ).exists()
            if not recent_alert:
                sub.expiry_warning_5d_sent = True
                sub.save(update_fields=["expiry_warning_5d_sent", "updated_at"])

                tier = sub.plan_tier or SubscriptionPlan.objects.filter(code=sub.plan).first()
                wallet = CompanyWalletService.get_or_create_wallet(company)

                if sub.auto_renew and tier and tier.code != "free" and tier.price > Decimal("0.00"):
                    _, _, total_needed = cls.calculate_gst_breakdown(tier.price)
                    if wallet.available_balance < total_needed:
                        deficit = total_needed - wallet.available_balance
                        for member in company.members.filter(is_active=True, role="ADMIN"):
                            create_notification(
                                user=member.user,
                                company=company,
                                notification_type=Notification.NotificationType.SYSTEM,
                                title=f"Recharge Wallet Before {sub.valid_till} (Auto-Pay)",
                                message=(
                                    f"Your {tier.name} plan is set to Auto-Pay on {sub.valid_till} for ₹{total_needed:.2f} (incl. 18% GST). "
                                    f"Your company wallet has only ₹{wallet.available_balance:.2f} (short by ₹{deficit:.2f}). "
                                    f"Please recharge your wallet before {sub.valid_till} or Auto-Pay will be cancelled."
                                ),
                                link="/company/billing/",
                                icon="fa-wallet",
                                color="amber",
                            )
                        return True
                    else:
                        for member in company.members.filter(is_active=True, role="ADMIN"):
                            create_notification(
                                user=member.user,
                                company=company,
                                notification_type=Notification.NotificationType.SYSTEM,
                                title=f"Subscription Auto-Pay in {days_left} Days",
                                message=f"Your {sub.get_plan_display()} subscription will automatically renew on {sub.valid_till}. Wallet balance is sufficient.",
                                link="/company/billing/",
                                icon="fa-arrows-rotate",
                                color="emerald",
                            )
                        return True
                else:
                    auto_pay_msg = "Auto-Pay is active." if sub.auto_renew else "Auto-Pay is OFF. Please renew manually before expiry to maintain access."
                    for member in company.members.filter(is_active=True, role="ADMIN"):
                        create_notification(
                            user=member.user,
                            company=company,
                            notification_type=Notification.NotificationType.SYSTEM,
                            title=f"Subscription Expiry Notice ({days_left} Days Remaining)",
                            message=f"Your {sub.get_plan_display()} subscription expires on {sub.valid_till}. {auto_pay_msg}",
                            link="/company/billing/",
                            icon="fa-clock-rotate-left",
                            color="amber",
                        )
                    return True
        return False

    @classmethod
    def can_add_team_member(cls, company):
        """
        Checks whether the company has reached its plan tier team member limit.
        Returns (can_add: bool, current_count: int, max_limit: int).
        """
        if not company:
            return True, 0, 999
        sub = getattr(company, "subscription", None)
        max_limit = sub.plan_tier.max_team_members if (sub and sub.plan_tier) else 10
        current_count = company.members.filter(is_active=True).count()
        return (current_count < max_limit), current_count, max_limit

    @classmethod
    def can_add_product(cls, company):
        """
        Checks whether the company has reached its plan tier product catalog limit.
        Returns (can_add: bool, current_count: int, max_limit: int).
        """
        if not company:
            return True, 0, 999
        from apps.catalog.models import Product
        sub = getattr(company, "subscription", None)
        max_limit = sub.plan_tier.max_products if (sub and sub.plan_tier) else 100
        current_count = Product.objects.filter(company=company, is_deleted=False).count()
        return (current_count < max_limit), current_count, max_limit

    @classmethod
    def request_credit_topup(cls, company, user, requested_pack="500", note=""):
        """
        Dispatches an in-app top-up alert to all Company Admins on behalf of a Company User.
        """
        admin_members = company.members.filter(is_active=True, role="ADMIN")
        user_name = user.get_full_name() or user.email
        for admin_member in admin_members:
            create_notification(
                user=admin_member.user,
                company=company,
                notification_type=Notification.NotificationType.LOW_CREDITS,
                title=f"Credit Recharge Request from {user_name}",
                message=f"{user_name} requested a top-up of {requested_pack} AI credits for active procurement operations. {note}".strip(),
                link="/company/billing/",
                icon="fa-bolt",
                color="amber",
            )
        return admin_members.count()

    @classmethod
    def _trigger_low_credit_notification(cls, company, wallet):
        """
        Emits an in-app low credit warning if available credits drop below 15% of pool or 50 credits.
        """
        if wallet.total_available <= 50 or (wallet.total_pool > 0 and (wallet.total_available / wallet.total_pool) < 0.15):
            # Check if recently notified within 24h
            recent = Notification.objects.filter(
                company=company,
                notification_type=Notification.NotificationType.LOW_CREDITS,
                created_at__gte=timezone.now() - timezone.timedelta(hours=24),
            ).exists()
            if not recent:
                for member in company.members.filter(is_active=True, role="ADMIN"):
                    create_notification(
                        user=member.user,
                        company=company,
                        notification_type=Notification.NotificationType.LOW_CREDITS,
                        title="AI Credits Running Low",
                        message=f"Your company has only {wallet.total_available} AI search credits remaining. Please upgrade your plan or top up credits.",
                        link="/company/billing/",
                        icon="fa-battery-quarter",
                        color="rose",
                    )


class CompanyWalletService:
    """
    Production-grade enterprise service for company currency wallet operations:
    - ₹0.00 initial balance on company registration
    - Currency rate-card lookups with caching
    - Concurrency-safe atomic reservations and deductions with select_for_update()
    - Full idempotency protection preventing double charges
    - Automatic refunds on failed or zero-result search jobs
    - Super Admin audited adjustments with mandatory justification
    - Complete ledger reconciliation and multi-tenant isolation
    """

    DEFAULT_SERVICE_PRICING = [
        {
            "service_code": "supplier_discovery",
            "display_name": "AI Supplier Discovery Search",
            "price": Decimal("20.00"),
            "charging_unit": ServicePricing.ChargingUnit.PER_JOB,
            "description": "Executes multi-source supplier crawler and candidate scraping per search job.",
        },
        {
            "service_code": "buyer_discovery",
            "display_name": "AI Buyer Discovery Search",
            "price": Decimal("20.00"),
            "charging_unit": ServicePricing.ChargingUnit.PER_JOB,
            "description": "Executes industrial procurement crawler and commercial buyers discovery per search job.",
        },
        {
            "service_code": "ai_matching",
            "display_name": "AI Parameter Matching",
            "price": Decimal("5.00"),
            "charging_unit": ServicePricing.ChargingUnit.PER_MATCH,
            "description": "Calculates technical specification alignment and Gemini fit scoring per evaluated company.",
        },
        {
            "service_code": "lead_enrichment",
            "display_name": "Detailed Lead & Website Enrichment",
            "price": Decimal("2.00"),
            "charging_unit": ServicePricing.ChargingUnit.PER_LEAD,
            "description": "Deep website scraping, commercial catalog extraction, and contact verification per lead.",
        },
        {
            "service_code": "report_export",
            "display_name": "Executive Intelligence Report Export",
            "price": Decimal("10.00"),
            "charging_unit": ServicePricing.ChargingUnit.PER_EXPORT,
            "description": "Compiles executive PDF / Excel audit dossier for offline procurement and sales review.",
        },
    ]

    @classmethod
    def ensure_default_service_pricing(cls):
        """
        Seeds standard service pricing rate card in INR if not already present.
        """
        for item in cls.DEFAULT_SERVICE_PRICING:
            ServicePricing.objects.get_or_create(
                service_code=item["service_code"],
                defaults=item,
            )

    @classmethod
    def get_or_create_wallet(cls, company):
        """
        Retrieves or initializes a company's prepaid currency wallet with ₹0.00 initial balance.
        Guarantees strict tenant isolation.
        """
        if not company:
            return None
        cls.ensure_default_service_pricing()
        wallet, _ = CompanyWallet.objects.get_or_create(
            company=company,
            defaults={
                "available_balance": Decimal("0.00"),
                "reserved_balance": Decimal("0.00"),
                "total_credited": Decimal("0.00"),
                "total_spent": Decimal("0.00"),
                "currency": "INR",
                "status": CompanyWallet.WalletStatus.ACTIVE,
            },
        )
        return wallet

    @classmethod
    def get_service_price(cls, service_code: str) -> Decimal:
        """
        Returns active price in INR for a service_code with 5-minute caching.
        """
        cache_key = f"srv_price:{service_code}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Decimal(str(cached))

        price = Decimal("20.00")
        try:
            rule = ServicePricing.objects.filter(service_code=service_code, is_active=True).first()
            if rule:
                price = rule.price
            else:
                for default_item in cls.DEFAULT_SERVICE_PRICING:
                    if default_item["service_code"] == service_code:
                        price = default_item["price"]
                        break
        except Exception as e:
            logger.warning("Error fetching service price for %s: %s", service_code, e)

        cache.set(cache_key, str(price), timeout=300)
        return price

    @classmethod
    def check_balance(cls, company, service_code: str, quantity: int = 1) -> tuple[bool, Decimal, Decimal]:
        """
        Checks whether the company's wallet has enough available liquid balance.
        Returns: (has_sufficient: bool, available_balance: Decimal, required_amount: Decimal)
        """
        wallet = cls.get_or_create_wallet(company)
        if not wallet or wallet.status != CompanyWallet.WalletStatus.ACTIVE:
            return False, Decimal("0.00"), Decimal("0.00")

        unit_price = cls.get_service_price(service_code)
        required_amount = unit_price * Decimal(max(1, quantity))
        available = wallet.available_balance
        return (available >= required_amount), available, required_amount

    @classmethod
    def reserve_funds(
        cls,
        company,
        user,
        service_code: str,
        quantity: int = 1,
        search_job=None,
        idempotency_key: str | None = None,
    ) -> tuple[bool, str, UsageRecord | None]:
        """
        Atomically reserves funds before launching an AI/search operation.
        Uses select_for_update() row lock to prevent concurrency race conditions.
        Returns: (success: bool, message: str, usage_record: UsageRecord or None)
        """
        cls.ensure_default_service_pricing()

        # 1. Idempotency check: if this key was already processed or reserved, return safely
        if idempotency_key:
            existing = UsageRecord.objects.filter(idempotency_key=idempotency_key).first()
            if existing:
                if existing.status == UsageRecord.ProcessingStatus.COMPLETED:
                    return True, "Operation already completed (idempotent)", existing
                elif existing.status == UsageRecord.ProcessingStatus.RESERVED:
                    return True, "Operation already reserved", existing

        unit_price = cls.get_service_price(service_code)
        needed_amount = unit_price * Decimal(max(1, quantity))

        with transaction.atomic():
            wallet = CompanyWallet.objects.select_for_update().filter(company=company).first()
            if not wallet:
                wallet = cls.get_or_create_wallet(company)
                wallet = CompanyWallet.objects.select_for_update().filter(company=company).first()

            if wallet.status != CompanyWallet.WalletStatus.ACTIVE:
                return False, f"Wallet is currently {wallet.get_status_display().lower()}. Please contact support.", None

            if wallet.available_balance < needed_amount:
                cls._trigger_low_balance_notification(company, wallet)
                return False, (
                    f"Insufficient wallet balance. Required: ₹{needed_amount:.2f}, "
                    f"Available: ₹{wallet.available_balance:.2f}. Please recharge your company wallet."
                ), None

            # Move funds from available to reserved
            wallet.available_balance -= needed_amount
            wallet.reserved_balance += needed_amount
            wallet.save(update_fields=["available_balance", "reserved_balance", "updated_at"])

            usage_record = UsageRecord.objects.create(
                company=company,
                user=user,
                wallet=wallet,
                feature_code=service_code,
                search_job=search_job,
                usage_quantity=quantity,
                amount_charged=needed_amount,
                credits_charged=int(needed_amount),
                status=UsageRecord.ProcessingStatus.RESERVED,
                idempotency_key=idempotency_key or "",
                started_at=timezone.now(),
            )

        return True, "Funds reserved successfully.", usage_record

    @classmethod
    def commit_usage(cls, usage_record: UsageRecord, actual_quantity: int | None = None) -> WalletTransaction | None:
        """
        Commits an operation on successful execution:
        1. Releases the reserved balance hold
        2. Deducts the final charge from company wallet
        3. Updates cumulative total_spent
        4. Writes an immutable WalletTransaction ledger row
        5. Updates the UsageRecord status to COMPLETED
        6. Triggers low-balance alert if threshold crossed
        """
        if not usage_record:
            return None

        with transaction.atomic():
            wallet = CompanyWallet.objects.select_for_update().filter(company=usage_record.company).first()
            if not wallet:
                wallet = cls.get_or_create_wallet(usage_record.company)
                wallet = CompanyWallet.objects.select_for_update().filter(company=usage_record.company).first()

            # Release the reservation hold
            reserved_hold = usage_record.amount_charged
            wallet.reserved_balance = max(Decimal("0.00"), wallet.reserved_balance - reserved_hold)

            # Determine final amount to charge
            final_charge = reserved_hold
            if actual_quantity is not None:
                unit_price = cls.get_service_price(usage_record.feature_code)
                final_charge = unit_price * Decimal(max(1, actual_quantity))
                # Adjust available balance for difference between initial hold and actual quantity
                difference = reserved_hold - final_charge
                wallet.available_balance += difference

            wallet.total_spent += final_charge
            wallet.last_transaction_at = timezone.now()
            wallet.save(update_fields=["available_balance", "reserved_balance", "total_spent", "last_transaction_at", "updated_at"])

            # Resolve human-readable service name
            service_rule = ServicePricing.objects.filter(service_code=usage_record.feature_code).first()
            service_name = service_rule.display_name if service_rule else usage_record.feature_code.replace("_", " ").title()

            # Record immutable WalletTransaction
            tx = WalletTransaction.objects.create(
                wallet=wallet,
                company=usage_record.company,
                transaction_type=WalletTransaction.TransactionType.USAGE_DEBIT,
                amount=-final_charge,
                balance_after=wallet.available_balance,
                service_code=usage_record.feature_code,
                search_job=usage_record.search_job,
                idempotency_key=usage_record.idempotency_key or None,
                status=WalletTransaction.TransactionStatus.COMPLETED,
                description=f"{service_name} (₹{final_charge:.2f})",
                created_by=usage_record.user,
            )

            # Finalize Usage Record
            usage_record.status = UsageRecord.ProcessingStatus.COMPLETED
            usage_record.amount_charged = final_charge
            usage_record.credits_charged = int(final_charge)
            usage_record.completed_at = timezone.now()
            usage_record.save(update_fields=["status", "amount_charged", "credits_charged", "completed_at"])

            # Trigger low-balance notification if threshold breached
            if wallet.is_low_balance:
                cls._trigger_low_balance_notification(usage_record.company, wallet)

        return tx

    @classmethod
    def refund_funds(cls, usage_record: UsageRecord, reason: str = "Operation failed") -> WalletTransaction | None:
        """
        Releases reserved balance hold on failure, or credits back an already-committed charge.
        """
        if not usage_record:
            return None

        with transaction.atomic():
            wallet = CompanyWallet.objects.select_for_update().filter(company=usage_record.company).first()
            if not wallet:
                wallet = cls.get_or_create_wallet(usage_record.company)
                wallet = CompanyWallet.objects.select_for_update().filter(company=usage_record.company).first()

            if usage_record.status == UsageRecord.ProcessingStatus.RESERVED:
                # Release reserved balance hold back to available
                wallet.reserved_balance = max(Decimal("0.00"), wallet.reserved_balance - usage_record.amount_charged)
                wallet.available_balance += usage_record.amount_charged
                wallet.save(update_fields=["available_balance", "reserved_balance", "updated_at"])

                usage_record.status = UsageRecord.ProcessingStatus.FAILED
                usage_record.failure_reason = reason
                usage_record.completed_at = timezone.now()
                usage_record.save(update_fields=["status", "failure_reason", "completed_at"])
                return None

            elif usage_record.status == UsageRecord.ProcessingStatus.COMPLETED:
                # Refund committed funds
                refund_amount = usage_record.amount_charged
                wallet.available_balance += refund_amount
                wallet.total_spent = max(Decimal("0.00"), wallet.total_spent - refund_amount)
                wallet.last_transaction_at = timezone.now()
                wallet.save(update_fields=["available_balance", "total_spent", "last_transaction_at", "updated_at"])

                tx = WalletTransaction.objects.create(
                    wallet=wallet,
                    company=usage_record.company,
                    transaction_type=WalletTransaction.TransactionType.REFUND,
                    amount=refund_amount,
                    balance_after=wallet.available_balance,
                    service_code=usage_record.feature_code,
                    search_job=usage_record.search_job,
                    idempotency_key=f"rfd-{usage_record.id}",
                    status=WalletTransaction.TransactionStatus.COMPLETED,
                    description=f"Auto Refund: {reason}",
                    created_by=usage_record.user,
                )

                usage_record.status = UsageRecord.ProcessingStatus.REFUNDED
                usage_record.failure_reason = reason
                usage_record.save(update_fields=["status", "failure_reason"])
                return tx

        return None

    @classmethod
    def recharge_wallet(
        cls,
        company,
        amount: Decimal | float | int | str,
        payment=None,
        user=None,
        notes: str = "",
    ) -> WalletTransaction:
        """
        Credits company wallet upon verified payment gateway confirmation.
        Idempotent via unique reference_id and idempotency_key.
        """
        amount_decimal = Decimal(str(amount))
        if amount_decimal <= Decimal("0.00"):
            raise ValueError("Recharge amount must be strictly positive.")

        idempotency_key = f"rch-pay-{payment.id}" if payment else f"rch-direct-{company.id}-{int(timezone.now().timestamp() * 1000)}"

        # Check idempotency
        existing_tx = WalletTransaction.objects.filter(idempotency_key=idempotency_key).first()
        if existing_tx:
            logger.info("Wallet recharge with key %s already processed. Returning existing transaction.", idempotency_key)
            return existing_tx

        with transaction.atomic():
            wallet = CompanyWallet.objects.select_for_update().filter(company=company).first()
            if not wallet:
                wallet = cls.get_or_create_wallet(company)
                wallet = CompanyWallet.objects.select_for_update().filter(company=company).first()

            wallet.available_balance += amount_decimal
            wallet.total_credited += amount_decimal
            wallet.last_transaction_at = timezone.now()
            wallet.save(update_fields=["available_balance", "total_credited", "last_transaction_at", "updated_at"])

            ref_id = payment.provider_reference if payment else ""
            desc = notes or f"Prepaid Wallet Recharge (+₹{amount_decimal:.2f})"

            tx = WalletTransaction.objects.create(
                wallet=wallet,
                company=company,
                transaction_type=WalletTransaction.TransactionType.RECHARGE,
                amount=amount_decimal,
                balance_after=wallet.available_balance,
                reference_id=ref_id,
                idempotency_key=idempotency_key,
                status=WalletTransaction.TransactionStatus.COMPLETED,
                description=desc,
                created_by=user,
            )

        logger.info("Successfully recharged wallet for %s by ₹%s. New balance: ₹%s", company.name, amount_decimal, wallet.available_balance)

        try:
            from apps.notifications.services.email_engine import trigger_email_notification
            trigger_email_notification(
                event_key="billing.wallet_recharged",
                company=company,
                user=user,
                context={
                    "amount": f"{amount_decimal:,.2f}",
                    "transaction_ref": tx.reference_id or f"TXN-{tx.id}",
                    "new_balance": f"{wallet.available_balance:,.2f}",
                    "cta_url": "/company/billing/",
                    "details": {
                        "Credited Amount": f"₹{amount_decimal:,.2f}",
                        "New Balance": f"₹{wallet.available_balance:,.2f}",
                        "Transaction Ref": tx.reference_id or f"TXN-{tx.id}",
                        "Date": tx.created_at.strftime("%Y-%m-%d %H:%M:%S") if hasattr(tx, 'created_at') else "",
                    },
                },
                idempotency_key=f"email_rch_{tx.id}",
            )
        except Exception as exc:
            logger.warning(f"Failed to trigger wallet recharge email: {exc}")

        return tx

    @classmethod
    def debit_wallet_for_purchase(
        cls,
        company,
        amount: Decimal | float | int | str,
        description: str,
        user=None,
        reference_id: str = "",
    ) -> tuple[bool, str, WalletTransaction | None]:
        """
        Atomically debits the company currency wallet for purchases like Credit Top-Up Packs.
        Returns: (success: bool, message: str, transaction: WalletTransaction or None)
        """
        amount_decimal = Decimal(str(amount))
        if amount_decimal <= Decimal("0.00"):
            return False, "Purchase amount must be positive.", None

        with transaction.atomic():
            wallet = CompanyWallet.objects.select_for_update().filter(company=company).first()
            if not wallet:
                wallet = cls.get_or_create_wallet(company)
                wallet = CompanyWallet.objects.select_for_update().filter(company=company).first()

            if wallet.status != CompanyWallet.WalletStatus.ACTIVE:
                return False, f"Wallet is currently {wallet.get_status_display().lower()}.", None

            if wallet.available_balance < amount_decimal:
                cls._trigger_low_balance_notification(company, wallet)
                return False, (
                    f"Insufficient wallet balance! Required ₹{amount_decimal:.2f}, "
                    f"but available balance is only ₹{wallet.available_balance:.2f}. "
                    f"Please recharge your company wallet first before purchasing top-up credits."
                ), None

            wallet.available_balance -= amount_decimal
            wallet.total_spent += amount_decimal
            wallet.last_transaction_at = timezone.now()
            wallet.save(update_fields=["available_balance", "total_spent", "last_transaction_at", "updated_at"])

            tx = WalletTransaction.objects.create(
                wallet=wallet,
                company=company,
                transaction_type=WalletTransaction.TransactionType.USAGE_DEBIT,
                amount=-amount_decimal,
                balance_after=wallet.available_balance,
                reference_id=reference_id,
                status=WalletTransaction.TransactionStatus.COMPLETED,
                description=description,
                created_by=user,
            )

            if wallet.is_low_balance:
                cls._trigger_low_balance_notification(company, wallet)

        return True, "Payment successful.", tx

    @classmethod
    def adjust_balance_admin(
        cls,
        company,
        delta_amount: Decimal | float | int | str,
        reason: str,
        admin_user,
    ) -> WalletTransaction:
        """
        Authorized Super Admin manual adjustment. Requires non-empty reason for audit compliance.
        Prevents balance from falling below zero.
        """
        if not reason or not reason.strip():
            raise ValueError("An explicit, audited justification reason is required for manual balance adjustment.")

        delta = Decimal(str(delta_amount))
        if delta == Decimal("0.00"):
            raise ValueError("Adjustment delta must be non-zero.")

        with transaction.atomic():
            wallet = CompanyWallet.objects.select_for_update().filter(company=company).first()
            if not wallet:
                wallet = cls.get_or_create_wallet(company)
                wallet = CompanyWallet.objects.select_for_update().filter(company=company).first()

            new_balance = wallet.available_balance + delta
            if new_balance < Decimal("0.00"):
                raise ValueError(
                    f"Adjustment would result in negative balance. Current: ₹{wallet.available_balance:.2f}, "
                    f"Requested Delta: ₹{delta:.2f}."
                )

            wallet.available_balance = new_balance
            if delta > Decimal("0.00"):
                wallet.total_credited += delta
            wallet.last_transaction_at = timezone.now()
            wallet.save(update_fields=["available_balance", "total_credited", "last_transaction_at", "updated_at"])

            sign = "+" if delta > 0 else ""
            desc = f"Super Admin Adjustment: {reason.strip()} ({sign}₹{delta:.2f})"

            tx = WalletTransaction.objects.create(
                wallet=wallet,
                company=company,
                transaction_type=WalletTransaction.TransactionType.ADMIN_ADJUSTMENT,
                amount=delta,
                balance_after=wallet.available_balance,
                description=desc,
                status=WalletTransaction.TransactionStatus.COMPLETED,
                created_by=admin_user,
            )

        logger.info("Super Admin %s adjusted wallet for %s by %s. Reason: %s", admin_user, company.name, delta, reason)
        return tx

    @classmethod
    def execute_auto_pay_renewal(cls, company, subscription) -> tuple[bool, str, WalletTransaction | None]:
        """
        Executes subscription renewal via Company Prepaid Wallet if sufficient balance is available.
        If wallet balance is insufficient, automatically cancels Auto-Pay and expires the subscription,
        broadcasting high-priority notification to Company Admins.
        """
        if not subscription or not subscription.auto_renew:
            return False, "Auto-Pay is not enabled for this subscription.", None

        tier = subscription.plan_tier
        if not tier:
            tier = SubscriptionPlan.objects.filter(code=subscription.plan).first()
        if not tier:
            return False, "No subscription plan tier associated.", None

        # Free tier requires no wallet deduction
        if tier.code == "free" or tier.price <= Decimal("0.00"):
            return False, "Free plan does not require wallet auto-pay.", None

        # Calculate renewal cost including 18% GST
        subtotal, tax, total_amount = CreditWalletService.calculate_gst_breakdown(tier.price)

        with transaction.atomic():
            wallet = CompanyWallet.objects.select_for_update().filter(company=company).first()
            if not wallet:
                wallet = cls.get_or_create_wallet(company)
                wallet = CompanyWallet.objects.select_for_update().filter(company=company).first()

            if wallet.available_balance >= total_amount:
                # 1. SUFFICIENT BALANCE: Execute Auto-Pay Renewal
                wallet.available_balance -= total_amount
                wallet.total_spent += total_amount
                wallet.last_transaction_at = timezone.now()
                wallet.save(update_fields=["available_balance", "total_spent", "last_transaction_at", "updated_at"])

                ref_id = f"AUTOPAY-{timezone.now().strftime('%Y%m%d%H%M%S')}"
                desc = f"Auto-Pay Renewal: {tier.name} Plan (-₹{total_amount:.2f} incl. 18% GST)"

                tx = WalletTransaction.objects.create(
                    wallet=wallet,
                    company=company,
                    transaction_type=WalletTransaction.TransactionType.USAGE_DEBIT,
                    amount=-total_amount,
                    balance_after=wallet.available_balance,
                    reference_id=ref_id,
                    service_code="subscription_renewal",
                    description=desc,
                    status=WalletTransaction.TransactionStatus.COMPLETED,
                    created_by=None,
                )

                payment = Payment.objects.create(
                    company=company,
                    subscription=subscription,
                    amount=total_amount,
                    currency=tier.currency or "INR",
                    provider="Prepaid Company Wallet Auto-Pay",
                    provider_reference=ref_id,
                    status=Payment.Status.SUCCESS,
                    payment_date=timezone.now(),
                )

                cycle_days = 365 if tier.billing_cycle == "yearly" else 30
                new_valid_till = (subscription.valid_till or timezone.now().date()) + timezone.timedelta(days=cycle_days)

                invoice = Invoice.objects.create(
                    company=company,
                    subscription=subscription,
                    payment=payment,
                    billing_period_start=timezone.now().date(),
                    billing_period_end=new_valid_till,
                    subtotal=subtotal,
                    tax=tax,
                    total_amount=total_amount,
                    currency=tier.currency or "INR",
                    status=Invoice.Status.PAID,
                    issue_date=timezone.now().date(),
                    due_date=timezone.now().date(),
                )

                subscription.valid_till = new_valid_till
                subscription.renewal_date = timezone.now() + timezone.timedelta(days=cycle_days)
                subscription.status = Subscription.Status.ACTIVE
                subscription.expiry_warning_5d_sent = False
                subscription.save(update_fields=["valid_till", "renewal_date", "status", "expiry_warning_5d_sent", "updated_at"])

                CreditWalletService.allocate_plan_credits(company, tier, is_renewal=True)

                # Send success notification to Company Admins
                for member in company.members.filter(is_active=True, role="ADMIN"):
                    create_notification(
                        user=member.user,
                        company=company,
                        notification_type=Notification.NotificationType.SYSTEM,
                        title=f"Auto-Pay Successful: {tier.name} Plan Renewed",
                        message=(
                            f"Auto-Pay successfully renewed your {tier.name} subscription! "
                            f"₹{total_amount:.2f} was deducted from your company wallet. "
                            f"Tax invoice #{invoice.invoice_number} generated. Valid till {new_valid_till}."
                        ),
                        link="/company/billing/",
                        icon="fa-arrows-rotate",
                        color="emerald",
                    )

                logger.info("Auto-Pay executed successfully for %s: Plan %s, Amount ₹%s", company.name, tier.name, total_amount)
                return True, "Auto-Pay successfully processed.", tx

            else:
                # 2. INSUFFICIENT BALANCE: Cancel Auto-Pay and expire/restrict plan
                subscription.auto_renew = False
                subscription.cancelled_at = timezone.now()
                subscription.status = Subscription.Status.EXPIRED
                subscription.save(update_fields=["auto_renew", "cancelled_at", "status", "updated_at"])

                deficit = total_amount - wallet.available_balance

                # Broadcast Auto-Pay cancellation alert to Company Admins
                for member in company.members.filter(is_active=True, role="ADMIN"):
                    create_notification(
                        user=member.user,
                        company=company,
                        notification_type=Notification.NotificationType.SYSTEM,
                        title="Auto-Pay Cancelled: Insufficient Wallet Balance",
                        message=(
                            f"Auto-Pay renewal for your {tier.name} plan failed on {subscription.valid_till} "
                            f"due to insufficient wallet balance (Required: ₹{total_amount:.2f}, Available: ₹{wallet.available_balance:.2f}, "
                            f"Shortfall: ₹{deficit:.2f}). Auto-Pay has been cancelled and your plan has expired. "
                            f"Please recharge your wallet and renew your subscription to resume operations."
                        ),
                        link="/company/billing/",
                        icon="fa-triangle-exclamation",
                        color="rose",
                    )

                logger.warning(
                    "Auto-Pay failed and cancelled for %s: Required ₹%s, Available ₹%s",
                    company.name, total_amount, wallet.available_balance,
                )
                return False, f"Insufficient wallet balance (₹{wallet.available_balance:.2f} / ₹{total_amount:.2f}). Auto-Pay cancelled.", None

    @classmethod
    def release_stale_reservations(cls, timeout_minutes: int = 30) -> int:
        """
        Reaps stale/orphaned fund reservations older than timeout_minutes.
        Safely releases reserved balance holds back to available balance.
        """
        cutoff = timezone.now() - timezone.timedelta(minutes=timeout_minutes)
        stale_records = UsageRecord.objects.filter(
            status=UsageRecord.ProcessingStatus.RESERVED,
            started_at__lt=cutoff,
            wallet__isnull=False,
        )

        released_count = 0
        for rec in stale_records:
            try:
                cls.refund_funds(rec, reason=f"Automatic cleanup: Job timed out (> {timeout_minutes} min)")
                released_count += 1
            except Exception as e:
                logger.error("Failed to release stale reservation #%s: %s", rec.id, e)

        if released_count > 0:
            logger.info("Released %s stale fund reservation(s) older than %s minutes", released_count, timeout_minutes)
        return released_count

    @classmethod
    def reconcile_wallet(cls, company) -> dict:
        """
        Reconciles wallet balance against sum of immutable ledger transactions.
        Guarantees zero discrepancies even when active reservations are held.
        """
        wallet = cls.get_or_create_wallet(company)
        transactions = WalletTransaction.objects.filter(company=company, status=WalletTransaction.TransactionStatus.COMPLETED)
        ledger_sum = sum((t.amount for t in transactions), Decimal("0.00"))
        
        # Invariant: Total liquid + held funds must match net completed ledger sum
        total_balance = wallet.available_balance + wallet.reserved_balance
        discrepancy = total_balance - ledger_sum

        return {
            "company_id": company.id,
            "company_name": company.name,
            "wallet_available_balance": wallet.available_balance,
            "wallet_reserved_balance": wallet.reserved_balance,
            "total_balance": total_balance,
            "ledger_sum": ledger_sum,
            "discrepancy": discrepancy,
            "is_balanced": (discrepancy == Decimal("0.00")),
            "transaction_count": transactions.count(),
        }

    @classmethod
    def _trigger_low_balance_notification(cls, company, wallet):
        """
        Triggers an in-app low balance alert to Company Admins when available balance is below threshold.
        """
        if not wallet.is_low_balance:
            return

        recent = Notification.objects.filter(
            company=company,
            notification_type=Notification.NotificationType.LOW_CREDITS,
            created_at__gte=timezone.now() - timezone.timedelta(hours=24),
        ).exists()

        if not recent:
            admin_members = company.members.filter(is_active=True, role="ADMIN")
            for member in admin_members:
                create_notification(
                    user=member.user,
                    company=company,
                    notification_type=Notification.NotificationType.LOW_CREDITS,
                    title="Low Wallet Balance Warning",
                    message=(
                        f"Your company wallet balance is low (₹{wallet.available_balance:.2f} remaining). "
                        "Recharge now to ensure uninterrupted AI procurement and sales searches."
                    ),
                    link="/company/billing/",
                    icon="fa-wallet",
                    color="amber",
                )

            try:
                from apps.notifications.services.email_engine import trigger_email_notification
                trigger_email_notification(
                    event_key="billing.low_balance",
                    company=company,
                    context={
                        "current_balance": f"{wallet.available_balance:,.2f}",
                        "cta_url": "/company/billing/",
                        "details": {
                            "Remaining Balance": f"₹{wallet.available_balance:,.2f}",
                            "Threshold": f"₹{wallet.low_balance_threshold:,.2f}",
                            "Action": "Recharge required to maintain active discovery",
                        },
                    },
                    severity="WARNING",
                    idempotency_key=f"email_lowbal_{company.id}_{timezone.now().strftime('%Y%m%d')}",
                )
            except Exception as exc:
                logger.warning(f"Failed to trigger low balance email: {exc}")

