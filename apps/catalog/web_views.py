from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from apps.catalog.models import Product, Category
from apps.ai_search.models import SearchJob, SearchResult
from apps.companies.rbac import get_user_rbac_context


@login_required
def products_list_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Your company or user account does not have Sales module permissions.")
        return redirect("dashboard")

    products = Product.objects.filter(company=company).select_related("category").order_by("-created_at")

    return render(request, "products/list.html", {
        "products": products,
        "company": company,
        "rbac": rbac,
        "page_title": "My Products & Services",
    })


@login_required
def product_create_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    categories = Category.objects.all().order_by("name")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        cat_id = request.POST.get("category_id")
        price_min = request.POST.get("price_min") or None
        price_max = request.POST.get("price_max") or None
        unit = request.POST.get("unit", "pcs")
        moq = request.POST.get("moq", 1)
        description = request.POST.get("description", "")
        search_scope = request.POST.get("search_scope", "global")

        prod = Product.objects.create(
            company=company,
            name=name,
            category_id=cat_id if cat_id else None,
            price_min=price_min,
            price_max=price_max,
            unit=unit,
            moq=moq,
            description=description,
            search_scope=search_scope,
        )

        messages.success(request, f"Product '{name}' added successfully!")
        return redirect("products-list")

    return render(request, "products/create.html", {
        "categories": categories,
        "company": company,
        "rbac": rbac,
        "page_title": "Add Product / Service",
    })


@login_required
def find_buyers_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    leads = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.LEAD,
    ).select_related("external_company", "search_job").order_by("-match_score")[:20]

    return render(request, "products/find_buyers.html", {
        "leads": leads,
        "company": company,
        "rbac": rbac,
        "page_title": "Find Buyers (AI)",
    })
