import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal
from datetime import timedelta
import requests
from django.conf import settings
from django.utils import timezone

from apps.billing.models import (
    CompanyCreditWallet,
    CreditTransaction,
    Invoice,
    Payment,
    Subscription,
    SubscriptionPlan,
)
from apps.billing.services.wallet import CreditWalletService

logger = logging.getLogger(__name__)


class PaymentGatewayService:
    """
    Enterprise Payment Gateway Integration supporting:
    1. Razorpay (India: UPI, Netbanking, Cards with INR & 18% GST)
    2. Stripe (International: Multi-currency Cards & Checkout Sessions)
    3. Development Gateway (Instant simulated sandbox checkout)
    """

    @staticmethod
    def get_configured_providers():
        """Returns dictionary of active gateway providers based on environment settings."""
        providers = {
            "dev": {
                "name": "Instant Test Gateway",
                "enabled": True,
                "mode": "test",
            },
            "razorpay": {
                "name": "Razorpay (UPI / NetBanking / Cards)",
                "enabled": bool(getattr(settings, "RAZORPAY_KEY_ID", None) and getattr(settings, "RAZORPAY_KEY_SECRET", None)),
                "key_id": getattr(settings, "RAZORPAY_KEY_ID", ""),
            },
            "stripe": {
                "name": "Stripe (International Cards)",
                "enabled": bool(getattr(settings, "STRIPE_PUBLIC_KEY", None) and getattr(settings, "STRIPE_SECRET_KEY", None)),
                "public_key": getattr(settings, "STRIPE_PUBLIC_KEY", ""),
            },
        }
        return providers

    @classmethod
    def create_order(cls, company, user, action_type: str, item_id_or_pack: str, provider: str = "auto") -> dict:
        """
        Creates a payment order and pre-initializes pending Payment and Invoice records.
        Returns payload for client-side checkout initiation.
        """
        subscription = getattr(company, "subscription", None)
        base_price = Decimal("0.00")
        currency = "INR"
        order_desc = ""
        metadata = {
            "company_id": company.id,
            "user_id": user.id if user else None,
            "action_type": action_type,
            "item_key": item_id_or_pack,
        }

        # 1. Resolve pricing and descriptions based on action
        if action_type == "upgrade_plan":
            target_plan = SubscriptionPlan.objects.filter(code=item_id_or_pack, status=SubscriptionPlan.PlanStatus.ACTIVE).first()
            if not target_plan:
                raise ValueError(f"Subscription plan '{item_id_or_pack}' not found or inactive.")
            base_price = target_plan.price
            currency = target_plan.currency
            order_desc = f"Upgrade to {target_plan.name} Plan"
            metadata["plan_code"] = target_plan.code
            metadata["credits"] = target_plan.included_credits

        elif action_type == "renew_plan":
            current_plan = subscription.plan_tier if subscription else None
            if not current_plan:
                current_plan = SubscriptionPlan.objects.filter(code="pro").first()
            if not current_plan:
                raise ValueError("Active plan tier could not be determined for renewal.")
            base_price = current_plan.price
            currency = current_plan.currency
            order_desc = f"Renewal of {current_plan.name} Plan"
            metadata["plan_code"] = current_plan.code
            metadata["credits"] = current_plan.included_credits

        elif action_type == "buy_credits":
            try:
                credits_pack = int(item_id_or_pack)
            except (ValueError, TypeError):
                credits_pack = 100
            price_map = {100: Decimal("2499.00"), 500: Decimal("9999.00"), 1000: Decimal("17999.00")}
            base_price = price_map.get(credits_pack, Decimal(credits_pack * 25))
            currency = "INR"
            order_desc = f"Purchase Top-Up Pack (+{credits_pack} Credits)"
            metadata["credits_pack"] = credits_pack

        else:
            raise ValueError(f"Unsupported payment action: '{action_type}'")

        # 2. Calculate 18% GST Breakdown
        subtotal, tax, total_amount = CreditWalletService.calculate_gst_breakdown(base_price)

        # 3. Determine active provider
        razorpay_key_id = getattr(settings, "RAZORPAY_KEY_ID", "")
        razorpay_key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
        stripe_secret_key = getattr(settings, "STRIPE_SECRET_KEY", "")

        active_provider = provider.lower()
        if active_provider == "auto":
            if razorpay_key_id and razorpay_key_secret:
                active_provider = "razorpay"
            elif stripe_secret_key:
                active_provider = "stripe"
            else:
                active_provider = "dev"

        # 4. Generate reference IDs
        receipt_ref = f"ORD-{action_type.upper()}-{item_id_or_pack}-{company.id}-{timezone.now().strftime('%Y%m%d%H%M%S')}"

        # 5. Pre-create Payment record in PENDING state
        payment = Payment.objects.create(
            company=company,
            subscription=subscription,
            amount=total_amount,
            currency=currency,
            provider=f"Gateway: {active_provider.upper()}",
            provider_reference=receipt_ref,
            status=Payment.Status.PENDING,
            payment_date=timezone.now(),
        )

        billing_days = 30
        if action_type in ("upgrade_plan", "renew_plan"):
            plan_obj = metadata.get("plan_code")
            if plan_obj and SubscriptionPlan.objects.filter(code=plan_obj, billing_cycle="yearly").exists():
                billing_days = 365

        invoice = Invoice.objects.create(
            company=company,
            subscription=subscription,
            payment=payment,
            billing_period_start=timezone.now().date(),
            billing_period_end=timezone.now().date() + timedelta(days=billing_days),
            subtotal=subtotal,
            tax=tax,
            total_amount=total_amount,
            currency=currency,
            status=Invoice.Status.DRAFT,
            issue_date=timezone.now().date(),
            due_date=timezone.now().date() + timedelta(days=3),
        )

        # 6. Provider-specific gateway order initialization
        gateway_order_id = receipt_ref
        client_payload = {
            "provider": active_provider,
            "order_id": receipt_ref,
            "payment_id": payment.id,
            "invoice_id": invoice.id,
            "amount": float(total_amount),
            "currency": currency,
            "description": order_desc,
            "company_name": company.name,
            "user_email": user.email if user else "",
            "key_id": "",
        }

        if active_provider == "razorpay" and razorpay_key_id and razorpay_key_secret:
            try:
                # Amount in paise (1 INR = 100 paise)
                amount_in_paise = int(round(total_amount * 100))
                resp = requests.post(
                    "https://api.razorpay.com/v1/orders",
                    auth=(razorpay_key_id, razorpay_key_secret),
                    json={
                        "amount": amount_in_paise,
                        "currency": currency,
                        "receipt": receipt_ref,
                        "notes": metadata,
                    },
                    timeout=10,
                )
                if resp.status_code in (200, 201):
                    rp_order = resp.json()
                    gateway_order_id = rp_order.get("id")
                    payment.provider_reference = gateway_order_id
                    payment.save(update_fields=["provider_reference"])
                    client_payload["gateway_order_id"] = gateway_order_id
                    client_payload["key_id"] = razorpay_key_id
                else:
                    logger.warning("Razorpay order API error %s: %s; falling back to receipt ref", resp.status_code, resp.text)
            except Exception as e:
                logger.error("Error creating Razorpay order: %s", e)

        elif active_provider == "stripe" and stripe_secret_key:
            client_payload["key_id"] = getattr(settings, "STRIPE_PUBLIC_KEY", "")

        return client_payload

    @classmethod
    def verify_razorpay_signature(cls, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str) -> bool:
        """
        Cryptographically verifies Razorpay payment signature using HMAC SHA256.
        """
        key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
        if not key_secret:
            logger.warning("RAZORPAY_KEY_SECRET not set; falling back to permissive signature match for testing.")
            return True

        message = f"{razorpay_order_id}|{razorpay_payment_id}".encode("utf-8")
        generated_signature = hmac.new(
            key_secret.encode("utf-8"),
            message,
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(generated_signature, razorpay_signature)

    @classmethod
    def verify_stripe_signature(cls, payload_bytes: bytes, sig_header: str, webhook_secret: str = "") -> bool:
        """
        Verifies Stripe Webhook signature header format (t=...,v1=...).
        """
        secret = webhook_secret or getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
        if not secret:
            return True

        try:
            pairs = dict(item.split("=", 1) for item in sig_header.split(","))
            timestamp = pairs.get("t")
            v1_sig = pairs.get("v1")
            if not timestamp or not v1_sig:
                return False

            # Prevent replay attacks (> 5 min tolerance)
            if abs(time.time() - float(timestamp)) > 300:
                return False

            signed_payload = f"{timestamp}.".encode("utf-8") + payload_bytes
            computed_sig = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
            return hmac.compare_digest(computed_sig, v1_sig)
        except Exception as e:
            logger.error("Stripe signature validation failed: %s", e)
            return False

    @classmethod
    def fulfill_successful_payment(
        cls,
        payment: Payment,
        provider_payment_id: str = "",
        notes: str = "",
        user = None,
    ) -> bool:
        """
        Fulfills successful payment idempotently:
        1. Transitions Payment -> SUCCESS
        2. Transitions Invoice -> PAID
        3. Credits the Company Wallet (Plan upgrade, renewal, or top-up)
        4. Triggers confirmation email (if configured)
        """
        if payment.status == Payment.Status.SUCCESS:
            logger.info("Payment #%s already fulfilled. Skipping redundant fulfillment.", payment.id)
            return True

        company = payment.company
        invoice = getattr(payment, "invoice", None)

        # Update payment
        payment.status = Payment.Status.SUCCESS
        if provider_payment_id:
            payment.provider_reference = provider_payment_id
        payment.payment_date = timezone.now()
        payment.save(update_fields=["status", "provider_reference", "payment_date"])

        # Update invoice
        if invoice:
            invoice.status = Invoice.Status.PAID
            invoice.paid_date = timezone.now().date()
            invoice.save(update_fields=["status"])

        # Extract metadata from provider_reference or invoice
        ref = payment.provider_reference or ""
        sub = payment.subscription or getattr(company, "subscription", None)

        # Determine fulfillment type
        if "TOPUP" in ref or "credits_pack" in notes:
            # Extract credits pack quantity if available
            credits = 100
            for pack in [1000, 500, 100]:
                if str(pack) in notes or str(pack) in ref:
                    credits = pack
                    break
            CreditWalletService.adjust_credits_admin(
                company=company,
                delta=credits,
                reason=f"Payment Gateway Top-Up ({payment.provider}) - {payment.currency} {payment.amount}",
                admin_user=user,
            )
            logger.info("Fulfilled credit top-up for %s: +%s credits", company.name, credits)

        elif "REN" in ref or "renewal" in notes.lower():
            if sub and sub.plan_tier:
                CreditWalletService.allocate_plan_credits(company, sub.plan_tier, is_renewal=True)
                logger.info("Fulfilled subscription renewal for %s: %s", company.name, sub.plan_tier.name)

        else:
            # Plan Upgrade or default plan allocation
            target_plan = None
            active_plans = SubscriptionPlan.objects.filter(status=SubscriptionPlan.PlanStatus.ACTIVE)
            for p in active_plans:
                if f"-{p.code.upper()}-" in ref.upper() or p.code.lower() in notes.lower() or p.name.lower() in notes.lower():
                    target_plan = p
                    break

            if not target_plan and sub and sub.plan_tier:
                target_plan = sub.plan_tier
            elif not target_plan:
                target_plan = SubscriptionPlan.objects.filter(code="pro").first()

            if target_plan:
                CreditWalletService.allocate_plan_credits(company, target_plan, is_renewal=False)
                logger.info("Fulfilled plan allocation for %s: %s", company.name, target_plan.name)

        if invoice:
            try:
                from apps.billing.services.transactional_email import TransactionalEmailService
                TransactionalEmailService.send_payment_invoice_email(invoice)
            except Exception as e:
                logger.warning("Could not send invoice email for payment #%s: %s", payment.id, e)

        return True

    @classmethod
    def record_payment_failure(cls, payment: Payment, reason: str = ""):
        """Records payment failure and marks associated invoice as draft/overdue."""
        payment.status = Payment.Status.FAILED
        payment.failure_reason = reason
        payment.save(update_fields=["status", "failure_reason"])

        invoice = getattr(payment, "invoice", None)
        if invoice and invoice.status == Invoice.Status.DRAFT:
            invoice.status = Invoice.Status.OVERDUE
            invoice.save(update_fields=["status"])

        logger.warning("Payment #%s marked as FAILED: %s", payment.id, reason)
