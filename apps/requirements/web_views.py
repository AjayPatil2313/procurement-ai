from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.ai_search.models import SearchJob, SearchResult
from apps.ai_search.services.pipeline import run_find_suppliers_search
from apps.catalog.models import Category
from apps.companies.rbac import get_user_rbac_context
from apps.requirements.models import Requirement


@login_required
def requirements_list_view(request):
    """
    Buyer Requirements List View:
    Displays active (is_deleted=False) requirements belonging to current company.
    Strict multi-tenant data isolation & granular 6-action RBAC permissions.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Your company or user account does not have Procurement module access.")
        return redirect("dashboard")

    # Permissions check for viewing
    can_read = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("READ" in rbac["permissions"])
        or ("requirements:READ" in rbac["permissions"])
        or ("general:READ" in rbac["permissions"])
    )
    if not can_read:
        messages.error(request, "Permission denied: You do not have 'READ' permission for requirements.")
        return redirect("dashboard")

    can_create = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("EDIT" in rbac["permissions"])
        or ("requirements:EDIT" in rbac["permissions"])
        or ("general:EDIT" in rbac["permissions"])
    )
    can_edit = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("UPDATE" in rbac["permissions"])
        or ("requirements:UPDATE" in rbac["permissions"])
        or ("general:UPDATE" in rbac["permissions"])
    )
    can_delete = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("DELETE" in rbac["permissions"])
        or ("requirements:DELETE" in rbac["permissions"])
        or ("general:DELETE" in rbac["permissions"])
    )

    # Scoped query: company-isolated, soft-deleted excluded
    queryset = Requirement.objects.filter(company=company, is_deleted=False).select_related("category", "created_by")

    # Search filter
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(item_name__icontains=q)
            | Q(description__icontains=q)
            | Q(specifications__icontains=q)
            | Q(delivery_city__icontains=q)
            | Q(category__name__icontains=q)
        )

    category_id = request.GET.get("category", "").strip()
    if category_id:
        queryset = queryset.filter(category_id=category_id)

    status_val = request.GET.get("status", "").strip()
    if status_val:
        queryset = queryset.filter(status=status_val)

    scope_val = request.GET.get("scope", "").strip()
    if scope_val:
        queryset = queryset.filter(search_scope=scope_val)

    requirements = list(queryset.order_by("-created_at"))
    categories = Category.objects.all().order_by("name")

    # Metric counts
    total_count = len(requirements)
    searching_count = sum(1 for r in requirements if r.status == Requirement.Status.SEARCHING)
    completed_count = sum(1 for r in requirements if r.status == Requirement.Status.COMPLETED)
    draft_count = sum(1 for r in requirements if r.status == Requirement.Status.DRAFT)

    return render(request, "requirements/list.html", {
        "requirements": requirements,
        "categories": categories,
        "company": company,
        "rbac": rbac,
        "can_create": can_create,
        "can_edit": can_edit,
        "can_delete": can_delete,
        "total_count": total_count,
        "searching_count": searching_count,
        "completed_count": completed_count,
        "draft_count": draft_count,
        "q": q,
        "selected_category": category_id,
        "selected_status": status_val,
        "selected_scope": scope_val,
        "page_title": "My Requirements",
    })


@login_required
def requirement_detail_view(request, pk):
    """
    Requirement Detail View:
    Displays complete specifications, target pricing, delivery parameters, and matches.
    Strict multi-tenant data isolation & granular RBAC permissions.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Buyer module access required.")
        return redirect("dashboard")

    can_read = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("READ" in rbac["permissions"])
        or ("requirements:READ" in rbac["permissions"])
        or ("general:READ" in rbac["permissions"])
    )
    if not can_read:
        messages.error(request, "Permission denied: You do not have 'READ' permission for requirements.")
        return redirect("dashboard")

    if rbac["is_super_admin"] and not company:
        req = get_object_or_404(Requirement, id=pk, is_deleted=False)
    else:
        req = get_object_or_404(Requirement, id=pk, company=company, is_deleted=False)

    can_edit = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("UPDATE" in rbac["permissions"])
        or ("requirements:UPDATE" in rbac["permissions"])
        or ("general:UPDATE" in rbac["permissions"])
    )
    can_delete = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("DELETE" in rbac["permissions"])
        or ("requirements:DELETE" in rbac["permissions"])
        or ("general:DELETE" in rbac["permissions"])
    )

    # Related search jobs
    search_jobs = req.search_jobs.all().order_by("-created_at")[:5]

    return render(request, "requirements/detail.html", {
        "requirement": req,
        "company": company,
        "rbac": rbac,
        "can_edit": can_edit,
        "can_delete": can_delete,
        "search_jobs": search_jobs,
        "page_title": f"{req.item_name} — Details",
    })


@login_required
def requirement_create_view(request):
    """
    Create Requirement View:
    Allows authorized buyers to post procurement requirements with specifications.
    Requires 'EDIT' action permission.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Buyer module permission required.")
        return redirect("dashboard")

    can_create = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("EDIT" in rbac["permissions"])
        or ("requirements:EDIT" in rbac["permissions"])
        or ("general:EDIT" in rbac["permissions"])
    )
    if not can_create:
        messages.error(request, "Permission denied: You do not have 'EDIT' (create) permission for requirements.")
        return redirect("requirements-list")

    categories = Category.objects.all().order_by("name")

    if request.method == "POST":
        item_name = request.POST.get("item_name", "").strip()
        category_id = request.POST.get("category_id") or None
        description = request.POST.get("description", "").strip()
        specifications = request.POST.get("specifications", "").strip()
        quantity_str = request.POST.get("quantity", "1").strip()
        unit = request.POST.get("unit", "pcs").strip() or "pcs"
        target_price_str = request.POST.get("target_price", "").strip()
        currency = request.POST.get("currency", "INR").strip() or "INR"
        delivery_city = request.POST.get("delivery_city", "").strip() or (company.city or "Mumbai")
        delivery_country = request.POST.get("delivery_country", "India").strip() or "India"
        required_by = request.POST.get("required_by") or None
        search_scope = request.POST.get("search_scope", Requirement.SearchScope.GLOBAL)
        radius_km_str = request.POST.get("radius_km", "").strip()
        status_val = request.POST.get("status", Requirement.Status.SEARCHING)

        if not item_name:
            messages.error(request, "Item name is required.")
            return render(request, "requirements/create.html", {
                "categories": categories,
                "company": company,
                "rbac": rbac,
                "page_title": "Create Requirement",
            })

        try:
            quantity = Decimal(quantity_str) if quantity_str else Decimal("1.00")
        except Exception:
            quantity = Decimal("1.00")

        target_price = None
        if target_price_str:
            try:
                target_price = Decimal(target_price_str)
            except Exception:
                target_price = None

        radius_km = None
        if radius_km_str:
            try:
                radius_km = int(radius_km_str)
            except Exception:
                radius_km = None

        category = None
        if category_id:
            category = Category.objects.filter(id=category_id).first()

        req = Requirement.objects.create(
            company=company,
            created_by=request.user,
            item_name=item_name,
            category=category,
            description=description,
            specifications=specifications,
            quantity=quantity,
            unit=unit,
            target_price=target_price,
            currency=currency,
            delivery_city=delivery_city,
            delivery_country=delivery_country,
            required_by=required_by,
            search_scope=search_scope,
            radius_km=radius_km,
            status=status_val,
            is_deleted=False,
        )

        messages.success(request, f"Requirement '{req.item_name}' created successfully!")
        return redirect("requirement-detail", pk=req.id)

    return render(request, "requirements/create.html", {
        "categories": categories,
        "company": company,
        "rbac": rbac,
        "page_title": "Create Requirement",
    })


@login_required
def requirement_edit_view(request, pk):
    """
    Edit Requirement View:
    Allows authorized buyers to update existing requirements.
    Requires 'UPDATE' action permission.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Buyer module permission required.")
        return redirect("dashboard")

    can_edit = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("UPDATE" in rbac["permissions"])
        or ("requirements:UPDATE" in rbac["permissions"])
        or ("general:UPDATE" in rbac["permissions"])
    )
    if not can_edit:
        messages.error(request, "Permission denied: You do not have 'UPDATE' permission for requirements.")
        return redirect("requirements-list")

    if rbac["is_super_admin"] and not company:
        req = get_object_or_404(Requirement, id=pk, is_deleted=False)
    else:
        req = get_object_or_404(Requirement, id=pk, company=company, is_deleted=False)

    categories = Category.objects.all().order_by("name")

    if request.method == "POST":
        item_name = request.POST.get("item_name", "").strip()
        category_id = request.POST.get("category_id") or None
        description = request.POST.get("description", "").strip()
        specifications = request.POST.get("specifications", "").strip()
        quantity_str = request.POST.get("quantity", "").strip()
        unit = request.POST.get("unit", "").strip()
        target_price_str = request.POST.get("target_price", "").strip()
        currency = request.POST.get("currency", "INR").strip()
        delivery_city = request.POST.get("delivery_city", "").strip()
        delivery_country = request.POST.get("delivery_country", "India").strip()
        required_by = request.POST.get("required_by") or None
        search_scope = request.POST.get("search_scope", req.search_scope)
        radius_km_str = request.POST.get("radius_km", "").strip()
        status_val = request.POST.get("status", req.status)

        if not item_name:
            messages.error(request, "Item name cannot be empty.")
            return render(request, "requirements/edit.html", {
                "requirement": req,
                "categories": categories,
                "company": company,
                "rbac": rbac,
                "page_title": f"Edit {req.item_name}",
            })

        req.item_name = item_name
        req.description = description
        req.specifications = specifications

        if category_id:
            req.category = Category.objects.filter(id=category_id).first()
        else:
            req.category = None

        if quantity_str:
            try:
                req.quantity = Decimal(quantity_str)
            except Exception:
                pass
        if unit:
            req.unit = unit

        if target_price_str:
            try:
                req.target_price = Decimal(target_price_str)
            except Exception:
                req.target_price = None
        else:
            req.target_price = None

        if currency:
            req.currency = currency
        if delivery_city:
            req.delivery_city = delivery_city
        if delivery_country:
            req.delivery_country = delivery_country

        req.required_by = required_by
        req.search_scope = search_scope

        if radius_km_str:
            try:
                req.radius_km = int(radius_km_str)
            except Exception:
                req.radius_km = None
        else:
            req.radius_km = None

        if status_val in [s[0] for s in Requirement.Status.choices]:
            req.status = status_val

        req.save()
        messages.success(request, f"Requirement '{req.item_name}' updated successfully.")
        return redirect("requirement-detail", pk=req.id)

    return render(request, "requirements/edit.html", {
        "requirement": req,
        "categories": categories,
        "company": company,
        "rbac": rbac,
        "page_title": f"Edit {req.item_name}",
    })


@login_required
def requirement_delete_view(request, pk):
    """
    Delete Requirement View:
    Performs soft-delete on requirement.
    Requires 'DELETE' action permission.
    """
    if request.method != "POST":
        return redirect("requirements-list")

    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Buyer module permission required.")
        return redirect("dashboard")

    can_delete = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("DELETE" in rbac["permissions"])
        or ("requirements:DELETE" in rbac["permissions"])
        or ("general:DELETE" in rbac["permissions"])
    )
    if not can_delete:
        messages.error(request, "Permission denied: You do not have 'DELETE' permission for requirements.")
        return redirect("requirements-list")

    if rbac["is_super_admin"] and not company:
        req = get_object_or_404(Requirement, id=pk, is_deleted=False)
    else:
        req = get_object_or_404(Requirement, id=pk, company=company, is_deleted=False)

    # Soft-delete requirement
    req.soft_delete()
    messages.success(request, f"Requirement '{req.item_name}' soft-deleted successfully.")
    return redirect("requirements-list")


@login_required
def find_suppliers_view(request):
    """
    Find Suppliers View (AI Match & Web Scraper):
    Initiates live web search and deep scraping for suppliers meeting buyer requirements,
    extracting manufacturer contact info, location, MOQ, and competitive pricing.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Buyer module permission required.")
        return redirect("dashboard")

    requirements = Requirement.objects.filter(company=company, is_deleted=False).order_by("-created_at")

    # Handle search initiation via POST or GET ?run_search=1
    if request.method == "POST" and request.POST.get("action") == "run_search":
        req_id = request.POST.get("requirement_id")
        target_req = Requirement.objects.filter(id=req_id, company=company, is_deleted=False).first()
        if target_req:
            job = run_find_suppliers_search(target_req, request.user, company)
            messages.success(request, f"Supplier discovery complete! Found {job.total_results} matching suppliers for '{target_req.item_name}'.")
            return redirect(f"{reverse('find-suppliers')}?requirement_id={target_req.id}")
        else:
            messages.error(request, "Please select an active requirement to find suppliers.")
            return redirect("find-suppliers")

    requirement_id = request.GET.get("requirement_id")
    if request.GET.get("run_search") == "1" and requirement_id:
        target_req = Requirement.objects.filter(id=requirement_id, company=company, is_deleted=False).first()
        if target_req:
            job = run_find_suppliers_search(target_req, request.user, company)
            messages.success(request, f"Search completed: Identified {job.total_results} suppliers for '{target_req.item_name}'.")
            return redirect(f"{reverse('find-suppliers')}?requirement_id={target_req.id}")

    results_qs = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.SUPPLIER,
    ).select_related("external_company", "search_job", "search_job__requirement").order_by("-match_score", "-created_at")

    selected_requirement = None
    if requirement_id and requirement_id.isdigit():
        results_qs = results_qs.filter(search_job__requirement_id=requirement_id)
        selected_requirement = Requirement.objects.filter(id=requirement_id, company=company, is_deleted=False).first()

    results = results_qs[:30]

    return render(request, "requirements/find_suppliers.html", {
        "results": results,
        "requirements": requirements,
        "selected_requirement": selected_requirement,
        "selected_requirement_id": int(requirement_id) if requirement_id and requirement_id.isdigit() else None,
        "company": company,
        "rbac": rbac,
        "page_title": "Find Suppliers (AI Sourcing)",
    })

