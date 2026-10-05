from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.ai_search.models import SearchJob, SearchResult
from apps.catalog.models import Category, Product, ProductImage
from apps.companies.rbac import get_user_rbac_context


@login_required
def products_list_view(request):
    """
    Seller Product Catalog List View:
    Displays active (is_deleted=False) products belonging to current company.
    Strict multi-tenant data isolation & granular RBAC permissions.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Your company or user account does not have Sales module access.")
        return redirect("dashboard")

    # Permissions check for viewing
    can_read = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("READ" in rbac["permissions"])
        or ("products:READ" in rbac["permissions"])
        or ("general:READ" in rbac["permissions"])
    )
    if not can_read:
        messages.error(request, "Permission denied: You do not have 'READ' permission for products.")
        return redirect("dashboard")

    can_create = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("EDIT" in rbac["permissions"])
        or ("products:EDIT" in rbac["permissions"])
        or ("general:EDIT" in rbac["permissions"])
    )
    can_edit = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("UPDATE" in rbac["permissions"])
        or ("products:UPDATE" in rbac["permissions"])
        or ("general:UPDATE" in rbac["permissions"])
    )
    can_delete = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("DELETE" in rbac["permissions"])
        or ("products:DELETE" in rbac["permissions"])
        or ("general:DELETE" in rbac["permissions"])
    )

    # Scoped query: ABC company will ONLY see ABC products; soft-deleted excluded
    queryset = Product.objects.filter(company=company, is_deleted=False).select_related("category", "created_by").prefetch_related("images")

    # Filters
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(name__icontains=q)
            | Q(description__icontains=q)
            | Q(specifications__icontains=q)
            | Q(location__icontains=q)
            | Q(category__name__icontains=q)
        )

    category_id = request.GET.get("category", "").strip()
    if category_id:
        queryset = queryset.filter(category_id=category_id)

    availability_val = request.GET.get("availability", "").strip()
    if availability_val:
        queryset = queryset.filter(availability=availability_val)

    products = list(queryset.order_by("-created_at"))
    categories = Category.objects.all().order_by("name")

    # Metrics
    total_count = len(products)
    in_stock_count = sum(1 for p in products if p.availability == Product.Availability.IN_STOCK)
    made_to_order_count = sum(1 for p in products if p.availability == Product.Availability.MADE_TO_ORDER)

    return render(request, "products/list.html", {
        "products": products,
        "categories": categories,
        "company": company,
        "rbac": rbac,
        "can_create": can_create,
        "can_edit": can_edit,
        "can_delete": can_delete,
        "total_count": total_count,
        "in_stock_count": in_stock_count,
        "made_to_order_count": made_to_order_count,
        "q": q,
        "selected_category": category_id,
        "selected_availability": availability_val,
        "page_title": "Products Catalog",
    })


@login_required
def product_create_view(request):
    """
    Add Product View:
    Allows authorized seller user or company admin to register a new product item.
    Enforces CREATE/EDIT RBAC permission and sets current company multi-tenancy.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    can_create = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("EDIT" in rbac["permissions"])
        or ("products:EDIT" in rbac["permissions"])
        or ("general:EDIT" in rbac["permissions"])
    )
    if not can_create:
        messages.error(request, "Permission denied: You do not have 'CREATE' or 'EDIT' permission to add products.")
        return redirect("products-list")

    categories = Category.objects.all().order_by("name")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        cat_id = request.POST.get("category_id")
        description = request.POST.get("description", "").strip()
        specifications = request.POST.get("specifications", "").strip()
        price_str = request.POST.get("price", "").strip()
        currency = request.POST.get("currency", "INR").strip().upper()
        moq_str = request.POST.get("minimum_order_quantity", "").strip() or request.POST.get("moq", "").strip()
        unit = request.POST.get("unit", "pcs").strip()
        availability = request.POST.get("availability", Product.Availability.IN_STOCK).strip()
        location = request.POST.get("location", "").strip()
        image_url = request.POST.get("image_url", "").strip()
        search_scope = request.POST.get("search_scope", Product.SearchScope.COUNTRY).strip()

        if not name:
            messages.error(request, "Product name is mandatory.")
            return render(request, "products/create.html", {
                "categories": categories,
                "company": company,
                "rbac": rbac,
                "form_data": request.POST,
            })

        price = None
        if price_str:
            try:
                price = Decimal(price_str)
            except Exception:
                price = None

        moq = Decimal(1)
        if moq_str:
            try:
                moq = Decimal(moq_str)
            except Exception:
                moq = Decimal(1)

        product = Product.objects.create(
            company=company,
            name=name,
            category_id=cat_id if cat_id else None,
            description=description,
            specifications=specifications,
            price=price,
            price_min=price,
            currency=currency or "INR",
            minimum_order_quantity=moq,
            moq=moq,
            unit=unit or "pcs",
            availability=availability or Product.Availability.IN_STOCK,
            location=location,
            search_scope=search_scope or Product.SearchScope.COUNTRY,
            created_by=request.user,
        )

        if image_url:
            ProductImage.objects.create(
                product=product,
                image_url=image_url,
                is_primary=True,
            )

        messages.success(request, f"Product '{name}' was created successfully!")
        return redirect("product-detail", pk=product.pk)

    return render(request, "products/create.html", {
        "categories": categories,
        "company": company,
        "rbac": rbac,
        "availability_choices": Product.Availability.choices,
        "page_title": "Add Product",
    })


@login_required
def product_detail_view(request, pk):
    """
    Product Detail View:
    Displays complete specifications, pricing, MOQ, stock availability, and images.
    Strict company isolation: Users cannot view products of other companies!
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    can_read = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("READ" in rbac["permissions"])
        or ("products:READ" in rbac["permissions"])
        or ("general:READ" in rbac["permissions"])
    )
    if not can_read:
        messages.error(request, "Permission denied: You do not have 'READ' permission for products.")
        return redirect("dashboard")

    can_edit = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("UPDATE" in rbac["permissions"])
        or ("EDIT" in rbac["permissions"])
        or ("products:UPDATE" in rbac["permissions"])
        or ("products:EDIT" in rbac["permissions"])
    )
    can_delete = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("DELETE" in rbac["permissions"])
        or ("products:DELETE" in rbac["permissions"])
    )

    # Multi-tenant scoping: 404 if product does not belong to user's company or is soft-deleted
    if rbac["is_super_admin"] and not company:
        product = get_object_or_404(
            Product.objects.select_related("company", "category", "created_by").prefetch_related("images"),
            pk=pk,
            is_deleted=False,
        )
    else:
        product = get_object_or_404(
            Product.objects.select_related("company", "category", "created_by").prefetch_related("images"),
            pk=pk,
            company=company,
            is_deleted=False,
        )

    return render(request, "products/detail.html", {
        "product": product,
        "company": company,
        "rbac": rbac,
        "can_edit": can_edit,
        "can_delete": can_delete,
        "page_title": product.name,
    })


@login_required
def product_edit_view(request, pk):
    """
    Edit Product View:
    Updates existing product details. Enforces UPDATE/EDIT permission & multi-tenant isolation.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    can_edit = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("UPDATE" in rbac["permissions"])
        or ("products:UPDATE" in rbac["permissions"])
        or ("general:UPDATE" in rbac["permissions"])
    )
    if not can_edit:
        messages.error(request, "Permission denied: You do not have 'UPDATE' permission for products.")
        return redirect("product-detail", pk=pk)

    if rbac["is_super_admin"] and not company:
        product = get_object_or_404(Product, pk=pk, is_deleted=False)
    else:
        product = get_object_or_404(Product, pk=pk, company=company, is_deleted=False)

    categories = Category.objects.all().order_by("name")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        cat_id = request.POST.get("category_id")
        description = request.POST.get("description", "").strip()
        specifications = request.POST.get("specifications", "").strip()
        price_str = request.POST.get("price", "").strip()
        currency = request.POST.get("currency", "INR").strip().upper()
        moq_str = request.POST.get("minimum_order_quantity", "").strip() or request.POST.get("moq", "").strip()
        unit = request.POST.get("unit", "pcs").strip()
        availability = request.POST.get("availability", product.availability).strip()
        location = request.POST.get("location", "").strip()
        image_url = request.POST.get("image_url", "").strip()
        search_scope = request.POST.get("search_scope", product.search_scope).strip()

        if not name:
            messages.error(request, "Product name cannot be empty.")
            return render(request, "products/edit.html", {
                "product": product,
                "categories": categories,
                "company": company,
                "rbac": rbac,
                "availability_choices": Product.Availability.choices,
            })

        product.name = name
        product.category_id = cat_id if cat_id else None
        product.description = description
        product.specifications = specifications
        product.currency = currency or "INR"
        product.unit = unit or "pcs"
        product.availability = availability
        product.location = location
        product.search_scope = search_scope

        if price_str:
            try:
                product.price = Decimal(price_str)
                product.price_min = product.price
            except Exception:
                pass
        else:
            product.price = None

        if moq_str:
            try:
                product.minimum_order_quantity = Decimal(moq_str)
                product.moq = product.minimum_order_quantity
            except Exception:
                pass

        product.save()

        if image_url:
            primary_img = product.images.filter(is_primary=True).first()
            if primary_img:
                primary_img.image_url = image_url
                primary_img.save()
            else:
                ProductImage.objects.create(
                    product=product,
                    image_url=image_url,
                    is_primary=True,
                )

        messages.success(request, f"Product '{product.name}' was successfully updated.")
        return redirect("product-detail", pk=product.pk)

    primary_image = product.images.filter(is_primary=True).first()
    image_url_val = primary_image.image_url if primary_image else ""

    return render(request, "products/edit.html", {
        "product": product,
        "categories": categories,
        "company": company,
        "rbac": rbac,
        "primary_image_url": image_url_val,
        "availability_choices": Product.Availability.choices,
        "page_title": f"Edit {product.name}",
    })


@login_required
def product_delete_view(request, pk):
    """
    Soft Delete Product View:
    Permanently keeps data in DB, flags is_deleted=True and records deleted_at.
    Enforces DELETE RBAC permission and company data isolation.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    can_delete = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("DELETE" in rbac["permissions"])
        or ("products:DELETE" in rbac["permissions"])
        or ("general:DELETE" in rbac["permissions"])
    )
    if not can_delete:
        messages.error(request, "Permission denied: You do not have 'DELETE' permission for products.")
        return redirect("products-list")

    if rbac["is_super_admin"] and not company:
        product = get_object_or_404(Product, pk=pk, is_deleted=False)
    else:
        product = get_object_or_404(Product, pk=pk, company=company, is_deleted=False)

    if request.method == "POST":
        product_name = product.name
        # Soft delete instead of hard delete
        product.soft_delete()
        messages.success(request, f"Product '{product_name}' was removed successfully (soft-deleted).")
        return redirect("products-list")

    return redirect("product-detail", pk=pk)


@login_required
def find_buyers_view(request):
    """
    Find Buyers (AI Matching):
    Lists AI-matched buyer leads for this seller's products.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    product_id = request.GET.get("product_id")
    leads_qs = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.LEAD,
    ).select_related("external_company", "search_job").order_by("-match_score")

    if product_id:
        leads_qs = leads_qs.filter(search_job__product_id=product_id)

    leads = leads_qs[:25]
    products = Product.objects.filter(company=company, is_deleted=False).order_by("name")

    return render(request, "products/find_buyers.html", {
        "leads": leads,
        "products": products,
        "selected_product_id": int(product_id) if product_id and product_id.isdigit() else None,
        "company": company,
        "rbac": rbac,
        "page_title": "Find Buyers (AI)",
    })
