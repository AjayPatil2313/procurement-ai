import csv
import io
import re
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.ai_search.models import (
    SearchJob,
    SearchResult,
    MatchingParameter,
    ensure_default_parameters_for_company,
)
from apps.ai_search.services.pipeline import run_find_buyers_search
from apps.catalog.models import Category, Product, ProductImage
from apps.companies.rbac import get_user_rbac_context
from apps.leads.models import SavedItem
from apps.dashboard.ratelimit import rate_limit


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
    can_export = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("EXPORT" in rbac["permissions"])
        or ("products:EXPORT" in rbac["permissions"])
        or ("general:EXPORT" in rbac["permissions"])
    )
    can_import = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("IMPORT" in rbac["permissions"])
        or ("products:IMPORT" in rbac["permissions"])
        or ("general:IMPORT" in rbac["permissions"])
    )

    # Scoped query: ABC company will ONLY see ABC products
    status_filter = request.GET.get("status", "active").strip().lower()
    is_archived_view = status_filter == "archived"

    if is_archived_view:
        queryset = Product.objects.filter(company=company, is_deleted=True).select_related("category", "created_by").prefetch_related("images")
    else:
        queryset = Product.objects.filter(company=company, is_deleted=False).select_related("category", "created_by").prefetch_related("images")

    # Metrics
    active_count = Product.objects.filter(company=company, is_deleted=False).count()
    archived_count = Product.objects.filter(company=company, is_deleted=True).count()
    in_stock_count = Product.objects.filter(company=company, is_deleted=False, availability=Product.Availability.IN_STOCK).count()
    made_to_order_count = Product.objects.filter(company=company, is_deleted=False, availability=Product.Availability.MADE_TO_ORDER).count()

    # Filters
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(name__icontains=q)
            | Q(sku__icontains=q)
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

    total_count = queryset.count()
    categories = Category.objects.all().order_by("name")

    # Server-side pagination (20 items per page)
    paginator = Paginator(queryset.order_by("-created_at"), 20)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    return render(request, "products/list.html", {
        "products": page_obj.object_list,
        "page_obj": page_obj,
        "paginator": paginator,
        "status_filter": status_filter,
        "is_archived_view": is_archived_view,
        "active_count": active_count,
        "archived_count": archived_count,
        "categories": categories,
        "company": company,
        "rbac": rbac,
        "can_create": can_create,
        "can_edit": can_edit,
        "can_delete": can_delete,
        "can_export": can_export,
        "can_import": can_import,
        "total_count": total_count,
        "in_stock_count": in_stock_count,
        "made_to_order_count": made_to_order_count,
        "q": q,
        "selected_category": category_id,
        "selected_availability": availability_val,
        "availability_choices": Product.Availability.choices,
        "page_title": "Archived Products" if is_archived_view else "Products Catalog",
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
    form_data = {}
    duplicate_id = request.GET.get("duplicate_id", "").strip()
    if request.method == "GET" and duplicate_id:
        orig = Product.objects.filter(pk=duplicate_id, company=company, is_deleted=False).first()
        if orig:
            primary_img = orig.images.filter(is_primary=True).first()
            form_data = {
                "name": f"[Copy] {orig.name}",
                "sku": f"{orig.sku}-COPY" if orig.sku else "",
                "category_id": str(orig.category_id) if orig.category_id else "",
                "description": orig.description,
                "specifications": orig.specifications,
                "price": str(orig.price) if orig.price else "",
                "currency": orig.currency,
                "location": orig.location,
                "search_scope": orig.search_scope,
                "image_url": primary_img.image_url if primary_img else "",
            }
            messages.info(request, f"Pre-filled form based on existing product '{orig.name}'. Customize details and save as a new item.")

    if request.method == "POST":
        from apps.billing.services.wallet import CreditWalletService
        can_add, current_count, max_limit = CreditWalletService.can_add_product(company)
        if not can_add:
            messages.error(request, f"Cannot add product: Plan catalog limit reached ({current_count}/{max_limit} products). Please upgrade your subscription plan.")
            return redirect("products-list")

        name = request.POST.get("name", "").strip()
        sku = request.POST.get("sku", "").strip()
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
                if price < 0:
                    price = None
            except Exception:
                price = None

        moq = Decimal(1)
        if moq_str:
            try:
                moq = Decimal(moq_str)
            except Exception:
                moq = Decimal(1)

        duplicate_exists = Product.objects.filter(company=company, name__iexact=name, is_deleted=False).exists()

        product = Product.objects.create(
            company=company,
            name=name,
            sku=sku,
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

        uploaded_img = request.FILES.get("image_file")
        if uploaded_img:
            ProductImage.objects.create(
                product=product,
                image=uploaded_img,
                is_primary=True,
            )
        elif image_url:
            ProductImage.objects.create(
                product=product,
                image_url=image_url,
                is_primary=True,
            )

        if duplicate_exists:
            messages.warning(request, f"Product '{name}' created. Note: Another product with this exact name already exists in your catalog.")
        else:
            messages.success(request, f"Product '{name}' was created successfully!")
        return redirect("product-detail", pk=product.pk)

    return render(request, "products/create.html", {
        "categories": categories,
        "company": company,
        "rbac": rbac,
        "form_data": form_data,
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
        sku = request.POST.get("sku", "").strip()
        cat_id = request.POST.get("category_id")
        description = request.POST.get("description", "").strip()
        specifications = request.POST.get("specifications", "").strip()
        price_str = request.POST.get("price", "").strip()
        currency = request.POST.get("currency", "INR").strip().upper()
        moq_str = request.POST.get("minimum_order_quantity", "").strip() or request.POST.get("moq", "").strip()
        unit = request.POST.get("unit", product.unit).strip() or product.unit
        availability = request.POST.get("availability", product.availability).strip() or product.availability
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
        product.sku = sku
        product.category_id = cat_id if cat_id else None
        product.description = description
        product.specifications = specifications
        product.currency = currency or "INR"
        product.unit = unit or product.unit or "pcs"
        product.availability = availability or product.availability or Product.Availability.IN_STOCK
        if moq_str:
            try:
                product.minimum_order_quantity = Decimal(moq_str)
                product.moq = product.minimum_order_quantity
            except Exception:
                pass
        product.location = location
        product.search_scope = search_scope

        if price_str:
            try:
                p_val = Decimal(price_str)
                product.price = p_val if p_val >= 0 else None
                product.price_min = product.price
            except Exception:
                pass
        else:
            product.price = None

        product.save()

        uploaded_img = request.FILES.get("image_file")
        if uploaded_img:
            primary_img = product.images.filter(is_primary=True).first()
            if primary_img:
                primary_img.image = uploaded_img
                primary_img.image_url = ""
                primary_img.save()
            else:
                ProductImage.objects.create(
                    product=product,
                    image=uploaded_img,
                    is_primary=True,
                )
        elif image_url:
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
def product_restore_view(request, pk):
    """
    1-Click Restore Soft-Deleted Product:
    Restores is_deleted=False, deleted_at=None and returns product to active catalog.
    Enforces UPDATE/DELETE RBAC permission and company data isolation.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    can_restore = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("UPDATE" in rbac["permissions"])
        or ("DELETE" in rbac["permissions"])
        or ("products:UPDATE" in rbac["permissions"])
        or ("products:DELETE" in rbac["permissions"])
        or ("general:UPDATE" in rbac["permissions"])
    )
    if not can_restore:
        messages.error(request, "Permission denied: You do not have permission to restore products.")
        return redirect("products-list")

    if rbac["is_super_admin"] and not company:
        product = get_object_or_404(Product, pk=pk, is_deleted=True)
    else:
        product = get_object_or_404(Product, pk=pk, company=company, is_deleted=True)

    if request.method == "POST":
        product.restore()
        messages.success(request, f"Product '{product.name}' was successfully restored to your active catalog.")
        return redirect(f"{reverse('products-list')}?status=active")

    return redirect(f"{reverse('products-list')}?status=archived")


STATIC_CRITERIA_METADATA = [
    {
        "key": "entity_type",
        "name": "Legal Entity Structure (Pvt Ltd / Ltd)",
        "default_value": "Private Limited (Pvt Ltd), Public Limited, Corporate Entity",
        "description": "Target buyer/vendor must be an officially incorporated Private Limited (Pvt Ltd) or Public Ltd commercial entity with corporate standing.",
        "icon": "fa-building-columns",
        "color": "blue",
    },
    {
        "key": "verified_status",
        "name": "Verified Contact & Registration",
        "default_value": "Verified Phone, Direct Corporate Email & Official Web Domain",
        "description": "Company must possess reachable communication channels, confirmed phone lines, and verified operational presence.",
        "icon": "fa-circle-check",
        "color": "emerald",
    },
    {
        "key": "categories_match",
        "name": "Product Category Alignment",
        "default_value": "Target Industrial Category & Sector Classification",
        "description": "Company actively manufactures, supplies, or procures within the specific product category classification.",
        "icon": "fa-layer-group",
        "color": "amber",
    },
    {
        "key": "industry_match",
        "name": "Industry Vertical Match",
        "default_value": "Manufacturing, Infrastructure, EPC, Chemical, Heavy Engineering",
        "description": "Company operates in an industrial vertical that routinely consumes or supplies relevant engineering goods.",
        "icon": "fa-industry",
        "color": "indigo",
    },
    {
        "key": "specifications",
        "name": "Company Specifications (Size, Grade, Capacity)",
        "default_value": "Enterprise Scale, Quality Grade (ISO/MTC), Plant Capacity",
        "description": "Operational scale, unit capacity, dimensions, and quality grade standards match procurement requirements.",
        "icon": "fa-ruler-combined",
        "color": "teal",
    },
    {
        "key": "product_matching",
        "name": "Product Technical Matching",
        "default_value": "Direct Technical Compatibility & SKU Relevance",
        "description": "Direct operational alignment between the offered product technical specifications and catalog item.",
        "icon": "fa-microchip",
        "color": "purple",
    },
    {
        "key": "location",
        "name": "Geographic Location & Delivery Proximity",
        "default_value": "Regional Hub, Industrial Corridor, Domestic Proximity",
        "description": "Proximity between buyer plant or project location and target logistics dispatch corridor.",
        "icon": "fa-location-dot",
        "color": "rose",
    },
    {
        "key": "buyer_req_match",
        "name": "Buyer Requirement & Demand Intent",
        "default_value": "Active Tender, Expansion Capex, Recurring Maintenance Demand",
        "description": "Buyer demonstrates an active buying signal, vendor empanelement window, ongoing RFQ, or replenishment cycle.",
        "icon": "fa-bell",
        "color": "amber",
    },
    {
        "key": "b2b_model",
        "name": "B2B Commercial Operating Model",
        "default_value": "Commercial B2B Wholesale / Institutional Bulk Consumer",
        "description": "Buyer must operate on a B2B model (wholesale, institutional project, OEM, or distributor) and not retail/consumer.",
        "icon": "fa-handshake",
        "color": "blue",
    },
    {
        "key": "profile_relevance",
        "name": "Company Profile & Operational Relevance",
        "default_value": "Operational Plant, Facility Infrastructure, Historical Track Record",
        "description": "Buyer's plant infrastructure and published commercial activities align with our technical portfolio.",
        "icon": "fa-id-card",
        "color": "slate",
    },
]

STATIC_CRITERIA_KEYS = {m["key"] for m in STATIC_CRITERIA_METADATA}


def _sync_company_matching_criteria(company, post_data):
    """
    Synchronizes static checkboxes and custom key-value dynamic rows from form POST.
    Ensures persistence per company and returns the list of active MatchingParameter objects.
    """
    if not company:
        return []

    ensure_default_parameters_for_company(company)
    all_params = {p.parameter_key: p for p in MatchingParameter.objects.filter(company=company)}

    # 1. Update static parameters
    static_keys_checked = set(post_data.getlist("static_criteria"))
    for meta in STATIC_CRITERIA_METADATA:
        key = meta["key"]
        p = all_params.get(key)
        submitted_val = post_data.get(f"static_val_{key}", "").strip()
        val = submitted_val if submitted_val else (p.criteria_value if (p and p.criteria_value) else meta["default_value"])
        is_active = key in static_keys_checked

        if p:
            p.criteria_value = val[:255]
            p.is_active = is_active
            p.save(update_fields=["criteria_value", "is_active", "updated_at"])
        else:
            MatchingParameter.objects.create(
                company=company,
                name=meta["name"],
                parameter_key=key,
                criteria_value=val[:255],
                description=meta["description"],
                rule_type=MatchingParameter.RuleType.WEIGHTED,
                weight_percentage=10,
                is_active=is_active,
            )

    # 2. Update dynamic custom parameters (Key & Value pairs)
    custom_keys = post_data.getlist("custom_criteria_key[]")
    custom_vals = post_data.getlist("custom_criteria_val[]")

    # Clean existing custom parameters not in static list
    existing_custom = MatchingParameter.objects.filter(company=company).exclude(parameter_key__in=STATIC_CRITERIA_KEYS)
    existing_custom.delete()

    for k, v in zip(custom_keys, custom_vals):
        k_clean = k.strip()
        v_clean = v.strip()
        if not k_clean:
            continue
        p_key = re.sub(r"[^a-zA-Z0-9_]", "_", k_clean.lower())[:80]
        if not p_key:
            p_key = f"custom_{timezone.now().timestamp()}"
        MatchingParameter.objects.create(
            company=company,
            name=k_clean[:150],
            parameter_key=p_key,
            criteria_value=v_clean[:255],
            description=f"Custom Criteria: {k_clean}",
            rule_type=MatchingParameter.RuleType.WEIGHTED,
            weight_percentage=10,
            is_active=True,
        )

    return list(MatchingParameter.objects.filter(company=company, is_active=True))


@login_required
@rate_limit(rate=30, period=60, key_type="company")
def find_buyers_view(request):
    """
    Find Buyers (AI Matching & Web Scraping):
    Allows sellers to initiate live web scraping + AI lead discovery for products,
    evaluating results with customizable static enterprise criteria and dynamic key-value parameters.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    products = Product.objects.filter(company=company, is_deleted=False).order_by(Lower("name"))
    subscription = getattr(company, "subscription", None) if company else None

    # Handle save_criteria action (standalone or AJAX)
    if request.method == "POST" and request.POST.get("action") == "save_criteria":
        active_params = _sync_company_matching_criteria(company, request.POST)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "active_count": len(active_params),
                "message": f"Successfully updated and applied {len(active_params)} matching parameters.",
            })
        messages.success(request, f"Updated and applied {len(active_params)} matching parameters.")
        prod_id = request.POST.get("product_id")
        return redirect(f"{reverse('find-buyers')}?product_id={prod_id}" if prod_id else reverse('find-buyers'))

    # Handle search initiation via POST or GET ?run_search=1
    if request.method == "POST" and request.POST.get("action") == "run_search":
        prod_id = request.POST.get("product_id")
        target_prod = Product.objects.filter(id=prod_id, company=company, is_deleted=False).first()
        if target_prod:
            if subscription and subscription.credits_remaining <= 0:
                messages.error(request, f"Search blocked: AI credits exhausted ({subscription.credits_used}/{subscription.credits_total}). Please contact Super Admin to top up credits.")
                return redirect(f"{reverse('find-buyers')}?product_id={target_prod.id}")

            # If payload has criteria fields, sync them
            if request.POST.get("has_criteria_payload") == "1":
                active_params = _sync_company_matching_criteria(company, request.POST)
            else:
                active_params = list(MatchingParameter.objects.filter(company=company, is_active=True))

            job = run_find_buyers_search(target_prod, request.user, company, criteria_override=active_params)
            if job.status == SearchJob.Status.FAILED:
                messages.error(request, f"Search failed: {job.error_message}")
            else:
                messages.success(request, f"Found {job.total_results} buyer leads with contact details for '{target_prod.name}' evaluated against {len(active_params)} criteria! (1 AI credit debited)")
            return redirect(f"{reverse('find-buyers')}?product_id={target_prod.id}")
        else:
            messages.error(request, "Please select a valid product to search for buyers.")
            return redirect("find-buyers")

    product_id = request.GET.get("product_id")
    if request.GET.get("run_search") == "1" and product_id:
        target_prod = Product.objects.filter(id=product_id, company=company, is_deleted=False).first()
        if target_prod:
            if subscription and subscription.credits_remaining <= 0:
                messages.error(request, f"Search blocked: AI credits exhausted ({subscription.credits_used}/{subscription.credits_total}). Please contact Super Admin to top up credits.")
                return redirect(f"{reverse('find-buyers')}?product_id={target_prod.id}")
            active_params = list(MatchingParameter.objects.filter(company=company, is_active=True))
            job = run_find_buyers_search(target_prod, request.user, company, criteria_override=active_params)
            if job.status == SearchJob.Status.FAILED:
                messages.error(request, f"Search failed: {job.error_message}")
            else:
                messages.success(request, f"Search completed: Discovered {job.total_results} potential buyer leads for '{target_prod.name}'! (1 AI credit debited)")
            return redirect(f"{reverse('find-buyers')}?product_id={target_prod.id}")

    leads_qs = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.LEAD,
    ).select_related("external_company", "search_job", "search_job__product").order_by("-match_score", "-created_at")

    selected_product = None
    leads = []
    if product_id and product_id.isdigit():
        leads_qs = leads_qs.filter(search_job__product_id=product_id)
        selected_product = Product.objects.filter(id=product_id, company=company, is_deleted=False).first()
        if selected_product:
            seen_company_ids = set()
            for lead in leads_qs:
                cid = lead.external_company_id
                if cid and cid in seen_company_ids:
                    continue
                if cid:
                    seen_company_ids.add(cid)
                leads.append(lead)
                if len(leads) >= 40:
                    break

    saved_result_ids = set(SavedItem.objects.filter(company=company).values_list("search_result_id", flat=True)) if company else set()

    # Load and organize criteria for this company
    company_params = ensure_default_parameters_for_company(company) if company else []
    params_by_key = {p.parameter_key: p for p in company_params}

    static_criteria_list = []
    for meta in STATIC_CRITERIA_METADATA:
        p = params_by_key.get(meta["key"])
        static_criteria_list.append({
            "key": meta["key"],
            "name": meta["name"],
            "description": meta["description"],
            "icon": meta["icon"],
            "color": meta["color"],
            "is_active": p.is_active if p else True,
            "criteria_value": p.criteria_value if (p and p.criteria_value) else meta["default_value"],
        })

    custom_criteria_list = [p for p in company_params if p.parameter_key not in STATIC_CRITERIA_KEYS]
    active_criteria_count = sum(1 for sc in static_criteria_list if sc["is_active"]) + sum(1 for cc in custom_criteria_list if cc.is_active)

    latest_job_qs = SearchJob.objects.filter(company=company, job_type=SearchJob.JobType.FIND_BUYERS)
    if selected_product:
        latest_job_qs = latest_job_qs.filter(product=selected_product)
    current_job = latest_job_qs.order_by("-created_at").first()

    return render(request, "products/find_buyers.html", {
        "leads": leads,
        "saved_result_ids": saved_result_ids,
        "products": products,
        "selected_product": selected_product,
        "selected_product_id": int(product_id) if product_id and product_id.isdigit() else None,
        "current_job": current_job,
        "company": company,
        "subscription": subscription,
        "rbac": rbac,
        "static_criteria_list": static_criteria_list,
        "custom_criteria_list": custom_criteria_list,
        "active_criteria_count": active_criteria_count,
        "page_title": "Find Buyers (AI Lead Engine)",
    })


@login_required
def export_discovered_vendors_csv_view(request):
    """
    Export Discovered Vendors (CSV):
    Downloads a structured CSV containing qualified leads discovered for the selected product.
    Includes Company Name, Role, Industry, Website, Email, Phone, Address, City, State, Country,
    Match Score, Match Reason, Need Signal, and Pipeline status.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    product_id = request.GET.get("product_id")
    selected_product = None
    leads_qs = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.LEAD,
    ).select_related("external_company", "search_job", "search_job__product").order_by("-match_score", "-created_at")

    if product_id and product_id.isdigit():
        selected_product = Product.objects.filter(id=product_id, company=company, is_deleted=False).first()
        if selected_product:
            leads_qs = leads_qs.filter(search_job__product_id=selected_product.id)

    saved_result_ids = set(SavedItem.objects.filter(company=company).values_list("search_result_id", flat=True)) if company else set()

    seen_company_ids = set()
    leads = []
    for lead in leads_qs:
        cid = lead.external_company_id
        if cid and cid in seen_company_ids:
            continue
        if cid:
            seen_company_ids.add(cid)
        leads.append(lead)

    response = HttpResponse(content_type="text/csv")
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", selected_product.name.lower()) if selected_product else "all"
    response["Content-Disposition"] = f'attachment; filename="discovered_vendors_{safe_name}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Product Name",
        "Company Name",
        "Company Role",
        "Industry",
        "Website",
        "Email",
        "Phone",
        "Address",
        "City",
        "State",
        "Country",
        "Match Score (%)",
        "Match Reason",
        "Need Signal",
        "Saved in Pipeline",
        "Discovered Date",
    ])

    for l in leads:
        ext = l.external_company
        is_saved = "Yes" if l.id in saved_result_ids else "No"
        writer.writerow([
            l.product_title or (selected_product.name if selected_product else ""),
            ext.name if ext else "",
            ext.get_company_role_display() if ext else "Vendor",
            ext.industry if ext else "",
            ext.website or l.source_url if ext else l.source_url,
            ext.email if ext else "",
            ext.phone if ext else "",
            ext.address if ext else "",
            ext.city if ext else "",
            ext.state if ext else "",
            ext.country if ext else "India",
            l.match_score,
            l.match_reason or "",
            l.need_signal or "",
            is_saved,
            l.created_at.strftime("%Y-%m-%d %H:%M") if l.created_at else "",
        ])

    return response


@login_required
def product_toggle_availability_view(request, pk):
    """
    1-Click Stock Availability Toggle:
    Updates availability (IN_STOCK, MADE_TO_ORDER, AVAILABLE_ON_REQUEST, OUT_OF_STOCK)
    directly without requiring a full edit form submission.
    Supports both AJAX and standard POST.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Access restricted"}, status=403)
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
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Permission denied"}, status=403)
        messages.error(request, "Permission denied: You do not have 'UPDATE' permission for products.")
        return redirect("products-list")

    if rbac["is_super_admin"] and not company:
        product = get_object_or_404(Product, pk=pk, is_deleted=False)
    else:
        product = get_object_or_404(Product, pk=pk, company=company, is_deleted=False)

    if request.method == "POST":
        new_avail = request.POST.get("availability", "").strip()
        if new_avail in dict(Product.Availability.choices):
            product.availability = new_avail
            product.save(update_fields=["availability", "updated_at"])

            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({
                    "success": True,
                    "product_id": product.id,
                    "availability": product.availability,
                    "availability_display": product.get_availability_display(),
                    "message": f"Updated stock status for '{product.name}' to {product.get_availability_display()}",
                })

            messages.success(request, f"Updated '{product.name}' status to {product.get_availability_display()}.")
        else:
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": False, "error": "Invalid availability status"}, status=400)
            messages.error(request, "Invalid availability status selected.")

    return redirect("products-list")


@login_required
def products_export_csv_view(request):
    """
    Catalog CSV Export:
    Exports all active products for the company into a cleanly formatted CSV.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    can_export = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("EXPORT" in rbac["permissions"])
        or ("products:EXPORT" in rbac["permissions"])
        or ("general:EXPORT" in rbac["permissions"])
    )
    if not can_export:
        messages.error(request, "Permission denied: You do not have 'EXPORT' permission.")
        return redirect("products-list")

    products = Product.objects.filter(company=company, is_deleted=False).select_related("category", "created_by").order_by("name")

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    comp_slug = (company.name if company else "Company").replace(" ", "_")
    response["Content-Disposition"] = f'attachment; filename="Product_Catalog_{comp_slug}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Product Name",
        "SKU",
        "Category",
        "Type",
        "Price",
        "Currency",
        "MOQ",
        "Unit",
        "Availability",
        "Location",
        "Search Scope",
        "Description",
        "Specifications",
        "Created At",
    ])

    for p in products:
        writer.writerow([
            p.name,
            p.sku or "",
            p.category.name if p.category else "General",
            p.get_type_display(),
            p.price or "",
            p.currency,
            p.minimum_order_quantity or p.moq or 1,
            p.unit,
            p.get_availability_display(),
            p.location or (company.city if company else "India"),
            p.get_search_scope_display(),
            p.description or "",
            p.specifications or "",
            p.created_at.strftime("%Y-%m-%d %H:%M"),
        ])

    return response


@login_required
def products_import_template_view(request):
    """
    Returns a sample CSV template for bulk product upload.
    """
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="Product_Catalog_Sample_Template.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Product Name",
        "SKU",
        "Category",
        "Price",
        "Currency",
        "MOQ",
        "Unit",
        "Availability",
        "Location",
        "Search Scope",
        "Description",
        "Specifications",
    ])
    writer.writerow([
        "Industrial SS Gate Valve 2-inch",
        "VLV-SS-002",
        "Industrial Machinery",
        "14500.00",
        "INR",
        "5",
        "pcs",
        "IN_STOCK",
        "Ahmedabad, Gujarat",
        "country",
        "Heavy-duty forged stainless steel gate valve rated for 150 PSI industrial steam lines.",
        "Material: SS316; Pressure: 150 PSI; Connection: Flanged",
    ])
    writer.writerow([
        "High-Grade Aluminum Casting Rods",
        "AL-ROD-6061",
        "Metals & Alloys",
        "320.00",
        "INR",
        "100",
        "kg",
        "MADE_TO_ORDER",
        "Pune, Maharashtra",
        "global",
        "Custom extruded aluminum alloy 6061 rods for aerospace and automotive tooling.",
        "Alloy: 6061-T6; Diameter: 25mm to 100mm; Purity: 99.7%",
    ])
    return response


@login_required
def products_import_csv_view(request):
    """
    Bulk CSV Product Import:
    Parses an uploaded CSV file, validates columns, maps or creates categories,
    and bulk creates product catalog entries for the current active company.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_seller"]:
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    can_import = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("IMPORT" in rbac["permissions"])
        or ("products:IMPORT" in rbac["permissions"])
        or ("general:IMPORT" in rbac["permissions"])
    )
    if not can_import:
        messages.error(request, "Permission denied: You do not have 'IMPORT' permission to upload products.")
        return redirect("products-list")

    if request.method != "POST" or "csv_file" not in request.FILES:
        messages.error(request, "Please select a valid CSV file to upload.")
        return redirect("products-list")

    csv_file = request.FILES["csv_file"]
    if not csv_file.name.lower().endswith(".csv"):
        messages.error(request, "Invalid file format. Please upload a .csv file.")
        return redirect("products-list")

    try:
        file_data = csv_file.read()
        try:
            decoded_file = file_data.decode("utf-8-sig")
        except UnicodeDecodeError:
            decoded_file = file_data.decode("latin-1")

        reader = csv.DictReader(io.StringIO(decoded_file))
        if not reader.fieldnames:
            messages.error(request, "CSV file appears to be empty or corrupted.")
            return redirect("products-list")

        # Normalize fieldnames
        field_map = {fn.strip().lower(): fn for fn in reader.fieldnames}
        name_col = next((field_map[k] for k in ["product name", "name", "title", "item"] if k in field_map), None)
        if not name_col:
            messages.error(request, "CSV must contain a 'Product Name' or 'Name' column.")
            return redirect("products-list")

        sku_col = next((field_map[k] for k in ["sku", "product code", "item code", "part number", "part no"] if k in field_map), None)
        cat_col = next((field_map[k] for k in ["category", "category name"] if k in field_map), None)
        price_col = next((field_map[k] for k in ["price", "unit price"] if k in field_map), None)
        curr_col = next((field_map[k] for k in ["currency"] if k in field_map), None)
        moq_col = next((field_map[k] for k in ["moq", "minimum order quantity", "min order qty"] if k in field_map), None)
        unit_col = next((field_map[k] for k in ["unit", "uom"] if k in field_map), None)
        avail_col = next((field_map[k] for k in ["availability", "stock status", "status"] if k in field_map), None)
        loc_col = next((field_map[k] for k in ["location", "city", "dispatch hub"] if k in field_map), None)
        scope_col = next((field_map[k] for k in ["search scope", "scope"] if k in field_map), None)
        desc_col = next((field_map[k] for k in ["description", "desc"] if k in field_map), None)
        specs_col = next((field_map[k] for k in ["specifications", "specs"] if k in field_map), None)

        imported_count = 0
        skipped_count = 0
        skip_reasons = []
        row_idx = 1

        with transaction.atomic():
            for row in reader:
                row_idx += 1
                raw_name = row.get(name_col, "").strip()
                if not raw_name:
                    skipped_count += 1
                    skip_reasons.append(f"Row {row_idx}: Missing product name")
                    continue

                raw_sku = row.get(sku_col, "").strip() if sku_col else ""

                category_obj = None
                if cat_col and row.get(cat_col, "").strip():
                    cat_name = row.get(cat_col, "").strip()
                    category_obj, _ = Category.objects.get_or_create(name=cat_name)

                price = None
                if price_col and row.get(price_col, "").strip():
                    try:
                        p_val = Decimal(row[price_col].strip().replace(",", ""))
                        price = p_val if p_val >= 0 else None
                    except Exception:
                        price = None

                moq = Decimal(1)
                if moq_col and row.get(moq_col, "").strip():
                    try:
                        m_val = Decimal(row[moq_col].strip().replace(",", ""))
                        moq = m_val if m_val > 0 else Decimal(1)
                    except Exception:
                        moq = Decimal(1)

                currency = (row.get(curr_col, "INR").strip().upper() if curr_col else "INR") or "INR"
                unit = (row.get(unit_col, "pcs").strip() if unit_col else "pcs") or "pcs"

                avail_val = Product.Availability.IN_STOCK
                if avail_col and row.get(avail_col, "").strip():
                    raw_avail = row.get(avail_col, "").strip().upper().replace(" ", "_")
                    if raw_avail in dict(Product.Availability.choices):
                        avail_val = raw_avail
                    elif "OUT" in raw_avail:
                        avail_val = Product.Availability.OUT_OF_STOCK
                    elif "ORDER" in raw_avail:
                        avail_val = Product.Availability.MADE_TO_ORDER
                    elif "REQUEST" in raw_avail:
                        avail_val = Product.Availability.AVAILABLE_ON_REQUEST

                scope_val = Product.SearchScope.COUNTRY
                if scope_col and row.get(scope_col, "").strip():
                    raw_scope = row.get(scope_col, "").strip().lower()
                    if raw_scope in dict(Product.SearchScope.choices):
                        scope_val = raw_scope

                location_val = row.get(loc_col, "").strip() if loc_col else ""
                desc_val = row.get(desc_col, "").strip() if desc_col else ""
                specs_val = row.get(specs_col, "").strip() if specs_col else ""

                Product.objects.create(
                    company=company,
                    name=raw_name,
                    sku=raw_sku,
                    category=category_obj,
                    price=price,
                    price_min=price,
                    currency=currency,
                    minimum_order_quantity=moq,
                    moq=moq,
                    unit=unit,
                    availability=avail_val,
                    location=location_val,
                    search_scope=scope_val,
                    description=desc_val,
                    specifications=specs_val,
                    created_by=request.user,
                )
                imported_count += 1

        if imported_count > 0:
            success_msg = f"Successfully imported {imported_count} products into your catalog!"
            if skipped_count > 0:
                preview_reasons = "; ".join(skip_reasons[:3])
                if len(skip_reasons) > 3:
                    preview_reasons += f" (and {len(skip_reasons) - 3} more)"
                messages.warning(request, f"{success_msg} Note: {skipped_count} row(s) were skipped ({preview_reasons}).")
            else:
                messages.success(request, success_msg)
        else:
            reason_text = f" ({'; '.join(skip_reasons[:3])})" if skip_reasons else ""
            messages.warning(request, f"No products could be imported from the CSV file. Please check row data{reason_text}.")

    except Exception as e:
        messages.error(request, f"Error processing CSV file: {str(e)}")

    return redirect("products-list")


