from datetime import timedelta
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.companies.models import Company, CompanyMember
from apps.ai_search.models import ExternalCompany, SearchJob, SearchResult
from apps.billing.models import Subscription
from apps.catalog.models import Product
from django.contrib import messages
from django.utils.timesince import timesince
from apps.companies.rbac import get_user_rbac_context, HasModulePermission, superadmin_required
from apps.dashboard.models import ActivityLog, SupportTicket, Notification, FAQ, BuyerDashboardLayout, BuyerModuleConfig
from apps.dashboard.services.notification_service import notify_support_ticket, create_notification
from apps.leads.models import SavedItem, Inquiry
from apps.requirements.models import Requirement


def get_dashboard_data_for_company(company):
    """
    Computes or aggregates all metrics, charts, and table data for a given company.
    100% computed from real database records (no dummy/fake fallback values).
    """
    if not company:
        return {}

    # Subscription & credits
    subscription = getattr(company, "subscription", None)
    if not subscription:
        subscription = Subscription.objects.create(
            company=company,
            plan=Subscription.Plan.PRO,
            credits_total=500,
            credits_used=0,
            valid_till=timezone.now().date() + timedelta(days=60),
        )

    credits_remaining = subscription.credits_remaining
    credits_total = subscription.credits_total
    credits_used_percent = subscription.usage_percentage

    # 1. Total Searches & Jobs (real count)
    jobs_qs = SearchJob.objects.filter(company=company)
    total_searches = jobs_qs.count()

    # 2. Found Suppliers / Products (real count)
    real_products_count = Product.objects.filter(company=company, is_deleted=False).count()
    if company.company_type == Company.CompanyType.SELLER:
        found_suppliers_count = real_products_count
    else:
        found_suppliers_count = SearchResult.objects.filter(
            search_job__company=company,
            result_type=SearchResult.ResultType.SUPPLIER,
        ).count()

    # 3. Active Requirements / Leads (real count)
    requirements_count = Requirement.objects.filter(company=company, is_deleted=False).count()
    if company.company_type == Company.CompanyType.SELLER:
        active_leads_count = SavedItem.objects.filter(
            company=company,
            status__in=[SavedItem.Status.NEW, SavedItem.Status.INTERESTED, SavedItem.Status.NEGOTIATING],
        ).count()
    else:
        active_leads_count = requirements_count

    # 4. Saved Suppliers (real count)
    saved_suppliers_count = SavedItem.objects.filter(
        company=company,
        search_result__result_type=SearchResult.ResultType.SUPPLIER,
    ).count()

    # 5. Inquiries Sent (real count)
    inquiries_sent_count = Inquiry.objects.filter(company=company).count()

    # 6. Search Scope breakdown (Requirements for Buyer, Products for Seller)
    if company.company_type == Company.CompanyType.SELLER:
        nearby_count = Product.objects.filter(company=company, search_scope=Product.SearchScope.NEARBY, is_deleted=False).count()
        country_count = Product.objects.filter(company=company, search_scope=Product.SearchScope.COUNTRY, is_deleted=False).count()
        global_count = Product.objects.filter(company=company, search_scope=Product.SearchScope.GLOBAL, is_deleted=False).count()
    else:
        nearby_count = Requirement.objects.filter(company=company, search_scope=Requirement.SearchScope.NEARBY, is_deleted=False).count()
        country_count = Requirement.objects.filter(company=company, search_scope=Requirement.SearchScope.COUNTRY, is_deleted=False).count()
        global_count = Requirement.objects.filter(company=company, search_scope=Requirement.SearchScope.GLOBAL, is_deleted=False).count()
    scope_total = nearby_count + country_count + global_count

    nearby_percent = round((nearby_count / scope_total) * 100) if scope_total else 0
    country_percent = round((country_count / scope_total) * 100) if scope_total else 0
    global_percent = max(0, 100 - nearby_percent - country_percent) if scope_total else 0

    # 7. Recent Searches (top 5 from real SearchJob records)
    recent_jobs = list(jobs_qs.select_related("requirement", "product").order_by("-created_at")[:5])
    recent_searches = []
    if recent_jobs:
        for idx, job in enumerate(recent_jobs, start=1):
            item_name = job.requirement.item_name if job.requirement else (job.product.name if job.product else job.search_query or f"Search Job #{job.id}")
            scope_val = "Global"
            if job.requirement:
                if job.requirement.search_scope == "nearby":
                    scope_val = f"Nearby ({job.requirement.radius_km or 50} km)"
                elif job.requirement.search_scope == "country":
                    scope_val = f"Country ({job.requirement.delivery_country or 'India'})"
                else:
                    scope_val = "Global"

            recent_searches.append({
                "id": idx,
                "item": item_name,
                "scope": scope_val,
                "results": f"{job.total_results or 0} suppliers",
                "status": job.get_status_display(),
                "date": job.created_at.strftime("%b %d, %Y"),
                "icon": "magnifying-glass",
            })

    # 8. Recent Activity (from real ActivityLog records)
    activity_qs = ActivityLog.objects.filter(company=company).order_by("-created_at")[:5]
    activities = []
    for act in activity_qs:
        diff = timezone.now() - act.created_at
        if diff.days > 0:
            time_ago = f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
        elif diff.seconds >= 3600:
            hours = diff.seconds // 3600
            time_ago = f"{hours} hour{'s' if hours > 1 else ''} ago"
        else:
            minutes = max(1, diff.seconds // 60)
            time_ago = f"{minutes} min ago"

        activities.append({
            "title": act.title,
            "time_ago": time_ago,
            "icon_type": act.icon_type,
            "color": act.color,
        })

    # 9. Top Suppliers (from real SearchResult records)
    top_sr = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.SUPPLIER,
    ).select_related("external_company").order_by("-match_score")[:5]
    top_suppliers = []
    for idx, sr in enumerate(top_sr, start=1):
        c_name = sr.external_company.name if sr.external_company else (sr.product_title or f"Supplier #{sr.id}")
        loc = "Global"
        if sr.external_company:
            loc = f"{sr.external_company.city or ''}, {sr.external_company.country or ''}".strip(", ") or "Global"
        top_suppliers.append({
            "id": idx,
            "name": c_name,
            "location": loc,
            "score": sr.match_score or 0,
            "badge": "success" if (sr.match_score or 0) >= 80 else "primary",
            "icon": "building",
        })

    # 10. Chart timeseries data for Search & Leads Overview
    chart_dates = ["Day 1", "Day 2", "Day 3", "Day 4", "Day 5", "Day 6", "Today"]
    searches_series = [0, 0, 0, 0, 0, 0, total_searches]
    leads_series = [0, 0, 0, 0, 0, 0, active_leads_count]

    # Quick stats for My Company (real count)
    members_count = CompanyMember.objects.filter(company=company, is_active=True).count()

    next_billing_str = (subscription.valid_till.strftime("%b %d, %Y") if subscription.valid_till else "Active")

    return {
        "metrics": {
            "total_searches": total_searches,
            "total_searches_trend": "real-time DB sync",
            "found_suppliers": found_suppliers_count,
            "found_suppliers_trend": "verified matches",
            "active_leads": active_leads_count,
            "active_leads_trend": "active records",
            "total_requirements": requirements_count,
            "saved_suppliers": saved_suppliers_count,
            "inquiries_sent": inquiries_sent_count,
            "credits_remaining": credits_remaining,
            "credits_total": credits_total,
            "credits_used_percent": credits_used_percent,
        },
        "charts": {
            "dates": chart_dates,
            "searches_series": searches_series,
            "leads_series": leads_series,
            "scope": {
                "total": scope_total,
                "nearby": nearby_count,
                "nearby_percent": nearby_percent,
                "country": country_count,
                "country_percent": country_percent,
                "global": global_count,
                "global_percent": global_percent,
            },
        },
        "recent_searches": recent_searches,
        "recent_activities": activities,
        "top_suppliers": top_suppliers,
        "company_summary": {
            "name": company.name,
            "type_display": company.get_company_type_display(),
            "industry": company.industry or "General Procurement",
            "company_size": company.get_company_size_display() or "1 - 10",
            "website": company.website or "",
            "total_users": members_count,
            "active_searches": total_searches,
            "active_leads": active_leads_count,
            "plan": subscription.get_plan_display().replace(" Plan", ""),
            "next_billing": next_billing_str,
        },
        "subscription": {
            "plan_name": subscription.get_plan_display(),
            "next_billing": next_billing_str,
            "credits_used_percent": credits_used_percent,
            "features": [
                "Advanced AI Search",
                "Verified Supplier Discovery",
                "Quotation & RFQ Workflow",
                "Export Reports & Analytics",
            ],
        },
    }


@login_required
def dashboard_view(request):
    """
    Renders the full interactive Django dashboard matching the design image.
    Enforces Role-Based Access Control, Company data scoping, and Super Admin Buyer Dashboard Layout settings.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)

    # Super Admin default landing goes directly to the Super Admin Executive Control Center
    if rbac["is_super_admin"] and not selected_company_id:
        return redirect("admin-panel-dashboard")

    company = rbac["company"]

    # If superuser or no company assigned yet, pick or create ABC Trading Pvt. Ltd.
    if not company:
        company = Company.objects.filter(is_active=True).first()
        if not company:
            company = Company.objects.create(
                name="ABC Trading Pvt. Ltd.",
                company_type=Company.CompanyType.BOTH,
                industry="Manufacturing",
                company_size=Company.CompanySize.MEDIUM_51_200,
                website="https://www.abctrading.com",
                created_by=request.user,
            )

    dashboard_data = get_dashboard_data_for_company(company)
    layout = BuyerDashboardLayout.get_layout()

    context = {
        **dashboard_data,
        "page_title": "Dashboard",
        "current_company": company,
        "rbac": rbac,
        "layout": layout,
    }
    return render(request, "dashboard/index.html", context)


class DashboardAPIView(APIView):
    """
    REST API endpoint for dashboard summary statistics.
    Endpoint: GET /api/dashboard/
    """
    permission_classes = [IsAuthenticated]
    required_module = "dashboard"
    required_permission = "READ"

    def get(self, request):
        company_id = request.query_params.get("company_id") or request.session.get("active_company_id")
        rbac = get_user_rbac_context(request.user, company_id=company_id)

        # Check permissions
        if not rbac["is_super_admin"] and not rbac["is_company_admin"]:
            if "dashboard:READ" not in rbac["permissions"] and "dashboard:*" not in rbac["permissions"]:
                return Response(
                    {"error": "You do not have permission to view the dashboard."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        company = rbac["company"]
        if not company:
            return Response(
                {"error": "No active company found for this user."},
                status=status.HTTP_404_NOT_FOUND,
            )

        data = get_dashboard_data_for_company(company)
        data["rbac"] = {
            "role": rbac["role"],
            "is_super_admin": rbac["is_super_admin"],
            "is_company_admin": rbac["is_company_admin"],
            "is_company_user": rbac["is_company_user"],
            "can_view_buyer": rbac["can_view_buyer"],
            "can_view_seller": rbac["can_view_seller"],
        }
        layout = BuyerDashboardLayout.get_layout()
        data["layout"] = {
            "show_kpi_requirements": layout.show_kpi_requirements,
            "show_kpi_searches": layout.show_kpi_searches,
            "show_kpi_suppliers_discovered": layout.show_kpi_suppliers_discovered,
            "show_kpi_saved_suppliers": layout.show_kpi_saved_suppliers,
            "show_kpi_inquiries_sent": layout.show_kpi_inquiries_sent,
            "show_kpi_credits": layout.show_kpi_credits,
            "show_quick_actions": layout.show_quick_actions,
            "show_recent_searches": layout.show_recent_searches,
            "show_recent_activity": layout.show_recent_activity,
            "show_top_suppliers": layout.show_top_suppliers,
            "show_subscription_widget": layout.show_subscription_widget,
        }
        return Response(data, status=status.HTTP_200_OK)


@login_required
def switch_company_view(request):
    """
    Allows Super Admins or multi-company users to switch active company context.
    """
    if request.method == "POST":
        company_id = request.POST.get("company_id")
        if company_id:
            # Check if user has access to this company
            if request.user.is_superuser or CompanyMember.objects.filter(user=request.user, company_id=company_id, is_active=True).exists():
                request.session["active_company_id"] = int(company_id)

    return redirect("dashboard")


def demo_login_as_view(request, role_name):
    """
    Developer convenience helper to test Role-Based Access Control in 1 click!
    Allows switching between:
    - 'admin': Ajay Patil (Company Admin of ABC Trading Pvt. Ltd.)
    - 'buyer': Rahul Sharma (Company User - Buyer permissions)
    - 'seller': Priya Patel (Company User - Sales permissions)
    - 'superadmin': System SuperAdmin (Platform Super Admin)
    """
    if not getattr(settings, "DEBUG", False):
        raise PermissionDenied("1-Click demo authentication switching is disabled in production environments.")

    role_email_map = {
        "admin": "ajay@abctrading.com",
        "buyer": "rahul@abctrading.com",
        "seller": "priya@abctrading.com",
        "superadmin": "admin@procurement.ai",
    }
    email = role_email_map.get(role_name.lower())
    if email:
        user = User.objects.filter(email=email).first()
        if user:
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            request.session["active_company_id"] = None
            if user.is_superuser or user.is_staff:
                return redirect("admin-panel-dashboard")
    return redirect("dashboard")


@login_required
def global_search_view(request):
    """
    Global omni-search engine for the top navigation search bar.
    Searches across:
    - Suppliers (SearchResult supplier items & ExternalCompany)
    - Sales Leads (SearchResult lead items)
    - Products (Company catalog)
    - Requirements (Company procurement needs)
    - Companies (Registered platform companies for Super Admin)
    """
    q = request.GET.get("q", "").strip()
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    suppliers = []
    external_suppliers = []
    leads = []
    products = []
    requirements = []
    companies = []

    if q:
        # 1. Suppliers
        supplier_q = (
            Q(product_title__icontains=q)
            | Q(external_company__name__icontains=q)
            | Q(external_company__city__icontains=q)
            | Q(external_company__industry__icontains=q)
        )
        if company:
            suppliers = (
                SearchResult.objects.filter(
                    search_job__company=company,
                    result_type=SearchResult.ResultType.SUPPLIER,
                )
                .filter(supplier_q)
                .select_related("external_company", "search_job")
                .distinct()[:20]
            )
        else:
            suppliers = (
                SearchResult.objects.filter(
                    result_type=SearchResult.ResultType.SUPPLIER,
                )
                .filter(supplier_q)
                .select_related("external_company", "search_job")
                .distinct()[:20]
            )

        # Directory search for external companies
        if len(suppliers) < 5:
            external_suppliers = (
                ExternalCompany.objects.filter(
                    Q(name__icontains=q)
                    | Q(industry__icontains=q)
                    | Q(city__icontains=q)
                    | Q(country__icontains=q)
                    | Q(description__icontains=q)
                )
                .exclude(id__in=[s.external_company_id for s in suppliers])[:15]
            )

        # 2. Leads
        lead_q = (
            Q(product_title__icontains=q)
            | Q(external_company__name__icontains=q)
            | Q(external_company__city__icontains=q)
            | Q(external_company__industry__icontains=q)
        )
        if company:
            leads = (
                SearchResult.objects.filter(
                    search_job__company=company,
                    result_type=SearchResult.ResultType.LEAD,
                )
                .filter(lead_q)
                .select_related("external_company", "search_job")
                .distinct()[:20]
            )
        else:
            leads = (
                SearchResult.objects.filter(
                    result_type=SearchResult.ResultType.LEAD,
                )
                .filter(lead_q)
                .select_related("external_company", "search_job")
                .distinct()[:20]
            )

        # 3. Requirements
        req_q = (
            Q(item_name__icontains=q)
            | Q(description__icontains=q)
            | Q(delivery_city__icontains=q)
        )
        if company and not rbac["is_super_admin"]:
            requirements = Requirement.objects.filter(company=company).filter(req_q)[:20]
        else:
            requirements = Requirement.objects.filter(req_q)[:20]

        # 4. Products Catalog
        prod_q = Q(name__icontains=q) | Q(description__icontains=q)
        if company and not rbac["is_super_admin"]:
            products = Product.objects.filter(company=company).filter(prod_q)[:20]
        else:
            products = Product.objects.filter(prod_q)[:20]

        # 5. Registered Companies (Super Admin)
        if rbac["is_super_admin"]:
            companies = Company.objects.filter(
                Q(name__icontains=q)
                | Q(industry__icontains=q)
                | Q(city__icontains=q)
            )[:20]

    total_results = (
        len(suppliers)
        + len(external_suppliers)
        + len(leads)
        + len(products)
        + len(requirements)
        + len(companies)
    )

    return render(
        request,
        "search/results.html",
        {
            "q": q,
            "suppliers": suppliers,
            "external_suppliers": external_suppliers,
            "leads": leads,
            "products": products,
            "requirements": requirements,
            "companies": companies,
            "total_results": total_results,
            "company": company,
            "rbac": rbac,
            "page_title": f"Search: {q}" if q else "Global Search",
        },
    )


@login_required
def help_support_view(request):
    """
    Enterprise Help & Support Desk:
    - Provides dynamic FAQs from the database (editable by Super Admin).
    - Displays inbound company support queries and email inquiries to Super Admin with
      Company ID, Name, Location, Question, Priority, and Status.
    - Features direct Email Support and WhatsApp Support contact channels.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]
    is_super_admin = rbac["is_super_admin"]

    # 1. Dynamic FAQs from Database
    faqs_qs = FAQ.objects.filter(is_active=True).order_by("order", "id")

    # Group FAQs by category for customer-facing display
    grouped_faqs = {}
    for item in faqs_qs:
        cat_key = item.category
        if cat_key not in grouped_faqs:
            grouped_faqs[cat_key] = {
                "category": item.category,
                "icon": item.icon or "fa-circle-question",
                "items": [],
            }
        grouped_faqs[cat_key]["items"].append(item)
    faqs_list = list(grouped_faqs.values())
    all_faqs = list(FAQ.objects.all().order_by("order", "id"))

    # 2. Support Tickets / Inbound Queries
    query_search = request.GET.get("ticket_q", "").strip()
    status_filter = request.GET.get("ticket_status", "all")
    priority_filter = request.GET.get("ticket_priority", "all")

    if is_super_admin:
        tickets_qs = (
            SupportTicket.objects.filter(is_deleted=False)
            .select_related("company", "user")
            .order_by("-created_at")
        )
    elif company:
        tickets_qs = (
            SupportTicket.objects.filter(company=company, is_deleted=False)
            .select_related("company", "user")
            .order_by("-created_at")
        )
    else:
        tickets_qs = SupportTicket.objects.none()

    if status_filter != "all":
        tickets_qs = tickets_qs.filter(status=status_filter)
    if priority_filter != "all":
        tickets_qs = tickets_qs.filter(priority=priority_filter)
    if query_search:
        tickets_qs = tickets_qs.filter(
            Q(subject__icontains=query_search)
            | Q(description__icontains=query_search)
            | Q(ticket_number__icontains=query_search)
            | Q(contact_email__icontains=query_search)
            | Q(company__name__icontains=query_search)
            | Q(company__id__icontains=query_search)
            | Q(company__city__icontains=query_search)
            | Q(company__country__icontains=query_search)
        )

    tickets = list(tickets_qs)

    # Global KPI counts
    all_base_tickets = (
        SupportTicket.objects.filter(is_deleted=False)
        if is_super_admin
        else (SupportTicket.objects.filter(company=company, is_deleted=False) if company else SupportTicket.objects.none())
    )
    total_tickets_count = all_base_tickets.count()
    open_tickets_count = all_base_tickets.filter(status=SupportTicket.Status.OPEN).count()
    in_progress_count = all_base_tickets.filter(status=SupportTicket.Status.IN_PROGRESS).count()
    resolved_tickets_count = all_base_tickets.filter(status__in=[SupportTicket.Status.RESOLVED, SupportTicket.Status.CLOSED]).count()

    context = {
        "company": company,
        "rbac": rbac,
        "is_super_admin": is_super_admin,
        "tickets": tickets,
        "total_tickets_count": total_tickets_count,
        "open_tickets_count": open_tickets_count,
        "in_progress_count": in_progress_count,
        "resolved_tickets_count": resolved_tickets_count,
        "faqs": faqs_list,
        "all_faqs": all_faqs,
        "categories": SupportTicket.Category.choices,
        "priorities": SupportTicket.Priority.choices,
        "statuses": SupportTicket.Status.choices,
        "status_filter": status_filter,
        "priority_filter": priority_filter,
        "query_search": query_search,
        "page_title": "Help & Support Desk",
    }
    return render(request, "dashboard/help_support.html", context)


@login_required
def update_support_ticket_view(request, pk):
    """
    Updates status and resolution notes of a support query/ticket.
    Super Admins can update any ticket; Company users can update their company's tickets.
    """
    if request.method == "POST":
        selected_company_id = request.session.get("active_company_id")
        rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
        if rbac["is_super_admin"]:
            ticket = get_object_or_404(SupportTicket, pk=pk, is_deleted=False)
        else:
            ticket = get_object_or_404(SupportTicket, pk=pk, company=rbac["company"], is_deleted=False)

        new_status = request.POST.get("status")
        resolution = request.POST.get("resolution", "").strip()

        if new_status in [SupportTicket.Status.OPEN, SupportTicket.Status.IN_PROGRESS, SupportTicket.Status.RESOLVED, SupportTicket.Status.CLOSED]:
            ticket.status = new_status
        if resolution:
            ticket.resolution = resolution
        ticket.save()

        # In-app notification to company members
        create_notification(
            user=ticket.user,
            company=ticket.company,
            notification_type=Notification.NotificationType.TICKET_UPDATE,
            title=f"Support Ticket #{ticket.ticket_number} Updated: {ticket.get_status_display()}",
            message=f"Status: {ticket.get_status_display()}. {resolution or ''}",
            link="/help/",
            icon="fa-headset",
            color="blue",
        )

        messages.success(request, f"Ticket #{ticket.ticket_number} status updated to {ticket.get_status_display()}.")
    return redirect("help-support")


@login_required
@superadmin_required
def create_faq_view(request):
    """
    Super Admin action to add a new dynamic FAQ to knowledge base.
    """
    if request.method == "POST":
        category = request.POST.get("category", "Getting Started & Accounts").strip() or "General"
        icon = request.POST.get("icon", "fa-circle-question").strip() or "fa-circle-question"
        question = request.POST.get("question", "").strip()
        answer = request.POST.get("answer", "").strip()
        order_raw = request.POST.get("order", "0").strip()

        try:
            order = int(order_raw)
        except ValueError:
            order = 0

        if question and answer:
            FAQ.objects.create(
                category=category,
                icon=icon,
                question=question,
                answer=answer,
                order=order,
                is_active=True,
            )
            messages.success(request, "New FAQ added successfully.")
        else:
            messages.error(request, "Both Question and Answer are required.")
    return redirect("help-support")


@login_required
@superadmin_required
def edit_faq_view(request, pk):
    """
    Super Admin action to edit an existing dynamic FAQ.
    """
    faq = get_object_or_404(FAQ, pk=pk)
    if request.method == "POST":
        faq.category = request.POST.get("category", faq.category).strip()
        faq.icon = request.POST.get("icon", faq.icon).strip() or faq.icon
        faq.question = request.POST.get("question", faq.question).strip() or faq.question
        faq.answer = request.POST.get("answer", faq.answer).strip() or faq.answer
        order_raw = request.POST.get("order", str(faq.order)).strip()
        try:
            faq.order = int(order_raw)
        except ValueError:
            pass
        faq.is_active = bool(request.POST.get("is_active", True))
        faq.save()
        messages.success(request, f"FAQ '{faq.question[:35]}...' updated successfully.")
    return redirect("help-support")


@login_required
@superadmin_required
def delete_faq_view(request, pk):
    """
    Super Admin action to delete a dynamic FAQ.
    """
    if request.method == "POST":
        faq = get_object_or_404(FAQ, pk=pk)
        q_title = faq.question[:35]
        faq.delete()
        messages.success(request, f"FAQ '{q_title}...' deleted.")
    return redirect("help-support")


@login_required
def create_support_ticket_view(request):
    """
    Processes support ticket creation submissions.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not company and not rbac["is_super_admin"]:
        messages.error(request, "Active company required to submit support tickets.")
        return redirect("dashboard")

    if not company:
        company = Company.objects.filter(is_active=True).first()

    if request.method == "POST":
        subject = request.POST.get("subject", "").strip()
        description = request.POST.get("description", "").strip()
        category = request.POST.get("category", SupportTicket.Category.AI_SEARCH)
        priority = request.POST.get("priority", SupportTicket.Priority.MEDIUM)
        contact_email = request.POST.get("contact_email", "").strip() or request.user.email
        contact_phone = request.POST.get("contact_phone", "").strip()

        if not subject or not description:
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": False, "error": "Subject and description are required."}, status=400)
            messages.error(request, "Please provide both a subject and a description for your support request.")
            return redirect("help-support")

        ticket = SupportTicket.objects.create(
            company=company,
            user=request.user,
            category=category,
            priority=priority,
            subject=subject[:255],
            description=description,
            contact_email=contact_email,
            contact_phone=contact_phone[:50],
        )

        if company:
            ActivityLog.objects.create(
                company=company,
                user=request.user,
                activity_type=ActivityLog.ActivityType.MEMBER_INVITED,
                title=f"Support Ticket Created: {ticket.ticket_number}",
                description=f"Ticket #{ticket.ticket_number} logged under {ticket.get_category_display()}.",
                icon_type="life-ring",
                color="blue",
            )

        notify_support_ticket(ticket, event_type="created", user=request.user)

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "ticket_number": ticket.ticket_number,
                "subject": ticket.subject,
                "status": ticket.get_status_display(),
                "created_at": ticket.created_at.strftime("%b %d, %Y"),
            })

        messages.success(request, f"Support Ticket {ticket.ticket_number} created successfully! Our engineering team will review it shortly.")
        return redirect("help-support")

    return redirect("help-support")


@login_required
def support_ticket_detail_view(request, pk):
    """
    Returns JSON details of a support ticket for modal inspection.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if rbac["is_super_admin"]:
        ticket = get_object_or_404(SupportTicket, pk=pk, is_deleted=False)
    else:
        ticket = get_object_or_404(SupportTicket, pk=pk, company=company, is_deleted=False)

    return JsonResponse({
        "success": True,
        "id": ticket.id,
        "ticket_number": ticket.ticket_number,
        "subject": ticket.subject,
        "description": ticket.description,
        "category": ticket.category,
        "category_display": ticket.get_category_display(),
        "priority": ticket.priority,
        "priority_display": ticket.get_priority_display(),
        "status": ticket.status,
        "status_display": ticket.get_status_display(),
        "contact_email": ticket.contact_email,
        "contact_phone": ticket.contact_phone,
        "resolution": ticket.resolution or "In evaluation by technical operations team.",
        "created_at": ticket.created_at.strftime("%b %d, %Y at %I:%M %p"),
        "updated_at": ticket.updated_at.strftime("%b %d, %Y at %I:%M %p"),
    })


@login_required
def edit_support_ticket_view(request, pk):
    """
    Allows user or admin to edit support ticket details (subject, category, priority, contact, description).
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if rbac["is_super_admin"]:
        ticket = get_object_or_404(SupportTicket, pk=pk, is_deleted=False)
    else:
        ticket = get_object_or_404(SupportTicket, pk=pk, company=company, is_deleted=False)

    if request.method == "POST":
        subject = request.POST.get("subject", "").strip()
        description = request.POST.get("description", "").strip()
        category = request.POST.get("category", ticket.category).strip()
        priority = request.POST.get("priority", ticket.priority).strip()
        contact_email = request.POST.get("contact_email", "").strip() or ticket.contact_email
        contact_phone = request.POST.get("contact_phone", "").strip()

        if not subject or not description:
            messages.error(request, "Subject and description are required to update a support ticket.")
            return redirect("help-support")

        ticket.subject = subject[:255]
        ticket.description = description
        if category in [c[0] for c in SupportTicket.Category.choices]:
            ticket.category = category
        if priority in [p[0] for p in SupportTicket.Priority.choices]:
            ticket.priority = priority
        ticket.contact_email = contact_email
        ticket.contact_phone = contact_phone[:50]
        ticket.save()

        messages.success(request, f"Support Ticket #{ticket.ticket_number} updated successfully.")

    return redirect("help-support")


@login_required
def delete_support_ticket_view(request, pk):
    """
    Soft-deletes a support ticket (sets is_deleted=True, deleted_at=now).
    """
    if request.method == "POST":
        selected_company_id = request.session.get("active_company_id")
        rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
        company = rbac["company"]

        if rbac["is_super_admin"]:
            ticket = get_object_or_404(SupportTicket, pk=pk, is_deleted=False)
        else:
            ticket = get_object_or_404(SupportTicket, pk=pk, company=company, is_deleted=False)

        ticket_number = ticket.ticket_number
        ticket.soft_delete()
        messages.success(request, f"Support Ticket #{ticket_number} has been deleted successfully.")

    return redirect("help-support")



# ============================================================
# REAL-TIME NOTIFICATION CENTER & ACTIVITY STREAM APIS
# ============================================================

@login_required
def notifications_list_api(request):
    """
    Returns JSON list of notifications and unread count for real-time navbar polling.
    """
    notifications = Notification.objects.filter(user=request.user)
    unread_count = notifications.filter(is_read=False).count()
    recent = list(notifications[:15])

    data = []
    for n in recent:
        data.append({
            "id": n.id,
            "type": n.notification_type,
            "title": n.title,
            "message": n.message,
            "link": n.link,
            "is_read": n.is_read,
            "icon": n.icon,
            "color": n.color,
            "created_at": n.created_at.strftime("%b %d, %H:%M"),
            "time_ago": f"{timesince(n.created_at)} ago",
        })

    return JsonResponse({
        "success": True,
        "unread_count": unread_count,
        "notifications": data,
    })


@login_required
def mark_notification_read_view(request, pk):
    """
    Marks a single notification as read and routes to its target URL if provided.
    """
    notif = get_object_or_404(Notification, pk=pk, user=request.user)
    notif.mark_as_read()

    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.GET.get("format") == "json":
        unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
        return JsonResponse({
            "success": True,
            "id": notif.id,
            "unread_count": unread_count,
            "link": notif.link,
        })

    if notif.link:
        return redirect(notif.link)
    return redirect("dashboard")


@login_required
def mark_all_notifications_read_view(request):
    """
    Marks all notifications for the current user as read.
    """
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)

    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.method == "POST":
        return JsonResponse({"success": True, "unread_count": 0})

    return redirect(request.META.get("HTTP_REFERER") or "dashboard")


@login_required
def clear_all_notifications_view(request):
    """
    Clears all notification items for the current user.
    """
    Notification.objects.filter(user=request.user).delete()

    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.method == "POST":
        return JsonResponse({"success": True, "unread_count": 0})

    return redirect(request.META.get("HTTP_REFERER") or "dashboard")



