import json
import logging
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.utils import timezone

from apps.companies.context_processors import get_user_rbac_context
from apps.billing.models import Payment, Invoice
from apps.billing.services.payment_gateway import PaymentGatewayService

logger = logging.getLogger(__name__)


@login_required
def create_payment_order_api(request):
    """
    Authenticated endpoint for Company Admin to initialize checkout.
    Returns gateway payload (order id, key id, amount in paise/cents, etc.).
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed. Use POST."}, status=405)

    try:
        data = json.loads(request.body) if request.body else request.POST
    except Exception:
        data = request.POST

    action_type = data.get("action", "").strip()
    item_key = data.get("item_key", "").strip()
    provider = data.get("provider", "auto").strip()

    active_comp_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=active_comp_id)
    company = rbac["company"]

    if not company:
        return JsonResponse({"error": "No active company selected."}, status=400)

    if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
        return JsonResponse({"error": "Forbidden: Company Administrator access required."}, status=403)

    try:
        payload = PaymentGatewayService.create_order(
            company=company,
            user=request.user,
            action_type=action_type,
            item_id_or_pack=item_key,
            provider=provider,
        )
        return JsonResponse({"success": True, "order": payload})
    except Exception as e:
        logger.error("Failed to create payment order: %s", e)
        return JsonResponse({"error": str(e)}, status=400)


@login_required
def verify_payment_api(request):
    """
    Client callback endpoint to verify Razorpay / Dev payment and fulfill order.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    try:
        data = json.loads(request.body) if request.body else request.POST
    except Exception:
        data = request.POST

    payment_id = data.get("payment_id")
    provider = data.get("provider", "dev")

    payment = Payment.objects.filter(id=payment_id).first()
    if not payment:
        return JsonResponse({"error": "Payment record not found."}, status=404)

    # Check RBAC
    active_comp_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=active_comp_id)
    if payment.company != rbac["company"] and not rbac["is_super_admin"]:
        return JsonResponse({"error": "Unauthorized access to this payment."}, status=403)

    if provider == "razorpay":
        rp_order_id = data.get("razorpay_order_id", "")
        rp_payment_id = data.get("razorpay_payment_id", "")
        rp_signature = data.get("razorpay_signature", "")

        is_valid = PaymentGatewayService.verify_razorpay_signature(rp_order_id, rp_payment_id, rp_signature)
        if not is_valid:
            PaymentGatewayService.record_payment_failure(payment, "Razorpay signature verification failed")
            return JsonResponse({"error": "Cryptographic payment verification failed."}, status=400)

        PaymentGatewayService.fulfill_successful_payment(
            payment=payment,
            provider_payment_id=rp_payment_id,
            notes=f"Razorpay Order: {rp_order_id}",
            user=request.user,
        )
    else:
        # Dev / instant mode
        PaymentGatewayService.fulfill_successful_payment(
            payment=payment,
            provider_payment_id=f"DEV-PAID-{timezone.now().strftime('%H%M%S')}",
            notes="Instant sandbox confirmation",
            user=request.user,
        )

    return JsonResponse({
        "success": True,
        "message": "Payment verified and credits updated successfully!",
        "redirect_url": "/company/billing/",
    })


@csrf_exempt
def razorpay_webhook_view(request):
    """
    Receives async webhooks from Razorpay servers.
    Verifies webhook signature and executes order fulfillment.
    """
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    webhook_signature = request.headers.get("X-Razorpay-Signature", "")
    webhook_secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "") or getattr(settings, "RAZORPAY_KEY_SECRET", "")

    if webhook_secret and webhook_signature:
        import hmac, hashlib
        expected_sig = hmac.new(
            webhook_secret.encode("utf-8"),
            request.body,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected_sig, webhook_signature):
            logger.warning("Invalid Razorpay webhook signature received.")
            return JsonResponse({"error": "Invalid signature"}, status=400)

    try:
        payload = json.loads(request.body.decode("utf-8"))
        event = payload.get("event")
        entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = entity.get("order_id")
        payment_id = entity.get("id")

        logger.info("Razorpay webhook event: %s for order: %s", event, order_id)

        if event in ("payment.captured", "order.paid"):
            payment = Payment.objects.filter(provider_reference=order_id).first()
            if payment:
                PaymentGatewayService.fulfill_successful_payment(
                    payment=payment,
                    provider_payment_id=payment_id or order_id,
                    notes=f"Razorpay webhook: {event}",
                )

        elif event in ("payment.failed",):
            payment = Payment.objects.filter(provider_reference=order_id).first()
            if payment:
                error_desc = entity.get("error_description", "Payment capture failed")
                PaymentGatewayService.record_payment_failure(payment, error_desc)

        return JsonResponse({"status": "handled"})
    except Exception as e:
        logger.error("Error processing Razorpay webhook: %s", e)
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def stripe_webhook_view(request):
    """
    Receives async webhooks from Stripe servers.
    Verifies stripe signature header and fulfills successful checkout sessions.
    """
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    sig_header = request.headers.get("Stripe-Signature", "")
    is_valid = PaymentGatewayService.verify_stripe_signature(request.body, sig_header)
    if not is_valid:
        logger.warning("Invalid Stripe webhook signature.")
        return JsonResponse({"error": "Invalid signature"}, status=400)

    try:
        payload = json.loads(request.body.decode("utf-8"))
        event_type = payload.get("type")
        data_object = payload.get("data", {}).get("object", {})

        logger.info("Stripe webhook received: %s", event_type)

        if event_type in ("checkout.session.completed", "payment_intent.succeeded"):
            client_ref = data_object.get("client_reference_id") or data_object.get("metadata", {}).get("payment_id")
            payment = None
            if client_ref:
                payment = Payment.objects.filter(id=client_ref).first()
            if not payment:
                tx_id = data_object.get("id")
                payment = Payment.objects.filter(provider_reference=tx_id).first()

            if payment:
                PaymentGatewayService.fulfill_successful_payment(
                    payment=payment,
                    provider_payment_id=data_object.get("id", ""),
                    notes=f"Stripe webhook: {event_type}",
                )

        elif event_type in ("payment_intent.payment_failed", "invoice.payment_failed"):
            client_ref = data_object.get("metadata", {}).get("payment_id")
            if client_ref:
                payment = Payment.objects.filter(id=client_ref).first()
                if payment:
                    PaymentGatewayService.record_payment_failure(payment, "Stripe payment intent failed")

        return JsonResponse({"status": "handled"})
    except Exception as e:
        logger.error("Error processing Stripe webhook: %s", e)
        return JsonResponse({"error": str(e)}, status=500)
