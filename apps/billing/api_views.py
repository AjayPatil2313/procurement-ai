import json
import logging
from decimal import Decimal
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.core.paginator import Paginator
from django.db.models import Q

from apps.companies.context_processors import get_user_rbac_context
from apps.companies.models import Company
from apps.billing.models import (
    CompanyWallet,
    WalletTransaction,
    ServicePricing,
    UsageRecord,
    Invoice,
    Payment,
)
from apps.billing.services.wallet import CompanyWalletService
from apps.billing.services.payment_gateway import PaymentGatewayService

logger = logging.getLogger(__name__)


def _get_rbac_and_company(request):
    """Helper to retrieve active company and user RBAC context."""
    active_comp_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=active_comp_id)
    return rbac, rbac["company"]


@login_required
def company_wallet_api(request):
    """
    GET /api/billing/wallet/
    Returns current company's prepaid currency wallet details.
    """
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed. Use GET."}, status=405)

    rbac, company = _get_rbac_and_company(request)
    if not company:
        return JsonResponse({"error": "No active company selected."}, status=400)

    wallet = CompanyWalletService.get_or_create_wallet(company)

    return JsonResponse({
        "success": True,
        "wallet": {
            "company_id": company.id,
            "company_name": company.name,
            "available_balance": float(wallet.available_balance),
            "reserved_balance": float(wallet.reserved_balance),
            "total_balance": float(wallet.total_balance),
            "total_credited": float(wallet.total_credited),
            "total_spent": float(wallet.total_spent),
            "currency": wallet.currency,
            "status": wallet.status,
            "is_low_balance": wallet.is_low_balance,
            "low_balance_threshold": float(wallet.low_balance_threshold),
            "last_transaction_at": wallet.last_transaction_at.isoformat() if wallet.last_transaction_at else None,
            "is_company_admin": rbac["is_company_admin"] or rbac["is_super_admin"],
        }
    })


@login_required
def wallet_transactions_api(request):
    """
    GET /api/billing/transactions/
    Returns immutable financial ledger transaction history for the company.
    Supports filtering by transaction_type and date range.
    """
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed. Use GET."}, status=405)

    rbac, company = _get_rbac_and_company(request)
    if not company:
        return JsonResponse({"error": "No active company selected."}, status=400)

    # Regular company users can view transactions if permitted, or only admin
    tx_qs = WalletTransaction.objects.filter(company=company).select_related("created_by")

    tx_type = request.GET.get("type", "").strip()
    if tx_type:
        tx_qs = tx_qs.filter(transaction_type=tx_type)

    date_from = request.GET.get("date_from", "").strip()
    if date_from:
        tx_qs = tx_qs.filter(created_at__date__gte=date_from)

    date_to = request.GET.get("date_to", "").strip()
    if date_to:
        tx_qs = tx_qs.filter(created_at__date__lte=date_to)

    page = int(request.GET.get("page", 1))
    page_size = min(int(request.GET.get("page_size", 10)), 100)
    paginator = Paginator(tx_qs, page_size)
    page_obj = paginator.get_page(page)

    results = []
    for tx in page_obj:
        user_name = tx.created_by.get_full_name() or tx.created_by.email if tx.created_by else "System Engine"
        results.append({
            "id": tx.id,
            "reference_id": tx.reference_id,
            "transaction_type": tx.transaction_type,
            "transaction_type_display": tx.get_transaction_type_display(),
            "amount": float(tx.amount),
            "balance_after": float(tx.balance_after),
            "service_code": tx.service_code,
            "description": tx.description,
            "created_by": user_name,
            "created_at": tx.created_at.isoformat(),
        })

    return JsonResponse({
        "success": True,
        "count": paginator.count,
        "total_pages": paginator.num_pages,
        "current_page": page,
        "transactions": results,
    })


@login_required
def wallet_recharge_api(request):
    """
    POST /api/billing/recharge/
    Initializes a prepaid wallet recharge order for the company.
    Restricted to Company Admin and Super Admin.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed. Use POST."}, status=405)

    rbac, company = _get_rbac_and_company(request)
    if not company:
        return JsonResponse({"error": "No active company selected."}, status=400)

    if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
        return JsonResponse({"error": "Forbidden: Company Administrator access required to recharge wallet."}, status=403)

    try:
        data = json.loads(request.body) if request.body else request.POST
    except Exception:
        data = request.POST

    raw_amount = data.get("amount") or data.get("item_key") or "1000"
    provider = data.get("provider", "auto")

    try:
        amount_val = Decimal(str(raw_amount))
        if amount_val < Decimal("100.00"):
            return JsonResponse({"error": "Minimum recharge amount is ₹100.00."}, status=400)
    except Exception:
        return JsonResponse({"error": "Invalid amount specified."}, status=400)

    try:
        order_payload = PaymentGatewayService.create_order(
            company=company,
            user=request.user,
            action_type="recharge_wallet",
            item_id_or_pack=str(amount_val),
            provider=provider,
        )
        return JsonResponse({"success": True, "order": order_payload})
    except Exception as e:
        logger.error("Failed to initiate wallet recharge: %s", e)
        return JsonResponse({"error": str(e)}, status=400)


@login_required
def usage_history_api(request):
    """
    GET /api/billing/usage/
    Returns granular AI search usage history.
    Company Admin sees all company usage; Company User sees their own usage.
    """
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed. Use GET."}, status=405)

    rbac, company = _get_rbac_and_company(request)
    if not company:
        return JsonResponse({"error": "No active company selected."}, status=400)

    qs = UsageRecord.objects.filter(company=company).select_related("user", "search_job")

    # If regular company user, restrict to personal usage
    if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
        qs = qs.filter(user=request.user)

    feature = request.GET.get("feature", "").strip()
    if feature:
        qs = qs.filter(feature_code=feature)

    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        qs = qs.filter(status=status_filter)

    page = int(request.GET.get("page", 1))
    page_size = min(int(request.GET.get("page_size", 10)), 100)
    paginator = Paginator(qs, page_size)
    page_obj = paginator.get_page(page)

    records = []
    for r in page_obj:
        user_name = r.user.get_full_name() or r.user.email if r.user else "System"
        records.append({
            "id": r.id,
            "feature_code": r.feature_code,
            "feature_name": r.feature_code.replace("_", " ").title(),
            "usage_quantity": r.usage_quantity,
            "amount_charged": float(r.amount_charged),
            "credits_charged": r.credits_charged,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "user": user_name,
            "failure_reason": r.failure_reason,
            "search_job_id": r.search_job_id,
        })

    return JsonResponse({
        "success": True,
        "count": paginator.count,
        "total_pages": paginator.num_pages,
        "current_page": page,
        "usage_records": records,
    })


@login_required
def company_invoices_api(request):
    """
    GET /api/billing/invoices/
    Returns company tax invoices and payment receipts.
    """
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed. Use GET."}, status=405)

    rbac, company = _get_rbac_and_company(request)
    if not company:
        return JsonResponse({"error": "No active company selected."}, status=400)

    invoices = Invoice.objects.filter(company=company).order_by("-created_at")[:50]
    results = []
    for inv in invoices:
        results.append({
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "subtotal": float(inv.subtotal),
            "tax": float(inv.tax),
            "total_amount": float(inv.total_amount),
            "currency": inv.currency,
            "status": inv.status,
            "issue_date": inv.issue_date.isoformat() if inv.issue_date else None,
            "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
            "pdf_url": f"/company/invoices/{inv.id}/pdf/",
        })

    return JsonResponse({"success": True, "invoices": results})


@login_required
def service_pricing_api(request):
    """
    GET /api/billing/pricing/
    Returns active pricing rate card for all AI procurement and sales services.
    """
    CompanyWalletService.ensure_default_service_pricing()
    pricing_list = ServicePricing.objects.filter(is_active=True).order_by("service_code")

    rates = []
    for p in pricing_list:
        rates.append({
            "service_code": p.service_code,
            "display_name": p.display_name,
            "price": float(p.price),
            "currency": p.currency,
            "charging_unit": p.charging_unit,
            "charging_unit_display": p.get_charging_unit_display(),
            "description": p.description,
        })

    return JsonResponse({"success": True, "rates": rates})


# =========================================================================
# SUPER ADMIN BILLING MANAGEMENT APIS
# =========================================================================

def _require_super_admin(user):
    """Verifies that the user has Super Admin platform privileges."""
    return user.is_authenticated and (user.is_superuser or user.is_staff)


@login_required
def admin_wallets_list_api(request):
    """
    GET /api/admin/billing/wallets/
    Super Admin view of all companies and their prepaid wallet balances.
    """
    if not _require_super_admin(request.user):
        return JsonResponse({"error": "Forbidden: Super Administrator access required."}, status=403)

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

    companies = Company.objects.filter(is_deleted=False).select_related("wallet").order_by("name")
    if q:
        companies = companies.filter(Q(name__icontains=q) | Q(country__icontains=q))

    wallets_data = []
    for comp in companies:
        wallet = CompanyWalletService.get_or_create_wallet(comp)
        if status_filter and wallet.status != status_filter:
            continue
        wallets_data.append({
            "company_id": comp.id,
            "company_name": comp.name,
            "company_type": comp.company_type,
            "available_balance": float(wallet.available_balance),
            "reserved_balance": float(wallet.reserved_balance),
            "total_credited": float(wallet.total_credited),
            "total_spent": float(wallet.total_spent),
            "currency": wallet.currency,
            "status": wallet.status,
            "is_low_balance": wallet.is_low_balance,
            "last_transaction_at": wallet.last_transaction_at.isoformat() if wallet.last_transaction_at else None,
        })

    return JsonResponse({
        "success": True,
        "count": len(wallets_data),
        "wallets": wallets_data,
    })


@login_required
def admin_manual_adjustment_api(request):
    """
    POST /api/admin/billing/adjustments/
    Authorized manual wallet balance adjustment with mandatory audit justification.
    """
    if not _require_super_admin(request.user):
        return JsonResponse({"error": "Forbidden: Super Administrator access required."}, status=403)

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed. Use POST."}, status=405)

    try:
        data = json.loads(request.body) if request.body else request.POST
    except Exception:
        data = request.POST

    company_id = data.get("company_id")
    raw_delta = data.get("amount") or data.get("delta")
    reason = str(data.get("reason", "")).strip()

    if not company_id:
        return JsonResponse({"error": "company_id is required."}, status=400)

    if not reason:
        return JsonResponse({"error": "A clear, auditable justification reason is mandatory for manual adjustments."}, status=400)

    try:
        delta = Decimal(str(raw_delta))
        if delta == Decimal("0.00"):
            return JsonResponse({"error": "Adjustment amount must be non-zero."}, status=400)
    except Exception:
        return JsonResponse({"error": "Invalid adjustment amount specified."}, status=400)

    company = Company.objects.filter(pk=company_id).first()
    if not company:
        return JsonResponse({"error": "Target company not found."}, status=404)

    try:
        tx = CompanyWalletService.adjust_balance_admin(
            company=company,
            delta_amount=delta,
            reason=reason,
            admin_user=request.user,
        )
        wallet = CompanyWalletService.get_or_create_wallet(company)
        return JsonResponse({
            "success": True,
            "message": f"Successfully adjusted wallet for {company.name} by ₹{delta:.2f}.",
            "transaction_id": tx.id,
            "reference_id": tx.reference_id,
            "new_available_balance": float(wallet.available_balance),
        })
    except ValueError as ve:
        return JsonResponse({"error": str(ve)}, status=400)
    except Exception as e:
        logger.error("Admin adjustment failed: %s", e)
        return JsonResponse({"error": f"Adjustment failed: {str(e)}"}, status=500)


@login_required
def admin_service_pricing_config_api(request):
    """
    GET / POST /api/admin/billing/pricing/
    Super Admin endpoint to view and update service pricing rate card.
    """
    if not _require_super_admin(request.user):
        return JsonResponse({"error": "Forbidden: Super Administrator access required."}, status=403)

    CompanyWalletService.ensure_default_service_pricing()

    if request.method == "POST":
        try:
            data = json.loads(request.body) if request.body else request.POST
        except Exception:
            data = request.POST

        service_code = str(data.get("service_code", "")).strip()
        raw_price = data.get("price")

        if not service_code:
            return JsonResponse({"error": "service_code is required."}, status=400)

        try:
            price_val = Decimal(str(raw_price))
            if price_val < Decimal("0.00"):
                return JsonResponse({"error": "Price cannot be negative."}, status=400)
        except Exception:
            return JsonResponse({"error": "Invalid price specified."}, status=400)

        rule = ServicePricing.objects.filter(service_code=service_code).first()
        if not rule:
            return JsonResponse({"error": f"Service pricing for '{service_code}' not found."}, status=404)

        rule.price = price_val
        rule.updated_by = request.user
        rule.save(update_fields=["price", "updated_by", "updated_at"])

        # Invalidate cache
        from django.core.cache import cache
        cache.delete(f"srv_price:{service_code}")

        return JsonResponse({
            "success": True,
            "message": f"Updated {rule.display_name} rate to ₹{rule.price:.2f}.",
            "service_code": rule.service_code,
            "price": float(rule.price),
        })

    # GET
    pricing_list = ServicePricing.objects.all().order_by("service_code")
    rates = []
    for p in pricing_list:
        rates.append({
            "service_code": p.service_code,
            "display_name": p.display_name,
            "price": float(p.price),
            "currency": p.currency,
            "charging_unit": p.charging_unit,
            "charging_unit_display": p.get_charging_unit_display(),
            "is_active": p.is_active,
            "description": p.description,
            "updated_at": p.updated_at.isoformat(),
        })

    return JsonResponse({"success": True, "pricing_catalog": rates})
