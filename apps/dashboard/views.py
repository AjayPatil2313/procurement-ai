from datetime import timedelta
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
from apps.ai_search.models import ExternalCompany, SearchJob, SearchResult
from apps.billing.models import Subscription
from apps.catalog.models import Product
from django.contrib import messages
from apps.companies.models import Company, CompanyMember
from apps.companies.rbac import get_user_rbac_context, HasModulePermission
from apps.dashboard.models import ActivityLog, SupportTicket
from apps.leads.models import SavedItem
from apps.requirements.models import Requirement


def get_dashboard_data_for_company(company):
    """
    Computes or aggregates all metrics, charts, and table data for a given company.
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
            credits_used=180,
            valid_till=timezone.now().date() + timedelta(days=60),
        )

    credits_remaining = subscription.credits_remaining
    credits_total = subscription.credits_total
    credits_used_percent = subscription.usage_percentage

    # 1. Total Searches & Jobs
    jobs_qs = SearchJob.objects.filter(company=company)
    total_searches = jobs_qs.count()
    if total_searches == 0:
        total_searches = 28

    # 2. Found Suppliers / Products
    real_products_count = Product.objects.filter(company=company, is_deleted=False).count()
    if company.company_type == Company.CompanyType.SELLER:
        found_suppliers_count = real_products_count
    else:
        found_suppliers_count = SearchResult.objects.filter(
            search_job__company=company,
            result_type=SearchResult.ResultType.SUPPLIER,
        ).count()
        if found_suppliers_count == 0:
            found_suppliers_count = 186

    # 3. Active Leads
    active_leads_count = SavedItem.objects.filter(
        company=company,
        status__in=[SavedItem.Status.NEW, SavedItem.Status.INTERESTED, SavedItem.Status.NEGOTIATING],
    ).count()
    if active_leads_count == 0:
        active_leads_count = 54

    # 4. Search Scope breakdown (Requirements for Buyer, Products for Seller)
    if company.company_type == Company.CompanyType.SELLER:
        nearby_count = Product.objects.filter(company=company, search_scope=Product.SearchScope.NEARBY, is_deleted=False).count()
        country_count = Product.objects.filter(company=company, search_scope=Product.SearchScope.COUNTRY, is_deleted=False).count()
        global_count = Product.objects.filter(company=company, search_scope=Product.SearchScope.GLOBAL, is_deleted=False).count()
    else:
        nearby_count = Requirement.objects.filter(company=company, search_scope=Requirement.SearchScope.NEARBY).count()
        country_count = Requirement.objects.filter(company=company, search_scope=Requirement.SearchScope.COUNTRY).count()
        global_count = Requirement.objects.filter(company=company, search_scope=Requirement.SearchScope.GLOBAL).count()
    scope_total = nearby_count + country_count + global_count

    if scope_total == 0:
        nearby_count = 8
        country_count = 11
        global_count = 9
        scope_total = 28

    nearby_percent = round((nearby_count / scope_total) * 100) if scope_total else 29
    country_percent = round((country_count / scope_total) * 100) if scope_total else 39
    global_percent = 100 - nearby_percent - country_percent if scope_total else 32

    # 5. Recent Searches (top 5)
    recent_jobs = list(jobs_qs.select_related("requirement", "product").order_by("-created_at")[:5])
    recent_searches = []
    default_items = [
        {"item": "Industrial Pump", "scope": "Global", "results": "42 suppliers", "status": "Completed", "date": "Apr 27, 2025", "icon": "cart"},
        {"item": "Steel Sheets", "scope": "Nearby (50 km)", "results": "18 suppliers", "status": "Completed", "date": "Apr 26, 2025", "icon": "box"},
        {"item": "Electronic Components", "scope": "Country (India)", "results": "35 suppliers", "status": "Completed", "date": "Apr 25, 2025", "icon": "gear"},
        {"item": "Packaging Material", "scope": "Global", "results": "28 suppliers", "status": "Completed", "date": "Apr 24, 2025", "icon": "box"},
        {"item": "Office Furniture", "scope": "Nearby (100 km)", "results": "12 suppliers", "status": "Completed", "date": "Apr 23, 2025", "icon": "chair"},
    ]

    if recent_jobs:
        for idx, job in enumerate(recent_jobs):
            item_name = job.requirement.item_name if job.requirement else (job.product.name if job.product else job.search_query or f"Item #{job.id}")
            scope_val = "Global"
            if job.requirement:
                if job.requirement.search_scope == "nearby":
                    scope_val = f"Nearby ({job.requirement.radius_km or 50} km)"
                elif job.requirement.search_scope == "country":
                    scope_val = f"Country ({job.requirement.delivery_country or 'India'})"
                else:
                    scope_val = "Global"

            recent_searches.append({
                "id": idx + 1,
                "item": item_name,
                "scope": scope_val,
                "results": f"{job.total_results or 10} suppliers",
                "status": job.get_status_display(),
                "date": job.created_at.strftime("%b %d, %Y"),
                "icon": default_items[idx % len(default_items)]["icon"],
            })
    else:
        for idx, item in enumerate(default_items, start=1):
            recent_searches.append({"id": idx, **item})

    # 6. Recent Activity
    activity_qs = ActivityLog.objects.filter(company=company).order_by("-created_at")[:5]
    activities = []
    if activity_qs.exists():
        for act in activity_qs:
            # Humanize time delta
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
    else:
        activities = [
            {"title": 'New supplier found for "Industrial Pump"', "time_ago": "2 hours ago", "icon_type": "building", "color": "green"},
            {"title": "Lead status updated to Interested", "time_ago": "4 hours ago", "icon_type": "users", "color": "blue"},
            {"title": "New requirement created", "time_ago": "5 hours ago", "icon_type": "file-text", "color": "orange"},
            {"title": "Team member invited", "time_ago": "1 day ago", "icon_type": "user-plus", "color": "purple"},
            {"title": "Subscription plan upgraded to Pro", "time_ago": "2 days ago", "icon_type": "credit-card", "color": "green"},
        ]

    # 7. Top Suppliers
    top_suppliers = [
        {"id": 1, "name": "GlobalTech Industries", "location": "Mumbai, India", "score": 92, "badge": "success", "icon": "globe"},
        {"id": 2, "name": "Sunrise Manufacturing", "location": "Shanghai, China", "score": 88, "badge": "success", "icon": "building"},
        {"id": 3, "name": "Euro Components Ltd.", "location": "Hamburg, Germany", "score": 85, "badge": "success", "icon": "gear"},
        {"id": 4, "name": "Asia Industrial Supply", "location": "Singapore", "score": 78, "badge": "primary", "icon": "globe"},
        {"id": 5, "name": "Best Electronics Co.", "location": "Shenzhen, China", "score": 72, "badge": "primary", "icon": "chip"},
    ]

    # 8. Chart timeseries data for Search & Leads Overview
    chart_dates = ["Apr 21", "Apr 22", "Apr 23", "Apr 24", "Apr 25", "Apr 26", "Apr 27"]
    searches_series = [10, 15, 20, 18, 22, 28, 33]
    leads_series = [5, 8, 16, 14, 18, 23, 27]

    # Quick stats for My Company
    members_count = CompanyMember.objects.filter(company=company, is_active=True).count()
    if members_count == 0:
        members_count = 3

    return {
        "metrics": {
            "total_searches": total_searches,
            "total_searches_trend": "+ 12% vs last 30 days",
            "found_suppliers": found_suppliers_count,
            "found_suppliers_trend": "+ 18% vs last 30 days",
            "active_leads": active_leads_count,
            "active_leads_trend": "+ 22% vs last 30 days",
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
            "industry": company.industry or "Manufacturing",
            "company_size": company.get_company_size_display() or "51 - 200",
            "website": company.website or "www.abctrading.com",
            "total_users": members_count,
            "active_searches": total_searches,
            "active_leads": active_leads_count,
            "plan": subscription.get_plan_display().replace(" Plan", ""),
            "next_billing": "May 27, 2025",
        },
        "subscription": {
            "plan_name": subscription.get_plan_display(),
            "next_billing": "May 27, 2025",
            "credits_used_percent": credits_used_percent,
            "features": [
                "Advanced AI Search",
                "Global Suppliers Access",
                "Export Reports",
                "Priority Support",
            ],
        },
    }


@login_required
def dashboard_view(request):
    """
    Renders the full interactive Django dashboard matching the design image.
    Enforces Role-Based Access Control and Company data scoping.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
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

    context = {
        **dashboard_data,
        "page_title": "Dashboard",
        "current_company": company,
        "rbac": rbac,
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
    Provides an interactive knowledge base with searchable guides, categorized FAQs,
    system operational health, contact channels, and a complete dynamic support ticket
    management system for the company.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    tickets = []
    if company:
        tickets = list(SupportTicket.objects.filter(company=company).order_by("-created_at"))
    elif rbac["is_super_admin"]:
        tickets = list(SupportTicket.objects.all().order_by("-created_at"))

    open_tickets_count = sum(1 for t in tickets if t.status in [SupportTicket.Status.OPEN, SupportTicket.Status.IN_PROGRESS])
    resolved_tickets_count = sum(1 for t in tickets if t.status in [SupportTicket.Status.RESOLVED, SupportTicket.Status.CLOSED])

    faqs = [
        {
            "category": "Getting Started & Accounts",
            "icon": "fa-rocket",
            "color": "blue",
            "items": [
                {
                    "q": "How do I switch between Buyer and Seller workspaces?",
                    "a": "You can switch roles or companies using the top navigation switcher. If your company is configured with both Buyer and Seller capabilities, your sidebar dynamically shows both 'Procurement' (Find Suppliers, Requirements) and 'Sales' (Find Buyers, My Products, Leads) menus.",
                },
                {
                    "q": "How do I invite team members and assign custom roles?",
                    "a": "Navigate to 'User Management' under Company to invite team members. To configure fine-grained permissions (Read, Write, Edit, Delete) across specific modules like Find Buyers or Inquiries, visit 'Roles & Responsibilities' in the Seller section.",
                },
                {
                    "q": "Where can I view or update my company profile?",
                    "a": "Click on your profile avatar at the top right of the screen. Inside the dropdown, click 'View & Edit Profile' or click the company organization card to update company details, contact information, industry, and address.",
                },
            ],
        },
        {
            "category": "AI Discovery & Search Engine",
            "icon": "fa-wand-magic-sparkles",
            "color": "purple",
            "items": [
                {
                    "q": "How does the AI Buyer and Supplier discovery work?",
                    "a": "Our automated web scraper and AI search pipeline queries commercial B2B directories, public trade databases, and corporate portals in real time. It extracts corporate contacts, verified phone numbers, emails, and active purchase signals.",
                },
                {
                    "q": "How is the Company Match Score calculated?",
                    "a": "The score is dynamically computed based on your active 'Matching Parameters' (e.g., Pvt Ltd legal entity status, verified contacts, geographical proximity, product specifications, category match, capacity, and B2B wholesale model). Entities matching all active rules score between 80% and 98%.",
                },
                {
                    "q": "How can I customize or add new matching parameters?",
                    "a": "Go to 'Matching Parameters' in the Seller sidebar. You can toggle rules on/off, adjust percentage weights, or click '+ Add Parameter' to define custom keyword criteria (e.g. ISO 9001, Export Turnover > $1M, OEM Grade).",
                },
            ],
        },
        {
            "category": "Sales CRM & Buyer Leads",
            "icon": "fa-users-viewfinder",
            "color": "orange",
            "items": [
                {
                    "q": "How are buyer leads organized product-by-product?",
                    "a": "In the 'Buyer Leads' section, leads are grouped under each product from your catalog. You can filter by any specific product from the dropdown or clear the filter to view all products at once.",
                },
                {
                    "q": "What happens when I save a lead to the pipeline?",
                    "a": "Clicking 'Save Lead' moves the discovered company into your 'Saved Buyers' CRM pipeline, where you can track deal stages (Discovered, Contacted, In Negotiation, Proposal Sent, Won, Lost) and add internal follow-up notes.",
                },
                {
                    "q": "How do I send commercial proposals or RFQ inquiries?",
                    "a": "Click 'Contact & Inquire' on any discovered buyer card or lead row. Fill in the proposal subject, target unit price, and requirement details to dispatch an inquiry directly to the buyer's contact channel.",
                },
            ],
        },
        {
            "category": "Subscriptions, Billing & AI Credits",
            "icon": "fa-coins",
            "color": "emerald",
            "items": [
                {
                    "q": "How are AI Search credits deducted?",
                    "a": "Each AI search run (Find Buyers or Find Suppliers) consumes 1 search credit. Normal browsing, CRM pipeline updates, and exporting reports do not consume credits.",
                },
                {
                    "q": "How do I upgrade my plan or purchase more credits?",
                    "a": "Navigate to 'Subscription & Credits' under the Company section. You can view your current plan (Free, Starter, Pro, Enterprise), check credit consumption analytics, and upgrade to higher quotas.",
                },
            ],
        },
    ]

    return render(
        request,
        "dashboard/help_support.html",
        {
            "company": company,
            "rbac": rbac,
            "tickets": tickets,
            "open_tickets_count": open_tickets_count,
            "resolved_tickets_count": resolved_tickets_count,
            "faqs": faqs,
            "categories": SupportTicket.Category.choices,
            "priorities": SupportTicket.Priority.choices,
            "page_title": "Help & Support Desk",
        },
    )


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
        ticket = get_object_or_404(SupportTicket, pk=pk)
    else:
        ticket = get_object_or_404(SupportTicket, pk=pk, company=company)

    return JsonResponse({
        "success": True,
        "id": ticket.id,
        "ticket_number": ticket.ticket_number,
        "subject": ticket.subject,
        "description": ticket.description,
        "category": ticket.get_category_display(),
        "priority": ticket.get_priority_display(),
        "status": ticket.get_status_display(),
        "contact_email": ticket.contact_email,
        "contact_phone": ticket.contact_phone,
        "resolution": ticket.resolution or "In evaluation by technical operations team.",
        "created_at": ticket.created_at.strftime("%b %d, %Y at %I:%M %p"),
        "updated_at": ticket.updated_at.strftime("%b %d, %Y at %I:%M %p"),
    })


