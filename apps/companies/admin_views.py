import csv
import datetime
import random
import string
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.db.models import Sum, Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from decimal import Decimal
from apps.companies.models import Company, CompanyMember
from apps.accounts.models import User
from apps.billing.models import (
    Subscription,
    SubscriptionPlan,
    CompanyCreditWallet,
    FeatureCreditCost,
    CreditTransaction,
    UsageRecord,
    Payment,
    Invoice,
)
from apps.billing.services.wallet import CreditWalletService
from apps.ai_search.models import APILog, SearchJob
from apps.dashboard.models import ActivityLog, SupportTicket, PlatformSetting
from apps.leads.models import Inquiry
from apps.catalog.models import Product
from apps.requirements.models import Requirement
from apps.companies.rbac import superadmin_required
from apps.dashboard.services.notification_service import create_notification


@login_required
@superadmin_required
def admin_panel_dashboard_view(request):
    """
    Main Executive Super Admin Dashboard.
    Provides platform-wide KPIs, revenue/plan distributions, AI API usage,
    and live system activity streams across all companies.
    """
    # 1. Company metrics
    total_companies = Company.objects.count()
    active_companies = Company.objects.filter(is_active=True).count()
    verified_companies = Company.objects.filter(is_verified=True).count()
    inactive_companies = total_companies - active_companies

    buyer_companies = Company.objects.filter(company_type=Company.CompanyType.BUYER).count()
    seller_companies = Company.objects.filter(company_type=Company.CompanyType.SELLER).count()
    both_companies = Company.objects.filter(company_type=Company.CompanyType.BOTH).count()

    # 2. User metrics
    total_users = User.objects.count()
    active_users = User.objects.filter(is_active=True).count()
    super_admins = User.objects.filter(Q(is_superuser=True) | Q(is_staff=True)).count()
    company_admins = CompanyMember.objects.filter(role=CompanyMember.Role.ADMIN).count()
    company_users = CompanyMember.objects.filter(role=CompanyMember.Role.USER).count()

    # 3. Subscription & Credit metrics
    sub_free = Subscription.objects.filter(plan=Subscription.Plan.FREE).count()
    sub_pro = Subscription.objects.filter(plan=Subscription.Plan.PRO).count()
    sub_enterprise = Subscription.objects.filter(plan=Subscription.Plan.ENTERPRISE).count()

    sub_free_pct = round((sub_free / total_companies) * 100, 1) if total_companies > 0 else 0
    sub_pro_pct = round((sub_pro / total_companies) * 100, 1) if total_companies > 0 else 0
    sub_enterprise_pct = round((sub_enterprise / total_companies) * 100, 1) if total_companies > 0 else 0

    credits_allocated = Subscription.objects.aggregate(total=Sum("credits_total"))["total"] or 0
    credits_used = Subscription.objects.aggregate(total=Sum("credits_used"))["total"] or 0
    credits_remaining = max(0, credits_allocated - credits_used)

    # 4. AI Searches & External API Usage
    total_search_jobs = SearchJob.objects.count()
    completed_jobs = SearchJob.objects.filter(status=SearchJob.Status.COMPLETED).count()
    failed_jobs = SearchJob.objects.filter(status=SearchJob.Status.FAILED).count()

    total_api_calls = APILog.objects.count()
    total_api_cost = APILog.objects.aggregate(total=Sum("cost"))["total"] or 0.0

    provider_breakdown = list(
        APILog.objects.values("provider")
        .annotate(total_calls=Count("id"), total_cost=Sum("cost"))
        .order_by("-total_calls")
    )

    # 5. Deal & Support Metrics
    total_inquiries = Inquiry.objects.count()
    deals_won = Inquiry.objects.filter(status=Inquiry.Status.WON).count()
    open_tickets = SupportTicket.objects.filter(
        is_deleted=False,
        status__in=[SupportTicket.Status.OPEN, SupportTicket.Status.IN_PROGRESS]
    ).count()

    # 6. Recent streams
    recent_companies = Company.objects.select_related("subscription").order_by("-created_at")[:6]
    recent_activities = ActivityLog.objects.select_related("company", "user").order_by("-created_at")[:8]
    recent_tickets = SupportTicket.objects.filter(is_deleted=False).select_related("company", "user").order_by("-created_at")[:5]

    context = {
        "page_title": "Super Admin Executive Dashboard",
        "total_companies": total_companies,
        "active_companies": active_companies,
        "verified_companies": verified_companies,
        "inactive_companies": inactive_companies,
        "buyer_companies": buyer_companies,
        "seller_companies": seller_companies,
        "both_companies": both_companies,
        "total_users": total_users,
        "active_users": active_users,
        "super_admins": super_admins,
        "company_admins": company_admins,
        "company_users": company_users,
        "sub_free": sub_free,
        "sub_pro": sub_pro,
        "sub_enterprise": sub_enterprise,
        "sub_free_pct": sub_free_pct,
        "sub_pro_pct": sub_pro_pct,
        "sub_enterprise_pct": sub_enterprise_pct,
        "credits_allocated": credits_allocated,
        "credits_used": credits_used,
        "credits_remaining": credits_remaining,
        "total_search_jobs": total_search_jobs,
        "completed_jobs": completed_jobs,
        "failed_jobs": failed_jobs,
        "total_api_calls": total_api_calls,
        "total_api_cost": total_api_cost,
        "provider_breakdown": provider_breakdown,
        "total_inquiries": total_inquiries,
        "deals_won": deals_won,
        "open_tickets": open_tickets,
        "recent_companies": recent_companies,
        "recent_activities": recent_activities,
        "recent_tickets": recent_tickets,
    }
    return render(request, "admin_panel/dashboard.html", context)


@login_required
@superadmin_required
def admin_panel_companies_view(request):
    """
    All platform companies directory with search, filtering,
    quick activate/suspend actions, and verification toggles.
    """
    companies = Company.objects.select_related("subscription").prefetch_related("members").order_by("-created_at")

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "all")
    type_filter = request.GET.get("type", "all")
    verified_filter = request.GET.get("verified", "all")

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

    if type_filter in [Company.CompanyType.BUYER, Company.CompanyType.SELLER, Company.CompanyType.BOTH]:
        companies = companies.filter(company_type=type_filter)

    if verified_filter == "verified":
        companies = companies.filter(is_verified=True)
    elif verified_filter == "pending":
        companies = companies.filter(is_verified=False)

    total_count = Company.objects.filter(is_deleted=False).count()
    active_count = Company.objects.filter(is_deleted=False, is_active=True).count()
    verified_count = Company.objects.filter(is_deleted=False, is_verified=True).count()
    deleted_count = Company.objects.filter(is_deleted=True).count()

    return render(request, "admin_panel/companies.html", {
        "companies": companies,
        "page_title": "All Companies (Super Admin)",
        "q": q,
        "status_filter": status_filter,
        "type_filter": type_filter,
        "verified_filter": verified_filter,
        "total_count": total_count,
        "active_count": active_count,
        "verified_count": verified_count,
        "deleted_count": deleted_count,
    })


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
        company_type = request.POST.get("company_type", Company.CompanyType.BOTH)
        industry = request.POST.get("industry", "").strip()
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

        # 3. Associate or Create Primary Admin User if provided
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
        ActivityLog.objects.create(
            company=company,
            user=request.user,
            activity_type=ActivityLog.ActivityType.MEMBER_INVITED,
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
        Company.objects.select_related("subscription", "created_by"),
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
        company_type = request.POST.get("company_type", company.company_type)
        if company_type in [Company.CompanyType.BUYER, Company.CompanyType.SELLER, Company.CompanyType.BOTH]:
            company.company_type = company_type

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
        ActivityLog.objects.create(
            company=company,
            user=request.user,
            activity_type=ActivityLog.ActivityType.MEMBER_INVITED,
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
    POST action to toggle company active status (Activate / Suspend).
    """
    if request.method == "POST":
        company = get_object_or_404(Company, pk=pk)
        company.is_active = not company.is_active
        company.save(update_fields=["is_active"])

        action_name = "Activated" if company.is_active else "Suspended"
        ActivityLog.objects.create(
            company=company,
            user=request.user,
            activity_type=ActivityLog.ActivityType.MEMBER_INVITED,
            title=f"Company {action_name} by Super Admin",
            description=f"Company account '{company.name}' was set to {action_name.lower()}.",
            icon_type="building",
            color="emerald" if company.is_active else "rose",
        )

        messages.success(request, f"Company '{company.name}' has been {action_name.lower()}.")
    return redirect("admin-panel-companies")


@login_required
@superadmin_required
def admin_panel_toggle_company_verification_view(request, pk):
    """
    POST action to toggle company verification badge.
    """
    if request.method == "POST":
        company = get_object_or_404(Company, pk=pk)
        company.is_verified = not company.is_verified
        company.save(update_fields=["is_verified"])

        action_name = "Verified" if company.is_verified else "Verification Revoked"
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

        ActivityLog.objects.create(
            company=company,
            user=request.user,
            activity_type=ActivityLog.ActivityType.MEMBER_INVITED,
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

        ActivityLog.objects.create(
            company=company,
            user=request.user,
            activity_type=ActivityLog.ActivityType.MEMBER_INVITED,
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

    subscriptions = (
        Subscription.objects.select_related("company", "plan_tier")
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

    # Platform overview KPIs
    total_companies = Company.objects.filter(is_active=True).count()
    active_subs_count = Subscription.objects.filter(status=Subscription.Status.ACTIVE).count()
    trial_subs_count = Subscription.objects.filter(status=Subscription.Status.TRIAL).count()
    suspended_subs_count = Subscription.objects.filter(status=Subscription.Status.SUSPENDED).count()

    total_allocated = Subscription.objects.aggregate(t=Sum("credits_total"))["t"] or 0
    total_used = Subscription.objects.aggregate(t=Sum("credits_used"))["t"] or 0
    total_revenue = Invoice.objects.filter(status=Invoice.Status.PAID).aggregate(s=Sum("total_amount"))["s"] or Decimal("0.00")
    failed_jobs_count = UsageRecord.objects.filter(status=UsageRecord.ProcessingStatus.FAILED).count()

    plans_catalog = list(SubscriptionPlan.objects.all().order_by("order", "price"))
    feature_costs = list(FeatureCreditCost.objects.all().order_by("feature_code"))
    recent_transactions = list(CreditTransaction.objects.select_related("company", "user").order_by("-created_at")[:30])
    recent_invoices = list(Invoice.objects.select_related("company").order_by("-created_at")[:25])
    all_companies = list(Company.objects.filter(is_active=True).order_by("name"))

    return render(request, "admin_panel/plans.html", {
        "page_title": "Plans & Subscription Management",
        "subscriptions": subscriptions,
        "plans_catalog": plans_catalog,
        "feature_costs": feature_costs,
        "recent_transactions": recent_transactions,
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
    })


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
        ActivityLog.objects.create(
            company=company,
            user=request.user,
            activity_type=ActivityLog.ActivityType.PLAN_UPGRADED,
            title="Subscription & Credits Adjusted by Super Admin",
            description=f"Plan: {subscription.get_plan_display()}, Total Credits: {subscription.credits_total} ({'+' if credits_delta >= 0 else ''}{credits_delta}). Note: {notes}{receipt_ref_msg}",
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
            messages.success(request, f"Credit rate for '{rule.display_name}' updated to {cost} credits.")
        else:
            messages.error(request, f"Feature rule '{feature_code}' not found.")

    return redirect("admin-panel-plans")


@login_required
@superadmin_required
def admin_panel_users_view(request):
    """
    Platform Users directory with search, filtering by role/status,
    and user activation toggle.
    """
    users = User.objects.select_related("company_membership__company").order_by("-created_at")

    q = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "all")
    status_filter = request.GET.get("status", "all")

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

    total_users = User.objects.count()
    active_users = User.objects.filter(is_active=True).count()
    superadmin_count = User.objects.filter(Q(is_superuser=True) | Q(is_staff=True)).count()

    return render(request, "admin_panel/users.html", {
        "users": users,
        "page_title": "Platform Users (Super Admin)",
        "q": q,
        "role_filter": role_filter,
        "status_filter": status_filter,
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
        messages.success(request, f"User account {target_user.email} has been {state}.")

    return redirect("admin-panel-users")


@login_required
@superadmin_required
def admin_panel_apilogs_view(request):
    """
    API Logs & External Provider Costs monitor.
    Dynamically tracks real costs, API provider calls, status codes,
    and search jobs.
    """
    logs = APILog.objects.select_related("search_job__company").order_by("-created_at")

    provider_filter = request.GET.get("provider", "all")
    status_filter = request.GET.get("status", "all")
    q = request.GET.get("q", "").strip()

    if q:
        logs = logs.filter(
            Q(endpoint__icontains=q)
            | Q(provider__icontains=q)
            | Q(search_job__company__name__icontains=q)
        )

    if provider_filter != "all":
        logs = logs.filter(provider=provider_filter)

    if status_filter == "200":
        logs = logs.filter(status_code=200)
    elif status_filter == "error":
        logs = logs.exclude(status_code=200)

    # Real calculated statistics
    total_calls = APILog.objects.count()
    total_cost = APILog.objects.aggregate(total=Sum("cost"))["total"] or 0.00
    monthly_budget = 250.00

    provider_stats = list(
        APILog.objects.values("provider")
        .annotate(calls=Count("id"), cost=Sum("cost"))
        .order_by("-calls")
    )

    available_providers = APILog.objects.values_list("provider", flat=True).distinct()

    return render(request, "admin_panel/api_logs.html", {
        "logs": logs[:100],
        "page_title": "API Logs & External Provider Costs",
        "total_calls": total_calls,
        "total_cost": total_cost,
        "monthly_budget": monthly_budget,
        "provider_stats": provider_stats,
        "available_providers": available_providers,
        "provider_filter": provider_filter,
        "status_filter": status_filter,
        "q": q,
    })


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

    companies = Company.objects.filter(is_active=True).order_by("name")
    activity_types = ActivityLog.ActivityType.choices

    total_activities = ActivityLog.objects.count()

    return render(request, "admin_panel/audit_logs.html", {
        "activities": activities[:120],
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

        ActivityLog.objects.create(
            company=company,
            user=request.user,
            activity_type=ActivityLog.ActivityType.MEMBER_INVITED,
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

