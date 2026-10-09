import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.billing.models import (
    SubscriptionPlan,
    Subscription,
    CompanySubscription,
    CompanyCreditWallet,
    FeatureCreditCost,
    CreditTransaction,
    UsageRecord,
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

        # 1. Subscriptions past valid_till date
        active_subs = Subscription.objects.filter(
            status__in=[Subscription.Status.ACTIVE, Subscription.Status.TRIAL],
            valid_till__lt=today,
        )
        for sub in active_subs:
            # If auto-renew is disabled, expire immediately
            if not sub.auto_renew:
                sub.status = Subscription.Status.EXPIRED
                summary["expired"] += 1
            else:
                days_overdue = (today - sub.valid_till).days
                if days_overdue <= 3:
                    sub.status = Subscription.Status.PAST_DUE
                    summary["past_due"] += 1
                else:
                    sub.status = Subscription.Status.EXPIRED
                    summary["expired"] += 1
            sub.save(update_fields=["status", "updated_at"])

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

        # 2. Advance renewal alerts: 7 days, 5 days, and 1 day prior
        for days_ahead in [7, 5, 1]:
            target_date = today + timezone.timedelta(days=days_ahead)
            expiring_soon = Subscription.objects.filter(
                status=Subscription.Status.ACTIVE,
                valid_till=target_date,
            )
            for sub in expiring_soon:
                recent_alert = Notification.objects.filter(
                    company=sub.company,
                    title__icontains=f"Expires in {days_ahead} Day",
                    created_at__gte=now - timezone.timedelta(hours=20),
                ).exists()
                if not recent_alert:
                    summary["advance_notified"] += 1
                    if days_ahead == 5:
                        sub.expiry_warning_5d_sent = True
                        sub.save(update_fields=["expiry_warning_5d_sent", "updated_at"])
                    for member in sub.company.members.filter(is_active=True, role="ADMIN"):
                        auto_pay_text = "Auto-Pay is ACTIVE and will automatically renew." if sub.auto_renew else "Auto-Pay is OFF. Please renew manually to prevent restriction."
                        create_notification(
                            user=member.user,
                            company=sub.company,
                            notification_type=Notification.NotificationType.SYSTEM,
                            title=f"Subscription Expires in {days_ahead} Day{'s' if days_ahead > 1 else ''}",
                            message=f"Your {sub.get_plan_display()} plan will renew or expire on {sub.valid_till}. {auto_pay_text}",
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
        If not yet notified for this cycle, sends notification to Company Admins.
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
                title__icontains="5 Day",
                created_at__gte=timezone.now() - timezone.timedelta(days=3),
            ).exists()
            if not recent_alert:
                sub.expiry_warning_5d_sent = True
                sub.save(update_fields=["expiry_warning_5d_sent", "updated_at"])
                auto_pay_msg = "Auto-Pay is active and will renew your plan." if sub.auto_renew else "Auto-Pay is currently OFF. Please renew or turn on Auto-Pay to prevent service restriction."
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
