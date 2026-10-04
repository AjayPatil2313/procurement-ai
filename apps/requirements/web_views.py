from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from apps.requirements.models import Requirement
from apps.catalog.models import Category
from apps.ai_search.models import SearchJob, SearchResult, ExternalCompany
from apps.companies.rbac import get_user_rbac_context, module_permission_required


@login_required
def requirements_list_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Your company or user account does not have Buyer module permissions.")
        return redirect("dashboard")

    requirements = Requirement.objects.filter(company=company).select_related("category", "created_by").order_by("-created_at")

    return render(request, "requirements/list.html", {
        "requirements": requirements,
        "company": company,
        "rbac": rbac,
        "page_title": "My Requirements",
    })


@login_required
def requirement_create_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Buyer module permission required.")
        return redirect("dashboard")

    categories = Category.objects.all().order_by("name")

    if request.method == "POST":
        item_name = request.POST.get("item_name", "").strip()
        cat_id = request.POST.get("category_id")
        quantity = request.POST.get("quantity", 1)
        unit = request.POST.get("unit", "pcs")
        target_price = request.POST.get("target_price") or None
        search_scope = request.POST.get("search_scope", "global")
        radius_km = request.POST.get("radius_km") or None
        specifications = request.POST.get("specifications", "")

        req = Requirement.objects.create(
            company=company,
            created_by=request.user,
            item_name=item_name,
            category_id=cat_id if cat_id else None,
            quantity=quantity,
            unit=unit,
            target_price=target_price,
            search_scope=search_scope,
            radius_km=radius_km if radius_km else None,
            specifications=specifications,
            delivery_city=company.city or "Mumbai",
            delivery_country=company.country or "India",
            status=Requirement.Status.COMPLETED,
        )

        # Trigger or simulate AI search job
        job = SearchJob.objects.create(
            company=company,
            user=request.user,
            job_type=SearchJob.JobType.FIND_SUPPLIERS,
            requirement=req,
            search_query=f"{item_name} suppliers {search_scope}",
            status=SearchJob.Status.COMPLETED,
            total_results=24,
            progress_percent=100,
        )

        messages.success(request, f"Requirement '{item_name}' created and AI Supplier Search completed!")
        return redirect("requirements-list")

    return render(request, "requirements/create.html", {
        "categories": categories,
        "company": company,
        "rbac": rbac,
        "page_title": "Create Requirement",
    })


@login_required
def find_suppliers_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Buyer module permission required.")
        return redirect("dashboard")

    # Get recent supplier results for this company
    results = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.SUPPLIER,
    ).select_related("external_company", "search_job").order_by("-match_score")[:20]

    return render(request, "requirements/find_suppliers.html", {
        "results": results,
        "company": company,
        "rbac": rbac,
        "page_title": "Find Suppliers (AI)",
    })
