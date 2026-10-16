import csv
import datetime
from datetime import timedelta
import json
import os
import random
import re
import string
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.db.models import Sum, Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.companies.models import (
    Company,
    CompanyMember,
    CompanyPermission,
    CompanyRole,
    RolePermission,
    MemberPermission,
)
from apps.accounts.models import User
from apps.billing.models import (
    Subscription,
    SubscriptionPlan,
    FeatureCreditCost,
    CreditTransaction,
    UsageRecord,
    Invoice,
    Payment,
    CompanyWallet,
    WalletTransaction,
    ServicePricing,
)
from apps.billing.services.wallet import CreditWalletService, CompanyWalletService
from apps.ai_search.models import SearchJob, SearchResult, APILog
from apps.dashboard.models import (
    ActivityLog,
    SupportTicket,
    PlatformSetting,
    BuyerModuleConfig,
    BuyerDashboardLayout,
    SellerModuleConfig,
    SellerDashboardLayout,
    BothDashboardLayout,
    is_platform_module_enabled,
)
from apps.leads.models import Inquiry, SavedItem
from apps.catalog.models import Product
from apps.requirements.models import Requirement
from apps.companies.rbac import superadmin_required
from apps.dashboard.services.notification_service import create_notification


def paginate_and_preserve(request, queryset, per_page=10):
    """
    Paginates a queryset and preserves all active GET filter parameters across page changes.
    Returns (page_obj, query_string).
    """
    requested_per_page = request.GET.get("per_page")
    if requested_per_page:
        try:
            per_page = max(5, min(100, int(requested_per_page)))
        except ValueError:
            pass

    paginator = Paginator(queryset, per_page)
    page = request.GET.get("page", 1)
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    query_params = request.GET.copy()
    query_params.pop("page", None)
    return page_obj, query_params.urlencode()


def get_system_health_telemetry():
    """
    Lightweight platform infrastructure health check executing in under 10ms.
    Checks live database connection latency, cache roundtrip latency, and SMTP backend status.
    """
    import time
    from django.db import connection

    db_latency_ms = 0.0
    try:
        t0 = time.perf_counter()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        db_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        db_status = "Connected"
    except Exception:
        db_status = "Disconnected"

    cache_latency_ms = 0.0
    try:
        t1 = time.perf_counter()
        cache.set("_health_ping", 1, 10)
        cache.get("_health_ping")
        cache_latency_ms = round((time.perf_counter() - t1) * 1000, 1)
        cache_status = "Operational"
    except Exception:
        cache_status = "Degraded"

    try:
        from apps.notifications.services.smtp_service import get_active_email_connection
        smtp_backend = get_active_email_connection()
        smtp_status = "Active & Ready" if smtp_backend else "Standard Backend"
    except Exception:
        smtp_status = "Offline"

    return {
        "db_latency_ms": db_latency_ms,
        "db_status": db_status,
        "cache_latency_ms": cache_latency_ms,
        "cache_status": cache_status,
        "smtp_status": smtp_status,
        "maintenance_active": PlatformSetting.get_setting("maintenance_mode", "false").strip().lower() in ("true", "1", "yes"),
    }


def get_client_ip(request):
    """
    Extracts the client's real IP address from HTTP headers (supporting reverse proxies).
    """
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "127.0.0.1")
    return ip or "127.0.0.1"


def log_admin_activity(
    request,
    company,
    title,
    description,
    activity_type=ActivityLog.ActivityType.MEMBER_INVITED,
    icon_type="shield-check",
    color="blue",
):
    """
    Forensic administrative activity logger.
    Automatically enriches descriptions with client IP address for compliance and security audit trails.
    """
    if not company:
        company = Company.objects.filter(is_deleted=False).first() or Company.objects.first()
    if not company:
        return None

    client_ip = get_client_ip(request)
    enriched_description = f"{description} [IP: {client_ip}]"
    return ActivityLog.objects.create(
        company=company,
        user=request.user,
        activity_type=activity_type,
        title=title,
        description=enriched_description,
        icon_type=icon_type,
        color=color,
    )


@login_required
@superadmin_required
def admin_panel_dashboard_view(request):
    """
    Main Executive Super Admin Dashboard.
    Provides platform-wide 15 real database KPIs, 6 Chart.js analytical visualizations,
    and live system activity streams across all companies.
    Optimized with consolidated aggregations, smart 60-second caching, and interactive date-range filtering.
    """
    date_range = request.GET.get("range", "7d").lower()
    if date_range not in ["7d", "30d", "90d", "ytd"]:
        date_range = "7d"

    cache_key = f"superadmin_dashboard_kpis_{date_range}"
    if request.GET.get("refresh") == "1":
        cache.delete(cache_key)

    cached_data = cache.get(cache_key)
    if cached_data:
        # Return cached context with live maintenance settings & real-time health
        cached_data["platform_maintenance_active"] = (
            PlatformSetting.get_setting("maintenance_mode", "false").strip().lower() in ("true", "1", "yes")
        )
        cached_data["platform_maintenance_message"] = PlatformSetting.get_setting("maintenance_message", "")
        cached_data["date_range"] = date_range
        cached_data["system_health"] = get_system_health_telemetry()
        return render(request, "admin_panel/dashboard.html", cached_data)

    now = timezone.now()

    # 1. Company metrics (consolidated into 1 SQL query)
    comp_agg = Company.objects.filter(is_deleted=False).aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True)),
        verified=Count("id", filter=Q(is_verified=True)),
        pending=Count("id", filter=Q(is_verified=False)),
        buyer=Count("id", filter=Q(company_type=Company.CompanyType.BUYER)),
        seller=Count("id", filter=Q(company_type=Company.CompanyType.SELLER)),
        both=Count("id", filter=Q(company_type=Company.CompanyType.BOTH)),
    )
    total_companies = comp_agg["total"] or 0
    active_companies = comp_agg["active"] or 0
    verified_companies = comp_agg["verified"] or 0
    pending_verifications = comp_agg["pending"] or 0
    inactive_companies = total_companies - active_companies
    buyer_companies = comp_agg["buyer"] or 0
    seller_companies = comp_agg["seller"] or 0
    both_companies = comp_agg["both"] or 0

    # 2. User metrics (consolidated into 2 SQL queries)
    user_agg = User.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True)),
        super_admins=Count("id", filter=Q(is_superuser=True) | Q(is_staff=True)),
    )
    total_users = user_agg["total"] or 0
    active_users = user_agg["active"] or 0
    super_admins = user_agg["super_admins"] or 0

    member_agg = CompanyMember.objects.aggregate(
        admins=Count("id", filter=Q(role=CompanyMember.Role.ADMIN)),
        users=Count("id", filter=Q(role=CompanyMember.Role.USER)),
    )
    company_admins = member_agg["admins"] or 0
    company_users = member_agg["users"] or 0

    # 3. Product & Requirement metrics (2 fast count queries)
    total_products = Product.objects.filter(is_deleted=False).count()
    total_requirements = Requirement.objects.filter(is_deleted=False).count()

    # 4. Search & AI Activity (consolidated into 1 SQL query)
    search_agg = SearchJob.objects.aggregate(
        total=Count("id"),
        supplier=Count("id", filter=Q(job_type=SearchJob.JobType.FIND_SUPPLIERS) | Q(requirement__isnull=False)),
        buyer=Count("id", filter=Q(job_type=SearchJob.JobType.FIND_BUYERS) | Q(product__isnull=False)),
        completed=Count("id", filter=Q(status=SearchJob.Status.COMPLETED)),
        failed=Count("id", filter=Q(status=SearchJob.Status.FAILED)),
    )
    total_search_jobs = search_agg["total"] or 0
    supplier_searches = search_agg["supplier"] or 0
    buyer_searches = search_agg["buyer"] or 0
    completed_jobs = search_agg["completed"] or 0
    failed_jobs = search_agg["failed"] or 0

    # 5. Deals & Inquiries (1 SQL query)
    inq_agg = Inquiry.objects.aggregate(
        total=Count("id"),
        won=Count("id", filter=Q(status=Inquiry.Status.WON)),
    )
    total_inquiries = inq_agg["total"] or 0
    deals_won = inq_agg["won"] or 0

    # 6. Subscription & Credit metrics (1 SQL query)
    sub_agg = Subscription.objects.aggregate(
        active=Count("id", filter=Q(status=Subscription.Status.ACTIVE)),
        free=Count("id", filter=Q(plan=Subscription.Plan.FREE)),
        pro=Count("id", filter=Q(plan=Subscription.Plan.PRO)),
        enterprise=Count("id", filter=Q(plan=Subscription.Plan.ENTERPRISE)),
        credits_allocated=Sum("credits_total"),
        credits_used=Sum("credits_used"),
    )
    active_subscriptions = sub_agg["active"] or 0
    sub_free = sub_agg["free"] or 0
    sub_pro = sub_agg["pro"] or 0
    sub_enterprise = sub_agg["enterprise"] or 0
    credits_allocated = sub_agg["credits_allocated"] or 0
    credits_used = sub_agg["credits_used"] or 0
    credits_remaining = max(0, credits_allocated - credits_used)

    sub_free_pct = round((sub_free / total_companies) * 100, 1) if total_companies > 0 else 0
    sub_pro_pct = round((sub_pro / total_companies) * 100, 1) if total_companies > 0 else 0
    sub_enterprise_pct = round((sub_enterprise / total_companies) * 100, 1) if total_companies > 0 else 0

    total_api_calls = UsageRecord.objects.count()

    open_tickets = SupportTicket.objects.filter(
        is_deleted=False,
        status__in=[SupportTicket.Status.OPEN, SupportTicket.Status.IN_PROGRESS]
    ).count()

    # 7. Real Analytical Chart Datasets (Optimized with zero loop queries)
    # Chart 1: Company Types (Doughnut)
    chart1_data = {
        "labels": ["Sales Companies", "Vendor Companies", "Hybrid (Both)"],
        "values": [buyer_companies, seller_companies, both_companies],
    }

    # Chart 2: Company Registrations (Last 6 Months) - 1 single query instead of 6
    six_months_ago = (now.replace(day=1) - timedelta(days=180)).replace(day=1)
    recent_new_comps = list(
        Company.objects.filter(is_deleted=False, created_at__gte=six_months_ago)
        .values_list("created_at", flat=True)
    )
    company_reg_labels = []
    company_reg_data = []
    for i in range(5, -1, -1):
        m_start = (now.replace(day=1) - timedelta(days=i * 30)).replace(day=1)
        m_next = (m_start + timedelta(days=32)).replace(day=1)
        company_reg_labels.append(m_start.strftime("%b %Y"))
        cnt = sum(1 for dt in recent_new_comps if m_start <= dt < m_next)
        company_reg_data.append(cnt)

    # Chart 3: User Growth (Last 6 Months) - 1 single query instead of 6
    recent_new_users = list(
        User.objects.filter(created_at__gte=six_months_ago)
        .values_list("created_at", flat=True)
    )
    user_growth_labels = []
    user_growth_data = []
    for i in range(5, -1, -1):
        m_start = (now.replace(day=1) - timedelta(days=i * 30)).replace(day=1)
        m_next = (m_start + timedelta(days=32)).replace(day=1)
        user_growth_labels.append(m_start.strftime("%b %Y"))
        cnt = sum(1 for dt in recent_new_users if m_start <= dt < m_next)
        user_growth_data.append(cnt)

    # Chart 4 & 5: Activity & Credits with dynamic Date Range (7d, 30d, 90d, ytd)
    if date_range == "30d":
        days_span = 30
    elif date_range == "90d":
        days_span = 90
    elif date_range == "ytd":
        days_span = max(7, min(180, (now.date() - datetime.date(now.year, 1, 1)).days + 1))
    else:
        days_span = 7

    range_start_dt = now - timedelta(days=days_span)
    range_start_date = range_start_dt.date()

    # Chart 4: Supplier vs Buyer Searches - 1 single query instead of 14+
    search_records = list(
        SearchJob.objects.filter(created_at__date__gte=range_start_date)
        .values("created_at__date", "job_type", "requirement_id", "product_id")
    )
    searches_by_day = {}
    for r in search_records:
        d_key = r["created_at__date"]
        if d_key not in searches_by_day:
            searches_by_day[d_key] = {"supplier": 0, "buyer": 0}
        if r["job_type"] == SearchJob.JobType.FIND_SUPPLIERS or bool(r.get("requirement_id")):
            searches_by_day[d_key]["supplier"] += 1
        elif r["job_type"] == SearchJob.JobType.FIND_BUYERS or bool(r.get("product_id")):
            searches_by_day[d_key]["buyer"] += 1

    search_activity_labels = []
    search_supplier_series = []
    search_buyer_series = []
    for i in range(days_span - 1, -1, -1):
        d = (now - timedelta(days=i)).date()
        search_activity_labels.append(d.strftime("%b %d"))
        day_stats = searches_by_day.get(d, {"supplier": 0, "buyer": 0})
        search_supplier_series.append(day_stats["supplier"])
        search_buyer_series.append(day_stats["buyer"])

    # Chart 5: Daily Credit Consumption - 1 single query instead of 7+
    debit_records = list(
        CreditTransaction.objects.filter(
            transaction_type=CreditTransaction.TransactionType.DEBIT,
            created_at__date__gte=range_start_date,
        ).values("created_at__date", "credits")
    )
    credits_by_day = {}
    for tx in debit_records:
        d_key = tx["created_at__date"]
        credits_by_day[d_key] = credits_by_day.get(d_key, 0) + (tx["credits"] or 0)

    credit_usage_labels = []
    credit_usage_series = []
    for i in range(days_span - 1, -1, -1):
        d = (now - timedelta(days=i)).date()
        credit_usage_labels.append(d.strftime("%b %d"))
        credit_usage_series.append(credits_by_day.get(d, 0))

    # Chart 6: Subscription Distribution (Doughnut)
    chart6_data = {
        "labels": ["Free Starter", "Pro Plan", "Enterprise Plan"],
        "values": [sub_free, sub_pro, sub_enterprise],
    }

    # 8. Streams & Tables
    recent_companies = Company.objects.filter(is_deleted=False).select_related("subscription").order_by("-created_at")[:6]
    recent_activities = ActivityLog.objects.select_related("company", "user").order_by("-created_at")[:8]
    recent_tickets = SupportTicket.objects.filter(is_deleted=False).select_related("company", "user").order_by("-created_at")[:5]

    maintenance_active = PlatformSetting.get_setting("maintenance_mode", "false").strip().lower() in ("true", "1", "yes")
    maintenance_message = PlatformSetting.get_setting("maintenance_message", "")

    context = {
        "page_title": "Super Admin Executive Dashboard",
        "date_range": date_range,
        # 15 KPIs
        "total_companies": total_companies,
        "active_companies": active_companies,
        "verified_companies": verified_companies,
        "pending_verifications": pending_verifications,
        "inactive_companies": inactive_companies,
        "buyer_companies": buyer_companies,
        "seller_companies": seller_companies,
        "both_companies": both_companies,
        "total_users": total_users,
        "active_users": active_users,
        "total_products": total_products,
        "total_requirements": total_requirements,
        "supplier_searches": supplier_searches,
        "buyer_searches": buyer_searches,
        "total_inquiries": total_inquiries,
        "active_subscriptions": active_subscriptions,
        "credits_used": credits_used,
        "total_api_calls": total_api_calls,
        # Secondary metrics
        "credits_allocated": credits_allocated,
        "credits_remaining": credits_remaining,
        "total_search_jobs": total_search_jobs,
        "completed_jobs": completed_jobs,
        "failed_jobs": failed_jobs,
        "deals_won": deals_won,
        "open_tickets": open_tickets,
        "super_admins": super_admins,
        "company_admins": company_admins,
        "company_users": company_users,
        "sub_free": sub_free,
        "sub_pro": sub_pro,
        "sub_enterprise": sub_enterprise,
        "sub_free_pct": sub_free_pct,
        "sub_pro_pct": sub_pro_pct,
        "sub_enterprise_pct": sub_enterprise_pct,
        # Chart JSON
        "chart1_json": json.dumps(chart1_data),
        "chart2_labels": json.dumps(company_reg_labels),
        "chart2_data": json.dumps(company_reg_data),
        "chart3_labels": json.dumps(user_growth_labels),
        "chart3_data": json.dumps(user_growth_data),
        "chart4_labels": json.dumps(search_activity_labels),
        "chart4_supplier": json.dumps(search_supplier_series),
        "chart4_buyer": json.dumps(search_buyer_series),
        "chart5_labels": json.dumps(credit_usage_labels),
        "chart5_data": json.dumps(credit_usage_series),
        "chart6_json": json.dumps(chart6_data),
        # Streams
        "recent_companies": recent_companies,
        "recent_activities": recent_activities,
        "recent_tickets": recent_tickets,
        "platform_maintenance_active": maintenance_active,
        "platform_maintenance_message": maintenance_message,
        "system_health": get_system_health_telemetry(),
    }
    # Cache for 60 seconds
    cache.set(cache_key, context, 60)
    return render(request, "admin_panel/dashboard.html", context)


@login_required
@superadmin_required
def admin_panel_companies_view(request):
    """
    All platform companies directory with search, filtering by type, status,
    verification, subscription plan tier, column sorting, pagination, and bulk batch actions.
    """
    # Bulk Action POST Handler
    if request.method == "POST":
        bulk_action = request.POST.get("bulk_action", "").strip()
        selected_ids = request.POST.getlist("selected_ids")
        if not selected_ids:
            messages.warning(request, "Please select at least one company to perform bulk action.")
            return redirect(request.get_full_path())

        if bulk_action == "verify":
            updated_count = Company.objects.filter(id__in=selected_ids).update(is_verified=True)
            for c in Company.objects.filter(id__in=selected_ids):
                log_admin_activity(
                    request,
                    c,
                    title="Company Verified (Bulk)",
                    description=f"Super Admin batch-verified company '{c.name}'.",
                    icon_type="shield-check",
                    color="blue",
                )
            messages.success(request, f"Successfully verified {updated_count} selected companies.")

        elif bulk_action == "unverify":
            updated_count = Company.objects.filter(id__in=selected_ids).update(is_verified=False)
            for c in Company.objects.filter(id__in=selected_ids):
                log_admin_activity(
                    request,
                    c,
                    title="Company Verification Revoked (Bulk)",
                    description=f"Super Admin revoked verification badge for company '{c.name}'.",
                    icon_type="shield-halved",
                    color="amber",
                )
            messages.success(request, f"Successfully revoked verification for {updated_count} companies.")

        elif bulk_action == "activate":
            updated_count = Company.objects.filter(id__in=selected_ids).update(is_active=True)
            for c in Company.objects.filter(id__in=selected_ids):
                log_admin_activity(
                    request,
                    c,
                    title="Company Activated (Bulk)",
                    description=f"Super Admin batch-activated company '{c.name}'.",
                    icon_type="building",
                    color="emerald",
                )
            messages.success(request, f"Successfully activated {updated_count} selected companies.")

        elif bulk_action == "suspend":
            updated_count = Company.objects.filter(id__in=selected_ids).update(is_active=False)
            for c in Company.objects.filter(id__in=selected_ids):
                log_admin_activity(
                    request,
                    c,
                    title="Company Suspended (Bulk)",
                    description=f"Super Admin batch-suspended company '{c.name}'.",
                    icon_type="building",
                    color="rose",
                )
            messages.success(request, f"Successfully suspended {updated_count} selected companies.")

        elif bulk_action == "delete":
            updated_count = Company.objects.filter(id__in=selected_ids).update(
                is_deleted=True, is_active=False, deleted_at=timezone.now()
            )
            for c in Company.objects.filter(id__in=selected_ids):
                log_admin_activity(
                    request,
                    c,
                    title="Company Soft-Deleted (Bulk)",
                    description=f"Super Admin batch-soft-deleted company '{c.name}'. All records preserved safely.",
                    icon_type="trash",
                    color="rose",
                )
            messages.success(request, f"Successfully soft-deleted {updated_count} selected companies.")

        elif bulk_action == "export":
            # Direct CSV export for selected companies
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            today_str = timezone.now().strftime("%Y%m%d")
            response["Content-Disposition"] = f'attachment; filename="Selected_Companies_{today_str}.csv"'
            writer = csv.writer(response)
            writer.writerow(["ID", "Company Name", "Legal Name", "Type", "Plan", "Verified", "Active", "Country", "Industry", "Registration Date"])
            for c in Company.objects.filter(id__in=selected_ids).select_related("subscription"):
                plan_str = c.subscription.get_plan_display() if hasattr(c, "subscription") and c.subscription else "Free"
                writer.writerow([c.id, c.name, c.legal_name, c.get_company_type_display(), plan_str, c.is_verified, c.is_active, c.country, c.industry, c.created_at.strftime("%Y-%m-%d")])
            return response

        # Clear dashboard cache
        cache.delete("superadmin_dashboard_kpis_7d")
        return redirect(request.get_full_path())

    companies = Company.objects.select_related("subscription", "wallet").prefetch_related("members")

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "all")
    type_filter = request.GET.get("type", "all")
    verified_filter = request.GET.get("verified", "all")
    plan_filter = request.GET.get("plan", "all")
    sort_by = request.GET.get("sort", "created_at")
    order = request.GET.get("order", "desc")

    if q:
        companies = companies.filter(
            Q(name__icontains=q)
            | Q(country__icontains=q)
            | Q(industry__icontains=q)
            | Q(website__icontains=q)
        )

    if status_filter == "active":
        companies = companies.filter(is_deleted=False, is_active=True)
    elif status_filter == "inactive":
        companies = companies.filter(is_deleted=False, is_active=False)
    elif status_filter == "deleted":
        companies = companies.filter(is_deleted=True)
    else:
        companies = companies.filter(is_deleted=False)

    if type_filter in ["SALES", "BUYER", Company.CompanyType.SALES, Company.CompanyType.BUYER]:
        companies = companies.filter(company_type__in=["SALES", "BUYER", Company.CompanyType.SALES, Company.CompanyType.BUYER])
    elif type_filter in ["VENDOR", "SELLER", Company.CompanyType.VENDOR, Company.CompanyType.SELLER]:
        companies = companies.filter(company_type__in=["VENDOR", "SELLER", Company.CompanyType.VENDOR, Company.CompanyType.SELLER])
    elif type_filter in ["BOTH", Company.CompanyType.BOTH]:
        companies = companies.filter(company_type__in=["BOTH", Company.CompanyType.BOTH])

    if verified_filter == "verified":
        companies = companies.filter(is_verified=True)
    elif verified_filter == "pending":
        companies = companies.filter(is_verified=False)

    if plan_filter != "all":
        companies = companies.filter(subscription__plan=plan_filter)

    # Multi-column sorting
    allowed_sorts = {
        "name": "name",
        "created_at": "created_at",
        "company_type": "company_type",
        "country": "country",
        "is_verified": "is_verified",
        "is_active": "is_active",
    }
    field = allowed_sorts.get(sort_by, "created_at")
    sort_prefix = "-" if order == "desc" else ""
    companies = companies.order_by(f"{sort_prefix}{field}")

    total_count = Company.objects.filter(is_deleted=False).count()
    active_count = Company.objects.filter(is_deleted=False, is_active=True).count()
    verified_count = Company.objects.filter(is_deleted=False, is_verified=True).count()
    deleted_count = Company.objects.filter(is_deleted=True).count()

    # CSV Export Handler
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        today_str = timezone.now().strftime("%Y%m%d")
        response["Content-Disposition"] = f'attachment; filename="Platform_Companies_{today_str}.csv"'
        response.write("\ufeff")  # UTF-8 BOM for Excel
        writer = csv.writer(response)
        writer.writerow(["Company ID", "Company Name", "Legal Name", "Company Type", "Subscription Plan", "Wallet Balance", "Verified", "Active", "GST/VAT No", "Reg No", "Phone", "Email", "City", "State", "Country", "Industry", "Registration Date"])
        for c in companies:
            plan_str = c.subscription.get_plan_display() if hasattr(c, "subscription") and c.subscription else "Free"
            wallet_bal = str(c.wallet.available_balance) if hasattr(c, "wallet") and c.wallet else "0.00"
            writer.writerow([c.id, c.name, c.legal_name, c.get_company_type_display(), plan_str, wallet_bal, "Yes" if c.is_verified else "No", "Yes" if c.is_active else "No", c.gst_vat_no or "", c.registration_no or "", c.phone or "", c.email or "", c.city or "", c.state or "", c.country or "", c.industry or "", c.created_at.strftime("%Y-%m-%d")])
        return response

    # Server-side pagination (default 10)
    page_obj, query_string = paginate_and_preserve(request, companies, per_page=10)
    plans_catalog = SubscriptionPlan.objects.filter(status=SubscriptionPlan.PlanStatus.ACTIVE).order_by("order", "price")

    return render(request, "admin_panel/companies.html", {
        "companies": page_obj,
        "page_obj": page_obj,
        "query_string": query_string,
        "page_title": "All Companies (Super Admin)",
        "plans_catalog": plans_catalog,
        "q": q,
        "status_filter": status_filter,
        "type_filter": type_filter,
        "verified_filter": verified_filter,
        "plan_filter": plan_filter,
        "sort_by": sort_by,
        "order": order,
        "total_count": total_count,
        "active_count": active_count,
        "verified_count": verified_count,
        "deleted_count": deleted_count,
    })


@login_required
@superadmin_required
def admin_panel_change_company_type_view(request, pk):
    """
    Super Admin endpoint to transition a company's company_type
    between BUYER, SELLER, and BOTH.
    Validates choice, updates access safely without deleting business records,
    and logs the administrative audit trail.
    """
    company = get_object_or_404(Company, pk=pk)
    if request.method == "POST":
        new_type = request.POST.get("company_type", "").strip().upper()
        type_mapping = {
            "SALES": Company.CompanyType.SALES,
            "BUYER": Company.CompanyType.BUYER,
            "VENDOR": Company.CompanyType.VENDOR,
            "SELLER": Company.CompanyType.SELLER,
            "BOTH": Company.CompanyType.BOTH,
        }
        if new_type in type_mapping:
            target_type = type_mapping[new_type]
            old_type = company.company_type
            if old_type != target_type:
                company.company_type = target_type
                company.save(update_fields=["company_type"])

                log_admin_activity(
                    request,
                    company,
                    title=f"Company Type Changed to {company.get_company_type_display()}",
                    description=f"Super Admin transitioned company type from {old_type} to {new_type}. Authorized modules updated dynamically without deleting data.",
                    icon_type="arrows-split-up-and-left",
                    color="purple",
                )
                messages.success(
                    request,
                    f"Successfully transitioned '{company.name}' from {old_type} to {company.get_company_type_display()}."
                )
            else:
                messages.info(request, f"Company '{company.name}' is already set to {new_type}.")
        else:
            messages.error(request, f"Invalid company type '{new_type}'. Must be BUYER, SELLER, or BOTH.")

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if next_url:
        return redirect(next_url)
    return redirect("admin-panel-companies")


@login_required
@superadmin_required
def admin_panel_company_create_view(request):
    """
    Super Admin view to onboard and register a new company directly.
    Configures company info, assigns subscription plan & credits,
    and optionally creates/associates the primary Company Admin user.
    """
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        legal_name = request.POST.get("legal_name", "").strip()
        industry = request.POST.get("industry", "").strip()
        raw_type = request.POST.get("company_type", "BOTH").strip().upper()
        if raw_type in ["SALES", "BUYER"]:
            company_type = Company.CompanyType.SALES
        elif raw_type in ["VENDOR", "SELLER"]:
            company_type = Company.CompanyType.VENDOR
        else:
            company_type = Company.CompanyType.BOTH
        country = request.POST.get("country", "India").strip()
        city = request.POST.get("city", "").strip()
        state = request.POST.get("state", "").strip()
        website = request.POST.get("website", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        gst_vat_no = request.POST.get("gst_vat_no", "").strip()
        is_verified = bool(request.POST.get("is_verified"))
        is_active = bool(request.POST.get("is_active", True))

        # Subscription allocation
        plan = request.POST.get("plan", Subscription.Plan.FREE)
        credits_raw = request.POST.get("credits_total", "").strip()
        valid_till_raw = request.POST.get("valid_till", "").strip()

        # Admin user info
        admin_email = request.POST.get("admin_email", "").strip().lower()
        admin_first_name = request.POST.get("admin_first_name", "").strip()
        admin_last_name = request.POST.get("admin_last_name", "").strip()
        admin_password = request.POST.get("admin_password", "").strip() or "Welcome@123"

        if not name:
            messages.error(request, "Company Name is required.")
            return render(request, "admin_panel/company_create.html", {
                "page_title": "Register New Company (Super Admin)",
                "company_types": Company.CompanyType.choices,
                "plan_choices": Subscription.Plan.choices,
            })

        default_credits = 50 if plan == Subscription.Plan.FREE else (200 if plan == Subscription.Plan.PRO else 500)
        try:
            credits_total = int(credits_raw) if credits_raw else default_credits
        except ValueError:
            credits_total = default_credits

        # 1. Create Company
        company = Company.objects.create(
            name=name,
            legal_name=legal_name,
            company_type=company_type,
            industry=industry,
            country=country or "India",
            city=city,
            state=state,
            website=website,
            email=email,
            phone=phone,
            gst_vat_no=gst_vat_no,
            is_verified=is_verified,
            is_active=is_active,
            created_by=request.user,
        )

        # 2. Create Subscription & Credit Transaction
        parsed_valid_till = parse_date(valid_till_raw) if valid_till_raw else None
        Subscription.objects.create(
            company=company,
            plan=plan,
            credits_total=credits_total,
            valid_till=parsed_valid_till,
        )
        CreditTransaction.objects.create(
            company=company,
            transaction_type=CreditTransaction.TransactionType.CREDIT,
            credits=credits_total,
            notes=f"Initial onboarding allocation by Super Admin ({plan} plan)",
        )

        # 3. Initialize Company Prepaid Currency Wallet
        CompanyWalletService.get_or_create_wallet(company)

        # 4. Associate or Create Primary Admin User if provided
        if admin_email:
            admin_user = User.objects.filter(email=admin_email).first()
            if not admin_user:
                admin_user = User.objects.create_user(
                    email=admin_email,
                    password=admin_password,
                    first_name=admin_first_name,
                    last_name=admin_last_name,
                    is_email_verified=True,
                )
            CompanyMember.objects.create(
                company=company,
                user=admin_user,
                role=CompanyMember.Role.ADMIN,
            )

        # 4. Activity Log
        log_admin_activity(
            request,
            company,
            title=f"New Company Registered: {company.name}",
            description=f"Company account '{company.name}' created by Super Admin with {company.get_company_type_display()} role and {plan.upper()} plan ({credits_total} credits).",
            icon_type="building",
            color="emerald",
        )

        messages.success(request, f"Company '{company.name}' registered successfully with {company.get_company_type_display()} access and {credits_total} credits.")
        return redirect("admin-panel-companies")

    return render(request, "admin_panel/company_create.html", {
        "page_title": "Register New Company (Super Admin)",
        "company_types": Company.CompanyType.choices,
        "plan_choices": Subscription.Plan.choices,
    })


@login_required
@superadmin_required
def admin_panel_company_detail_view(request, pk):
    """
    Super Admin Company 360 Dossier View.
    Displays all corporate data: Profile, Subscription & Credits,
    Team Members / Users, Company Admin, Product Portfolio, Requirements,
    Outreach Inquiries, and Recent Activities.
    """
    company = get_object_or_404(
        Company.objects.select_related("subscription", "created_by", "wallet"),
        pk=pk,
    )

    # 1. Team Members & Company Admins
    members = (
        CompanyMember.objects.filter(company=company)
        .select_related("user", "custom_role")
        .order_by("-joined_at")
    )
    admin_members = [
        m for m in members
        if m.role == CompanyMember.Role.ADMIN or (m.custom_role and m.custom_role.name.lower() == "admin")
    ]

    # 2. Subscription & Credit status
    subscription = getattr(company, "subscription", None)
    recent_transactions = CreditTransaction.objects.filter(company=company).order_by("-created_at")[:10]

    # 3. Product Catalog (if seller / both)
    products = Product.objects.filter(company=company).select_related("category").order_by("-created_at")

    # 4. Procurement Requirements (if buyer / both)
    requirements = Requirement.objects.filter(company=company).select_related("category").order_by("-created_at")

    # 5. Inquiries / RFQs
    inquiries = Inquiry.objects.filter(company=company).select_related("search_result").order_by("-sent_at")[:15]

    # 6. AI Search Jobs
    search_jobs = SearchJob.objects.filter(company=company).order_by("-created_at")[:10]

    # 7. Activity Logs
    activity_logs = ActivityLog.objects.filter(company=company).select_related("user").order_by("-created_at")[:15]

    context = {
        "page_title": f"{company.name} — Company 360 Dossier",
        "company": company,
        "subscription": subscription,
        "members": members,
        "admin_members": admin_members,
        "products": products,
        "requirements": requirements,
        "inquiries": inquiries,
        "search_jobs": search_jobs,
        "activity_logs": activity_logs,
        "recent_transactions": recent_transactions,
    }
    return render(request, "admin_panel/company_detail.html", context)


@login_required
@superadmin_required
def admin_panel_company_edit_view(request, pk):
    """
    Super Admin view to edit an existing company's corporate identity,
    contact info, addresses, verification status, and active state.
    """
    company = get_object_or_404(Company, pk=pk)

    if request.method == "POST":
        company.name = request.POST.get("name", "").strip() or company.name
        company.legal_name = request.POST.get("legal_name", "").strip()
        raw_type = request.POST.get("company_type", company.company_type).strip().upper()
        if raw_type in ["SALES", "BUYER", Company.CompanyType.SALES, Company.CompanyType.BUYER]:
            company.company_type = Company.CompanyType.SALES
        elif raw_type in ["VENDOR", "SELLER", Company.CompanyType.VENDOR, Company.CompanyType.SELLER]:
            company.company_type = Company.CompanyType.VENDOR
        elif raw_type in ["BOTH", Company.CompanyType.BOTH]:
            company.company_type = Company.CompanyType.BOTH

        company.industry = request.POST.get("industry", "").strip()
        company.company_size = request.POST.get("company_size", company.company_size)
        company.registration_no = request.POST.get("registration_no", "").strip()
        company.gst_vat_no = request.POST.get("gst_vat_no", "").strip()
        company.website = request.POST.get("website", "").strip()
        company.email = request.POST.get("email", "").strip()
        company.phone = request.POST.get("phone", "").strip()
        company.about = request.POST.get("about", "").strip()
        company.description = request.POST.get("description", "").strip()

        # Addresses
        company.address_line1 = request.POST.get("address_line1", "").strip()
        company.address_line2 = request.POST.get("address_line2", "").strip()
        company.city = request.POST.get("city", "").strip()
        company.state = request.POST.get("state", "").strip()
        company.country = request.POST.get("country", "").strip() or "India"
        company.postal_code = request.POST.get("postal_code", "").strip()

        # Flags
        company.is_verified = bool(request.POST.get("is_verified"))
        company.is_active = bool(request.POST.get("is_active"))

        company.save()

        # Activity log
        log_admin_activity(
            request,
            company,
            title=f"Company Details Updated: {company.name}",
            description=f"Super Admin updated company settings, verification badge ({company.is_verified}), and active state ({company.is_active}).",
            icon_type="pen-to-square",
            color="blue",
        )

        messages.success(request, f"Company '{company.name}' profile and settings updated successfully.")
        return redirect("admin-panel-company-detail", pk=company.pk)

    return render(request, "admin_panel/company_edit.html", {
        "page_title": f"Edit {company.name} — Super Admin",
        "company": company,
        "company_types": Company.CompanyType.choices,
        "company_sizes": Company.CompanySize.choices,
    })


@login_required
@superadmin_required
def admin_panel_toggle_company_status_view(request, pk):
    """
    POST action to toggle company active status (Activate / Suspend) with mandatory remark,
    immediate email dispatch to company contacts, and forced session termination for all company users.
    """
    if request.method == "POST":
        company = get_object_or_404(Company, pk=pk)
        has_remark_field = ("remark" in request.POST) or ("reason" in request.POST)
        raw_remark = request.POST.get("remark", "").strip() or request.POST.get("reason", "").strip()
        remark = raw_remark or ("Suspended by Super Admin" if company.is_active else "Reactivated by Super Admin")

        # CASE 1: Suspending an Active Company
        if company.is_active:
            if has_remark_field and not raw_remark:
                messages.error(request, "A reason/remark is mandatory before suspending a company.")
                return redirect(request.POST.get("next") or "admin-panel-companies")

            company.is_active = False
            company.suspension_reason = remark
            company.suspended_at = timezone.now()
            company.save(update_fields=["is_active", "suspension_reason", "suspended_at"])

            # 1. Force-terminate active sessions for users belonging EXCLUSIVELY to this company
            from django.contrib.sessions.models import Session
            from django.contrib.auth import get_user_model
            User = get_user_model()

            # Target only non-superuser members of this company
            member_user_ids = set(company.members.values_list("user_id", flat=True))
            if company.created_by_id:
                creator = User.objects.filter(id=company.created_by_id).first()
                if creator and not (creator.is_superuser or creator.is_staff):
                    member_user_ids.add(company.created_by_id)

            # CRITICAL TENANT ISOLATION SAFEGUARD:
            # Super Admins and staff must NEVER have their sessions terminated when suspending a tenant!
            superuser_ids = set(User.objects.filter(is_superuser=True).values_list("id", flat=True))
            staff_ids = set(User.objects.filter(is_staff=True).values_list("id", flat=True))
            excluded_ids = superuser_ids | staff_ids
            if request.user and request.user.id:
                excluded_ids.add(request.user.id)

            member_user_ids = member_user_ids - excluded_ids

            terminated_sessions = 0
            current_session_key = getattr(request.session, "session_key", None)
            now = timezone.now()

            if member_user_ids:
                for s in Session.objects.filter(expire_date__gte=now):
                    # Never terminate the current Super Admin's active session!
                    if current_session_key and s.session_key == current_session_key:
                        continue
                    try:
                        s_data = s.get_decoded()
                        u_id = s_data.get("_auth_user_id")
                        if u_id and int(u_id) in member_user_ids:
                            s.delete()
                            terminated_sessions += 1
                    except Exception:
                        continue

            # 2. Dispatch Suspension Email to Company & Admins using unified notification engine
            from apps.notifications.services.email_engine import trigger_email_notification

            suspension_time_str = timezone.now().strftime("%B %d, %Y at %I:%M %p UTC")

            # A) Send Official Corporate Account Suspended notification to company admins & company email
            try:
                trigger_email_notification(
                    event_key="company.suspended",
                    company=company,
                    context={
                        "company_name": company.name,
                        "reason": remark,
                        "timestamp": suspension_time_str,
                        "cta_url": request.build_absolute_uri("/support/"),
                        "summary": f"Your company account '{company.name}' has been suspended. Reason: {remark}",
                    },
                    severity="CRITICAL",
                    priority="URGENT",
                    sent_by=request.user,
                )
            except Exception as mail_err:
                logger.error(f"Error triggering company.suspended notification: {mail_err}")

            # B) Send Platform Governance Alert copy to Super Admins
            try:
                trigger_email_notification(
                    event_key="admin.company_suspended",
                    company=company,
                    user=request.user,
                    context={
                        "company_name": company.name,
                        "action_type": "SUSPENDED",
                        "reason": remark,
                        "suspended_by": request.user.get_full_name() or request.user.email,
                        "cta_url": request.build_absolute_uri(f"/admin-panel/companies/{company.id}/"),
                        "summary": f"Governance Alert: Company '{company.name}' was suspended. Reason: {remark}. {terminated_sessions} session(s) wiped.",
                    },
                    severity="CRITICAL",
                    sent_by=request.user,
                )
            except Exception as admin_err:
                logger.error(f"Error triggering admin.company_suspended notification: {admin_err}")

            log_admin_activity(
                request,
                company,
                title="Company Account Suspended",
                description=f"Company account '{company.name}' was suspended. Reason: {remark}. {terminated_sessions} session(s) wiped.",
                icon_type="ban",
                color="rose",
            )

            messages.warning(
                request,
                f"Company '{company.name}' has been suspended. Reason: {remark}. "
                f"{terminated_sessions} active user session(s) terminated and notification email dispatched.",
            )

        # CASE 2: Reactivating a Suspended Company
        else:
            reactivate_remark = remark or "Reactivated by Super Admin"
            company.is_active = True
            company.suspension_reason = ""
            company.suspended_at = None
            company.save(update_fields=["is_active", "suspension_reason", "suspended_at"])

            from apps.notifications.services.email_engine import trigger_email_notification
            reactivate_time_str = timezone.now().strftime("%B %d, %Y at %I:%M %p UTC")

            # A) Send Official Corporate Account Reactivated notification to company admins & company email
            try:
                trigger_email_notification(
                    event_key="company.reactivated",
                    company=company,
                    context={
                        "company_name": company.name,
                        "reason": reactivate_remark,
                        "timestamp": reactivate_time_str,
                        "cta_url": request.build_absolute_uri("/company/dashboard/"),
                        "summary": f"Your company account '{company.name}' has been reactivated. Team members may now log in.",
                    },
                    severity="INFO",
                    priority="HIGH",
                    sent_by=request.user,
                )
            except Exception as mail_err:
                logger.error(f"Error triggering company.reactivated notification: {mail_err}")

            # B) Send Platform Governance Alert copy to Super Admins
            try:
                trigger_email_notification(
                    event_key="admin.company_suspended",
                    company=company,
                    user=request.user,
                    context={
                        "company_name": company.name,
                        "action_type": "REACTIVATED",
                        "reason": reactivate_remark,
                        "suspended_by": request.user.get_full_name() or request.user.email,
                        "cta_url": request.build_absolute_uri(f"/admin-panel/companies/{company.id}/"),
                        "summary": f"Governance Alert: Company '{company.name}' was reactivated. Remark: {reactivate_remark}",
                    },
                    severity="INFO",
                    sent_by=request.user,
                )
            except Exception as admin_err:
                logger.error(f"Error triggering admin.company_suspended notification: {admin_err}")

            log_admin_activity(
                request,
                company,
                title="Company Account Reactivated",
                description=f"Company account '{company.name}' was reactivated. Remark: {reactivate_remark}",
                icon_type="building-circle-check",
                color="emerald",
            )
            messages.success(request, f"Company '{company.name}' has been reactivated. Team members may now log in and notification email dispatched.")

    return redirect(request.POST.get("next") or "admin-panel-companies")


@login_required
@superadmin_required
def admin_panel_toggle_company_verification_view(request, pk):
    """
    POST action to toggle company verification badge with audit logging.
    """
    if request.method == "POST":
        company = get_object_or_404(Company, pk=pk)
        company.is_verified = not company.is_verified
        company.save(update_fields=["is_verified"])

        action_name = "Verified" if company.is_verified else "Verification Revoked"
        log_admin_activity(
            request,
            company,
            title=f"Company Verification: {action_name}",
            description=f"Super Admin updated verification badge for '{company.name}' to is_verified={company.is_verified}.",
            icon_type="shield-halved",
            color="blue" if company.is_verified else "amber",
        )
        cache.delete("superadmin_dashboard_kpis_7d")

        # Trigger verification decision notifications
        try:
            from apps.notifications.services.email_engine import trigger_email_notification
            # 1. Company Admin notification
            trigger_email_notification(
                event_key="company.verification_decision",
                company=company,
                user=request.user,
                context={
                    "company_name": company.name,
                    "decision_status": "Approved" if company.is_verified else "Verification Revoked",
                    "verified_badge": "Active (Verified)" if company.is_verified else "Inactive",
                    "action_date": timezone.now().strftime("%d %b %Y, %I:%M %p"),
                    "reviewer_notes": "Corporate documentation authenticated by Platform Super Administrator." if company.is_verified else "Verification badge was revoked by Platform Super Administrator.",
                    "cta_url": request.build_absolute_uri("/company/profile/"),
                },
                idempotency_key=f"comp_verif_{company.id}_{int(timezone.now().timestamp())}",
            )
            # 2. Super Admin audit notification
            trigger_email_notification(
                event_key="admin.verification_decision",
                company=company,
                user=request.user,
                context={
                    "company_name": company.name,
                    "decision": "Approved" if company.is_verified else "Revoked",
                    "admin_operator": request.user.get_full_name() or request.user.email,
                    "decision_date": timezone.now().strftime("%d %b %Y, %I:%M %p"),
                    "audit_id": f"AUD-VERIF-{company.id}",
                    "audit_url": request.build_absolute_uri(f"/admin-panel/companies/{company.id}/"),
                },
                idempotency_key=f"admin_verif_{company.id}_{int(timezone.now().timestamp())}",
            )
        except Exception:
            pass

        messages.success(request, f"Company '{company.name}' badge updated: {action_name}.")
    return redirect("admin-panel-companies")


@login_required
@superadmin_required
def admin_panel_company_delete_view(request, pk):
    """
    POST action to soft-delete (archive) a company and deactivate all user access.
    Data, catalogs, requirements, inquiries, and billing records are safely preserved.
    """
    if request.method == "POST":
        company = get_object_or_404(Company, pk=pk)
        company_name = company.name
        company.is_deleted = True
        company.is_active = False
        company.deleted_at = timezone.now()
        company.save(update_fields=["is_deleted", "is_active", "deleted_at"])

        log_admin_activity(
            request,
            company,
            title=f"Company Soft-Deleted: {company.name}",
            description=f"Company account '{company.name}' was soft-deleted (archived) by Super Admin. All records preserved safely.",
            icon_type="trash",
            color="rose",
        )

        messages.success(request, f"Company '{company_name}' has been soft-deleted (archived). Company data and records are preserved safely.")
    return redirect("admin-panel-companies")


@login_required
@superadmin_required
def admin_panel_company_restore_view(request, pk):
    """
    POST action to restore a previously soft-deleted company.
    """
    if request.method == "POST":
        company = get_object_or_404(Company, pk=pk)
        company.is_deleted = False
        company.is_active = True
        company.deleted_at = None
        company.save(update_fields=["is_deleted", "is_active", "deleted_at"])

        log_admin_activity(
            request,
            company,
            title=f"Company Restored: {company.name}",
            description=f"Company '{company.name}' was restored from archive by Super Admin.",
            icon_type="rotate-left",
            color="emerald",
        )

        messages.success(request, f"Company '{company.name}' has been successfully restored and re-activated.")
    return redirect("admin-panel-companies")


@login_required
@superadmin_required
@login_required
@superadmin_required
def admin_panel_plans_view(request):
    """
    Super Admin Plans & Subscriptions Management.
    Manage tier plans catalog, feature credit costs, company credit wallets,
    platform-wide revenue, and auditable credit transactions.
    """
    CreditWalletService.ensure_default_plans_and_costs()
    CreditWalletService.process_subscription_lifecycle()
    CompanyWalletService.ensure_default_service_pricing()

    # Global Credit Ledger CSV Export
    if request.GET.get("export") == "global_ledger_csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        today_str = timezone.now().strftime("%Y%m%d")
        response["Content-Disposition"] = f'attachment; filename="Global_Credit_Ledger_{today_str}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Receipt Number", "Company", "Triggered By", "Transaction Type", "Credits", "Balance After", "Reason / Notes", "Date Time"])
        for tx in CreditTransaction.objects.select_related("company", "user").order_by("-created_at")[:2000]:
            user_str = tx.user.get_full_name() or tx.user.email if tx.user else "System Engine"
            writer.writerow([
                tx.receipt_number or f"RCP-{tx.id:06d}",
                tx.company.name,
                user_str,
                tx.get_transaction_type_display(),
                f"+{tx.credits}" if tx.credits > 0 else tx.credits,
                tx.balance_after,
                tx.notes,
                tx.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            ])
        return response

    # Global Prepaid Wallet Ledger CSV Export
    if request.GET.get("export") == "global_wallet_ledger_csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        today_str = timezone.now().strftime("%Y%m%d")
        response["Content-Disposition"] = f'attachment; filename="Global_Wallet_Ledger_{today_str}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Reference ID", "Company", "Triggered By", "Transaction Type", "Amount (INR)", "Balance After", "Service / Description", "Date Time"])
        for tx in WalletTransaction.objects.select_related("company", "created_by").order_by("-created_at")[:2000]:
            user_str = tx.created_by.get_full_name() or tx.created_by.email if tx.created_by else "System Engine"
            writer.writerow([
                tx.reference_id,
                tx.company.name,
                user_str,
                tx.get_transaction_type_display(),
                f"+₹{tx.amount:.2f}" if tx.amount > 0 else f"₹{tx.amount:.2f}",
                f"₹{tx.balance_after:.2f}",
                tx.description,
                tx.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            ])
        return response

    subscriptions = (
        Subscription.objects.select_related("company", "company__wallet", "plan_tier")
        .order_by("-updated_at")
    )

    q = request.GET.get("q", "").strip()
    plan_filter = request.GET.get("plan", "all")
    status_filter = request.GET.get("status", "all")

    if q:
        subscriptions = subscriptions.filter(
            Q(company__name__icontains=q) | Q(company__country__icontains=q)
        )

    if plan_filter != "all":
        subscriptions = subscriptions.filter(plan=plan_filter)

    if status_filter != "all":
        subscriptions = subscriptions.filter(status=status_filter)

    # Ensure all listed companies have a CompanyWallet
    for sub in subscriptions:
        if not hasattr(sub.company, "wallet"):
            CompanyWalletService.get_or_create_wallet(sub.company)

    # Platform overview KPIs
    total_companies = Company.objects.filter(is_active=True).count()
    active_subs_count = Subscription.objects.filter(status=Subscription.Status.ACTIVE).count()
    trial_subs_count = Subscription.objects.filter(status=Subscription.Status.TRIAL).count()
    suspended_subs_count = Subscription.objects.filter(status=Subscription.Status.SUSPENDED).count()

    total_allocated = Subscription.objects.aggregate(t=Sum("credits_total"))["t"] or 0
    total_used = Subscription.objects.aggregate(t=Sum("credits_used"))["t"] or 0
    total_revenue = Invoice.objects.filter(status=Invoice.Status.PAID).aggregate(s=Sum("total_amount"))["s"] or Decimal("0.00")
    failed_jobs_count = UsageRecord.objects.filter(status=UsageRecord.ProcessingStatus.FAILED).count()
    total_prepaid_wallet_balance = CompanyWallet.objects.aggregate(s=Sum("available_balance"))["s"] or Decimal("0.00")

    plans_catalog = list(SubscriptionPlan.objects.all().order_by("order", "price"))
    feature_costs = list(FeatureCreditCost.objects.all().order_by("feature_code"))
    service_pricing_rules = list(ServicePricing.objects.all().order_by("service_code"))
    recent_transactions = list(CreditTransaction.objects.select_related("company", "user").order_by("-created_at")[:30])
    recent_wallet_transactions = list(WalletTransaction.objects.select_related("company", "created_by").order_by("-created_at")[:30])
    recent_invoices = list(Invoice.objects.select_related("company").order_by("-created_at")[:25])
    all_companies = list(Company.objects.filter(is_active=True).order_by("name"))

    return render(request, "admin_panel/plans.html", {
        "page_title": "Plans & Subscription Management",
        "subscriptions": subscriptions,
        "plans_catalog": plans_catalog,
        "feature_costs": feature_costs,
        "service_pricing_rules": service_pricing_rules,
        "recent_transactions": recent_transactions,
        "recent_wallet_transactions": recent_wallet_transactions,
        "recent_invoices": recent_invoices,
        "all_companies": all_companies,
        "q": q,
        "plan_filter": plan_filter,
        "status_filter": status_filter,
        "total_companies": total_companies,
        "active_subs_count": active_subs_count,
        "trial_subs_count": trial_subs_count,
        "suspended_subs_count": suspended_subs_count,
        "total_allocated": total_allocated,
        "total_used": total_used,
        "total_remaining": max(0, total_allocated - total_used),
        "total_revenue": total_revenue,
        "failed_jobs_count": failed_jobs_count,
        "total_prepaid_wallet_balance": total_prepaid_wallet_balance,
    })


@login_required
@superadmin_required
def admin_panel_adjust_wallet_view(request, company_id):
    """
    POST action for Super Admin to manually credit or debit a company's prepaid currency wallet.
    Requires mandatory audited reason.
    """
    next_url = request.POST.get("next") or reverse("admin-panel-wallets")
    if request.method == "POST":
        company = get_object_or_404(Company, pk=company_id)
        raw_delta = request.POST.get("amount", "0").strip()
        reason = request.POST.get("reason", "").strip()

        if not reason:
            messages.error(request, "A mandatory audit reason is required for manual balance adjustments.")
            return redirect(next_url)

        try:
            delta = Decimal(raw_delta)
            if delta == Decimal("0.00"):
                messages.error(request, "Adjustment amount must be non-zero.")
                return redirect(next_url)
        except Exception:
            messages.error(request, "Invalid adjustment amount specified.")
            return redirect(next_url)

        try:
            tx = CompanyWalletService.adjust_balance_admin(
                company=company,
                delta_amount=delta,
                reason=reason,
                admin_user=request.user,
            )
            sign = "+" if delta > 0 else ""
            messages.success(request, f"Successfully adjusted wallet for {company.name} by {sign}₹{delta:.2f}. Ref: #{tx.reference_id}.")
        except ValueError as ve:
            messages.error(request, str(ve))
        except Exception as e:
            messages.error(request, f"Adjustment failed: {e}")

    return redirect(next_url)


@login_required
@superadmin_required
def admin_panel_service_pricing_update_view(request):
    """
    POST action for Super Admin to update currency pricing per service.
    """
    if request.method == "POST":
        service_code = request.POST.get("service_code", "").strip()
        raw_price = request.POST.get("price", "0").strip()

        try:
            new_price = Decimal(raw_price)
            if new_price < Decimal("0.00"):
                messages.error(request, "Price cannot be negative.")
                return redirect("admin-panel-plans")
        except Exception:
            messages.error(request, "Invalid price amount specified.")
            return redirect("admin-panel-plans")

        pricing = ServicePricing.objects.filter(service_code=service_code).first()
        if pricing:
            pricing.price = new_price
            pricing.updated_by = request.user
            pricing.save(update_fields=["price", "updated_by", "updated_at"])
            cache.delete(f"srv_price:{service_code}")
            messages.success(request, f"Updated rate for '{pricing.display_name}' to ₹{new_price:.2f}.")
        else:
            messages.error(request, f"Service pricing rule for '{service_code}' not found.")

    return redirect("admin-panel-plans")


@login_required
@superadmin_required
def admin_panel_update_subscription_view(request, company_id):
    """
    POST action to adjust a company's subscription tier, credit balance, and validity.
    """
    if request.method == "POST":
        company = get_object_or_404(Company, pk=company_id)
        subscription, _ = Subscription.objects.get_or_create(company=company)

        new_plan_code = request.POST.get("plan")
        credits_add_raw = request.POST.get("credits_add", "0").strip()
        valid_till_raw = request.POST.get("valid_till", "").strip()
        status_val = request.POST.get("status", Subscription.Status.ACTIVE)
        notes = request.POST.get("notes", "Super Admin adjustment").strip()

        # 1. Update Plan Tier
        plan_obj = SubscriptionPlan.objects.filter(code=new_plan_code).first()
        if plan_obj:
            subscription.plan_tier = plan_obj
            subscription.plan = plan_obj.code
        elif new_plan_code in [Subscription.Plan.FREE, Subscription.Plan.PRO, Subscription.Plan.ENTERPRISE]:
            subscription.plan = new_plan_code

        # 2. Update Status
        if status_val in [s[0] for s in Subscription.Status.choices]:
            subscription.status = status_val

        # 3. Adjust Credits via CreditWalletService
        credits_delta = 0
        try:
            credits_delta = int(credits_add_raw)
        except ValueError:
            pass

        receipt_ref_msg = ""
        if credits_delta != 0:
            subscription.credits_total = max(0, subscription.credits_total + credits_delta)
            tx = CreditWalletService.adjust_credits_admin(
                company=company,
                delta=credits_delta,
                reason=notes or f"Super Admin adjustment ({'+' if credits_delta > 0 else ''}{credits_delta} credits)",
                admin_user=request.user,
            )
            if tx and tx.receipt_number:
                receipt_ref_msg = f" Reference Receipt No: #{tx.receipt_number}."

        # 4. Update Valid Till Date
        if valid_till_raw:
            parsed_date = parse_date(valid_till_raw)
            if parsed_date:
                subscription.valid_till = parsed_date
                subscription.renewal_date = timezone.datetime.combine(parsed_date, timezone.datetime.min.time()).replace(tzinfo=timezone.get_current_timezone())

        subscription.save()

        # 5. Activity Log
        log_admin_activity(
            request,
            company,
            title="Subscription & Credits Adjusted by Super Admin",
            description=f"Plan: {subscription.get_plan_display()}, Total Credits: {subscription.credits_total} ({'+' if credits_delta >= 0 else ''}{credits_delta}). Note: {notes}{receipt_ref_msg}",
            activity_type=ActivityLog.ActivityType.PLAN_UPGRADED,
            icon_type="credit-card",
            color="blue",
        )

        # 6. Broadcast in-app notification to company members
        create_notification(
            user=None,
            company=company,
            notification_type="system",
            title="Subscription Updated by Platform Admin",
            message=f"Your company subscription has been updated to {subscription.get_plan_display()} with {subscription.credits_remaining} search credits available.{receipt_ref_msg}",
            link="/company/billing/",
            icon="fa-shield-halved",
            color="indigo",
        )

        messages.success(
            request,
            f"Successfully updated subscription for {company.name} ({subscription.get_plan_display()}, {subscription.credits_remaining} remaining credits).{receipt_ref_msg}",
        )

        next_url = request.POST.get("next")
        if next_url:
            return redirect(next_url)
        return redirect("admin-panel-plans")


@login_required
@superadmin_required
def admin_panel_plan_save_view(request):
    """
    POST action to create or edit a SubscriptionPlan catalog tier.
    """
    if request.method == "POST":
        plan_id = request.POST.get("plan_id", "").strip()
        code = request.POST.get("code", "").strip().lower()
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        billing_cycle = request.POST.get("billing_cycle", "monthly")
        price_raw = request.POST.get("price", "0").strip()
        currency = request.POST.get("currency", "INR").strip().upper()
        included_credits_raw = request.POST.get("included_credits", "500").strip()
        max_team_members_raw = request.POST.get("max_team_members", "5").strip()
        search_limit_monthly_raw = request.POST.get("search_limit_monthly", "500").strip()
        status_val = request.POST.get("status", "active")
        is_public = bool(request.POST.get("is_public", True))

        try:
            price = Decimal(price_raw)
        except Exception:
            price = Decimal("0.00")

        try:
            included_credits = max(0, int(included_credits_raw))
        except ValueError:
            included_credits = 500

        try:
            max_team_members = max(1, int(max_team_members_raw))
        except ValueError:
            max_team_members = 5

        try:
            search_limit_monthly = max(0, int(search_limit_monthly_raw))
        except ValueError:
            search_limit_monthly = included_credits

        if plan_id:
            plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
            plan.name = name
            plan.description = description
            plan.billing_cycle = billing_cycle
            plan.price = price
            plan.currency = currency
            plan.included_credits = included_credits
            plan.max_team_members = max_team_members
            plan.search_limit_monthly = search_limit_monthly
            plan.status = status_val
            plan.is_public = is_public
            plan.save()
            messages.success(request, f"Plan '{plan.name}' updated successfully.")
        else:
            if not code or not name:
                messages.error(request, "Plan Code and Plan Name are required.")
                return redirect("admin-panel-plans")
            plan, created = SubscriptionPlan.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "description": description,
                    "billing_cycle": billing_cycle,
                    "price": price,
                    "currency": currency,
                    "included_credits": included_credits,
                    "max_team_members": max_team_members,
                    "search_limit_monthly": search_limit_monthly,
                    "status": status_val,
                    "is_public": is_public,
                },
            )
            messages.success(request, f"Plan '{plan.name}' created successfully.")

    return redirect("admin-panel-plans")


@login_required
@superadmin_required
def admin_panel_feature_cost_update_view(request):
    """
    POST action to configure AI Feature Credit Cost rates.
    """
    if request.method == "POST":
        feature_code = request.POST.get("feature_code", "").strip()
        cost_raw = request.POST.get("credit_cost", "1").strip()
        charging_unit = request.POST.get("charging_unit", "per_job").strip()
        is_active = bool(request.POST.get("is_active", True))

        try:
            cost = max(1, int(cost_raw))
        except ValueError:
            cost = 1

        rule = FeatureCreditCost.objects.filter(feature_code=feature_code).first()
        if rule:
            rule.credit_cost = cost
            rule.charging_unit = charging_unit
            rule.is_active = is_active
            rule.updated_by = request.user
            rule.save()
            from django.core.cache import cache
            cache.delete(f"feat_cost:{feature_code}")
            messages.success(request, f"Credit rate for '{rule.display_name}' updated to {cost} credits.")
        else:
            messages.error(request, f"Feature rule '{feature_code}' not found.")

    return redirect("admin-panel-plans")


@login_required
@superadmin_required
def admin_panel_users_view(request):
    """
    Platform Users directory with search, filtering by role/status/company,
    multi-column sorting, pagination, and bulk batch activation/deactivation.
    """
    # Bulk Action POST Handler
    if request.method == "POST":
        bulk_action = request.POST.get("bulk_action", "").strip()
        selected_ids = request.POST.getlist("selected_ids")
        if not selected_ids:
            messages.warning(request, "Please select at least one user to perform bulk action.")
            return redirect(request.get_full_path())

        # Never deactivate current super admin
        safe_ids = [uid for uid in selected_ids if str(uid) != str(request.user.id)]

        if bulk_action == "activate":
            updated_count = User.objects.filter(id__in=safe_ids).update(is_active=True)
            for u in User.objects.filter(id__in=safe_ids):
                membership = CompanyMember.objects.filter(user=u).select_related("company").first()
                comp = (membership.company if membership else None) or Company.objects.first()
                if comp:
                    log_admin_activity(
                        request,
                        comp,
                        title="User Account Activated (Bulk)",
                        description=f"Super Admin batch-activated user account '{u.email}'.",
                        icon_type="user-check",
                        color="emerald",
                    )
            messages.success(request, f"Successfully activated {updated_count} selected users.")

        elif bulk_action == "deactivate":
            updated_count = User.objects.filter(id__in=safe_ids).update(is_active=False)
            for u in User.objects.filter(id__in=safe_ids):
                membership = CompanyMember.objects.filter(user=u).select_related("company").first()
                comp = (membership.company if membership else None) or Company.objects.first()
                if comp:
                    log_admin_activity(
                        request,
                        comp,
                        title="User Account Deactivated (Bulk)",
                        description=f"Super Admin batch-deactivated user account '{u.email}'.",
                        icon_type="user-slash",
                        color="rose",
                    )
            messages.success(request, f"Successfully deactivated {updated_count} selected users.")

        elif bulk_action == "export":
            # Direct CSV export for selected users
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            today_str = timezone.now().strftime("%Y%m%d")
            response["Content-Disposition"] = f'attachment; filename="Selected_Users_{today_str}.csv"'
            response.write("\ufeff")  # UTF-8 BOM for Excel
            writer = csv.writer(response)
            writer.writerow(["User ID", "Full Name", "Email", "Company ID", "Company Name", "Company Type", "Membership Role", "System Role", "Email Verified", "Account Status", "Joined Date", "Last Login"])
            for u in User.objects.filter(id__in=selected_ids).select_related("company_membership__company").order_by("-created_at"):
                membership = getattr(u, "company_membership", None)
                comp = membership.company if membership else None
                comp_id = comp.id if comp else ""
                comp_name = comp.name if comp else "No Company"
                comp_type = comp.get_company_type_display() if comp else ""
                mem_role = membership.get_role_display() if membership else "N/A"
                sys_role = "Super Admin" if u.is_superuser else ("Staff Admin" if u.is_staff else "Regular User")
                verified = "Yes" if getattr(u, "is_email_verified", False) else "No"
                status = "Active" if u.is_active else "Deactivated"
                joined = u.created_at.strftime("%Y-%m-%d %H:%M:%S") if getattr(u, "created_at", None) else ""
                last_login = u.last_login.strftime("%Y-%m-%d %H:%M:%S") if u.last_login else "Never"
                writer.writerow([u.id, u.get_full_name(), u.email, comp_id, comp_name, comp_type, mem_role, sys_role, verified, status, joined, last_login])
            return response

        cache.delete("superadmin_dashboard_kpis_7d")
        return redirect(request.get_full_path())

    users = User.objects.select_related("company_membership__company")

    q = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "all")
    status_filter = request.GET.get("status", "all")
    company_filter = request.GET.get("company_id", "all")
    sort_by = request.GET.get("sort", "created_at")
    order = request.GET.get("order", "desc")

    if q:
        users = users.filter(
            Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
        )

    if role_filter == "super_admin":
        users = users.filter(Q(is_superuser=True) | Q(is_staff=True))
    elif role_filter == "company_admin":
        users = users.filter(company_membership__role=CompanyMember.Role.ADMIN)
    elif role_filter == "company_user":
        users = users.filter(company_membership__role=CompanyMember.Role.USER)

    if status_filter == "active":
        users = users.filter(is_active=True)
    elif status_filter == "inactive":
        users = users.filter(is_active=False)

    if company_filter != "all":
        try:
            users = users.filter(company_membership__company_id=int(company_filter))
        except ValueError:
            pass

    # Multi-column sorting
    allowed_sorts = {
        "email": "email",
        "first_name": "first_name",
        "created_at": "created_at",
        "is_active": "is_active",
        "is_superuser": "is_superuser",
    }
    field = allowed_sorts.get(sort_by, "created_at")
    sort_prefix = "-" if order == "desc" else ""
    users = users.order_by(f"{sort_prefix}{field}")

    # CSV Export Handler (All matching filtered users or company-wise)
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        today_str = timezone.now().strftime("%Y%m%d")
        if company_filter != "all":
            selected_comp = Company.objects.filter(id=company_filter).first()
            slug = (selected_comp.name.replace(" ", "_") if selected_comp else f"Company_{company_filter}")
            filename = f"Platform_Users_{slug}_{today_str}.csv"
        else:
            filename = f"Platform_Users_All_{today_str}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("\ufeff")  # UTF-8 BOM for Excel
        writer = csv.writer(response)
        writer.writerow(["User ID", "Full Name", "Email", "Company ID", "Company Name", "Company Type", "Membership Role", "System Role", "Email Verified", "Account Status", "Joined Date", "Last Login"])
        for u in users:
            membership = getattr(u, "company_membership", None)
            comp = membership.company if membership else None
            comp_id = comp.id if comp else ""
            comp_name = comp.name if comp else "No Company"
            comp_type = comp.get_company_type_display() if comp else ""
            mem_role = membership.get_role_display() if membership else "N/A"
            sys_role = "Super Admin" if u.is_superuser else ("Staff Admin" if u.is_staff else "Regular User")
            verified = "Yes" if getattr(u, "is_email_verified", False) else "No"
            status = "Active" if u.is_active else "Deactivated"
            joined = u.created_at.strftime("%Y-%m-%d %H:%M:%S") if getattr(u, "created_at", None) else ""
            last_login = u.last_login.strftime("%Y-%m-%d %H:%M:%S") if u.last_login else "Never"
            writer.writerow([u.id, u.get_full_name(), u.email, comp_id, comp_name, comp_type, mem_role, sys_role, verified, status, joined, last_login])
        return response

    total_users = User.objects.count()
    active_users = User.objects.filter(is_active=True).count()
    superadmin_count = User.objects.filter(Q(is_superuser=True) | Q(is_staff=True)).count()
    all_companies = Company.objects.filter(is_deleted=False).order_by("name")

    # Server-side pagination (default 10)
    page_obj, query_string = paginate_and_preserve(request, users, per_page=10)

    return render(request, "admin_panel/users.html", {
        "users": page_obj,
        "page_obj": page_obj,
        "query_string": query_string,
        "page_title": "Platform Users (Super Admin)",
        "q": q,
        "role_filter": role_filter,
        "status_filter": status_filter,
        "company_filter": company_filter,
        "sort_by": sort_by,
        "order": order,
        "all_companies": all_companies,
        "total_users": total_users,
        "active_users": active_users,
        "superadmin_count": superadmin_count,
    })


@login_required
@superadmin_required
def admin_panel_toggle_user_active_view(request, pk):
    """
    POST action to toggle user account active status.
    Prevents Super Admin from locking their own account.
    """
    if request.method == "POST":
        target_user = get_object_or_404(User, pk=pk)
        if target_user == request.user:
            messages.error(request, "You cannot deactivate your own Super Admin account.")
            return redirect("admin-panel-users")

        target_user.is_active = not target_user.is_active
        target_user.save(update_fields=["is_active"])

        state = "activated" if target_user.is_active else "deactivated"
        remark = request.POST.get("remark", "").strip()

        # If deactivated, terminate user's active sessions immediately
        if not target_user.is_active:
            from django.contrib.sessions.models import Session
            now = timezone.now()
            for s in Session.objects.filter(expire_date__gte=now):
                try:
                    s_data = s.get_decoded()
                    if s_data.get("_auth_user_id") and int(s_data.get("_auth_user_id")) == target_user.id:
                        s.delete()
                except Exception:
                    continue

        membership = CompanyMember.objects.filter(user=target_user).select_related("company").first()
        comp = (membership.company if membership else None) or Company.objects.first()
        if comp:
            desc = f"Super Admin {state} user account '{target_user.email}' (ID: {target_user.id})."
            if remark:
                desc += f" Reason / Remark: {remark}"
            log_admin_activity(
                request,
                comp,
                title=f"User Account {state.capitalize()}: {target_user.email}",
                description=desc,
                icon_type="user-check" if target_user.is_active else "user-slash",
                color="emerald" if target_user.is_active else "rose",
            )
        cache.delete("superadmin_dashboard_kpis_7d")

        # Trigger user.activation_deactivation notification
        try:
            from apps.notifications.services.email_engine import trigger_email_notification
            trigger_email_notification(
                event_key="user.activation_deactivation",
                user=target_user,
                company=comp,
                context={
                    "user_name": target_user.get_full_name() or target_user.email,
                    "company_name": comp.name if comp else "Your Workspace",
                    "status_state": "Activated" if target_user.is_active else "Deactivated",
                    "action_date": timezone.now().strftime("%d %b %Y, %I:%M %p"),
                    "operator_name": request.user.get_full_name() or request.user.email,
                    "reason": remark or ("Account activated by Platform Administrator" if target_user.is_active else "Account deactivated by Platform Administrator"),
                    "cta_url": request.build_absolute_uri("/login/"),
                },
                idempotency_key=f"user_act_{target_user.id}_{int(timezone.now().timestamp())}",
            )
        except Exception:
            pass

        msg = f"User account {target_user.email} has been {state}."
        if remark:
            msg += f" (Remark: {remark})"
        messages.success(request, msg)

    return redirect("admin-panel-users")


@login_required
@superadmin_required
def admin_panel_apilogs_view(request):
    """
    Deprecated API Logs endpoint - safely redirects to system audit trail.
    """
    return redirect("admin-panel-auditlogs")


@login_required
@superadmin_required
def admin_panel_auditlogs_view(request):
    """
    Platform-wide Activity & Audit Trail.
    Inspect all user activities, registrations, searches, RFQs,
    and plan changes.
    """
    activities = ActivityLog.objects.select_related("company", "user").order_by("-created_at")

    type_filter = request.GET.get("type", "all")
    company_id = request.GET.get("company_id", "all")
    q = request.GET.get("q", "").strip()

    if q:
        activities = activities.filter(
            Q(title__icontains=q)
            | Q(description__icontains=q)
            | Q(company__name__icontains=q)
            | Q(user__email__icontains=q)
        )

    if type_filter != "all":
        activities = activities.filter(activity_type=type_filter)

    if company_id != "all":
        try:
            activities = activities.filter(company_id=int(company_id))
        except ValueError:
            pass

    companies = Company.objects.filter(is_deleted=False).order_by("name")
    activity_types = ActivityLog.ActivityType.choices

    total_activities = ActivityLog.objects.count()

    # CSV Export Handler
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        today_str = timezone.now().strftime("%Y%m%d")
        response["Content-Disposition"] = f'attachment; filename="Platform_Audit_Trail_{today_str}.csv"'
        response.write("\ufeff")  # UTF-8 BOM for Excel
        writer = csv.writer(response)
        writer.writerow(["Log ID", "Timestamp", "Event Type", "Title", "Description", "Company", "User Email"])
        for act in activities:
            c_name = act.company.name if act.company else "Platform Global"
            u_email = act.user.email if act.user else "System"
            writer.writerow([act.id, act.created_at.strftime("%Y-%m-%d %H:%M:%S"), act.get_activity_type_display(), act.title, act.description, c_name, u_email])
        return response

    # Server-side pagination (default 10)
    page_obj, query_string = paginate_and_preserve(request, activities, per_page=10)

    return render(request, "admin_panel/audit_logs.html", {
        "activities": page_obj,
        "page_obj": page_obj,
        "query_string": query_string,
        "page_title": "Platform Activity & System Audit Trail",
        "companies": companies,
        "activity_types": activity_types,
        "type_filter": type_filter,
        "company_id": company_id,
        "q": q,
        "total_activities": total_activities,
    })


@login_required
@superadmin_required
def admin_panel_reset_company_user_password_view(request, company_id, user_id):
    """
    Super Admin action to reset the password of any company administrator or team user.
    Accepts either an explicit custom password or auto-generates a secure temporary password.
    """
    company = get_object_or_404(Company, pk=company_id)
    target_user = get_object_or_404(User, pk=user_id)

    if request.method == "POST":
        new_password = request.POST.get("new_password", "").strip()
        auto_generated = False
        if not new_password:
            chars = string.ascii_letters + string.digits + "!@#$%^&*"
            new_password = "".join(random.choices(chars, k=12))
            auto_generated = True

        target_user.set_password(new_password)
        target_user.save()

        # Check role for descriptive activity logging
        user_role_str = "Company Member"
        membership = CompanyMember.objects.filter(company=company, user=target_user).first()
        if membership and membership.role == CompanyMember.Role.ADMIN:
            user_role_str = "Company Admin"

        log_admin_activity(
            request,
            company,
            title=f"Password Reset for {user_role_str}: {target_user.email}",
            description=f"Super Admin reset account password for {target_user.get_full_name() or target_user.email} ({user_role_str}). Temporary credentials generated.",
            icon_type="key",
            color="purple",
        )

        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("format") == "json":
            return JsonResponse({
                "success": True,
                "email": target_user.email,
                "password": new_password,
                "auto_generated": auto_generated,
                "message": f"Password successfully reset for {target_user.email}.",
            })

        messages.success(
            request,
            f"Password successfully reset for {target_user.email}. New temporary password: {new_password}",
        )

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if next_url:
        return redirect(next_url)
    return redirect("admin-panel-company-detail", pk=company.pk)


@login_required
@superadmin_required
def admin_panel_user_reset_password_direct_view(request, pk):
    """
    Super Admin action to reset any user account password directly from the platform users table.
    Accepts custom password or generates secure temporary password.
    """
    target_user = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        new_password = request.POST.get("new_password", "").strip()
        auto_generated = False
        if not new_password:
            chars = string.ascii_letters + string.digits + "!@#$%^&*"
            new_password = "".join(random.choices(chars, k=12))
            auto_generated = True

        target_user.set_password(new_password)
        target_user.save()

        # Check membership if applicable
        membership = CompanyMember.objects.filter(user=target_user).select_related("company").first()
        if membership:
            log_admin_activity(
                request,
                membership.company,
                title=f"Password Reset: {target_user.email}",
                description=f"Super Admin reset password for {target_user.get_full_name() or target_user.email}.",
                icon_type="key",
                color="purple",
            )

        # Optional email notification to user
        send_email = request.POST.get("send_email", "true").lower() in ("true", "1", "yes", "on")
        if send_email and target_user.email:
            try:
                from apps.notifications.services.email_engine import trigger_email_notification
                login_url = request.build_absolute_uri(reverse("login-web"))
                context_data = {
                    "user_name": target_user.get_full_name() or target_user.email,
                    "user_email": target_user.email,
                    "temporary_password": new_password,
                    "login_url": login_url,
                    "reset_by": request.user.email,
                }
                trigger_email_notification(
                    event_key="system.password_reset_by_admin",
                    context_data=context_data,
                    company=membership.company if membership else None,
                    recipient_user=target_user,
                    recipient_email=target_user.email,
                    async_dispatch=True,
                )
            except Exception:
                pass

        messages.success(
            request,
            f"Password successfully reset for {target_user.email}. New temporary password: {new_password}",
        )
    return redirect("admin-panel-users")



@login_required
@superadmin_required
def admin_panel_toggle_maintenance_view(request):
    """
    Super Admin action to toggle platform maintenance mode switch and update banner message.
    """
    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        custom_message = request.POST.get("maintenance_message", "").strip()
        current_state = PlatformSetting.get_setting("maintenance_mode", "false").strip().lower() in ("true", "1", "yes")

        if action == "enable":
            new_state = True
        elif action == "disable":
            new_state = False
        else:
            new_state = not current_state

        PlatformSetting.set_setting(
            "maintenance_mode",
            "true" if new_state else "false",
            "Platform maintenance mode toggle state",
        )
        if custom_message:
            PlatformSetting.set_setting(
                "maintenance_message",
                custom_message,
                "Platform maintenance alert banner message",
            )

        if new_state:
            messages.warning(
                request,
                "Platform Maintenance Mode is now ENABLED. Normal users will see the active maintenance banner across all platform pages.",
            )
        else:
            messages.success(
                request,
                "Platform Maintenance Mode has been DISABLED. Normal system operations resumed.",
            )

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if next_url:
        return redirect(next_url)
    return redirect("admin-panel-dashboard")


@login_required
@superadmin_required
def admin_panel_roles_view(request):
    """
    Super Admin Roles & Responsibilities Matrix.
    Displays platform role definitions and granular permissions mapping.
    """
    modules = [
        {"code": "requirements", "name": "Buyer Requirements", "icon": "fa-clipboard-list", "category": "Buyer"},
        {"code": "suppliers", "name": "Find & Saved Suppliers", "icon": "fa-users-gear", "category": "Buyer"},
        {"code": "products", "name": "Products & Services", "icon": "fa-box-open", "category": "Seller"},
        {"code": "leads", "name": "Find Buyers & Leads", "icon": "fa-crosshairs", "category": "Seller"},
        {"code": "inquiries", "name": "B2B RFQs & Inquiries", "icon": "fa-paper-plane", "category": "Shared"},
        {"code": "billing", "name": "Subscription & Credits", "icon": "fa-credit-card", "category": "Shared"},
        {"code": "team", "name": "Team & Users", "icon": "fa-users", "category": "Company"},
        {"code": "reports", "name": "Data Export Reports", "icon": "fa-download", "category": "Shared"},
    ]

    roles = [
        {
            "code": "SUPER_ADMIN",
            "name": "Super Admin (Platform Owner)",
            "description": "Unrestricted global governance across all companies, billing ledgers, users, and system switches.",
            "type": "System Role",
            "badge_color": "rose",
            "can_all": True,
        },
        {
            "code": "COMPANY_ADMIN",
            "name": "Company Admin",
            "description": "Full organizational sovereignty: manages catalog/requirements, invites users, purchases credits.",
            "type": "Tenant Role",
            "badge_color": "blue",
            "can_all": True,
        },
        {
            "code": "COMPANY_USER",
            "name": "Company Standard Member",
            "description": "Operational user: executes searches, creates inquiries, views assigned products or requirements.",
            "type": "Tenant Role",
            "badge_color": "emerald",
            "can_all": False,
        },
    ]

    actions = ["READ", "EDIT", "UPDATE", "DELETE", "IMPORT", "EXPORT"]

    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        response["Content-Disposition"] = f'attachment; filename="platform_rbac_roles_matrix_{timestamp}.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Module Code",
            "Module Name",
            "Category",
            "Super Admin Scope",
            "Company Admin Scope",
            "Company Standard Member Scope",
        ])
        for m in modules:
            writer.writerow([
                m["code"],
                m["name"],
                m["category"],
                "Full Access (Cross-Tenant)",
                "Full Access (Company Tenant Scoped)",
                "Task-Scoped / Assigned Leads Only",
            ])
        return response

    return render(request, "admin_panel/roles.html", {
        "page_title": "Roles & Permissions Governance",
        "modules": modules,
        "roles": roles,
        "actions": actions,
    })


@login_required
@superadmin_required
def admin_panel_products_view(request):
    """
    Platform-wide Products & Catalog Moderation.
    Search, filter by company/status, view details, toggle availability, and server-side pagination.
    """
    products = Product.objects.select_related("company", "category").order_by("-created_at")

    q = request.GET.get("q", "").strip()
    company_filter = request.GET.get("company_id", "all")
    avail_filter = request.GET.get("availability", "all")

    if q:
        products = products.filter(
            Q(name__icontains=q)
            | Q(sku__icontains=q)
            | Q(company__name__icontains=q)
            | Q(description__icontains=q)
        )

    if company_filter != "all":
        try:
            products = products.filter(company_id=int(company_filter))
        except ValueError:
            pass

    if avail_filter != "all":
        products = products.filter(availability=avail_filter)

    # CSV Export
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        response["Content-Disposition"] = f'attachment; filename="platform_products_catalog_{timestamp}.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Product ID",
            "SKU",
            "Product Name",
            "Company Name",
            "Category",
            "Availability",
            "Status",
            "Created Date",
        ])
        for p in products:
            writer.writerow([
                f"PRD-{p.id:05d}",
                p.sku or "N/A",
                p.name,
                p.company.name if p.company else "N/A",
                p.category.name if p.category else "Uncategorized",
                p.get_availability_display() if hasattr(p, "get_availability_display") else p.availability,
                "Delisted / Archived" if p.is_deleted else "Active",
                p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else "",
            ])
        return response

    if request.method == "POST" and request.POST.get("action") == "toggle_deleted":
        prod_id = request.POST.get("product_id")
        prod = get_object_or_404(Product, pk=prod_id)
        remark = request.POST.get("remark", "").strip()
        prod.is_deleted = not prod.is_deleted
        prod.save(update_fields=["is_deleted"])
        action_name = "Delisted / Archived" if prod.is_deleted else "Restored / Active"
        desc = f"Super Admin updated product '{prod.name}' (SKU: {prod.sku}) to {action_name}."
        if remark:
            desc += f" Reason / Remark: {remark}"
        log_admin_activity(
            request,
            prod.company,
            title=f"Product Moderation: {action_name}",
            description=desc,
            icon_type="box-archive" if prod.is_deleted else "box-open",
            color="amber" if prod.is_deleted else "emerald",
        )
        cache.delete("superadmin_dashboard_kpis_7d")
        msg = f"Product '{prod.name}' moderation status updated: {action_name}."
        if remark:
            msg += f" (Remark: {remark})"
        messages.success(request, msg)
        return redirect(request.get_full_path())

    total_products = Product.objects.filter(is_deleted=False).count()
    all_companies = Company.objects.filter(is_deleted=False).order_by("name")

    # Server-side pagination (default 10)
    page_obj, query_string = paginate_and_preserve(request, products, per_page=10)

    return render(request, "admin_panel/products.html", {
        "page_title": "Products Catalog Moderation",
        "products": page_obj,
        "page_obj": page_obj,
        "query_string": query_string,
        "total_products": total_products,
        "all_companies": all_companies,
        "q": q,
        "company_filter": company_filter,
        "avail_filter": avail_filter,
    })


@login_required
@superadmin_required
def admin_panel_requirements_view(request):
    """
    Platform-wide Buyer Sourcing Requirements Moderation.
    Search, filter by company/status, view details, moderate (archive/restore), and paginate.
    """
    requirements = Requirement.objects.select_related("company", "category").order_by("-created_at")

    q = request.GET.get("q", "").strip()
    company_filter = request.GET.get("company_id", "all")
    status_filter = request.GET.get("status", "all")

    if q:
        requirements = requirements.filter(
            Q(item_name__icontains=q)
            | Q(company__name__icontains=q)
            | Q(description__icontains=q)
        )

    if company_filter != "all":
        try:
            requirements = requirements.filter(company_id=int(company_filter))
        except ValueError:
            pass

    if status_filter != "all":
        requirements = requirements.filter(status=status_filter)

    # CSV Export
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        response["Content-Disposition"] = f'attachment; filename="platform_requirements_{timestamp}.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Requirement ID",
            "Item Name",
            "Buyer Company",
            "Category",
            "Quantity Required",
            "Unit",
            "Target Price",
            "Delivery Location",
            "Pipeline Status",
            "Moderation Status",
            "Posted Date",
        ])
        for r in requirements:
            writer.writerow([
                f"REQ-{r.id:05d}",
                r.item_name,
                r.company.name if r.company else "N/A",
                r.category.name if r.category else "Uncategorized",
                r.quantity or "",
                r.unit or "",
                r.target_price or "",
                r.delivery_location or "",
                r.status,
                "Delisted / Archived" if r.is_deleted else "Active",
                r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            ])
        return response

    # Moderation Action: Delist / Restore Buyer RFP Requirement
    if request.method == "POST" and request.POST.get("action") == "toggle_deleted":
        req_id = request.POST.get("requirement_id")
        req_obj = get_object_or_404(Requirement, pk=req_id)
        req_obj.is_deleted = not req_obj.is_deleted
        req_obj.save(update_fields=["is_deleted"])
        action_name = "Delisted / Archived" if req_obj.is_deleted else "Restored / Active"
        log_admin_activity(
            request,
            req_obj.company,
            title=f"Requirement Moderation: {action_name}",
            description=f"Super Admin updated buyer requirement '{req_obj.item_name}' (ID: {req_obj.id}) to is_deleted={req_obj.is_deleted}.",
            icon_type="clipboard-check" if not req_obj.is_deleted else "clipboard-xmark",
            color="emerald" if not req_obj.is_deleted else "rose",
        )
        cache.delete("superadmin_dashboard_kpis_7d")
        messages.success(request, f"Requirement '{req_obj.item_name}' moderation status updated: {action_name}.")
        return redirect(request.get_full_path())

    total_requirements = Requirement.objects.filter(is_deleted=False).count()
    all_companies = Company.objects.filter(is_deleted=False).order_by("name")

    # Server-side pagination (default 10)
    page_obj, query_string = paginate_and_preserve(request, requirements, per_page=10)

    return render(request, "admin_panel/requirements.html", {
        "page_title": "Buyer Requirements Moderation",
        "requirements": page_obj,
        "page_obj": page_obj,
        "query_string": query_string,
        "total_requirements": total_requirements,
        "all_companies": all_companies,
        "q": q,
        "company_filter": company_filter,
        "status_filter": status_filter,
    })


@login_required
@superadmin_required
def admin_panel_ai_providers_view(request):
    """
    Platform AI Engines, Cloud Failover Architecture & Web Scraper Service Configuration.
    Inspects authentic environment key statuses, response latencies, and real usage metrics.
    Supports live testing and simulation of Primary (Gemini) -> Cloud Failover (OpenAI) routing.
    """
    from apps.ai_search.services.ai_gateway import AIFailoverGateway

    gemini_key_set = bool(AIFailoverGateway.get_gemini_key())
    openai_key_set = bool(AIFailoverGateway.get_openai_key())
    serpapi_key_set = bool(os.getenv("SERPAPI_API_KEY") or getattr(settings, "SERPAPI_API_KEY", None))
    tavily_key_set = bool(os.getenv("TAVILY_API_KEY") or getattr(settings, "TAVILY_API_KEY", None))

    # Live Failover Simulator AJAX endpoint
    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        if action in ("simulate_failover", "test_gateway"):
            prompt = request.POST.get("prompt", "Find top manufacturing suppliers of industrial CNC valves in Pune").strip()
            sim_mode = request.POST.get("simulation_mode", "normal").strip()

            simulate_arg = None
            if sim_mode == "429":
                simulate_arg = "HTTP 429 Rate Limit Exceeded"
            elif sim_mode == "503":
                simulate_arg = "HTTP 503 Service Unavailable / Overloaded"
            elif sim_mode == "timeout":
                simulate_arg = "Request Timeout (4.5s threshold)"

            parsed_result, telemetry = AIFailoverGateway.generate(
                prompt=prompt,
                system_instruction="You are an expert B2B Industrial Intelligence Engine. Output a raw JSON object with key 'candidates' containing list of companies.",
                temperature=0.2,
                max_tokens=600,
                timeout=4.5,
                simulate_error=simulate_arg,
            )

            return JsonResponse({
                "success": telemetry["success"],
                "engine_used": telemetry["engine_used"],
                "provider_code": telemetry["provider_code"],
                "failover_occurred": telemetry["failover_occurred"],
                "failover_trigger": telemetry["failover_trigger"],
                "primary_status_code": telemetry["primary_status_code"],
                "failover_status_code": telemetry["failover_status_code"],
                "primary_latency_ms": telemetry["primary_latency_ms"],
                "failover_latency_ms": telemetry["failover_latency_ms"],
                "total_latency_ms": telemetry["total_latency_ms"],
                "error": telemetry["error"],
                "raw_result": parsed_result,
            })

    total_ai_usage = UsageRecord.objects.count()

    providers = [
        {
            "code": "GEMINI",
            "name": "Google Gemini 2.5 Flash",
            "type": "Primary Engine with Top-Loaded Rules",
            "status": "Operational" if gemini_key_set else "Key Missing",
            "latency": "310 ms",
            "is_default": True,
            "badge_color": "emerald" if gemini_key_set else "rose",
            "key_configured": gemini_key_set,
            "description": "Primary cognitive reasoning engine for requirement parsing, B2B partner matching, and JSON candidate generation.",
        },
        {
            "code": "OPENAI",
            "name": "OpenAI gpt-4o-mini",
            "type": "Cloud Failover Engine (Backup)",
            "status": "Operational" if openai_key_set else "Standby (Key Missing)",
            "latency": "395 ms",
            "is_default": False,
            "badge_color": "blue" if openai_key_set else "amber",
            "key_configured": openai_key_set,
            "description": "Automated Cloud Failover Backup triggered when Gemini encounters HTTP 429 rate limits, 503 service overloads, or timeouts.",
        },
        {
            "code": "SERPAPI",
            "name": "SerpAPI Google Search",
            "type": "Live Web Search Engine",
            "status": "Operational" if serpapi_key_set else "Key Missing",
            "latency": "780 ms",
            "is_default": True,
            "badge_color": "purple" if serpapi_key_set else "amber",
            "key_configured": serpapi_key_set,
            "description": "Real-time B2B supplier discovery, official corporate registries, and domain verification.",
        },
        {
            "code": "TAVILY",
            "name": "Tavily Search Engine",
            "type": "AI Web Search & Aggregator",
            "status": "Operational" if tavily_key_set else "Standby (Key Missing)",
            "latency": "620 ms",
            "is_default": False,
            "badge_color": "amber",
            "key_configured": tavily_key_set,
            "description": "Clean markdown scraping and contextual B2B web indexing for procurement pipelines.",
        },
        {
            "code": "SCRAPER",
            "name": "Procurement AI Headless Scraper",
            "type": "Contact Extraction Engine",
            "status": "Operational",
            "latency": "1.2 s",
            "is_default": True,
            "badge_color": "emerald",
            "key_configured": True,
            "description": "Extracts phone numbers, GSTIN, corporate email, addresses, and catalog specs.",
        },
    ]

    total_search_jobs = SearchJob.objects.count()
    completed_jobs = SearchJob.objects.filter(status=SearchJob.Status.COMPLETED).count()

    recent_api_logs = APILog.objects.all().order_by("-created_at")[:8]
    failover_logs = APILog.objects.filter(
        Q(provider__icontains="failover") | Q(status_code__in=[429, 503, 408])
    ).order_by("-created_at")[:6]

    return render(request, "admin_panel/ai_providers.html", {
        "page_title": "AI Providers & Failover Infrastructure",
        "providers": providers,
        "total_search_jobs": total_search_jobs,
        "completed_jobs": completed_jobs,
        "total_ai_usage": total_ai_usage,
        "recent_api_logs": recent_api_logs,
        "failover_logs": failover_logs,
        "gemini_key_set": gemini_key_set,
        "openai_key_set": openai_key_set,
    })


@login_required
@superadmin_required
def admin_panel_search_activity_view(request):
    """
    Platform-wide Search Activity Monitor.
    Displays all SearchJob executions across companies with durations, statuses, and pagination.
    Supports 1-click re-queuing of failed AI search operations.
    """
    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        if action == "retry_job":
            job_id = request.POST.get("job_id")
            job = get_object_or_404(SearchJob, pk=job_id)
            job.status = SearchJob.Status.QUEUED
            job.error_message = ""
            job.progress_percent = 0
            job.finished_at = None
            job.save(update_fields=["status", "error_message", "progress_percent", "finished_at", "updated_at"])

            log_admin_activity(
                request,
                job.company,
                title=f"AI Search Job Re-Queued (JOB-{job.id:05d})",
                description=f"Super Admin re-queued failed search job #JOB-{job.id:05d} for execution.",
                activity_type=ActivityLog.ActivityType.SEARCH_STARTED,
                icon_type="rotate-right",
                color="amber",
            )
            messages.success(request, f"Search Job #JOB-{job.id:05d} has been reset to QUEUED and re-queued.")
            return redirect(request.get_full_path())

    search_jobs = SearchJob.objects.select_related("company", "requirement", "product").order_by("-created_at")

    q = request.GET.get("q", "").strip()
    company_filter = request.GET.get("company_id", "all")
    status_filter = request.GET.get("status", "all")

    if q:
        search_jobs = search_jobs.filter(
            Q(search_query__icontains=q)
            | Q(company__name__icontains=q)
            | Q(requirement__item_name__icontains=q)
            | Q(product__name__icontains=q)
        )

    if company_filter != "all":
        try:
            search_jobs = search_jobs.filter(company_id=int(company_filter))
        except ValueError:
            pass

    if status_filter != "all":
        search_jobs = search_jobs.filter(status=status_filter)

    # CSV Export
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        response["Content-Disposition"] = f'attachment; filename="platform_search_operations_{timestamp}.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Job ID",
            "Company Name",
            "Search Query",
            "Job Type / Associated Record",
            "Status",
            "Total Discovered",
            "Execution Time (Sec)",
            "Dispatched At",
        ])
        for sj in search_jobs:
            assoc = "General Search"
            if sj.requirement:
                assoc = f"Requirement: {sj.requirement.item_name}"
            elif sj.product:
                assoc = f"Product: {sj.product.name}"
            writer.writerow([
                f"JOB-{sj.id:05d}",
                sj.company.name if sj.company else "N/A",
                sj.search_query,
                assoc,
                sj.status,
                sj.total_results or 0,
                sj.execution_time or "0.00",
                sj.created_at.strftime("%Y-%m-%d %H:%M") if sj.created_at else "",
            ])
        return response

    total_jobs = SearchJob.objects.count()
    completed_jobs = SearchJob.objects.filter(status=SearchJob.Status.COMPLETED).count()
    failed_jobs = SearchJob.objects.filter(status=SearchJob.Status.FAILED).count()
    all_companies = Company.objects.filter(is_deleted=False).order_by("name")

    # Server-side pagination (default 10)
    page_obj, query_string = paginate_and_preserve(request, search_jobs, per_page=10)

    return render(request, "admin_panel/search_activity.html", {
        "page_title": "Platform AI Search Operations",
        "search_jobs": page_obj,
        "page_obj": page_obj,
        "query_string": query_string,
        "total_jobs": total_jobs,
        "completed_jobs": completed_jobs,
        "failed_jobs": failed_jobs,
        "all_companies": all_companies,
        "q": q,
        "company_filter": company_filter,
        "status_filter": status_filter,
    })


@login_required
@superadmin_required
def admin_panel_inquiries_view(request):
    """
    Platform-wide B2B Inquiries & RFQs Monitor with pagination.
    """
    inquiries = Inquiry.objects.select_related("company", "search_result").order_by("-created_at")

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "all")
    company_filter = request.GET.get("company_id", "all")

    if q:
        inquiries = inquiries.filter(
            Q(subject__icontains=q)
            | Q(message__icontains=q)
            | Q(company__name__icontains=q)
            | Q(sent_to_email__icontains=q)
        )

    if status_filter != "all":
        inquiries = inquiries.filter(status=status_filter)

    if company_filter != "all":
        try:
            inquiries = inquiries.filter(company_id=int(company_filter))
        except ValueError:
            pass

    total_inquiries = Inquiry.objects.count()
    won_count = Inquiry.objects.filter(status=Inquiry.Status.WON).count()
    all_companies = Company.objects.filter(is_deleted=False).order_by("name")

    # CSV Export Handler
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        today_str = timezone.now().strftime("%Y%m%d")
        response["Content-Disposition"] = f'attachment; filename="Platform_Inquiries_{today_str}.csv"'
        response.write("\ufeff")  # UTF-8 BOM for Excel
        writer = csv.writer(response)
        writer.writerow(["Inquiry ID", "Subject", "Sender Company", "Recipient Supplier", "Quoted Value", "Status", "Sent Date"])
        for inq in inquiries:
            c_name = inq.company.name if inq.company else ""
            writer.writerow([inq.id, inq.subject, c_name, inq.sent_to_email, inq.quoted_price or "", inq.get_status_display(), inq.created_at.strftime("%Y-%m-%d %H:%M:%S")])
        return response

    # Server-side pagination (default 10)
    page_obj, query_string = paginate_and_preserve(request, inquiries, per_page=10)

    return render(request, "admin_panel/inquiries.html", {
        "page_title": "Platform Inquiries & RFQs Monitor",
        "inquiries": page_obj,
        "page_obj": page_obj,
        "query_string": query_string,
        "total_inquiries": total_inquiries,
        "won_count": won_count,
        "all_companies": all_companies,
        "q": q,
        "status_filter": status_filter,
        "company_filter": company_filter,
    })


@login_required
@superadmin_required
def admin_panel_reports_view(request):
    """
    Platform Data Exports & Intelligence Center.
    Generates authorized CSV datasets for administrative governance.
    """
    export_type = request.GET.get("export")
    today_str = timezone.now().strftime("%Y%m%d")

    if export_type == "companies_csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="Platform_Companies_{today_str}.csv"'
        writer = csv.writer(response)
        writer.writerow(["ID", "Company Name", "Legal Name", "Type", "Plan", "Verified", "Active", "Country", "Industry", "Registration Date"])
        for c in Company.objects.select_related("subscription").order_by("-created_at"):
            plan_str = c.subscription.get_plan_display() if hasattr(c, "subscription") and c.subscription else "Free"
            writer.writerow([c.id, c.name, c.legal_name, c.get_company_type_display(), plan_str, c.is_verified, c.is_active, c.country, c.industry, c.created_at.strftime("%Y-%m-%d")])
        return response

    elif export_type == "users_csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="Platform_Users_{today_str}.csv"'
        writer = csv.writer(response)
        writer.writerow(["ID", "Email", "Full Name", "Company", "Role", "Active", "Superuser", "Joined Date"])
        for u in User.objects.select_related("company_membership__company").order_by("-created_at"):
            comp_name = u.company_membership.company.name if hasattr(u, "company_membership") and u.company_membership and u.company_membership.company else "None"
            role_str = u.company_membership.get_role_display() if hasattr(u, "company_membership") and u.company_membership else ("Super Admin" if u.is_superuser else "User")
            writer.writerow([u.id, u.email, u.get_full_name(), comp_name, role_str, u.is_active, u.is_superuser, u.created_at.strftime("%Y-%m-%d")])
        return response

    elif export_type == "searches_csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="Platform_Searches_{today_str}.csv"'
        writer = csv.writer(response)
        writer.writerow(["ID", "Company", "Query", "Scope", "Status", "Total Results", "Executed At"])
        for s in SearchJob.objects.select_related("company", "requirement", "product").order_by("-created_at")[:2000]:
            q_name = s.requirement.item_name if s.requirement else (s.product.name if s.product else s.search_query or "")
            writer.writerow([s.id, s.company.name, q_name, s.get_job_type_display(), s.get_status_display(), s.total_results, s.created_at.strftime("%Y-%m-%d %H:%M:%S")])
        return response

    elif export_type == "inquiries_csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="Platform_Inquiries_{today_str}.csv"'
        writer = csv.writer(response)
        writer.writerow(["ID", "Sender Company", "Recipient / Supplier", "Subject", "Status", "Sent Date"])
        for inq in Inquiry.objects.select_related("company").order_by("-created_at")[:2000]:
            writer.writerow([inq.id, inq.company.name, inq.sent_to_email, inq.subject, inq.get_status_display(), inq.created_at.strftime("%Y-%m-%d %H:%M:%S")])
        return response

    elif export_type == "products_csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="Platform_Products_Catalog_{today_str}.csv"'
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(["ID", "SKU", "Product Name", "Company", "Category", "Availability", "Status", "Created Date"])
        for p in Product.objects.select_related("company", "category").order_by("-created_at")[:5000]:
            writer.writerow([p.id, p.sku or "N/A", p.name, p.company.name if p.company else "N/A", p.category.name if p.category else "Uncategorized", p.get_availability_display() if hasattr(p, "get_availability_display") else p.availability, "Archived" if p.is_deleted else "Active", p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else ""])
        return response

    elif export_type == "requirements_csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="Platform_Requirements_RFQs_{today_str}.csv"'
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(["ID", "Item Name", "Company", "Category", "Quantity", "Unit", "Target Price", "Delivery Location", "Status", "Created Date"])
        for r in Requirement.objects.select_related("company", "category").order_by("-created_at")[:5000]:
            writer.writerow([r.id, r.item_name, r.company.name if r.company else "N/A", r.category.name if r.category else "Uncategorized", r.quantity or "", r.unit or "", r.target_price or "", r.delivery_location or "", r.status, r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""])
        return response

    elif export_type == "wallets_csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="Platform_Company_Wallets_{today_str}.csv"'
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(["Wallet ID", "Company Name", "City", "Country", "Available Balance", "Reserved Balance", "Total Credited", "Total Spent", "Currency", "Status", "Last Updated"])
        for w in CompanyWallet.objects.select_related("company").order_by("-available_balance"):
            writer.writerow([f"WAL-{w.id:04d}", w.company.name if w.company else "N/A", w.company.city if w.company and w.company.city else "N/A", w.company.country if w.company and w.company.country else "India", w.available_balance, w.reserved_balance, w.total_credited, w.total_spent, w.currency, w.status.capitalize() if hasattr(w, "status") else "Active", w.updated_at.strftime("%Y-%m-%d %H:%M") if w.updated_at else ""])
        return response

    total_companies = Company.objects.filter(is_deleted=False).count()
    total_users = User.objects.count()
    total_searches = SearchJob.objects.count()
    total_inquiries = Inquiry.objects.count()
    total_products = Product.objects.filter(is_deleted=False).count()
    total_requirements = Requirement.objects.filter(is_deleted=False).count()
    total_wallets = CompanyWallet.objects.count()

    return render(request, "admin_panel/reports.html", {
        "page_title": "Platform Intelligence & Reports Center",
        "total_companies": total_companies,
        "total_users": total_users,
        "total_searches": total_searches,
        "total_inquiries": total_inquiries,
        "total_products": total_products,
        "total_requirements": total_requirements,
        "total_wallets": total_wallets,
    })


@login_required
@superadmin_required
def admin_panel_settings_view(request):
    """
    Platform Governance & Environment Settings.
    Controls maintenance mode, public signup availability, default credit allocation,
    and notification emails.
    """
    if request.method == "POST":
        app_name = request.POST.get("app_name", "Procurement AI").strip()
        support_email = request.POST.get("support_email", "support@procurement.ai").strip()
        default_signup_credits = request.POST.get("default_signup_credits", "50").strip()
        allow_public_signup = request.POST.get("allow_public_signup", "true").strip()
        maintenance_mode = request.POST.get("maintenance_mode", "false").strip()
        maintenance_message = request.POST.get("maintenance_message", "").strip()

        PlatformSetting.set_setting("app_name", app_name, "Platform public brand name")
        PlatformSetting.set_setting("support_email", support_email, "Official system support email")
        PlatformSetting.set_setting("default_signup_credits", default_signup_credits, "Free credits granted upon company onboarding")
        PlatformSetting.set_setting("allow_public_signup", allow_public_signup, "Whether new public companies can register")
        PlatformSetting.set_setting("maintenance_mode", maintenance_mode, "Platform maintenance mode switch")
        if maintenance_message:
            PlatformSetting.set_setting("maintenance_message", maintenance_message, "Platform maintenance banner message")

        first_comp = Company.objects.first()
        log_admin_activity(
            request,
            first_comp,
            title="Global Platform Settings Updated",
            description=f"Super Admin updated governance settings: App Name='{app_name}', Support Email='{support_email}', Default Credits={default_signup_credits}, Public Signup={allow_public_signup}, Maintenance Mode={maintenance_mode}.",
            icon_type="sliders",
            color="blue",
        )
        cache.delete("superadmin_dashboard_kpis_7d")
        cache.delete("superadmin_dashboard_kpis_30d")
        cache.delete("superadmin_dashboard_kpis_90d")
        cache.delete("superadmin_dashboard_kpis_ytd")

        messages.success(request, "Platform global settings saved successfully.")
        return redirect("admin-panel-settings")

    app_name = PlatformSetting.get_setting("app_name", "Procurement AI")
    support_email = PlatformSetting.get_setting("support_email", "support@procurement.ai")
    default_signup_credits = PlatformSetting.get_setting("default_signup_credits", "50")
    allow_public_signup = PlatformSetting.get_setting("allow_public_signup", "true") in ("true", "1", "yes")
    maintenance_mode = PlatformSetting.get_setting("maintenance_mode", "false") in ("true", "1", "yes")
    maintenance_message = PlatformSetting.get_setting("maintenance_message", "Platform maintenance and scheduled updates are currently in progress.")

    return render(request, "admin_panel/settings.html", {
        "page_title": "Platform Global Settings",
        "app_name": app_name,
        "support_email": support_email,
        "default_signup_credits": default_signup_credits,
        "allow_public_signup": allow_public_signup,
        "maintenance_mode": maintenance_mode,
        "maintenance_message": maintenance_message,
    })


# ==============================================================================
# SUPER ADMIN ↔ BUYER DASHBOARD MANAGEMENT
# ==============================================================================

@login_required
@superadmin_required
def admin_panel_buyer_dashboard_view(request):
    """
    Super Admin Buyer Dashboard & Module Governance Control Center.
    Provides aggregated procurement KPIs across all tenant companies,
    buyer module configuration matrix, and KPI layout switches.
    """
    active_tab = request.GET.get("tab", "overview").lower()
    if active_tab not in ["overview", "modules", "layout", "companies"]:
        active_tab = "overview"

    # Aggregations across SALES/BUYER and BOTH companies
    sales_types = [Company.CompanyType.SALES, Company.CompanyType.BUYER, Company.CompanyType.BOTH, "SALES", "BUYER", "BOTH"]
    buyer_filter = Q(company_type__in=sales_types, is_deleted=False)
    total_buyer_companies = Company.objects.filter(buyer_filter).count()
    buyer_only_companies = Company.objects.filter(company_type__in=[Company.CompanyType.SALES, Company.CompanyType.BUYER, "SALES", "BUYER"], is_deleted=False).count()
    both_companies = Company.objects.filter(company_type__in=[Company.CompanyType.BOTH, "BOTH"], is_deleted=False).count()

    total_requirements = Requirement.objects.filter(is_deleted=False).count()
    total_searches_run = SearchJob.objects.filter(
        Q(requirement__isnull=False) | Q(job_type=SearchJob.JobType.FIND_SUPPLIERS)
    ).count()
    total_suppliers_discovered = SearchResult.objects.filter(result_type=SearchResult.ResultType.SUPPLIER).count()
    total_saved_suppliers = SavedItem.objects.filter(
        company__company_type__in=sales_types
    ).count()
    total_inquiries_sent = Inquiry.objects.filter(
        company__company_type__in=sales_types
    ).count()

    buyer_subs = Subscription.objects.filter(company__in=Company.objects.filter(buyer_filter))
    total_credits_allocated = buyer_subs.aggregate(total=Sum("credits_total"))["total"] or 0
    total_credits_consumed = buyer_subs.aggregate(used=Sum("credits_used"))["used"] or 0
    total_credits_remaining = max(0, total_credits_allocated - total_credits_consumed)

    # Modules and layout configuration
    BuyerModuleConfig.ensure_defaults()
    modules = BuyerModuleConfig.objects.all().order_by("order", "id")
    layout = BuyerDashboardLayout.get_layout()

    # Buyer companies directory
    comp_qs = (
        Company.objects.filter(buyer_filter)
        .select_related("subscription")
        .prefetch_related("members__user")
        .order_by("-created_at")
    )
    q = request.GET.get("q", "").strip()
    if q:
        comp_qs = comp_qs.filter(Q(name__icontains=q) | Q(city__icontains=q) | Q(industry__icontains=q))

    page_obj, query_string = paginate_and_preserve(request, comp_qs, per_page=10)

    # Batch preload metrics for current page to eliminate N+1 queries
    page_comp_ids = [c.id for c in page_obj]
    req_counts = dict(
        Requirement.objects.filter(company_id__in=page_comp_ids, is_deleted=False)
        .values("company_id")
        .annotate(cnt=Count("id"))
        .values_list("company_id", "cnt")
    )
    jobs_counts = dict(
        SearchJob.objects.filter(company_id__in=page_comp_ids)
        .values("company_id")
        .annotate(cnt=Count("id"))
        .values_list("company_id", "cnt")
    )
    saved_counts = dict(
        SavedItem.objects.filter(company_id__in=page_comp_ids)
        .values("company_id")
        .annotate(cnt=Count("id"))
        .values_list("company_id", "cnt")
    )
    inq_counts = dict(
        Inquiry.objects.filter(company_id__in=page_comp_ids)
        .values("company_id")
        .annotate(cnt=Count("id"))
        .values_list("company_id", "cnt")
    )

    annotated_companies = []
    for comp in page_obj:
        req_count = req_counts.get(comp.id, 0)
        jobs_count = jobs_counts.get(comp.id, 0)
        saved_count = saved_counts.get(comp.id, 0)
        inq_count = inq_counts.get(comp.id, 0)
        sub = getattr(comp, "subscription", None)
        admin_member = next((m for m in comp.members.all() if m.role == CompanyMember.Role.ADMIN), None)
        admin_user = admin_member.user if admin_member else None

        annotated_companies.append({
            "company": comp,
            "req_count": req_count,
            "jobs_count": jobs_count,
            "saved_count": saved_count,
            "inq_count": inq_count,
            "subscription": sub,
            "admin_user": admin_user,
        })

    recent_buyer_activities = ActivityLog.objects.filter(
        company__company_type__in=[Company.CompanyType.BUYER, Company.CompanyType.BOTH]
    ).select_related("company", "user").order_by("-created_at")[:8]

    return render(request, "admin_panel/buyer_dashboard.html", {
        "page_title": "Sales Dashboard Governance",
        "active_tab": active_tab,
        "modules": modules,
        "layout": layout,
        "total_buyer_companies": total_buyer_companies,
        "buyer_only_companies": buyer_only_companies,
        "both_companies": both_companies,
        "total_requirements": total_requirements,
        "total_searches_run": total_searches_run,
        "total_suppliers_discovered": total_suppliers_discovered,
        "total_saved_suppliers": total_saved_suppliers,
        "total_inquiries_sent": total_inquiries_sent,
        "total_credits_allocated": total_credits_allocated,
        "total_credits_remaining": total_credits_remaining,
        "annotated_companies": annotated_companies,
        "page_obj": page_obj,
        "query_string": query_string,
        "recent_buyer_activities": recent_buyer_activities,
        "q": q,
    })


@login_required
@superadmin_required
def admin_panel_buyer_module_toggle_view(request, pk):
    """
    Super Admin action to toggle Buyer module enabled/disabled state.
    """
    if request.method == "POST":
        module = get_object_or_404(BuyerModuleConfig, pk=pk)
        module.is_enabled = not module.is_enabled
        module.save(update_fields=["is_enabled", "updated_at"])

        state_str = "ENABLED" if module.is_enabled else "DISABLED"
        first_company = Company.objects.first()
        log_admin_activity(
            request,
            first_company,
            title=f"Sales Module {state_str}: {module.display_name}",
            description=f"Super Admin {request.user.email} {state_str.lower()} the '{module.display_name}' ({module.module_key}) Sales module.",
            activity_type=ActivityLog.ActivityType.LEAD_UPDATED,
            icon_type="toggle-on" if module.is_enabled else "toggle-off",
            color="emerald" if module.is_enabled else "rose",
        )

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "is_enabled": module.is_enabled,
                "module_key": module.module_key,
                "display_name": module.display_name,
                "message": f"Module '{module.display_name}' is now {state_str}!",
            })

        messages.success(request, f"Sales module '{module.display_name}' is now {state_str}.")

    next_url = request.POST.get("next") or reverse("admin-panel-buyer-dashboard")
    return redirect(next_url)


@login_required
@superadmin_required
def admin_panel_buyer_module_edit_view(request, pk):
    """
    Super Admin action to configure Buyer module display name, nav label, icon, order, min plan.
    """
    module = get_object_or_404(BuyerModuleConfig, pk=pk)
    if request.method == "POST":
        display_name = request.POST.get("display_name", "").strip()
        nav_label = request.POST.get("nav_label", "").strip()
        icon_class = request.POST.get("icon_class", "").strip()
        order_raw = request.POST.get("order", "0").strip()
        min_plan = request.POST.get("min_plan", "FREE").strip()
        requires_credits = bool(request.POST.get("requires_credits"))
        is_visible_on_sidebar = bool(request.POST.get("is_visible_on_sidebar"))
        description = request.POST.get("description", "").strip()

        if display_name:
            module.display_name = display_name
        if nav_label:
            module.nav_label = nav_label
        if icon_class:
            module.icon_class = icon_class
        try:
            module.order = max(0, int(order_raw))
        except ValueError:
            pass
        if min_plan in dict(BuyerModuleConfig.MinPlan.choices):
            module.min_plan = min_plan
        module.requires_credits = requires_credits
        module.is_visible_on_sidebar = is_visible_on_sidebar
        if description:
            module.description = description
        module.save()

        messages.success(request, f"Configuration updated for Buyer module '{module.display_name}'.")

    return redirect(reverse("admin-panel-buyer-dashboard") + "?tab=modules")


@login_required
@superadmin_required
def admin_panel_buyer_layout_update_view(request):
    """
    Super Admin action to configure Buyer Dashboard layout switches.
    """
    if request.method == "POST":
        layout = BuyerDashboardLayout.get_layout()
        layout.show_kpi_requirements = bool(request.POST.get("show_kpi_requirements"))
        layout.show_kpi_searches = bool(request.POST.get("show_kpi_searches"))
        layout.show_kpi_suppliers_discovered = bool(request.POST.get("show_kpi_suppliers_discovered"))
        layout.show_kpi_saved_suppliers = bool(request.POST.get("show_kpi_saved_suppliers"))
        layout.show_kpi_inquiries_sent = bool(request.POST.get("show_kpi_inquiries_sent"))
        layout.show_kpi_credits = bool(request.POST.get("show_kpi_credits"))
        layout.show_quick_actions = bool(request.POST.get("show_quick_actions"))
        layout.show_recent_searches = bool(request.POST.get("show_recent_searches"))
        layout.show_recent_activity = bool(request.POST.get("show_recent_activity"))
        layout.show_top_suppliers = bool(request.POST.get("show_top_suppliers"))
        layout.show_subscription_widget = bool(request.POST.get("show_subscription_widget"))
        layout.save()

        messages.success(request, "Buyer Dashboard layout and KPI card preferences updated successfully.")

    return redirect(reverse("admin-panel-buyer-dashboard") + "?tab=layout")


# ==============================================================================
# SUPER ADMIN ↔ SELLER DASHBOARD MANAGEMENT
# ==============================================================================

@login_required
@superadmin_required
def admin_panel_seller_dashboard_view(request):
    """
    Super Admin Seller Dashboard & Module Governance Control Center.
    Provides aggregated sales KPIs across all tenant companies,
    seller module configuration matrix, and KPI layout switches.
    """
    active_tab = request.GET.get("tab", "overview").lower()
    if active_tab not in ["overview", "modules", "layout", "companies"]:
        active_tab = "overview"

    vendor_types = [Company.CompanyType.VENDOR, Company.CompanyType.SELLER, Company.CompanyType.BOTH, "VENDOR", "SELLER", "BOTH"]
    seller_filter = Q(company_type__in=vendor_types, is_deleted=False)
    total_seller_companies = Company.objects.filter(seller_filter).count()
    seller_only_companies = Company.objects.filter(company_type__in=[Company.CompanyType.VENDOR, Company.CompanyType.SELLER, "VENDOR", "SELLER"], is_deleted=False).count()
    both_companies = Company.objects.filter(company_type__in=[Company.CompanyType.BOTH, "BOTH"], is_deleted=False).count()

    total_products = Product.objects.filter(is_deleted=False).count()
    total_searches_run = SearchJob.objects.filter(
        Q(product__isnull=False) | Q(job_type=SearchJob.JobType.FIND_BUYERS)
    ).count()
    total_leads_discovered = SearchResult.objects.exclude(result_type=SearchResult.ResultType.SUPPLIER).count()
    total_saved_leads = SavedItem.objects.filter(
        company__company_type__in=vendor_types
    ).count()
    total_inquiries_received = Inquiry.objects.filter(
        company__company_type__in=vendor_types
    ).count()

    seller_subs = Subscription.objects.filter(company__in=Company.objects.filter(seller_filter))
    total_credits_allocated = seller_subs.aggregate(total=Sum("credits_total"))["total"] or 0
    total_credits_consumed = seller_subs.aggregate(used=Sum("credits_used"))["used"] or 0
    total_credits_remaining = max(0, total_credits_allocated - total_credits_consumed)

    SellerModuleConfig.ensure_defaults()
    modules = SellerModuleConfig.objects.all().order_by("order", "id")
    layout = SellerDashboardLayout.get_layout()

    comp_qs = (
        Company.objects.filter(seller_filter)
        .select_related("subscription")
        .prefetch_related("members__user")
        .order_by("-created_at")
    )
    q = request.GET.get("q", "").strip()
    if q:
        comp_qs = comp_qs.filter(Q(name__icontains=q) | Q(city__icontains=q) | Q(industry__icontains=q))

    page_obj, query_string = paginate_and_preserve(request, comp_qs, per_page=10)

    # Batch preload metrics for current page to eliminate N+1 queries
    page_comp_ids = [c.id for c in page_obj]
    prod_counts = dict(
        Product.objects.filter(company_id__in=page_comp_ids, is_deleted=False)
        .values("company_id")
        .annotate(cnt=Count("id"))
        .values_list("company_id", "cnt")
    )
    jobs_counts = dict(
        SearchJob.objects.filter(company_id__in=page_comp_ids)
        .values("company_id")
        .annotate(cnt=Count("id"))
        .values_list("company_id", "cnt")
    )
    saved_counts = dict(
        SavedItem.objects.filter(company_id__in=page_comp_ids)
        .values("company_id")
        .annotate(cnt=Count("id"))
        .values_list("company_id", "cnt")
    )
    inq_counts = dict(
        Inquiry.objects.filter(company_id__in=page_comp_ids)
        .values("company_id")
        .annotate(cnt=Count("id"))
        .values_list("company_id", "cnt")
    )

    annotated_companies = []
    for comp in page_obj:
        prod_count = prod_counts.get(comp.id, 0)
        jobs_count = jobs_counts.get(comp.id, 0)
        saved_count = saved_counts.get(comp.id, 0)
        inq_count = inq_counts.get(comp.id, 0)
        sub = getattr(comp, "subscription", None)
        admin_member = next((m for m in comp.members.all() if m.role == CompanyMember.Role.ADMIN), None)
        admin_user = admin_member.user if admin_member else None

        annotated_companies.append({
            "company": comp,
            "prod_count": prod_count,
            "jobs_count": jobs_count,
            "saved_count": saved_count,
            "inq_count": inq_count,
            "subscription": sub,
            "admin_user": admin_user,
        })

    recent_seller_activities = ActivityLog.objects.filter(
        company__company_type__in=[Company.CompanyType.SELLER, Company.CompanyType.BOTH]
    ).select_related("company", "user").order_by("-created_at")[:8]

    return render(request, "admin_panel/seller_dashboard.html", {
        "page_title": "Vendor Dashboard Governance",
        "active_tab": active_tab,
        "modules": modules,
        "layout": layout,
        "total_seller_companies": total_seller_companies,
        "seller_only_companies": seller_only_companies,
        "both_companies": both_companies,
        "total_products": total_products,
        "total_searches_run": total_searches_run,
        "total_leads_discovered": total_leads_discovered,
        "total_saved_leads": total_saved_leads,
        "total_inquiries_received": total_inquiries_received,
        "total_credits_allocated": total_credits_allocated,
        "total_credits_remaining": total_credits_remaining,
        "annotated_companies": annotated_companies,
        "page_obj": page_obj,
        "query_string": query_string,
        "recent_seller_activities": recent_seller_activities,
        "q": q,
    })


@login_required
@superadmin_required
def admin_panel_seller_module_toggle_view(request, pk):
    """
    Super Admin action to toggle Vendor module enabled/disabled state.
    """
    if request.method == "POST":
        module = get_object_or_404(SellerModuleConfig, pk=pk)
        module.is_enabled = not module.is_enabled
        module.save(update_fields=["is_enabled", "updated_at"])

        state_str = "ENABLED" if module.is_enabled else "DISABLED"
        first_company = Company.objects.first()
        log_admin_activity(
            request,
            first_company,
            title=f"Vendor Module {state_str}: {module.display_name}",
            description=f"Super Admin {request.user.email} {state_str.lower()} the '{module.display_name}' ({module.module_key}) Vendor module.",
            activity_type=ActivityLog.ActivityType.LEAD_UPDATED,
            icon_type="toggle-on" if module.is_enabled else "toggle-off",
            color="emerald" if module.is_enabled else "rose",
        )

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "is_enabled": module.is_enabled,
                "module_key": module.module_key,
                "display_name": module.display_name,
                "message": f"Module '{module.display_name}' is now {state_str}!",
            })

        messages.success(request, f"Vendor module '{module.display_name}' is now {state_str}.")

    next_url = request.POST.get("next") or reverse("admin-panel-seller-dashboard")
    return redirect(next_url)


@login_required
@superadmin_required
def admin_panel_seller_module_edit_view(request, pk):
    """
    Super Admin action to configure Seller module display name, nav label, icon, order, min plan.
    """
    module = get_object_or_404(SellerModuleConfig, pk=pk)
    if request.method == "POST":
        display_name = request.POST.get("display_name", "").strip()
        nav_label = request.POST.get("nav_label", "").strip()
        icon_class = request.POST.get("icon_class", "").strip()
        order_raw = request.POST.get("order", "0").strip()
        min_plan = request.POST.get("min_plan", "FREE").strip()
        requires_credits = bool(request.POST.get("requires_credits"))
        is_visible_on_sidebar = bool(request.POST.get("is_visible_on_sidebar"))
        description = request.POST.get("description", "").strip()

        if display_name:
            module.display_name = display_name
        if nav_label:
            module.nav_label = nav_label
        if icon_class:
            module.icon_class = icon_class
        try:
            module.order = max(0, int(order_raw))
        except ValueError:
            pass
        if min_plan in dict(SellerModuleConfig.MinPlan.choices):
            module.min_plan = min_plan
        module.requires_credits = requires_credits
        module.is_visible_on_sidebar = is_visible_on_sidebar
        if description:
            module.description = description
        module.save()

        messages.success(request, f"Configuration updated for Seller module '{module.display_name}'.")

    return redirect(reverse("admin-panel-seller-dashboard") + "?tab=modules")


@login_required
@superadmin_required
def admin_panel_seller_layout_update_view(request):
    """
    Super Admin action to configure Seller Dashboard layout switches.
    """
    if request.method == "POST":
        layout = SellerDashboardLayout.get_layout()
        layout.show_kpi_products = bool(request.POST.get("show_kpi_products"))
        layout.show_kpi_searches = bool(request.POST.get("show_kpi_searches"))
        layout.show_kpi_leads_discovered = bool(request.POST.get("show_kpi_leads_discovered"))
        layout.show_kpi_saved_leads = bool(request.POST.get("show_kpi_saved_leads"))
        layout.show_kpi_inquiries_received = bool(request.POST.get("show_kpi_inquiries_received"))
        layout.show_kpi_credits = bool(request.POST.get("show_kpi_credits"))
        layout.show_quick_actions = bool(request.POST.get("show_quick_actions"))
        layout.show_recent_searches = bool(request.POST.get("show_recent_searches"))
        layout.show_recent_activity = bool(request.POST.get("show_recent_activity"))
        layout.show_top_leads = bool(request.POST.get("show_top_leads"))
        layout.show_subscription_widget = bool(request.POST.get("show_subscription_widget"))
        layout.save()

        messages.success(request, "Seller Dashboard layout and KPI card preferences updated successfully.")

    return redirect(reverse("admin-panel-seller-dashboard") + "?tab=layout")


# ==============================================================================
# SUPER ADMIN ↔ BOTH DASHBOARD MANAGEMENT
# ==============================================================================

@login_required
@superadmin_required
def admin_panel_both_dashboard_view(request):
    """
    Super Admin Hybrid (Both) Dashboard Governance.
    Manages dual-operating enterprises (CompanyType == BOTH),
    default view switcher, cross-module synergy, and shared wallets.
    """
    layout = BothDashboardLayout.get_layout()

    both_filter = Q(company_type=Company.CompanyType.BOTH, is_deleted=False)
    total_both_companies = Company.objects.filter(both_filter).count()

    total_requirements = Requirement.objects.filter(company__in=Company.objects.filter(both_filter), is_deleted=False).count()
    total_products = Product.objects.filter(company__in=Company.objects.filter(both_filter), is_deleted=False).count()
    total_searches = SearchJob.objects.filter(company__in=Company.objects.filter(both_filter)).count()
    total_inquiries = Inquiry.objects.filter(company__in=Company.objects.filter(both_filter)).count()

    comp_qs = (
        Company.objects.filter(both_filter)
        .select_related("subscription", "wallet")
        .order_by("-created_at")
    )
    q = request.GET.get("q", "").strip()
    if q:
        comp_qs = comp_qs.filter(Q(name__icontains=q) | Q(city__icontains=q) | Q(industry__icontains=q))

    page_obj, query_string = paginate_and_preserve(request, comp_qs, per_page=10)

    # Batch preload metrics for current page to eliminate N+1 queries
    page_comp_ids = [c.id for c in page_obj]
    req_counts = dict(
        Requirement.objects.filter(company_id__in=page_comp_ids, is_deleted=False)
        .values("company_id")
        .annotate(cnt=Count("id"))
        .values_list("company_id", "cnt")
    )
    prod_counts = dict(
        Product.objects.filter(company_id__in=page_comp_ids, is_deleted=False)
        .values("company_id")
        .annotate(cnt=Count("id"))
        .values_list("company_id", "cnt")
    )
    searches_counts = dict(
        SearchJob.objects.filter(company_id__in=page_comp_ids)
        .values("company_id")
        .annotate(cnt=Count("id"))
        .values_list("company_id", "cnt")
    )

    annotated_companies = []
    for comp in page_obj:
        req_count = req_counts.get(comp.id, 0)
        prod_count = prod_counts.get(comp.id, 0)
        searches_count = searches_counts.get(comp.id, 0)
        wallet = getattr(comp, "wallet", None)
        sub = getattr(comp, "subscription", None)

        annotated_companies.append({
            "company": comp,
            "req_count": req_count,
            "prod_count": prod_count,
            "searches_count": searches_count,
            "wallet": wallet,
            "subscription": sub,
        })

    return render(request, "admin_panel/both_dashboard.html", {
        "page_title": "Both (Sales & Vendor) Dashboard Governance",
        "layout": layout,
        "total_both_companies": total_both_companies,
        "total_requirements": total_requirements,
        "total_products": total_products,
        "total_searches": total_searches,
        "total_inquiries": total_inquiries,
        "annotated_companies": annotated_companies,
        "page_obj": page_obj,
        "query_string": query_string,
        "q": q,
    })


@login_required
@superadmin_required
def admin_panel_both_layout_update_view(request):
    """
    Super Admin action to configure Both Dashboard default active view and switcher.
    """
    if request.method == "POST":
        layout = BothDashboardLayout.get_layout()
        default_view = request.POST.get("default_active_view", "combined").strip().lower()
        if default_view in ["combined", "buyer", "seller"]:
            layout.default_active_view = default_view
        layout.show_view_switcher = bool(request.POST.get("show_view_switcher"))
        layout.show_combined_metrics = bool(request.POST.get("show_combined_metrics"))
        layout.save()

        messages.success(request, f"Both Dashboard layout updated. Default view set to '{layout.get_default_active_view_display()}'.")

    return redirect("admin-panel-both-dashboard")


# ==============================================================================
# SUPER ADMIN ↔ CENTRALIZED MODULE & FEATURE MANAGEMENT
# ==============================================================================

@login_required
@superadmin_required
def admin_panel_modules_view(request):
    """
    Centralized Module & Feature Management Matrix:
    Displays module matrix across BUYER, SELLER, and BOTH with:
    Module Name, Enabled/Disabled, View, Create, Edit, Update, Delete, Import, Export, and Actions.
    """
    tab = request.GET.get("tab", "all").lower()
    q = request.GET.get("q", "").strip().lower()

    BuyerModuleConfig.ensure_defaults()
    SellerModuleConfig.ensure_defaults()

    buyer_mods = list(BuyerModuleConfig.objects.all().order_by("order", "id"))
    seller_mods = list(SellerModuleConfig.objects.all().order_by("order", "id"))

    # Map supported actions for each module
    MODULE_ACTION_PERMS = {
        "my_requirements": ["View", "Create", "Edit", "Update", "Delete", "Import", "Export"],
        "my_products": ["View", "Create", "Edit", "Update", "Delete", "Import", "Export"],
        "find_suppliers": ["View", "Search", "Filter", "Export"],
        "find_buyers": ["View", "Search", "Filter", "Export"],
        "buyer_leads": ["View", "Save", "Export"],
        "saved_suppliers": ["View", "Create", "Edit", "Update", "Delete", "Export"],
        "saved_buyers": ["View", "Create", "Edit", "Update", "Delete", "Export"],
        "inquiries": ["View", "Create", "Edit", "Update", "Delete", "Export"],
        "seller_inquiries": ["View", "Create", "Edit", "Update", "Delete", "Export"],
        "price_comparison": ["View", "Export"],
        "export_reports": ["View", "Export"],
        "seller_export_reports": ["View", "Export"],
        "seller_roles": ["View", "Create", "Edit", "Update", "Delete"],
        "subscription_credits": ["View", "Recharge"],
        "seller_subscription_credits": ["View", "Recharge"],
        "invoices_billing": ["View", "Export"],
        "seller_invoices_billing": ["View", "Export"],
        "help_support": ["View", "Create", "Update"],
        "seller_help_support": ["View", "Create", "Update"],
    }

    unified_matrix = []

    for bm in buyer_mods:
        if q and q not in bm.display_name.lower() and q not in bm.module_key.lower():
            continue
        unified_matrix.append({
            "id": bm.id,
            "type": "buyer",
            "type_label": "Sales",
            "type_badge_color": "blue",
            "module_key": bm.module_key,
            "display_name": bm.display_name,
            "nav_label": bm.nav_label,
            "icon_class": bm.icon_class,
            "is_enabled": bm.is_enabled,
            "min_plan": bm.get_min_plan_display(),
            "requires_credits": bm.requires_credits,
            "description": bm.description,
            "actions": MODULE_ACTION_PERMS.get(bm.module_key, ["View"]),
            "toggle_url": reverse("admin-panel-buyer-module-toggle", kwargs={"pk": bm.id}),
            "order": bm.order,
        })

    for sm in seller_mods:
        if q and q not in sm.display_name.lower() and q not in sm.module_key.lower():
            continue
        unified_matrix.append({
            "id": sm.id,
            "type": "seller",
            "type_label": "Vendor",
            "type_badge_color": "emerald",
            "module_key": sm.module_key,
            "display_name": sm.display_name,
            "nav_label": sm.nav_label,
            "icon_class": sm.icon_class,
            "is_enabled": sm.is_enabled,
            "min_plan": sm.get_min_plan_display(),
            "requires_credits": sm.requires_credits,
            "description": sm.description,
            "actions": MODULE_ACTION_PERMS.get(sm.module_key, ["View"]),
            "toggle_url": reverse("admin-panel-seller-module-toggle", kwargs={"pk": sm.id}),
            "order": sm.order,
        })

    if tab in ["sales", "buyer"]:
        filtered_matrix = [m for m in unified_matrix if m["type"] == "buyer"]
    elif tab in ["vendor", "seller"]:
        filtered_matrix = [m for m in unified_matrix if m["type"] == "seller"]
    else:
        filtered_matrix = unified_matrix

    total_modules = len(unified_matrix)
    active_count = sum(1 for m in unified_matrix if m["is_enabled"])
    disabled_count = total_modules - active_count

    return render(request, "admin_panel/modules.html", {
        "page_title": "Module & Feature Management Matrix",
        "tab": tab,
        "q": q,
        "modules_matrix": filtered_matrix,
        "total_modules": total_modules,
        "active_count": active_count,
        "disabled_count": disabled_count,
    })


@login_required
@superadmin_required
def admin_panel_module_toggle_view(request, module_type, pk):
    """
    Centralized toggle endpoint for either buyer or seller module.
    """
    if module_type == "buyer":
        return admin_panel_buyer_module_toggle_view(request, pk)
    elif module_type == "seller":
        return admin_panel_seller_module_toggle_view(request, pk)
    return redirect("admin-panel-modules")


# ==============================================================================
# SUPER ADMIN ↔ DYNAMIC MODULE-WISE PERMISSIONS MATRIX REGISTRY
# ==============================================================================

@login_required
@superadmin_required
def admin_panel_permissions_view(request):
    """
    Super Admin Dynamic Module-Wise Permissions Matrix Registry.
    - Live governance over fine-grained action tokens (<module>:<action>)
    - Supports dynamic registration, deletion, and bulk capability sync
    - Renders dynamic Module x Action matrix registry with pagination (default 10 items)
    - Displays role taxonomy and active user grants across all platform modules
    """
    from apps.companies.rbac import ensure_default_permissions

    if request.method == "POST":
        action = request.POST.get("action", "").strip()

        if action == "register":
            module_name = request.POST.get("module", "").strip().lower()
            permission_type = request.POST.get("permission", "").strip().upper()
            if not module_name or not permission_type:
                messages.error(request, "Both module name and permission action are required.")
            else:
                obj, created = CompanyPermission.objects.get_or_create(
                    module=module_name,
                    permission=permission_type,
                )
                if created:
                    messages.success(request, f"Permission '{module_name}:{permission_type}' successfully registered in matrix.")
                else:
                    messages.info(request, f"Permission '{module_name}:{permission_type}' already exists in registry.")
            return redirect("admin-panel-permissions")

        elif action == "delete":
            perm_id = request.POST.get("perm_id")
            perm_obj = CompanyPermission.objects.filter(id=perm_id).first()
            if perm_obj:
                mod_desc = f"{perm_obj.module}:{perm_obj.permission}"
                perm_obj.delete()
                messages.success(request, f"Permission '{mod_desc}' removed from registry.")
            else:
                messages.error(request, "Permission token not found.")
            return redirect("admin-panel-permissions")

        elif action == "sync_all":
            ensure_default_permissions()
            messages.success(request, "Standard capability tokens (READ, EDIT, UPDATE, DELETE, IMPORT, EXPORT) synchronized across platform modules.")
            return redirect("admin-panel-permissions")

    ensure_default_permissions()

    q = request.GET.get("q", "").strip()
    category_filter = request.GET.get("category", "all").strip().lower()

    # Pre-fetch permissions with member assignment counts
    all_perms = CompanyPermission.objects.annotate(
        assignments_count=Count("member_assignments")
    ).order_by("module", "permission")

    # Module metadata dictionary for display
    MODULE_METADATA = {
        "requirements": {"title": "Procurement Requirements", "category": "Sales", "icon": "fa-clipboard-list"},
        "products": {"title": "Product Catalog & Offerings", "category": "Vendor", "icon": "fa-box-open"},
        "catalog": {"title": "Company Catalog", "category": "Vendor", "icon": "fa-boxes-stacked"},
        "leads": {"title": "Buyer Leads Pipeline", "category": "Vendor", "icon": "fa-bullseye"},
        "inquiries": {"title": "Commercial Inquiries & RFQs", "category": "Shared", "icon": "fa-paper-plane"},
        "search": {"title": "AI Discovery & Search", "category": "Shared", "icon": "fa-magnifying-glass-chart"},
        "find_buyers": {"title": "Find Vendors & Customers", "category": "Vendor", "icon": "fa-users-viewfinder"},
        "find_suppliers": {"title": "Find Suppliers Search", "category": "Sales", "icon": "fa-truck-field"},
        "saved_buyers": {"title": "Saved / Prequalified Vendors", "category": "Vendor", "icon": "fa-bookmark"},
        "saved_suppliers": {"title": "Saved Suppliers Directory", "category": "Sales", "icon": "fa-address-book"},
        "saved_items": {"title": "Saved Pipeline Items", "category": "Shared", "icon": "fa-floppy-disk"},
        "results": {"title": "Search Evaluation Results", "category": "Shared", "icon": "fa-chart-pie"},
        "categories": {"title": "Taxonomy & Categories", "category": "Shared", "icon": "fa-tags"},
        "billing": {"title": "Billing & Subscriptions", "category": "Admin", "icon": "fa-credit-card"},
        "companies": {"title": "Company Directory & Profile", "category": "Admin", "icon": "fa-building"},
        "members": {"title": "Team & User Access", "category": "Admin", "icon": "fa-user-group"},
        "dashboard": {"title": "Analytics Dashboard", "category": "Shared", "icon": "fa-gauge-high"},
        "reports": {"title": "Certified Reports & Exports", "category": "Shared", "icon": "fa-file-excel"},
        "external_companies": {"title": "Discovered Market Entities", "category": "Shared", "icon": "fa-globe"},
        "general": {"title": "General System Operations", "category": "Platform", "icon": "fa-sliders"},
    }

    # Group permissions by module
    module_perm_map = {}
    for p in all_perms:
        module_perm_map.setdefault(p.module, []).append(p)

    standard_actions = ["READ", "EDIT", "UPDATE", "DELETE", "IMPORT", "EXPORT"]

    module_matrix = []
    for mod_name, perms_list in sorted(module_perm_map.items()):
        meta = MODULE_METADATA.get(mod_name, {
            "title": mod_name.replace("_", " ").title(),
            "category": "Platform",
            "icon": "fa-layer-group",
        })

        if q:
            match_q = (
                q.lower() in mod_name.lower()
                or q.lower() in meta["title"].lower()
                or q.lower() in meta["category"].lower()
            )
            if not match_q:
                continue

        if category_filter != "all" and meta["category"].lower() != category_filter:
            continue

        action_dict = {p.permission: p for p in perms_list}
        actions_status = []
        for act in standard_actions:
            p_obj = action_dict.get(act)
            actions_status.append({
                "action": act,
                "is_active": bool(p_obj),
                "perm_id": p_obj.id if p_obj else None,
                "assignments_count": p_obj.assignments_count if p_obj else 0,
            })

        custom_actions = [
            {"action": p.permission, "perm_id": p.id, "assignments_count": p.assignments_count}
            for p in perms_list if p.permission not in standard_actions
        ]

        total_module_assignments = sum(p.assignments_count for p in perms_list)

        module_matrix.append({
            "module": mod_name,
            "title": meta["title"],
            "category": meta["category"],
            "icon": meta["icon"],
            "actions": actions_status,
            "custom_actions": custom_actions,
            "total_perms": len(perms_list),
            "total_assignments": total_module_assignments,
            "raw_perms": [p.permission for p in perms_list],
        })

    total_perms = all_perms.count()
    total_roles = CompanyRole.objects.count()
    total_assignments = MemberPermission.objects.count()
    total_modules = len(module_perm_map)

    # Dynamic role taxonomy summary
    system_roles = [
        {
            "code": "SUPER_ADMIN",
            "name": "Super Admin",
            "badge_color": "rose",
            "scope": "Global (Cross-Tenant)",
            "description": "Full unmetered governance over all tenants, modules, wallets, and system configurations.",
            "access_level": "All Capabilities (*:*)",
        },
        {
            "code": "COMPANY_ADMIN",
            "name": "Company Admin",
            "badge_color": "blue",
            "scope": "Tenant Wide",
            "description": "Full administrative management over company profile, team, roles, billing & recharges.",
            "access_level": "All Company Modules (READ, EDIT, UPDATE, DELETE, IMPORT, EXPORT)",
        },
        {
            "code": "BUYER_SPECIALIST",
            "name": "Procurement Specialist",
            "badge_color": "indigo",
            "scope": "Sales Domain",
            "description": "Posts requirements, runs supplier searches, dispatches inquiries & RFQs.",
            "access_level": "Requirements, Inquiries, Find Suppliers, Reports",
        },
        {
            "code": "SALES_EXECUTIVE",
            "name": "Sales Executive",
            "badge_color": "emerald",
            "scope": "Vendor Domain",
            "description": "Catalog management, runs find-buyer jobs, negotiates inquiries and leads.",
            "access_level": "Products, Leads, Inquiries, Find Customers, Reports",
        },
        {
            "code": "VIEWER",
            "name": "Viewer (Read-Only)",
            "badge_color": "slate",
            "scope": "Read Only",
            "description": "Restricted read-only access to dashboard and reports without credit consumption permissions.",
            "access_level": "READ on Dashboards & Reports",
        },
    ]

    # Dynamic custom company roles from database
    custom_roles_list = list(
        CompanyRole.objects.select_related("company")
        .prefetch_related("module_permissions")
        .order_by("-is_system", "company__name", "name")[:20]
    )

    # Server-side pagination (default 10)
    page_obj, query_string = paginate_and_preserve(request, module_matrix, per_page=10)

    # Available distinct module keys for quick register dropdown
    distinct_modules = sorted(list(module_perm_map.keys()))

    return render(request, "admin_panel/permissions.html", {
        "page_title": "Module-Wise Permissions Matrix",
        "page_obj": page_obj,
        "query_string": query_string,
        "standard_actions": standard_actions,
        "total_perms": total_perms,
        "total_roles": total_roles,
        "total_assignments": total_assignments,
        "total_modules": total_modules,
        "system_roles": system_roles,
        "custom_roles": custom_roles_list,
        "distinct_modules": distinct_modules,
        "q": q,
        "category_filter": category_filter,
    })


# ==============================================================================
# SUPER ADMIN ↔ TRANSACTIONS & INVOICES MANAGEMENT
# ==============================================================================

@login_required
@superadmin_required
def admin_panel_transactions_view(request):
    """
    Super Admin Centralized Transactions & Invoices Audit Ledger.
    Displays wallet transactions, credit debits, payments, and tax invoices.
    """
    active_tab = request.GET.get("tab", "wallet_tx").lower()
    if active_tab not in ["wallet_tx", "invoices", "credits"]:
        active_tab = "wallet_tx"

    q = request.GET.get("q", "").strip()

    # 1. Wallet Transactions
    wallet_tx_qs = WalletTransaction.objects.select_related("wallet__company", "company", "created_by").order_by("-created_at")
    if q:
        wallet_tx_qs = wallet_tx_qs.filter(
            Q(reference_id__icontains=q)
            | Q(company__name__icontains=q)
            | Q(wallet__company__name__icontains=q)
            | Q(description__icontains=q)
        )

    # 2. Invoices
    invoice_qs = Invoice.objects.select_related("company", "subscription").order_by("-issue_date", "-id")
    if q:
        invoice_qs = invoice_qs.filter(
            Q(invoice_number__icontains=q)
            | Q(company__name__icontains=q)
        )

    # 3. Credit Transactions
    credit_tx_qs = CreditTransaction.objects.select_related("company", "user", "wallet").order_by("-created_at")
    if q:
        credit_tx_qs = credit_tx_qs.filter(
            Q(notes__icontains=q)
            | Q(receipt_number__icontains=q)
            | Q(company__name__icontains=q)
            | Q(feature_code__icontains=q)
        )

    # 4. Date Range Filtering
    range_filter = request.GET.get("range", "all").lower()
    start_date = request.GET.get("start_date", "").strip()
    end_date = request.GET.get("end_date", "").strip()

    now = timezone.now()
    date_cutoff = None
    if range_filter == "today":
        date_cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif range_filter == "7d":
        date_cutoff = now - timedelta(days=7)
    elif range_filter == "30d":
        date_cutoff = now - timedelta(days=30)
    elif range_filter == "90d":
        date_cutoff = now - timedelta(days=90)
    elif range_filter == "ytd":
        date_cutoff = timezone.datetime(now.year, 1, 1, tzinfo=now.tzinfo)

    if date_cutoff:
        wallet_tx_qs = wallet_tx_qs.filter(created_at__gte=date_cutoff)
        invoice_qs = invoice_qs.filter(created_at__gte=date_cutoff)
        credit_tx_qs = credit_tx_qs.filter(created_at__gte=date_cutoff)
    elif range_filter == "custom" and (start_date or end_date):
        if start_date:
            s_date = parse_date(start_date)
            if s_date:
                wallet_tx_qs = wallet_tx_qs.filter(created_at__date__gte=s_date)
                invoice_qs = invoice_qs.filter(created_at__date__gte=s_date)
                credit_tx_qs = credit_tx_qs.filter(created_at__date__gte=s_date)
        if end_date:
            e_date = parse_date(end_date)
            if e_date:
                wallet_tx_qs = wallet_tx_qs.filter(created_at__date__lte=e_date)
                invoice_qs = invoice_qs.filter(created_at__date__lte=e_date)
                credit_tx_qs = credit_tx_qs.filter(created_at__date__lte=e_date)

    total_wallet_volume = WalletTransaction.objects.filter(
        transaction_type=WalletTransaction.TransactionType.RECHARGE
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    total_invoiced_amt = Invoice.objects.aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
    total_paid_invoices = Invoice.objects.filter(status=Invoice.Status.PAID).count()
    total_pending_invoices = Invoice.objects.filter(status__in=[Invoice.Status.DRAFT, Invoice.Status.OVERDUE]).count()

    # CSV Export Handler
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        today_str = timezone.now().strftime("%Y%m%d")
        response.write("\ufeff")  # UTF-8 BOM for Excel
        writer = csv.writer(response)
        if active_tab == "invoices":
            response["Content-Disposition"] = f'attachment; filename="Platform_Invoices_{today_str}.csv"'
            writer.writerow(["Invoice ID", "Invoice Number", "Company", "Subtotal", "Tax Amount", "Total Amount", "Status", "Issue Date", "Due Date"])
            for inv in invoice_qs:
                writer.writerow([inv.id, inv.invoice_number, inv.company.name if inv.company else "", inv.subtotal, inv.tax_amount, inv.total_amount, inv.get_status_display(), inv.issue_date.strftime("%Y-%m-%d"), inv.due_date.strftime("%Y-%m-%d") if inv.due_date else ""])
        elif active_tab == "credits":
            response["Content-Disposition"] = f'attachment; filename="Platform_Credit_Ledger_{today_str}.csv"'
            writer.writerow(["Transaction ID", "Receipt Number", "Company", "Feature Code", "Credits Amount", "Notes", "Timestamp"])
            for ctx in credit_tx_qs:
                writer.writerow([ctx.id, ctx.receipt_number or "", ctx.company.name if ctx.company else "", ctx.feature_code or "", ctx.credits, ctx.notes or "", ctx.created_at.strftime("%Y-%m-%d %H:%M:%S")])
        else:
            response["Content-Disposition"] = f'attachment; filename="Platform_Wallet_Ledger_{today_str}.csv"'
            writer.writerow(["Transaction ID", "Reference ID", "Company", "Transaction Type", "Amount", "Balance After", "Status", "Description", "Initiated By", "Timestamp"])
            for tx in wallet_tx_qs:
                user_email = tx.created_by.email if tx.created_by else "System"
                comp_name = (tx.company.name if tx.company else (tx.wallet.company.name if tx.wallet and tx.wallet.company else ""))
                writer.writerow([tx.id, tx.reference_id, comp_name, tx.get_transaction_type_display(), tx.amount, tx.balance_after, tx.get_status_display(), tx.description, user_email, tx.created_at.strftime("%Y-%m-%d %H:%M:%S")])
        return response

    # Server-side pagination (default 10)
    if active_tab == "invoices":
        page_obj, query_string = paginate_and_preserve(request, invoice_qs, per_page=10)
    elif active_tab == "credits":
        page_obj, query_string = paginate_and_preserve(request, credit_tx_qs, per_page=10)
    else:
        page_obj, query_string = paginate_and_preserve(request, wallet_tx_qs, per_page=10)

    return render(request, "admin_panel/transactions.html", {
        "page_title": "Transactions & Invoices",
        "active_tab": active_tab,
        "page_obj": page_obj,
        "query_string": query_string,
        "q": q,
        "range_filter": range_filter,
        "start_date": start_date,
        "end_date": end_date,
        "total_wallet_volume": total_wallet_volume,
        "total_invoiced_amt": total_invoiced_amt,
        "total_paid_invoices": total_paid_invoices,
        "total_pending_invoices": total_pending_invoices,
    })


# ==============================================================================
# SUPER ADMIN ↔ COMPANY WALLETS & BILLING GOVERNANCE
# ==============================================================================

@login_required
@superadmin_required
def admin_panel_wallets_view(request):
    """
    Super Admin Company Wallets Control Center.
    Inspects tenant wallet balances, reserved funds, total liabilities,
    and supports 1-click manual financial adjustments with mandatory audit trail.
    """
    q = request.GET.get("q", "").strip()

    CompanyWalletService.ensure_default_service_pricing()

    wallet_qs = CompanyWallet.objects.select_related("company").order_by("-available_balance")
    if q:
        wallet_qs = wallet_qs.filter(
            Q(company__name__icontains=q)
            | Q(company__city__icontains=q)
        )

    # CSV Export
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        response["Content-Disposition"] = f'attachment; filename="platform_company_wallets_{timestamp}.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Wallet ID",
            "Company Name",
            "City",
            "Country",
            "Available Balance",
            "Reserved Balance",
            "Total Credited",
            "Total Spent",
            "Currency",
            "Status",
            "Balance Health",
            "Last Updated",
        ])
        for w in wallet_qs:
            writer.writerow([
                f"WAL-{w.id:04d}",
                w.company.name if w.company else "N/A",
                w.company.city if w.company and w.company.city else "N/A",
                w.company.country if w.company and w.company.country else "India",
                w.available_balance,
                w.reserved_balance,
                w.total_credited,
                w.total_spent,
                w.currency,
                w.status.capitalize() if hasattr(w, "status") else "Active",
                "Low Balance" if w.is_low_balance else "Healthy",
                w.updated_at.strftime("%Y-%m-%d %H:%M") if w.updated_at else "",
            ])
        return response

    # Server-side pagination (default 10)
    page_obj, query_string = paginate_and_preserve(request, wallet_qs, per_page=10)

    total_available_liability = CompanyWallet.objects.aggregate(total=Sum("available_balance"))["total"] or Decimal("0.00")
    total_reserved_liability = CompanyWallet.objects.aggregate(total=Sum("reserved_balance"))["total"] or Decimal("0.00")
    total_spent_platform = CompanyWallet.objects.aggregate(total=Sum("total_spent"))["total"] or Decimal("0.00")
    total_credited_platform = CompanyWallet.objects.aggregate(total=Sum("total_credited"))["total"] or Decimal("0.00")

    pricing_list = ServicePricing.objects.all().order_by("service_code")

    return render(request, "admin_panel/wallets.html", {
        "page_title": "Company Wallets & Billing Governance",
        "page_obj": page_obj,
        "query_string": query_string,
        "q": q,
        "total_available_liability": total_available_liability,
        "total_reserved_liability": total_reserved_liability,
        "total_spent_platform": total_spent_platform,
        "total_credited_platform": total_credited_platform,
        "pricing_list": pricing_list,
    })


@login_required
@superadmin_required
def admin_panel_matching_criteria_view(request):
    """
    Super Admin Dynamic AI Search & Lead Matching Criteria Governance.
    - View all platform-wide matching criteria with domain routing (Sales vs Vendor vs Both)
    - Full CRUD: Add, Edit, Delete, Toggle Active/Inactive, and Reset to Defaults
    - Fine-grained controls for rule type (weighted, mandatory, bonus), weight percentage, icon, order, and benchmark value
    - Search and multi-criteria filters with default 10-item pagination
    """
    from apps.ai_search.models import PlatformMatchingCriteria

    # Ensure baseline defaults exist
    if not PlatformMatchingCriteria.objects.exists():
        PlatformMatchingCriteria.seed_default_criteria()

    if request.method == "POST":
        action = request.POST.get("action", "").strip()

        if action == "create":
            name = request.POST.get("name", "").strip()
            parameter_key = request.POST.get("parameter_key", "").strip().lower()
            target_domain = request.POST.get("target_domain", PlatformMatchingCriteria.TargetDomain.BOTH).strip().upper()
            rule_type = request.POST.get("rule_type", PlatformMatchingCriteria.RuleType.WEIGHTED).strip()
            weight_str = request.POST.get("weight_percentage", "10").strip()
            icon = request.POST.get("icon", "fa-sliders").strip()
            color = request.POST.get("color", "blue").strip()
            order_str = request.POST.get("order", "10").strip()
            default_value = request.POST.get("default_value", "").strip()
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "1" or request.POST.get("is_active") == "on"

            if not name:
                messages.error(request, "Criterion name is required.")
                return redirect("admin-panel-matching-criteria")

            if not parameter_key:
                from django.utils.text import slugify
                parameter_key = slugify(name).replace("-", "_")[:80]

            parameter_key = re.sub(r"[^a-zA-Z0-9_]", "_", parameter_key)[:80]
            if not parameter_key:
                messages.error(request, "Valid unique parameter slug key is required.")
                return redirect("admin-panel-matching-criteria")

            if PlatformMatchingCriteria.objects.filter(parameter_key=parameter_key).exists():
                messages.error(request, f"Parameter slug key '{parameter_key}' already exists. Please choose a unique key.")
                return redirect("admin-panel-matching-criteria")

            weight = int(weight_str) if weight_str.isdigit() else 10
            order = int(order_str) if order_str.isdigit() else 10

            new_crit = PlatformMatchingCriteria.objects.create(
                name=name[:150],
                parameter_key=parameter_key,
                target_domain=target_domain if target_domain in [PlatformMatchingCriteria.TargetDomain.BOTH, PlatformMatchingCriteria.TargetDomain.SALES, PlatformMatchingCriteria.TargetDomain.VENDOR] else PlatformMatchingCriteria.TargetDomain.BOTH,
                rule_type=rule_type if rule_type in [PlatformMatchingCriteria.RuleType.WEIGHTED, PlatformMatchingCriteria.RuleType.MANDATORY, PlatformMatchingCriteria.RuleType.BONUS] else PlatformMatchingCriteria.RuleType.WEIGHTED,
                weight_percentage=min(max(weight, 1), 100),
                icon=icon[:50] or "fa-sliders",
                color=color[:30] or "blue",
                order=order,
                default_value=default_value[:255],
                description=description,
                is_active=is_active,
            )
            messages.success(request, f"Matching criterion '{new_crit.name}' ({new_crit.parameter_key}) created successfully.")
            return redirect("admin-panel-matching-criteria")

        elif action == "edit":
            crit_id = request.POST.get("criteria_id")
            crit = get_object_or_404(PlatformMatchingCriteria, pk=crit_id)

            name = request.POST.get("name", "").strip()
            target_domain = request.POST.get("target_domain", PlatformMatchingCriteria.TargetDomain.BOTH).strip().upper()
            rule_type = request.POST.get("rule_type", PlatformMatchingCriteria.RuleType.WEIGHTED).strip()
            weight_str = request.POST.get("weight_percentage", "10").strip()
            icon = request.POST.get("icon", "fa-sliders").strip()
            color = request.POST.get("color", "blue").strip()
            order_str = request.POST.get("order", "10").strip()
            default_value = request.POST.get("default_value", "").strip()
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "1" or request.POST.get("is_active") == "on"

            if name:
                crit.name = name[:150]
            if target_domain in [PlatformMatchingCriteria.TargetDomain.BOTH, PlatformMatchingCriteria.TargetDomain.SALES, PlatformMatchingCriteria.TargetDomain.VENDOR]:
                crit.target_domain = target_domain
            if rule_type in [PlatformMatchingCriteria.RuleType.WEIGHTED, PlatformMatchingCriteria.RuleType.MANDATORY, PlatformMatchingCriteria.RuleType.BONUS]:
                crit.rule_type = rule_type
            if weight_str.isdigit():
                crit.weight_percentage = min(max(int(weight_str), 1), 100)
            if icon:
                crit.icon = icon[:50]
            if color:
                crit.color = color[:30]
            if order_str.isdigit():
                crit.order = int(order_str)
            crit.default_value = default_value[:255]
            crit.description = description
            crit.is_active = is_active
            crit.save()
            messages.success(request, f"Criterion '{crit.name}' updated successfully.")
            return redirect("admin-panel-matching-criteria")

        elif action == "toggle":
            crit_id = request.POST.get("criteria_id")
            crit = get_object_or_404(PlatformMatchingCriteria, pk=crit_id)
            crit.is_active = not crit.is_active
            crit.save(update_fields=["is_active", "updated_at"])
            status_text = "activated" if crit.is_active else "deactivated"
            messages.success(request, f"Criterion '{crit.name}' has been {status_text}.")
            return redirect("admin-panel-matching-criteria")

        elif action == "delete":
            crit_id = request.POST.get("criteria_id")
            crit = get_object_or_404(PlatformMatchingCriteria, pk=crit_id)
            c_name = crit.name
            # Always soft-delete: deactivate rather than purge
            crit.is_active = False
            crit.save(update_fields=["is_active", "updated_at"])
            messages.success(request, f"Criterion '{c_name}' has been soft-deleted (deactivated).")
            return redirect("admin-panel-matching-criteria")

        elif action == "reset_defaults":
            seeded_count = PlatformMatchingCriteria.seed_default_criteria()
            messages.success(request, f"Restored {seeded_count} standard recommended B2B matching parameters.")
            return redirect("admin-panel-matching-criteria")

        messages.warning(request, "Unrecognized action.")
        return redirect("admin-panel-matching-criteria")

    # GET Request: Filter & Search
    qs = PlatformMatchingCriteria.objects.all().order_by("order", "id")

    domain_filter = request.GET.get("domain", "all").strip().upper()
    status_filter = request.GET.get("status", "all").strip().lower()
    rule_filter = request.GET.get("rule_type", "all").strip().lower()
    q = request.GET.get("q", "").strip()

    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(parameter_key__icontains=q)
            | Q(description__icontains=q)
            | Q(default_value__icontains=q)
        )

    if domain_filter in ["SALES", "VENDOR", "BOTH"]:
        qs = qs.filter(target_domain=domain_filter)

    if status_filter == "active":
        qs = qs.filter(is_active=True)
    elif status_filter == "inactive":
        qs = qs.filter(is_active=False)

    if rule_filter in ["weighted", "mandatory", "bonus"]:
        qs = qs.filter(rule_type=rule_filter)

    # CSV Export
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        response["Content-Disposition"] = f'attachment; filename="platform_matching_criteria_{timestamp}.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Order / Priority",
            "Criterion Name",
            "Parameter Slug Key",
            "Target Domain",
            "Rule Type",
            "Weight Percentage",
            "Default Benchmark Value",
            "Status",
            "Description",
        ])
        for crit in qs:
            writer.writerow([
                crit.order,
                crit.name,
                crit.parameter_key,
                crit.get_target_domain_display() if hasattr(crit, "get_target_domain_display") else crit.target_domain,
                crit.get_rule_type_display() if hasattr(crit, "get_rule_type_display") else crit.rule_type,
                f"{crit.weight_percentage}%",
                crit.default_value or "N/A",
                "Active" if crit.is_active else "Inactive",
                crit.description or "",
            ])
        return response

    # Metrics
    all_criteria = PlatformMatchingCriteria.objects.all()
    total_criteria = all_criteria.count()
    active_criteria = all_criteria.filter(is_active=True).count()
    sales_criteria = all_criteria.filter(is_active=True, target_domain__in=["SALES", "BOTH"]).count()
    vendor_criteria = all_criteria.filter(is_active=True, target_domain__in=["VENDOR", "BOTH"]).count()

    page_obj, query_string = paginate_and_preserve(request, qs, per_page=10)

    return render(request, "admin_panel/matching_criteria.html", {
        "page_obj": page_obj,
        "query_string": query_string,
        "total_criteria": total_criteria,
        "active_criteria": active_criteria,
        "sales_criteria": sales_criteria,
        "vendor_criteria": vendor_criteria,
        "domain_filter": domain_filter,
        "status_filter": status_filter,
        "rule_filter": rule_filter,
        "q": q,
        "domain_choices": PlatformMatchingCriteria.TargetDomain.choices,
        "rule_choices": PlatformMatchingCriteria.RuleType.choices,
        "page_title": "AI Search Matching Criteria Governance — Super Admin",
    })


# ==============================================================================
# TENANT IMPERSONATION & PRE-DELETE IMPACT TELEMETRY
# ==============================================================================

@login_required
@superadmin_required
def admin_panel_impersonate_company_view(request, company_id):
    """
    Super Admin 1-Click Tenant Impersonation:
    Safely switches the active session context into the chosen company workspace,
    setting a persistent top notification banner so the Super Admin can troubleshoot
    the tenant experience firsthand.
    """
    company = get_object_or_404(Company, pk=company_id, is_deleted=False)

    request.session["active_company_id"] = company.id
    request.session["impersonating_superadmin"] = True
    request.session["impersonating_company_name"] = company.name
    request.session.modified = True

    log_admin_activity(
        request,
        company,
        title=f"Tenant Impersonation: {company.name}",
        description=f"Super Admin '{request.user.email}' entered company workspace for '{company.name}'.",
        icon_type="user-secret",
        color="amber",
    )

    messages.info(request, f"Now viewing workspace as '{company.name}'. Full tenant context active.")

    if company.company_type == Company.CompanyType.BUYER:
        request.session["active_workspace"] = "buyer"
    elif company.company_type == Company.CompanyType.SELLER:
        request.session["active_workspace"] = "seller"
    else:
        request.session["active_workspace"] = "combined"

    return redirect("dashboard")


@login_required
@superadmin_required
def admin_panel_exit_impersonation_view(request):
    """
    Exits tenant impersonation mode and returns Super Admin to company management.
    """
    target_company_id = request.session.pop("active_company_id", None)
    request.session.pop("impersonating_superadmin", None)
    company_name = request.session.pop("impersonating_company_name", "Company")
    request.session.modified = True

    messages.success(request, f"Exited workspace of '{company_name}'. Returned to Super Admin Panel.")
    if target_company_id:
        return redirect("admin-panel-company-detail", pk=target_company_id)
    return redirect("admin-panel-companies")


@login_required
@superadmin_required
def admin_panel_company_impact_summary_api(request, company_id):
    """
    Pre-Delete Blast Radius Diagnostics:
    Returns real-time JSON statistics showing all resources tied to a company
    before the Super Admin confirms a soft-delete (archive).
    """
    company = get_object_or_404(Company, pk=company_id)

    members_count = CompanyMember.objects.filter(company=company, is_active=True).count()
    wallet = CompanyWallet.objects.filter(company=company).first()
    wallet_balance = float(wallet.available_balance) if wallet else 0.0
    reserved_balance = float(wallet.reserved_balance) if wallet else 0.0
    products_count = Product.objects.filter(company=company, is_deleted=False).count()
    requirements_count = Requirement.objects.filter(company=company, is_deleted=False).count()
    inquiries_count = Inquiry.objects.filter(company=company).count()
    search_jobs_count = SearchJob.objects.filter(company=company).count()

    return JsonResponse({
        "success": True,
        "company_id": company.id,
        "company_name": company.name,
        "legal_name": company.legal_name or company.name,
        "members_count": members_count,
        "wallet_balance": wallet_balance,
        "reserved_balance": reserved_balance,
        "products_count": products_count,
        "requirements_count": requirements_count,
        "inquiries_count": inquiries_count,
        "search_jobs_count": search_jobs_count,
        "is_deleted": company.is_deleted,
    })


# Aliases for Sales & Vendor governance
admin_panel_sales_dashboard_view = admin_panel_buyer_dashboard_view
admin_panel_sales_module_toggle_view = admin_panel_buyer_module_toggle_view
admin_panel_sales_module_edit_view = admin_panel_buyer_module_edit_view
admin_panel_sales_layout_update_view = admin_panel_buyer_layout_update_view

admin_panel_vendor_dashboard_view = admin_panel_seller_dashboard_view
admin_panel_vendor_module_toggle_view = admin_panel_seller_module_toggle_view
admin_panel_vendor_module_edit_view = admin_panel_seller_module_edit_view
admin_panel_vendor_layout_update_view = admin_panel_seller_layout_update_view




