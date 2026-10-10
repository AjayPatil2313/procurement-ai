import csv
import io
import re
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
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
from apps.ai_search.services.pipeline import run_find_suppliers_search
from apps.catalog.models import Category
from apps.catalog.web_views import (
    STATIC_CRITERIA_METADATA,
    STATIC_CRITERIA_KEYS,
    _sync_company_matching_criteria,
)
from apps.companies.rbac import get_user_rbac_context
from apps.leads.models import SavedItem
from apps.requirements.models import Requirement
from apps.dashboard.ratelimit import rate_limit


@login_required
def requirements_list_view(request):
    """
    Buyer Requirements List View:
    Displays requirements belonging to current company with server-side pagination,
    Active vs Archived (soft-delete) tabs, category/status filters, and metric summary cards.
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
    can_export = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("EXPORT" in rbac["permissions"])
        or ("requirements:EXPORT" in rbac["permissions"])
        or ("general:EXPORT" in rbac["permissions"])
    )
    can_import = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("IMPORT" in rbac["permissions"])
        or ("requirements:IMPORT" in rbac["permissions"])
        or ("general:IMPORT" in rbac["permissions"])
    )

    # Tab filter: Active vs Archived
    status_filter = request.GET.get("status", "active").strip().lower()
    is_archived_view = status_filter == "archived"

    if is_archived_view:
        queryset = Requirement.objects.filter(company=company, is_deleted=True).select_related("category", "created_by")
    else:
        queryset = Requirement.objects.filter(company=company, is_deleted=False).select_related("category", "created_by")

    # Metric counts
    active_count = Requirement.objects.filter(company=company, is_deleted=False).count()
    archived_count = Requirement.objects.filter(company=company, is_deleted=True).count()
    searching_count = Requirement.objects.filter(company=company, is_deleted=False, status=Requirement.Status.SEARCHING).count()
    completed_count = Requirement.objects.filter(company=company, is_deleted=False, status=Requirement.Status.COMPLETED).count()
    draft_count = Requirement.objects.filter(company=company, is_deleted=False, status=Requirement.Status.DRAFT).count()

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

    status_val = request.GET.get("lifecycle_status", "").strip()
    if status_val and not is_archived_view:
        queryset = queryset.filter(status=status_val)

    scope_val = request.GET.get("scope", "").strip()
    if scope_val:
        queryset = queryset.filter(search_scope=scope_val)

    total_count = queryset.count()
    categories = Category.objects.all().order_by("name")

    # Server-side pagination (20 items per page)
    paginator = Paginator(queryset.order_by("-created_at"), 20)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    return render(request, "requirements/list.html", {
        "requirements": page_obj.object_list,
        "page_obj": page_obj,
        "paginator": paginator,
        "status_filter": status_filter,
        "is_archived_view": is_archived_view,
        "active_count": active_count,
        "archived_count": archived_count,
        "searching_count": searching_count,
        "completed_count": completed_count,
        "draft_count": draft_count,
        "total_count": total_count,
        "categories": categories,
        "company": company,
        "rbac": rbac,
        "can_create": can_create,
        "can_edit": can_edit,
        "can_delete": can_delete,
        "can_export": can_export,
        "can_import": can_import,
        "q": q,
        "selected_category": category_id,
        "selected_lifecycle_status": status_val,
        "selected_scope": scope_val,
        "status_choices": Requirement.Status.choices,
        "scope_choices": Requirement.SearchScope.choices,
        "page_title": "Archived Sourcing Requirements" if is_archived_view else "Sourcing Requirements",
    })


@login_required
def requirement_restore_view(request, pk):
    """
    Restore Archived Requirement:
    Allows authorized users to restore a previously soft-deleted requirement back to active.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Buyer module permission required.")
        return redirect("dashboard")

    can_restore = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("UPDATE" in rbac["permissions"])
        or ("DELETE" in rbac["permissions"])
        or ("requirements:UPDATE" in rbac["permissions"])
        or ("requirements:DELETE" in rbac["permissions"])
        or ("general:UPDATE" in rbac["permissions"])
    )
    if not can_restore:
        messages.error(request, "Permission denied: You do not have permission to restore requirements.")
        return redirect("requirements-list")

    if rbac["is_super_admin"] and not company:
        req = get_object_or_404(Requirement, pk=pk, is_deleted=True)
    else:
        req = get_object_or_404(Requirement, pk=pk, company=company, is_deleted=True)

    if request.method == "POST":
        req.restore()
        messages.success(request, f"Requirement '{req.item_name}' was successfully restored to your active list.")
        return redirect(f"{reverse('requirements-list')}?status=active")

    return redirect(f"{reverse('requirements-list')}?status=archived")


@login_required
def requirements_export_csv_view(request):
    """
    Requirements CSV Export:
    Exports all active procurement requirements for the company into a cleanly formatted CSV.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Buyer module permission required.")
        return redirect("dashboard")

    can_export = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("EXPORT" in rbac["permissions"])
        or ("requirements:EXPORT" in rbac["permissions"])
        or ("general:EXPORT" in rbac["permissions"])
    )
    if not can_export:
        messages.error(request, "Permission denied: You do not have 'EXPORT' permission.")
        return redirect("requirements-list")

    reqs = Requirement.objects.filter(company=company, is_deleted=False).select_related("category", "created_by").order_by("-created_at")

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    comp_slug = (company.name if company else "Company").replace(" ", "_")
    response["Content-Disposition"] = f'attachment; filename="Requirements_{comp_slug}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Requirement Item Name",
        "Category",
        "Quantity",
        "Unit",
        "Target Price",
        "Currency",
        "Delivery City",
        "Delivery Country",
        "Required By Date",
        "Search Scope",
        "Status",
        "Description",
        "Technical Specifications",
        "Created At",
    ])

    for r in reqs:
        writer.writerow([
            r.item_name,
            r.category.name if r.category else "General",
            r.quantity,
            r.unit,
            r.target_price or "",
            r.currency,
            r.delivery_city or "",
            r.delivery_country or "India",
            r.required_by.strftime("%Y-%m-%d") if r.required_by else "",
            r.get_search_scope_display(),
            r.get_status_display(),
            r.description or "",
            r.specifications or "",
            r.created_at.strftime("%Y-%m-%d %H:%M"),
        ])

    return response


@login_required
def requirements_import_template_view(request):
    """
    Returns a sample CSV template for bulk requirement upload.
    """
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="Requirements_Sample_Template.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Item Name",
        "Category",
        "Quantity",
        "Unit",
        "Target Price",
        "Currency",
        "Delivery City",
        "Delivery Country",
        "Required By",
        "Search Scope",
        "Description",
        "Specifications",
    ])
    writer.writerow([
        "SS316 Stainless Steel Flanges 4-inch",
        "Industrial Machinery",
        "50",
        "pcs",
        "3200.00",
        "INR",
        "Mumbai",
        "India",
        "2026-11-30",
        "country",
        "Forged stainless steel blind and slip-on flanges for chemical processing line.",
        "Grade: SS316L, Rating: Class 150, Standard: ANSI B16.5",
    ])
    writer.writerow([
        "High Strength Structural Carbon Steel Beams",
        "Metals & Alloys",
        "10",
        "tons",
        "65000.00",
        "INR",
        "Pune",
        "India",
        "2026-12-15",
        "global",
        "Hot-rolled structural I-beams for warehouse fabrication.",
        "Grade: IS 2062 E250 / ASTM A36, Length: 12 meters",
    ])
    return response


@login_required
def requirements_import_csv_view(request):
    """
    Bulk CSV Requirement Import:
    Parses an uploaded CSV file, validates columns, maps or creates categories,
    and bulk creates procurement requirement entries for the current active company.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Buyer module permission required.")
        return redirect("dashboard")

    can_import = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("IMPORT" in rbac["permissions"])
        or ("requirements:IMPORT" in rbac["permissions"])
        or ("general:IMPORT" in rbac["permissions"])
    )
    if not can_import:
        messages.error(request, "Permission denied: You do not have 'IMPORT' permission to upload requirements.")
        return redirect("requirements-list")

    if request.method != "POST" or "csv_file" not in request.FILES:
        messages.error(request, "Please select a valid CSV file to upload.")
        return redirect("requirements-list")

    csv_file = request.FILES["csv_file"]
    if not csv_file.name.lower().endswith(".csv"):
        messages.error(request, "Invalid file format. Please upload a .csv file.")
        return redirect("requirements-list")

    try:
        file_data = csv_file.read()
        try:
            decoded_file = file_data.decode("utf-8-sig")
        except UnicodeDecodeError:
            decoded_file = file_data.decode("latin-1")

        reader = csv.DictReader(io.StringIO(decoded_file))
        if not reader.fieldnames:
            messages.error(request, "CSV file appears to be empty or corrupted.")
            return redirect("requirements-list")

        # Normalize fieldnames
        field_map = {fn.strip().lower(): fn for fn in reader.fieldnames}
        name_col = next((field_map[k] for k in ["item name", "name", "title", "item", "requirement"] if k in field_map), None)
        if not name_col:
            messages.error(request, "CSV must contain an 'Item Name' or 'Name' column.")
            return redirect("requirements-list")

        cat_col = next((field_map[k] for k in ["category", "category name"] if k in field_map), None)
        qty_col = next((field_map[k] for k in ["quantity", "qty"] if k in field_map), None)
        unit_col = next((field_map[k] for k in ["unit", "uom"] if k in field_map), None)
        price_col = next((field_map[k] for k in ["target price", "price", "budget"] if k in field_map), None)
        curr_col = next((field_map[k] for k in ["currency"] if k in field_map), None)
        city_col = next((field_map[k] for k in ["delivery city", "city", "location"] if k in field_map), None)
        country_col = next((field_map[k] for k in ["delivery country", "country"] if k in field_map), None)
        date_col = next((field_map[k] for k in ["required by", "due date", "required_by"] if k in field_map), None)
        scope_col = next((field_map[k] for k in ["search scope", "scope"] if k in field_map), None)
        desc_col = next((field_map[k] for k in ["description", "details"] if k in field_map), None)
        spec_col = next((field_map[k] for k in ["specifications", "specs", "technical specs"] if k in field_map), None)

        imported_count = 0

        for row in reader:
            item_name = (row.get(name_col) or "").strip()
            if not item_name:
                continue

            category_obj = None
            if cat_col and row.get(cat_col):
                cat_name = row[cat_col].strip()
                if cat_name:
                    category_obj, _ = Category.objects.get_or_create(name=cat_name)

            qty = Decimal("1.00")
            if qty_col and row.get(qty_col):
                try:
                    qty = Decimal(re.sub(r"[^\d.]", "", row[qty_col].strip()) or "1.00")
                except Exception:
                    qty = Decimal("1.00")

            unit = (row.get(unit_col) or "pcs").strip() or "pcs"

            target_price = None
            if price_col and row.get(price_col):
                try:
                    target_price = Decimal(re.sub(r"[^\d.]", "", row[price_col].strip()))
                except Exception:
                    target_price = None

            currency = (row.get(curr_col) or "INR").strip().upper() or "INR"
            delivery_city = (row.get(city_col) or (company.city if company else "Mumbai")).strip()
            delivery_country = (row.get(country_col) or "India").strip() or "India"

            required_by = None
            if date_col and row.get(date_col):
                raw_d = row[date_col].strip()
                try:
                    from datetime import datetime
                    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
                        try:
                            required_by = datetime.strptime(raw_d, fmt).date()
                            break
                        except ValueError:
                            pass
                except Exception:
                    required_by = None

            search_scope = Requirement.SearchScope.GLOBAL
            if scope_col and row.get(scope_col):
                sc_raw = row[scope_col].strip().lower()
                if sc_raw in ["nearby", "local"]:
                    search_scope = Requirement.SearchScope.NEARBY
                elif sc_raw in ["country", "domestic"]:
                    search_scope = Requirement.SearchScope.COUNTRY

            description = (row.get(desc_col) or "").strip()
            specifications = (row.get(spec_col) or "").strip()

            Requirement.objects.create(
                company=company,
                created_by=request.user,
                item_name=item_name[:255],
                category=category_obj,
                description=description,
                specifications=specifications,
                quantity=qty,
                unit=unit[:30],
                target_price=target_price,
                currency=currency[:3],
                delivery_city=delivery_city[:100],
                delivery_country=delivery_country[:100],
                required_by=required_by,
                search_scope=search_scope,
                status=Requirement.Status.SEARCHING,
                is_deleted=False,
            )
            imported_count += 1

        if imported_count > 0:
            messages.success(request, f"Successfully imported {imported_count} requirement(s) into your procurement list.")
        else:
            messages.warning(request, "No requirements were imported. Please check your CSV data format.")

    except Exception as e:
        messages.error(request, f"Failed to import CSV: {e}")

    return redirect("requirements-list")


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
@rate_limit(rate=30, period=60, key_type="company")
def find_suppliers_view(request):
    """
    Find Suppliers View (AI Match & Web Scraper):
    Initiates live web search and deep scraping for suppliers meeting buyer requirements,
    extracting manufacturer contact info, location, MOQ, and competitive pricing.
    Supports dynamic multi-criteria evaluation parameters with instant persistence.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Buyer module permission required.")
        return redirect("dashboard")

    # AJAX criteria save action
    if request.method == "POST" and request.POST.get("action") == "save_criteria":
        active_params = _sync_company_matching_criteria(company, request.POST)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "active_count": len(active_params),
                "message": f"Successfully updated {len(active_params)} matching parameter(s) for supplier discovery.",
            })
        messages.success(request, f"Updated {len(active_params)} matching parameter(s) for supplier discovery.")
        return redirect("find-suppliers")

    requirements = Requirement.objects.filter(company=company, is_deleted=False).order_by("-created_at")
    subscription = getattr(company, "subscription", None) if company else None

    # Handle search initiation via POST or GET ?run_search=1
    if request.method == "POST" and request.POST.get("action") == "run_search":
        if request.POST.get("has_criteria_payload") == "1":
            _sync_company_matching_criteria(company, request.POST)

        req_id = request.POST.get("requirement_id")
        target_req = Requirement.objects.filter(id=req_id, company=company, is_deleted=False).first()
        if target_req:
            if subscription and subscription.credits_remaining <= 0:
                messages.error(request, f"Search blocked: AI credits exhausted ({subscription.credits_used}/{subscription.credits_total}). Please contact Super Admin to top up credits.")
                return redirect(f"{reverse('find-suppliers')}?requirement_id={target_req.id}")
            job = run_find_suppliers_search(target_req, request.user, company)
            if job.status == SearchJob.Status.FAILED:
                messages.error(request, f"Search failed: {job.error_message}")
            else:
                messages.success(request, f"Supplier discovery complete! Found {job.total_results} matching suppliers for '{target_req.item_name}'. (1 AI credit debited)")
            return redirect(f"{reverse('find-suppliers')}?requirement_id={target_req.id}")
        else:
            messages.error(request, "Please select an active requirement to find suppliers.")
            return redirect("find-suppliers")

    requirement_id = request.GET.get("requirement_id")
    if request.GET.get("run_search") == "1" and requirement_id:
        target_req = Requirement.objects.filter(id=requirement_id, company=company, is_deleted=False).first()
        if target_req:
            if subscription and subscription.credits_remaining <= 0:
                messages.error(request, f"Search blocked: AI credits exhausted ({subscription.credits_used}/{subscription.credits_total}). Please contact Super Admin to top up credits.")
                return redirect(f"{reverse('find-suppliers')}?requirement_id={target_req.id}")
            job = run_find_suppliers_search(target_req, request.user, company)
            if job.status == SearchJob.Status.FAILED:
                messages.error(request, f"Search failed: {job.error_message}")
            else:
                messages.success(request, f"Search completed: Identified {job.total_results} suppliers for '{target_req.item_name}'. (1 AI credit debited)")
            return redirect(f"{reverse('find-suppliers')}?requirement_id={target_req.id}")

    results_qs = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.SUPPLIER,
    ).select_related("external_company", "search_job", "search_job__requirement").order_by("-match_score", "-created_at")

    selected_requirement = None
    if requirement_id and requirement_id.isdigit():
        selected_requirement = Requirement.objects.filter(id=requirement_id, company=company, is_deleted=False).first()
        if selected_requirement:
            results_qs = results_qs.filter(search_job__requirement=selected_requirement)
            results = list(results_qs[:50])
        else:
            results = []
    else:
        results = []

    saved_result_ids = set(SavedItem.objects.filter(company=company).values_list("search_result_id", flat=True)) if company else set()

    current_job = None
    if selected_requirement:
        latest_job_qs = SearchJob.objects.filter(company=company, job_type=SearchJob.JobType.FIND_SUPPLIERS, requirement=selected_requirement)
        current_job = latest_job_qs.order_by("-created_at").first()

    # Dynamic Matching Criteria list
    ensure_default_parameters_for_company(company)
    all_params = {p.parameter_key: p for p in MatchingParameter.objects.filter(company=company)} if company else {}

    static_criteria_list = []
    for meta in STATIC_CRITERIA_METADATA:
        p = all_params.get(meta["key"])
        val = p.criteria_value if (p and p.criteria_value) else meta["default_value"]
        is_checked = p.is_active if p else True
        static_criteria_list.append({
            **meta,
            "current_value": val,
            "is_checked": is_checked,
        })

    custom_criteria_list = []
    if company:
        custom_params = MatchingParameter.objects.filter(company=company).exclude(parameter_key__in=STATIC_CRITERIA_KEYS)
        for cp in custom_params:
            custom_criteria_list.append({
                "id": cp.id,
                "key": cp.name,
                "val": cp.criteria_value,
            })

    active_criteria_count = sum(1 for item in static_criteria_list if item["is_checked"]) + len(custom_criteria_list)

    return render(request, "requirements/find_suppliers.html", {
        "results": results,
        "saved_result_ids": saved_result_ids,
        "requirements": requirements,
        "selected_requirement": selected_requirement,
        "selected_requirement_id": int(requirement_id) if requirement_id and requirement_id.isdigit() else None,
        "current_job": current_job,
        "static_criteria_list": static_criteria_list,
        "custom_criteria_list": custom_criteria_list,
        "active_criteria_count": active_criteria_count,
        "company": company,
        "rbac": rbac,
        "page_title": f"Find Customers — {selected_requirement.item_name}" if selected_requirement else "Find Customers (AI Matching)",
    })


@login_required
def export_discovered_suppliers_csv_view(request):
    """
    1-Click CSV Export for Discovered Suppliers:
    Exports all supplier candidates discovered via AI matching with contact info,
    pricing, MOQ, location, and match scores.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not rbac["can_view_buyer"]:
        messages.error(request, "Access restricted: Buyer module permission required.")
        return redirect("dashboard")

    can_export = (
        rbac["is_super_admin"]
        or rbac["is_company_admin"]
        or ("EXPORT" in rbac["permissions"])
        or ("requirements:EXPORT" in rbac["permissions"])
        or ("general:EXPORT" in rbac["permissions"])
    )
    if not can_export:
        messages.error(request, "Permission denied: You do not have 'EXPORT' permission.")
        return redirect("find-suppliers")

    requirement_id = request.GET.get("requirement_id")
    qs = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.SUPPLIER,
    ).select_related("external_company", "search_job", "search_job__requirement").order_by("-match_score", "-created_at")

    selected_req = None
    if requirement_id and requirement_id.isdigit():
        qs = qs.filter(search_job__requirement_id=int(requirement_id))
        selected_req = Requirement.objects.filter(id=int(requirement_id), company=company).first()

    req_slug = selected_req.item_name.replace(" ", "_") if selected_req else "All_Requirements"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="Discovered_Suppliers_{req_slug}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Supplier Company Name",
        "Role",
        "Industry",
        "City",
        "State",
        "Country",
        "Website",
        "Email",
        "Phone",
        "Sourced Requirement Item",
        "Estimated Unit Price",
        "Currency",
        "Unit",
        "MOQ",
        "Match Score (%)",
        "Fit Assessment Reason",
        "Procurement Signal",
        "Source Web URL",
        "Discovery Date",
    ])

    for r in qs:
        ext = r.external_company
        req_obj = r.search_job.requirement if r.search_job else None
        writer.writerow([
            ext.name if ext else "Unknown",
            ext.get_company_role_display() if ext else "Supplier",
            ext.industry or "" if ext else "",
            ext.city or "" if ext else "",
            ext.state or "" if ext else "",
            ext.country or "India" if ext else "India",
            ext.website or "" if ext else "",
            ext.email or "" if ext else "",
            ext.phone or "" if ext else "",
            req_obj.item_name if req_obj else r.product_title,
            r.price if r.price is not None else "",
            r.price_currency or "INR",
            r.price_unit or (req_obj.unit if req_obj else "pcs"),
            r.moq or "",
            r.match_score,
            r.match_reason or "",
            r.need_signal or "",
            r.source_url or "",
            r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        ])

    return response

