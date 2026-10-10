import csv
import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from django.db.models import Count, Q, Avg
from django.db.models.functions import Lower
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone
from apps.leads.models import SavedItem, Inquiry, PriceHistory, InquiryMessage
from apps.leads.services.email_service import send_inquiry_email
from apps.ai_search.models import SearchResult, ExternalCompany
from apps.catalog.models import Product
from apps.requirements.models import Requirement
from apps.companies.rbac import get_user_rbac_context
from apps.dashboard.services.notification_service import (
    notify_proposal_sent,
    notify_quote_recorded,
    notify_status_changed,
    notify_message_logged,
)


# ============================================================
# SELLER SIDE: LEAD MANAGEMENT & SALES PIPELINE (CRM)
# ============================================================

@login_required
def save_lead_toggle_view(request, result_id):
    """
    Save / Shortlist Lead Action:
    Allows sellers to bookmark a candidate buyer company into their pipeline.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    if rbac["is_super_admin"] and not company:
        result = get_object_or_404(SearchResult, id=result_id)
    else:
        result = get_object_or_404(SearchResult, id=result_id, search_job__company=company)

    # Prevent duplicate bookmarking of the same external vendor for this product
    target_product = result.search_job.product if result.search_job else None
    existing_saved = SavedItem.objects.filter(
        company=company,
        search_result__external_company=result.external_company,
        search_result__search_job__product=target_product,
    ).first()

    if existing_saved:
        saved_item = existing_saved
        created = False
    else:
        saved_item, created = SavedItem.objects.get_or_create(
            company=company,
            search_result=result,
            defaults={"status": SavedItem.Status.NEW},
        )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "created": created,
            "message": f"Lead '{result.external_company.name}' saved to pipeline!" if created else "Lead is already saved in your pipeline.",
        })

    if created:
        messages.success(request, f"Lead '{result.external_company.name}' saved to your sales pipeline!")
    else:
        messages.info(request, f"Lead '{result.external_company.name}' is already in your saved leads.")

    redirect_to = request.META.get("HTTP_REFERER") or reverse("find-buyers")
    return redirect(redirect_to)


def deduplicate_saved_items(items):
    """
    Deduplicates SavedItem records by search_result__external_company_id,
    retaining the record with the most advanced qualification stage and newest timestamp.
    """
    stage_priority = {
        SavedItem.Status.WON: 6,
        SavedItem.Status.NEGOTIATING: 5,
        SavedItem.Status.INTERESTED: 4,
        SavedItem.Status.CONTACTED: 3,
        SavedItem.Status.NEW: 2,
        SavedItem.Status.LOST: 1,
    }
    seen = {}
    for item in items:
        cid = item.search_result.external_company_id if item.search_result else None
        if not cid:
            seen[f"item_{item.id}"] = item
            continue
        if cid not in seen:
            seen[cid] = item
        else:
            prev = seen[cid]
            if stage_priority.get(item.status, 0) > stage_priority.get(prev.status, 0):
                seen[cid] = item
            elif stage_priority.get(item.status, 0) == stage_priority.get(prev.status, 0):
                if item.created_at and prev.created_at and item.created_at > prev.created_at:
                    seen[cid] = item
    return list(seen.values())


@login_required
def saved_leads_view(request):
    """
    Saved Leads (Sales CRM Pipeline - Product-Wise):
    Displays bookmarked buyer companies organized product-by-product.
    Supports:
    1. Product filter (?product_id=<id>) to track pipeline for a specific product.
    2. Grouped product view when no product_id is specified.
    3. Searchable combobox & quick tabs sorted A to Z.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Saved Leads is only available for Seller accounts.")
        return redirect("dashboard")

    # Fetch company products sorted strictly A to Z
    products = list(Product.objects.filter(company=company, is_deleted=False).order_by(Lower("name"))) if company else []

    base_qs = SavedItem.objects.filter(
        company=company,
        search_result__result_type=SearchResult.ResultType.LEAD,
    ).filter(
        Q(search_result__search_job__product__is_deleted=False) | Q(search_result__search_job__product__isnull=True)
    ).select_related(
        "search_result",
        "search_result__external_company",
        "search_result__search_job",
        "search_result__search_job__product",
        "search_result__search_job__product__category",
        "assigned_to",
    ).order_by("-created_at")

    # Optional Status and Assignee Filters
    status_filter = request.GET.get("status", "").strip().lower()
    assigned_filter = request.GET.get("assigned_to", "").strip()

    if status_filter and status_filter in dict(SavedItem.Status.choices):
        base_qs = base_qs.filter(status=status_filter)

    if assigned_filter:
        if assigned_filter == "unassigned":
            base_qs = base_qs.filter(assigned_to__isnull=True)
        elif assigned_filter.isdigit():
            base_qs = base_qs.filter(assigned_to_id=int(assigned_filter))

    # Calculate saved lead counts per product (deduplicated by external company)
    product_saved_counts = (
        base_qs
        .order_by()
        .filter(search_result__search_job__product__isnull=False)
        .values("search_result__search_job__product_id")
        .annotate(total=Count("search_result__external_company_id", distinct=True))
    )
    counts_map = {item["search_result__search_job__product_id"]: item["total"] for item in product_saved_counts}
    for p in products:
        p.saved_count = counts_map.get(p.id, 0)

    unassigned_count = (
        base_qs
        .order_by()
        .filter(search_result__search_job__product__isnull=True)
        .values("search_result__external_company_id")
        .distinct()
        .count()
    )
    total_saved_count = sum(p.saved_count for p in products) + unassigned_count

    product_id_str = request.GET.get("product_id", "").strip()
    selected_product = None
    is_unassigned_view = False
    is_single_product = False
    saved_items = []
    product_groups = []
    unassigned_group = None

    if product_id_str and product_id_str.isdigit():
        selected_product = Product.objects.filter(id=int(product_id_str), company=company, is_deleted=False).first()
        if selected_product:
            is_single_product = True
            raw_items = list(base_qs.filter(search_result__search_job__product=selected_product))
            saved_items = deduplicate_saved_items(raw_items)
    elif product_id_str == "unassigned":
        is_unassigned_view = True
        is_single_product = True
        raw_items = list(base_qs.filter(search_result__search_job__product__isnull=True))
        saved_items = deduplicate_saved_items(raw_items)
    else:
        # Group by product so it is NOT "all-in-one"
        for p in products:
            if p.saved_count > 0:
                raw_p_items = list(base_qs.filter(search_result__search_job__product=p))
                p_items = deduplicate_saved_items(raw_p_items)
                product_groups.append({
                    "product": p,
                    "items": p_items,
                    "count": len(p_items),
                    "new_count": sum(1 for i in p_items if i.status == SavedItem.Status.NEW),
                    "contacted_count": sum(1 for i in p_items if i.status == SavedItem.Status.CONTACTED),
                    "negotiating_count": sum(1 for i in p_items if i.status == SavedItem.Status.NEGOTIATING),
                    "interested_count": sum(1 for i in p_items if i.status == SavedItem.Status.INTERESTED),
                    "won_count": sum(1 for i in p_items if i.status == SavedItem.Status.WON),
                    "lost_count": sum(1 for i in p_items if i.status == SavedItem.Status.LOST),
                })
        if unassigned_count > 0:
            raw_unassigned = list(base_qs.filter(search_result__search_job__product__isnull=True))
            unassigned_items = deduplicate_saved_items(raw_unassigned)
            unassigned_group = {
                "title": "Other Prequalified Vendors",
                "items": unassigned_items,
                "count": len(unassigned_items),
            }

    active_items = saved_items if is_single_product else deduplicate_saved_items(list(base_qs))
    stage_counts = {
        "new": sum(1 for i in active_items if i.status == SavedItem.Status.NEW),
        "contacted": sum(1 for i in active_items if i.status == SavedItem.Status.CONTACTED),
        "negotiating": sum(1 for i in active_items if i.status == SavedItem.Status.NEGOTIATING),
        "interested": sum(1 for i in active_items if i.status == SavedItem.Status.INTERESTED),
        "won": sum(1 for i in active_items if i.status == SavedItem.Status.WON),
        "lost": sum(1 for i in active_items if i.status == SavedItem.Status.LOST),
    }

    # Executive Pipeline KPIs
    total_pipeline_count = len(active_items)
    won_count = stage_counts["won"]
    win_rate_pct = round((won_count / total_pipeline_count * 100)) if total_pipeline_count > 0 else 0
    active_velocity_count = stage_counts["contacted"] + stage_counts["negotiating"] + stage_counts["interested"]
    assigned_count = sum(1 for i in active_items if i.assigned_to_id is not None)
    assigned_coverage_pct = round((assigned_count / total_pipeline_count * 100)) if total_pipeline_count > 0 else 0

    team_members = company.members.filter(is_active=True).select_related("user") if company else []

    return render(request, "leads/saved_leads.html", {
        "products": products,
        "selected_product": selected_product,
        "selected_product_id": int(product_id_str) if product_id_str and product_id_str.isdigit() else None,
        "is_unassigned_view": is_unassigned_view,
        "is_single_product": is_single_product,
        "product_groups": product_groups,
        "unassigned_group": unassigned_group,
        "unassigned_count": unassigned_count,
        "total_saved_count": total_saved_count,
        "saved_items": saved_items,
        "stage_counts": stage_counts,
        "total_pipeline_count": total_pipeline_count,
        "win_rate_pct": win_rate_pct,
        "active_velocity_count": active_velocity_count,
        "assigned_coverage_pct": assigned_coverage_pct,
        "company": company,
        "rbac": rbac,
        "team_members": team_members,
        "status_choices": SavedItem.Status.choices,
        "status_filter": status_filter,
        "assigned_filter": assigned_filter,
        "page_title": f"Prequalified Vendors - {selected_product.name}" if selected_product else "Prequalified Vendors (Product-Wise)",
    })


@login_required
def update_saved_lead_view(request, item_id):
    """
    Updates status pipeline, private notes, or assigned team member for a saved lead.
    Supports both instant AJAX calls and safe standard HTTP POST with referer preservation.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    item = get_object_or_404(SavedItem, id=item_id, company=company)

    if request.method == "POST":
        status_val = request.POST.get("status")
        notes = request.POST.get("notes")
        assigned_to_id = request.POST.get("assigned_to_id")

        if status_val in [s[0] for s in SavedItem.Status.choices]:
            item.status = status_val

        if notes is not None:
            item.notes = notes.strip()

        if assigned_to_id is not None:
            if assigned_to_id == "":
                item.assigned_to = None
            elif assigned_to_id.isdigit():
                item.assigned_to_id = int(assigned_to_id)

        item.save()

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "status": item.status,
                "status_display": item.get_status_display(),
                "assigned_to_id": item.assigned_to_id,
                "assigned_to_name": item.assigned_to.get_full_name() or item.assigned_to.email if item.assigned_to else "Unassigned Rep",
                "notes": item.notes,
                "message": f"Lead '{item.search_result.external_company.name}' updated successfully.",
            })

        messages.success(request, f"Lead '{item.search_result.external_company.name}' updated successfully.")

    redirect_to = request.META.get("HTTP_REFERER") or reverse("saved-leads")
    return redirect(redirect_to)


@login_required
def delete_saved_lead_view(request, item_id):
    """
    Removes lead from the saved pipeline.
    Supports instant AJAX removals and safe HTTP referer redirects.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    item = get_object_or_404(SavedItem, id=item_id, company=company)
    lead_name = item.search_result.external_company.name
    item.delete()

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "message": f"Lead '{lead_name}' removed from your pipeline.",
        })

    messages.success(request, f"Lead '{lead_name}' removed from your pipeline.")
    redirect_to = request.META.get("HTTP_REFERER") or reverse("saved-leads")
    return redirect(redirect_to)


@login_required
def export_saved_leads_csv_view(request):
    """
    1-Click CSV Export for Prequalified Vendors Pipeline:
    Exports all bookmarked leads (product-scoped or company-wide) with qualification status,
    assigned sales rep, deal notes, and verified contact signals.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Sales module permission required.")
        return redirect("dashboard")

    product_id_str = request.GET.get("product_id", "").strip()
    selected_product = None
    qs = SavedItem.objects.filter(
        company=company,
        search_result__result_type=SearchResult.ResultType.LEAD,
    ).filter(
        Q(search_result__search_job__product__is_deleted=False) | Q(search_result__search_job__product__isnull=True)
    ).select_related(
        "search_result",
        "search_result__external_company",
        "search_result__search_job",
        "search_result__search_job__product",
        "search_result__search_job__product__category",
        "assigned_to",
    ).order_by("-created_at")

    if product_id_str and product_id_str.isdigit():
        selected_product = Product.objects.filter(id=int(product_id_str), company=company, is_deleted=False).first()
        if selected_product:
            qs = qs.filter(search_result__search_job__product=selected_product)
    elif product_id_str == "unassigned":
        qs = qs.filter(search_result__search_job__product__isnull=True)

    status_filter = request.GET.get("status", "").strip().lower()
    if status_filter and status_filter in dict(SavedItem.Status.choices):
        qs = qs.filter(status=status_filter)

    assigned_filter = request.GET.get("assigned_to", "").strip()
    if assigned_filter == "unassigned":
        qs = qs.filter(assigned_to__isnull=True)
    elif assigned_filter.isdigit():
        qs = qs.filter(assigned_to_id=int(assigned_filter))

    raw_items = list(qs)
    items = deduplicate_saved_items(raw_items)

    prod_slug = selected_product.name.replace(" ", "_") if selected_product else "All_Products"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="Prequalified_Vendors_{prod_slug}.csv"'
    writer = csv.writer(response)

    writer.writerow([
        "Company Name",
        "Industry",
        "City",
        "Country",
        "Website",
        "Email",
        "Phone",
        "Target Product",
        "Category",
        "Fit Score (%)",
        "Pipeline Stage",
        "Assigned Rep",
        "Deal Notes",
        "Date Saved",
    ])

    for item in items:
        ext = item.search_result.external_company if item.search_result else None
        prod_obj = item.search_result.search_job.product if (item.search_result and item.search_result.search_job) else None
        prod_name = prod_obj.name if prod_obj else (item.search_result.product_title if item.search_result else "General")
        cat_name = prod_obj.category.name if (prod_obj and prod_obj.category) else "General"
        rep_name = item.assigned_to.get_full_name() or item.assigned_to.email if item.assigned_to else "Unassigned Rep"
        writer.writerow([
            ext.name if ext else "Unknown",
            ext.industry or "" if ext else "",
            ext.city or "" if ext else "",
            ext.country or "India" if ext else "India",
            ext.website or "" if ext else "",
            ext.email or "" if ext else "",
            ext.phone or "" if ext else "",
            prod_name,
            cat_name,
            item.search_result.match_score if item.search_result else "",
            item.get_status_display(),
            rep_name,
            item.notes or "",
            item.created_at.strftime("%Y-%m-%d %H:%M") if item.created_at else "",
        ])

    return response


@login_required
def leads_list_view(request):
    """
    Sales Leads Discovery List (Product-Wise):
    Organizes and filters discovered buyer leads by product.
    Supports:
    1. Product filter (?product_id=<id>) to view only leads for that product.
    2. Grouped product view when no product_id is specified (product-by-product sections, not all-in-one).
    3. Searchable Combobox & quick-filter tabs for fast product switching.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Vendor list is only available for Seller accounts.")
        return redirect("dashboard")

    # Fetch company products sorted strictly alphabetically A to Z
    products = list(Product.objects.filter(company=company, is_deleted=False).order_by(Lower("name")))

    # Base query of all qualified buyer leads for this company
    base_leads_qs = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.LEAD,
    ).filter(
        Q(search_job__product__is_deleted=False) | Q(search_job__product__isnull=True)
    ).select_related(
        "external_company",
        "search_job",
        "search_job__product",
        "search_job__product__category",
    ).order_by("-match_score", "-created_at")

    # Lead counts by product (deduplicated by external company)
    lead_counts = (
        base_leads_qs
        .order_by()
        .filter(search_job__product__isnull=False)
        .values("search_job__product_id")
        .annotate(total=Count("external_company_id", distinct=True))
    )
    counts_map = {item["search_job__product_id"]: item["total"] for item in lead_counts}
    for p in products:
        p.lead_count = counts_map.get(p.id, 0)

    unassigned_count = (
        base_leads_qs
        .order_by()
        .filter(search_job__product__isnull=True)
        .values("external_company_id")
        .distinct()
        .count()
    )
    total_leads_count = sum(p.lead_count for p in products) + unassigned_count

    product_id_str = request.GET.get("product_id", "").strip()
    selected_product = None
    is_unassigned_view = False
    is_single_product = False
    leads = []
    product_groups = []
    unassigned_group = None

    if product_id_str and product_id_str.isdigit():
        selected_product = Product.objects.filter(id=int(product_id_str), company=company, is_deleted=False).first()
        if selected_product:
            is_single_product = True
            raw_leads = list(base_leads_qs.filter(search_job__product=selected_product))
            seen_cids = set()
            for l in raw_leads:
                cid = l.external_company_id
                if cid and cid in seen_cids:
                    continue
                if cid:
                    seen_cids.add(cid)
                leads.append(l)
    elif product_id_str == "unassigned":
        is_unassigned_view = True
        is_single_product = True
        raw_leads = list(base_leads_qs.filter(search_job__product__isnull=True))
        seen_cids = set()
        for l in raw_leads:
            cid = l.external_company_id
            if cid and cid in seen_cids:
                continue
            if cid:
                seen_cids.add(cid)
            leads.append(l)
    else:
        # Group by product so it is NOT "all-in-one"
        for p in products:
            if p.lead_count > 0:
                raw_leads = list(base_leads_qs.filter(search_job__product=p))
                seen_cids = set()
                p_leads = []
                for l in raw_leads:
                    cid = l.external_company_id
                    if cid and cid in seen_cids:
                        continue
                    if cid:
                        seen_cids.add(cid)
                    p_leads.append(l)

                product_groups.append({
                    "product": p,
                    "leads": p_leads,
                    "count": len(p_leads),
                })

        if unassigned_count > 0:
            raw_leads = list(base_leads_qs.filter(search_job__product__isnull=True))
            seen_cids = set()
            unassigned_leads = []
            for l in raw_leads:
                cid = l.external_company_id
                if cid and cid in seen_cids:
                    continue
                if cid:
                    seen_cids.add(cid)
                unassigned_leads.append(l)

            unassigned_group = {
                "title": "Other / Custom Search Leads",
                "leads": unassigned_leads,
                "count": len(unassigned_leads),
            }

    saved_result_ids = set(SavedItem.objects.filter(company=company).values_list("search_result_id", flat=True)) if company else set()

    # Executive KPI metrics calculation for current product view
    total_vendors_count = len(leads)
    high_fit_count = sum(1 for l in leads if l.match_score >= 85)
    verified_contact_count = sum(1 for l in leads if (l.external_company and (l.external_company.email or l.external_company.phone)))
    contact_coverage_pct = round((verified_contact_count / total_vendors_count * 100)) if total_vendors_count > 0 else 0
    saved_in_pipeline_count = sum(1 for l in leads if l.id in saved_result_ids)

    # Products with active leads for landing view summary cards
    products_with_leads = [p for p in products if getattr(p, "lead_count", 0) > 0]

    return render(request, "leads/leads_list.html", {
        "products": products,
        "selected_product": selected_product,
        "selected_product_id": int(product_id_str) if product_id_str and product_id_str.isdigit() else None,
        "is_unassigned_view": is_unassigned_view,
        "is_single_product": is_single_product,
        "product_groups": product_groups,
        "unassigned_group": unassigned_group,
        "unassigned_count": unassigned_count,
        "total_leads_count": total_leads_count,
        "leads": leads,
        "saved_result_ids": saved_result_ids,
        "total_vendors_count": total_vendors_count,
        "high_fit_count": high_fit_count,
        "verified_contact_count": verified_contact_count,
        "contact_coverage_pct": contact_coverage_pct,
        "saved_in_pipeline_count": saved_in_pipeline_count,
        "products_with_leads": products_with_leads,
        "company": company,
        "rbac": rbac,
        "page_title": f"Vendor List - {selected_product.name}" if selected_product else "Vendor List (Product-Wise)",
    })


# ============================================================
# BUYER SIDE: SUPPLIER SHORTLIST & RFQ INQUIRY WORKFLOW
# ============================================================

@login_required
def save_supplier_toggle_view(request, result_id):
    """
    Shortlist Supplier Action:
    Allows buyers to bookmark a qualified supplier for procurement.
    Deduplicates bookmarked suppliers per requirement.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Procurement module permission required.")
        return redirect("dashboard")

    if rbac["is_super_admin"] and not company:
        result = get_object_or_404(SearchResult, id=result_id)
    else:
        result = get_object_or_404(SearchResult, id=result_id, search_job__company=company)

    # Prevent duplicate bookmarking of the same supplier for this requirement
    target_req = result.search_job.requirement if result.search_job else None
    existing_saved = SavedItem.objects.filter(
        company=company,
        search_result__external_company=result.external_company,
        search_result__search_job__requirement=target_req,
    ).first()

    if existing_saved:
        saved_item = existing_saved
        created = False
    else:
        saved_item, created = SavedItem.objects.get_or_create(
            company=company,
            search_result=result,
            defaults={"status": SavedItem.Status.INTERESTED},
        )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "created": created,
            "message": f"Supplier '{result.external_company.name}' shortlisted!" if created else "Supplier is already shortlisted for this requirement.",
        })

    if created:
        messages.success(request, f"Supplier '{result.external_company.name}' added to your shortlisted suppliers!")
    else:
        messages.info(request, f"Supplier '{result.external_company.name}' is already in your shortlist.")

    redirect_to = request.META.get("HTTP_REFERER") or reverse("find-suppliers")
    return redirect(redirect_to)


@login_required
def saved_suppliers_view(request):
    """
    Saved / Shortlisted Suppliers Pipeline (Requirement-Wise):
    Displays bookmarked suppliers organized requirement-by-requirement.
    Supports:
    - Requirement-specific filtering or consolidated overview
    - Pipeline stage tracking (Interested, Contacted, Negotiating, Won, Lost)
    - Team member assignment and deal notes
    - Metric summary cards
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Saved Suppliers is only available for Buyer accounts.")
        return redirect("dashboard")

    requirements = list(Requirement.objects.filter(company=company, is_deleted=False).order_by(Lower("item_name"))) if company else []

    base_qs = SavedItem.objects.filter(
        company=company,
        search_result__result_type=SearchResult.ResultType.SUPPLIER,
    ).select_related(
        "search_result",
        "search_result__external_company",
        "search_result__search_job",
        "search_result__search_job__requirement",
        "search_result__search_job__requirement__category",
        "assigned_to",
    ).order_by("-created_at")

    # Status filter
    status_filter = request.GET.get("status", "").strip().lower()
    if status_filter and status_filter in dict(SavedItem.Status.choices):
        base_qs = base_qs.filter(status=status_filter)

    # Search filter
    q = request.GET.get("q", "").strip()
    if q:
        base_qs = base_qs.filter(
            Q(search_result__external_company__name__icontains=q)
            | Q(search_result__external_company__city__icontains=q)
            | Q(search_result__external_company__email__icontains=q)
            | Q(search_result__product_title__icontains=q)
            | Q(notes__icontains=q)
        )

    # Assigned to filter
    assigned_filter = request.GET.get("assigned_to", "").strip()
    if assigned_filter == "unassigned":
        base_qs = base_qs.filter(assigned_to__isnull=True)
    elif assigned_filter.isdigit():
        base_qs = base_qs.filter(assigned_to_id=int(assigned_filter))

    # Count saved items per requirement
    req_counts = (
        base_qs
        .order_by()
        .filter(search_result__search_job__requirement__isnull=False)
        .values("search_result__search_job__requirement_id")
        .annotate(total=Count("id"))
    )
    counts_map = {item["search_result__search_job__requirement_id"]: item["total"] for item in req_counts}
    for r in requirements:
        r.saved_count = counts_map.get(r.id, 0)

    unassigned_count = (
        base_qs
        .order_by()
        .filter(search_result__search_job__requirement__isnull=True)
        .count()
    )
    total_saved_count = sum(r.saved_count for r in requirements) + unassigned_count

    req_id_str = request.GET.get("requirement_id", "").strip()
    selected_requirement = None
    is_unassigned_view = False
    is_single_req = False
    items = []
    requirement_groups = []
    unassigned_group = None

    if req_id_str and req_id_str.isdigit():
        selected_requirement = Requirement.objects.filter(id=int(req_id_str), company=company, is_deleted=False).first()
        if selected_requirement:
            is_single_req = True
            raw_items = list(base_qs.filter(search_result__search_job__requirement=selected_requirement))
            items = deduplicate_saved_items(raw_items)
    elif req_id_str == "unassigned":
        is_unassigned_view = True
        is_single_req = True
        raw_items = list(base_qs.filter(search_result__search_job__requirement__isnull=True))
        items = deduplicate_saved_items(raw_items)

    active_items = items if is_single_req else []
    total_suppliers_count = len(active_items)
    high_fit_count = sum(1 for it in active_items if (it.search_result and it.search_result.match_score >= 85))
    verified_contact_count = sum(1 for it in active_items if (it.search_result and it.search_result.external_company and (it.search_result.external_company.email or it.search_result.external_company.phone)))
    negotiating_count = sum(1 for it in active_items if it.status in [SavedItem.Status.NEGOTIATING, SavedItem.Status.WON])

    # Company team members for assignment dropdown
    team_members = []
    if company:
        team_members = [
            m.user for m in company.members.filter(is_active=True).select_related("user") if m.user
        ]

    return render(request, "leads/saved_suppliers.html", {
        "requirements": requirements,
        "selected_requirement": selected_requirement,
        "selected_requirement_id": int(req_id_str) if req_id_str and req_id_str.isdigit() else None,
        "is_unassigned_view": is_unassigned_view,
        "is_single_req": is_single_req,
        "requirement_groups": requirement_groups,
        "unassigned_group": unassigned_group,
        "unassigned_count": unassigned_count,
        "total_saved_count": total_saved_count,
        "saved_suppliers": active_items,
        "items": items,
        "total_suppliers_count": total_suppliers_count,
        "high_fit_count": high_fit_count,
        "verified_contact_count": verified_contact_count,
        "negotiating_count": negotiating_count,
        "status_choices": SavedItem.Status.choices,
        "current_status": status_filter,
        "team_members": team_members,
        "search_query": q,
        "company": company,
        "page_title": f"Saved Customers — {selected_requirement.item_name}" if selected_requirement else "Saved Customers (Requirement-Wise)",
    })


@login_required
def update_saved_supplier_view(request, item_id):
    """
    Updates status, notes, or assigned team member for shortlisted supplier.
    Supports AJAX and standard POST.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["is_super_admin"]):
        return JsonResponse({"success": False, "error": "Access restricted"}, status=403)

    item = get_object_or_404(SavedItem, id=item_id, company=company)

    if request.method == "POST":
        status_val = request.POST.get("status", "").strip().lower()
        notes = request.POST.get("notes")
        assigned_to_id = request.POST.get("assigned_to")

        if status_val and status_val in dict(SavedItem.Status.choices):
            item.status = status_val

        if notes is not None:
            item.notes = notes.strip()

        if assigned_to_id is not None:
            if assigned_to_id == "":
                item.assigned_to = None
            elif assigned_to_id.isdigit():
                item.assigned_to_id = int(assigned_to_id)

        item.save()

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "status": item.status,
                "status_display": item.get_status_display(),
                "assigned_to_id": item.assigned_to_id,
                "assigned_to_name": item.assigned_to.get_full_name() or item.assigned_to.email if item.assigned_to else "Unassigned",
                "notes": item.notes,
                "message": f"Supplier '{item.search_result.external_company.name}' updated successfully.",
            })

        messages.success(request, f"Supplier '{item.search_result.external_company.name}' updated successfully.")

    redirect_to = request.META.get("HTTP_REFERER") or reverse("saved-suppliers")
    return redirect(redirect_to)


@login_required
def export_saved_suppliers_csv_view(request):
    """
    1-Click CSV Export for Shortlisted Suppliers Pipeline:
    Exports all bookmarked suppliers with qualification status,
    assigned team member, procurement notes, contact info, and pricing.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Procurement module permission required.")
        return redirect("dashboard")

    requirement_id_str = request.GET.get("requirement_id", "").strip()
    selected_requirement = None
    qs = SavedItem.objects.filter(
        company=company,
        search_result__result_type=SearchResult.ResultType.SUPPLIER,
    ).filter(
        Q(search_result__search_job__requirement__is_deleted=False) | Q(search_result__search_job__requirement__isnull=True)
    ).select_related(
        "search_result",
        "search_result__external_company",
        "search_result__search_job",
        "search_result__search_job__requirement",
        "search_result__search_job__requirement__category",
        "assigned_to",
    ).order_by("-created_at")

    if requirement_id_str and requirement_id_str.isdigit():
        selected_requirement = Requirement.objects.filter(id=int(requirement_id_str), company=company, is_deleted=False).first()
        if selected_requirement:
            qs = qs.filter(search_result__search_job__requirement=selected_requirement)
    elif requirement_id_str == "unassigned":
        qs = qs.filter(search_result__search_job__requirement__isnull=True)

    status_filter = request.GET.get("status", "").strip().lower()
    if status_filter and status_filter in dict(SavedItem.Status.choices):
        qs = qs.filter(status=status_filter)

    assigned_filter = request.GET.get("assigned_to", "").strip()
    if assigned_filter == "unassigned":
        qs = qs.filter(assigned_to__isnull=True)
    elif assigned_filter.isdigit():
        qs = qs.filter(assigned_to_id=int(assigned_filter))

    raw_items = list(qs)
    items = deduplicate_saved_items(raw_items)

    req_slug = selected_requirement.item_name.replace(" ", "_") if selected_requirement else "All_Requirements"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="Shortlisted_Suppliers_{req_slug}.csv"'
    writer = csv.writer(response)

    writer.writerow([
        "Supplier Company Name",
        "Role",
        "Industry",
        "City",
        "Country",
        "Website",
        "Email",
        "Phone",
        "Requirement Item",
        "Category",
        "Estimated Price",
        "Currency",
        "MOQ",
        "Match Score (%)",
        "Pipeline Stage",
        "Assigned Member",
        "Procurement Notes",
        "Date Saved",
    ])

    for item in items:
        ext = item.search_result.external_company if item.search_result else None
        req_obj = item.search_result.search_job.requirement if (item.search_result and item.search_result.search_job) else None
        req_name = req_obj.item_name if req_obj else (item.search_result.product_title if item.search_result else "General")
        cat_name = req_obj.category.name if (req_obj and req_obj.category) else "General"
        assigned_name = item.assigned_to.get_full_name() or item.assigned_to.email if item.assigned_to else "Unassigned"
        writer.writerow([
            ext.name if ext else "Unknown",
            ext.get_company_role_display() if ext else "Supplier",
            ext.industry or "" if ext else "",
            ext.city or "" if ext else "",
            ext.country or "India" if ext else "India",
            ext.website or "" if ext else "",
            ext.email or "" if ext else "",
            ext.phone or "" if ext else "",
            req_name,
            cat_name,
            item.search_result.price if (item.search_result and item.search_result.price is not None) else "",
            item.search_result.price_currency if item.search_result else "INR",
            item.search_result.moq if item.search_result else "",
            item.search_result.match_score if item.search_result else "",
            item.get_status_display(),
            assigned_name,
            item.notes or "",
            item.created_at.strftime("%Y-%m-%d %H:%M") if item.created_at else "",
        ])

    return response


@login_required
def delete_saved_supplier_view(request, item_id):
    """
    Removes supplier from shortlisted suppliers.
    Supports instant AJAX removals and safe HTTP referer redirects.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Procurement module permission required.")
        return redirect("dashboard")

    item = get_object_or_404(SavedItem, id=item_id, company=company)
    name = item.search_result.external_company.name
    item.delete()

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "message": f"Supplier '{name}' removed from your shortlist.",
        })

    messages.success(request, f"Supplier '{name}' removed from shortlist.")
    redirect_to = request.META.get("HTTP_REFERER") or reverse("saved-suppliers")
    return redirect(redirect_to)


@login_required
def send_rfq_view(request, result_id):
    """
    Send RFQ / Inquiry View:
    Processes dispatch of official Commercial Inquiry or RFQ to supplier/buyer.
    Dispatches email, records inquiry and initial message in DB,
    and redirects to the appropriate inquiries tracking list.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Active company module permission required.")
        return redirect("dashboard")

    if rbac["is_super_admin"] and not company:
        result = get_object_or_404(SearchResult, id=result_id)
    else:
        result = get_object_or_404(SearchResult, id=result_id, search_job__company=company)

    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])

    if request.method == "POST":
        default_subj = f"Commercial Proposal: {result.product_title}" if is_seller_context else f"Request for Quotation: {result.product_title}"
        subject = request.POST.get("subject", "").strip() or default_subj
        message = request.POST.get("message", "").strip()
        sent_to_email = request.POST.get("sent_to_email", "").strip() or result.external_company.email

        if not sent_to_email:
            sent_to_email = f"sales@{result.external_company.domain or 'company.com'}"

        target_price_str = request.POST.get("target_price", "").strip() or request.POST.get("quoted_price", "").strip()
        delivery_terms = request.POST.get("delivery_terms", "").strip()

        quoted_price = None
        if target_price_str:
            try:
                quoted_price = Decimal(target_price_str)
            except Exception:
                pass

        uploaded_attachment = request.FILES.get("attachment")

        inquiry = Inquiry.objects.create(
            company=company,
            search_result=result,
            subject=subject,
            message=message,
            sent_to_email=sent_to_email,
            status=Inquiry.Status.SENT,
            quoted_price=quoted_price,
            delivery_terms=delivery_terms,
            attachment=uploaded_attachment,
            attachment_name=uploaded_attachment.name if uploaded_attachment else "",
        )

        # Lifecycle sync: Update corresponding SavedItem to CONTACTED
        SavedItem.objects.filter(
            company=company,
            search_result__external_company=result.external_company,
            status=SavedItem.Status.NEW,
        ).update(status=SavedItem.Status.CONTACTED)

        # Log initial outbound message in conversation thread
        InquiryMessage.objects.create(
            inquiry=inquiry,
            sender=request.user,
            sender_name=request.user.get_full_name() or request.user.email,
            message_type=InquiryMessage.MessageType.OUTBOUND,
            subject=subject,
            body=message,
            attachment=inquiry.attachment,
            attachment_name=inquiry.attachment_name,
        )

        # Dispatch real email
        sent_ok, email_msg = send_inquiry_email(
            inquiry,
            user=request.user,
            attachment_file=uploaded_attachment,
        )
        if not sent_ok:
            inquiry.status = Inquiry.Status.FAILED
            inquiry.save(update_fields=["status"])
            messages.warning(
                request,
                f"Inquiry #{inquiry.id} recorded, but email dispatch failed ({email_msg}). You can follow up or retry."
            )
        else:
            messages.success(
                request,
                f"Inquiry #{inquiry.id} successfully recorded and dispatched to {result.external_company.name} ({sent_to_email})!"
            )

        # Dispatch real-time in-app notification
        notify_proposal_sent(inquiry, user=request.user)

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "inquiry_id": inquiry.id,
                "status": inquiry.status,
                "message": f"Inquiry dispatched to {sent_to_email}",
            })

        redirect_target = "sales-inquiries-list" if is_seller_context else "inquiries-list"
        return redirect(redirect_target)

    redirect_to = request.META.get("HTTP_REFERER") or (reverse("find-buyers") if is_seller_context else reverse("find-suppliers"))
    return redirect(redirect_to)


@login_required
def inquiries_list_view(request):
    """
    RFQs & Inquiries Tracking View (Product-Wise & Dynamic):
    Lists quotation requests, recipient contacts, status, and dates.
    Supports:
    1. Product-wise grouping and filtering (?product_id=<id>).
    2. Searchable product combobox (A to Z) & quick tabs.
    3. Live status filtering (all, sent, in_discussion, replied, won, lost, failed).
    4. Date range filter (7d, 30d, this_month, all).
    5. Text search across recipient, subject, item title, notes.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Active company module permission required.")
        return redirect("dashboard")

    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])

    base_qs = Inquiry.objects.filter(company=company).select_related(
        "search_result",
        "search_result__external_company",
        "search_result__search_job",
        "search_result__search_job__product",
        "search_result__search_job__product__category",
        "search_result__search_job__requirement",
        "search_result__search_job__requirement__category",
    ).annotate(messages_count=Count("messages"))

    # Filter by date range
    date_range = request.GET.get("date_range", "all").strip().lower()
    now = timezone.now()
    if date_range == "7d":
        base_qs = base_qs.filter(sent_at__gte=now - timedelta(days=7))
    elif date_range == "30d":
        base_qs = base_qs.filter(sent_at__gte=now - timedelta(days=30))
    elif date_range == "this_month":
        base_qs = base_qs.filter(sent_at__year=now.year, sent_at__month=now.month)

    # Filter by search term
    q = request.GET.get("q", "").strip()
    if q:
        base_qs = base_qs.filter(
            Q(subject__icontains=q)
            | Q(sent_to_email__icontains=q)
            | Q(search_result__product_title__icontains=q)
            | Q(search_result__external_company__name__icontains=q)
            | Q(message__icontains=q)
        )

    # Filter by status
    status_filter = request.GET.get("status", "").strip()
    if status_filter and status_filter in [s[0] for s in Inquiry.Status.choices]:
        base_qs = base_qs.filter(status=status_filter)

    item_id_str = request.GET.get("product_id", "").strip() or request.GET.get("requirement_id", "").strip()
    selected_item = None
    is_unassigned_view = False
    is_single_item = False
    inquiries = []
    item_groups = []
    unassigned_group = None

    if is_seller_context:
        # Fetch company products sorted strictly A to Z
        items_list = list(Product.objects.filter(company=company, is_deleted=False).order_by(Lower("name"))) if company else []
        inquiry_counts = (
            base_qs
            .order_by()
            .filter(search_result__search_job__product__isnull=False)
            .values("search_result__search_job__product_id")
            .annotate(total=Count("id"))
        )
        counts_map = {it["search_result__search_job__product_id"]: it["total"] for it in inquiry_counts}
        for p in items_list:
            p.inquiry_count = counts_map.get(p.id, 0)

        unassigned_count = base_qs.filter(search_result__search_job__product__isnull=True).count()

        if item_id_str and item_id_str.isdigit():
            selected_item = Product.objects.filter(id=int(item_id_str), company=company, is_deleted=False).first()
            if selected_item:
                is_single_item = True
                inquiries = list(base_qs.filter(search_result__search_job__product=selected_item).order_by("-sent_at"))
        elif item_id_str == "unassigned":
            is_unassigned_view = True
            is_single_item = True
            inquiries = list(base_qs.filter(search_result__search_job__product__isnull=True).order_by("-sent_at"))
        else:
            for p in items_list:
                if p.inquiry_count > 0:
                    p_inqs = list(base_qs.filter(search_result__search_job__product=p).order_by("-sent_at"))
                    item_groups.append({
                        "product": p,
                        "inquiries": p_inqs,
                        "count": len(p_inqs),
                        "sent_count": sum(1 for i in p_inqs if i.status == Inquiry.Status.SENT),
                        "discussion_count": sum(1 for i in p_inqs if i.status == Inquiry.Status.IN_DISCUSSION),
                        "replied_count": sum(1 for i in p_inqs if i.status == Inquiry.Status.REPLIED),
                        "won_count": sum(1 for i in p_inqs if i.status == Inquiry.Status.WON),
                    })
            if unassigned_count > 0:
                unassigned_inqs = list(base_qs.filter(search_result__search_job__product__isnull=True).order_by("-sent_at"))
                unassigned_group = {
                    "title": "Other Inquiries",
                    "inquiries": unassigned_inqs,
                    "count": len(unassigned_inqs),
                }
    else:
        # Buyer Context: Group by Procurement Requirements
        items_list = list(Requirement.objects.filter(company=company, is_deleted=False).order_by(Lower("item_name"))) if company else []
        for r in items_list:
            r.name = r.item_name

        inquiry_counts = (
            base_qs
            .order_by()
            .filter(search_result__search_job__requirement__isnull=False)
            .values("search_result__search_job__requirement_id")
            .annotate(total=Count("id"))
        )
        counts_map = {it["search_result__search_job__requirement_id"]: it["total"] for it in inquiry_counts}
        for r in items_list:
            r.inquiry_count = counts_map.get(r.id, 0)

        unassigned_count = base_qs.filter(search_result__search_job__requirement__isnull=True).count()

        if item_id_str and item_id_str.isdigit():
            selected_item = Requirement.objects.filter(id=int(item_id_str), company=company, is_deleted=False).first()
            if selected_item:
                selected_item.name = selected_item.item_name
                is_single_item = True
                inquiries = list(base_qs.filter(search_result__search_job__requirement=selected_item).order_by("-sent_at"))
        elif item_id_str == "unassigned":
            is_unassigned_view = True
            is_single_item = True
            inquiries = list(base_qs.filter(search_result__search_job__requirement__isnull=True).order_by("-sent_at"))
        else:
            for r in items_list:
                if r.inquiry_count > 0:
                    r_inqs = list(base_qs.filter(search_result__search_job__requirement=r).order_by("-sent_at"))
                    item_groups.append({
                        "product": r,
                        "requirement": r,
                        "inquiries": r_inqs,
                        "count": len(r_inqs),
                        "sent_count": sum(1 for i in r_inqs if i.status == Inquiry.Status.SENT),
                        "discussion_count": sum(1 for i in r_inqs if i.status == Inquiry.Status.IN_DISCUSSION),
                        "replied_count": sum(1 for i in r_inqs if i.status == Inquiry.Status.REPLIED),
                        "won_count": sum(1 for i in r_inqs if i.status == Inquiry.Status.WON),
                    })
            if unassigned_count > 0:
                unassigned_inqs = list(base_qs.filter(search_result__search_job__requirement__isnull=True).order_by("-sent_at"))
                unassigned_group = {
                    "title": "Other Inquiries",
                    "inquiries": unassigned_inqs,
                    "count": len(unassigned_inqs),
                }

    total_inquiries_count = base_qs.count()
    active_inqs = inquiries if is_single_item else list(base_qs)
    total_count = len(active_inqs)
    sent_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.SENT)
    discussion_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.IN_DISCUSSION)
    replied_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.REPLIED)
    won_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.WON)
    lost_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.LOST)
    failed_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.FAILED)
    conversion_rate = round(((won_count + replied_count) / total_count * 100), 1) if total_count else 0.0

    page_title = (
        (f"Buyer Inquiries - {selected_item.name}" if selected_item else "Buyer Inquiries (Product-Wise)")
        if is_seller_context else
        (f"Supplier Inquiries - {selected_item.name}" if selected_item else "Supplier Inquiries & RFQs (Requirement-Wise)")
    )

    return render(request, "leads/inquiries.html", {
        "products": items_list,
        "requirements": items_list if not is_seller_context else [],
        "selected_product": selected_item,
        "selected_requirement": selected_item if not is_seller_context else None,
        "selected_product_id": int(item_id_str) if item_id_str and item_id_str.isdigit() else None,
        "selected_requirement_id": int(item_id_str) if item_id_str and item_id_str.isdigit() else None,
        "is_unassigned_view": is_unassigned_view,
        "is_single_product": is_single_item,
        "product_groups": item_groups,
        "requirement_groups": item_groups if not is_seller_context else [],
        "unassigned_group": unassigned_group,
        "unassigned_count": unassigned_count,
        "total_inquiries_count": total_inquiries_count,
        "inquiries": inquiries,
        "total_count": total_count,
        "sent_count": sent_count,
        "discussion_count": discussion_count,
        "replied_count": replied_count,
        "won_count": won_count,
        "lost_count": lost_count,
        "failed_count": failed_count,
        "conversion_rate": conversion_rate,
        "current_status": status_filter,
        "current_date_range": date_range,
        "search_query": q,
        "status_choices": Inquiry.Status.choices,
        "is_seller_context": is_seller_context,
        "company": company,
        "rbac": rbac,
        "page_title": page_title,
    })


@login_required
def inquiry_detail_view(request, pk):
    """
    Detailed RFQ & Commercial Inquiry Dossier:
    Displays sent specifications, supplier/buyer details, delivery status,
    full conversation message stream, and quotation records.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Active company module permission required.")
        return redirect("dashboard")

    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])

    inquiry = get_object_or_404(Inquiry, pk=pk, company=company)
    inquiry.ensure_initial_message()

    messages_list = list(inquiry.messages.all().select_related("sender").order_by("created_at"))
    ext_company = inquiry.search_result.external_company

    price_history = PriceHistory.objects.filter(
        external_company=ext_company
    ).order_by("-recorded_at")[:10]

    return render(request, "leads/inquiry_detail.html", {
        "inquiry": inquiry,
        "ext_company": ext_company,
        "messages_list": messages_list,
        "price_history": price_history,
        "status_choices": Inquiry.Status.choices,
        "is_seller_context": is_seller_context,
        "is_print_mode": request.GET.get("print") == "1",
        "company": company,
        "rbac": rbac,
        "page_title": f"Inquiry Dossier #{inquiry.id} — {ext_company.name}",
    })


@login_required
def add_inquiry_message_view(request, pk):
    """
    Appends a new message (follow-up, reply, or internal commercial note)
    to the inquiry conversation stream.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Active company module permission required.")
        return redirect("dashboard")

    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])
    inquiry = get_object_or_404(Inquiry, pk=pk, company=company)

    if request.method == "POST":
        body = request.POST.get("message_body", "").strip()
        msg_type = request.POST.get("message_type", InquiryMessage.MessageType.OUTBOUND)
        sender_name = request.POST.get("sender_name", "").strip() or (request.user.get_full_name() or request.user.email)
        new_status = request.POST.get("new_status", "").strip()
        send_email_copy = "send_email_copy" in request.POST
        uploaded_attachment = request.FILES.get("attachment")

        if body or uploaded_attachment:
            message_content = body or "(Commercial Document Attached)"
            inquiry_msg = InquiryMessage.objects.create(
                inquiry=inquiry,
                sender=request.user,
                sender_name=sender_name,
                message_type=msg_type,
                subject=f"Re: {inquiry.subject}",
                body=message_content,
                attachment=uploaded_attachment,
                attachment_name=uploaded_attachment.name if uploaded_attachment else "",
            )

            if new_status and new_status in [s[0] for s in Inquiry.Status.choices]:
                inquiry.status = new_status
            elif inquiry.status == Inquiry.Status.SENT and msg_type == InquiryMessage.MessageType.OUTBOUND:
                inquiry.status = Inquiry.Status.IN_DISCUSSION
            inquiry.save(update_fields=["status", "updated_at"])

            if send_email_copy:
                send_inquiry_email(
                    inquiry,
                    user=request.user,
                    custom_body=message_content,
                    attachment_file=uploaded_attachment,
                )

            # Dispatch real-time in-app notification
            notify_message_logged(inquiry, inquiry_msg, user=request.user)

            messages.success(request, "Message logged to inquiry thread successfully.")

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "message": "Message logged successfully",
                "status": inquiry.status,
                "status_display": inquiry.get_status_display(),
            })

    detail_url = "sales-inquiry-detail" if is_seller_context else "inquiry-detail"
    return redirect(detail_url, pk=inquiry.id)


@login_required
def update_inquiry_status_view(request, pk):
    """
    Updates the commercial status of an inquiry (Sent, In Discussion, Quoted, Won, Lost, Failed).
    Supports instant AJAX requests directly from table dropdown or dossier.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        return JsonResponse({"success": False, "error": "Access restricted"}, status=403)

    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])
    inquiry = get_object_or_404(Inquiry, pk=pk, company=company)

    if request.method == "POST":
        new_status = request.POST.get("status", "").strip()
        if new_status in [s[0] for s in Inquiry.Status.choices]:
            old_status = inquiry.get_status_display()
            inquiry.status = new_status
            inquiry.save(update_fields=["status", "updated_at"])

            # Sync CRM SavedItem stage with Inquiry lifecycle
            if inquiry.search_result:
                if new_status == Inquiry.Status.WON:
                    SavedItem.objects.filter(
                        company=company,
                        search_result=inquiry.search_result,
                    ).update(status=SavedItem.Status.WON)
                elif new_status == Inquiry.Status.LOST:
                    SavedItem.objects.filter(
                        company=company,
                        search_result=inquiry.search_result,
                    ).update(status=SavedItem.Status.LOST)

            # Log system event in messages
            InquiryMessage.objects.create(
                inquiry=inquiry,
                sender=request.user,
                sender_name="System",
                message_type=InquiryMessage.MessageType.SYSTEM,
                subject=f"Status Changed to {inquiry.get_status_display()}",
                body=f"Commercial status transitioned from '{old_status}' to '{inquiry.get_status_display()}' by {request.user.get_full_name() or request.user.email}.",
            )

            # Dispatch real-time in-app notification
            notify_status_changed(inquiry, old_status, user=request.user)

            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({
                    "success": True,
                    "status": inquiry.status,
                    "status_display": inquiry.get_status_display(),
                })
            messages.success(request, f"Inquiry status updated to '{inquiry.get_status_display()}'.")

    detail_url = "sales-inquiry-detail" if is_seller_context else "inquiry-detail"
    return redirect(detail_url, pk=inquiry.id)


@login_required
def record_inquiry_quote_view(request, pk):
    """
    Records a formal quotation / commercial terms in response to an inquiry.
    Updates inquiry status to 'REPLIED', saves quoted price and terms,
    and logs both a PriceHistory record and a thread message.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Active company module permission required.")
        return redirect("dashboard")

    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])
    inquiry = get_object_or_404(Inquiry, pk=pk, company=company)

    if request.method == "POST":
        quoted_price_str = request.POST.get("quoted_price", "").strip()
        currency = request.POST.get("currency", "INR").strip().upper()
        delivery_terms = request.POST.get("delivery_terms", "").strip()
        notes = request.POST.get("notes", "").strip()

        price_val = None
        if quoted_price_str:
            try:
                price_val = Decimal(quoted_price_str)
                if inquiry.search_result:
                    PriceHistory.objects.create(
                        external_company=inquiry.search_result.external_company,
                        item_name=inquiry.search_result.product_title,
                        price=price_val,
                        currency=currency,
                    )
            except Exception:
                pass

        inquiry.status = Inquiry.Status.REPLIED
        if price_val is not None:
            inquiry.quoted_price = price_val
            inquiry.message = f"{inquiry.message}\n\n[Quotation: {currency} {quoted_price_str}]"
        if currency:
            inquiry.quoted_currency = currency
        if delivery_terms:
            inquiry.delivery_terms = delivery_terms

        inquiry.save(update_fields=["status", "quoted_price", "quoted_currency", "delivery_terms", "message", "updated_at"])

        # Advance SavedItem from CONTACTED to NEGOTIATING
        if inquiry.search_result:
            SavedItem.objects.filter(
                company=company,
                search_result=inquiry.search_result,
                status=SavedItem.Status.CONTACTED,
            ).update(status=SavedItem.Status.NEGOTIATING)

        # Log quote into conversation thread
        quote_body = f"Quoted Terms Recorded:\n• Price: {currency} {quoted_price_str}\n"
        if delivery_terms:
            quote_body += f"• Delivery/Lead time: {delivery_terms}\n"
        if notes:
            quote_body += f"• Notes: {notes}"

        vendor_name = inquiry.search_result.external_company.name if inquiry.search_result else "External Vendor"
        InquiryMessage.objects.create(
            inquiry=inquiry,
            sender=request.user,
            sender_name=vendor_name,
            message_type=InquiryMessage.MessageType.INBOUND,
            subject=f"Quotation Submitted: {currency} {quoted_price_str}",
            body=quote_body,
        )

        # Dispatch real-time in-app notification & email
        notify_quote_recorded(inquiry, user=request.user)
        try:
            from apps.billing.services.transactional_email import TransactionalEmailService
            TransactionalEmailService.send_quote_received_email(inquiry)
        except Exception:
            pass

        messages.success(
            request,
            f"Commercial quote recorded successfully! Inquiry #{inquiry.id} updated with quoted terms."
        )

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "status": inquiry.status,
                "quoted_price": str(price_val) if price_val else "",
                "currency": currency,
            })

        detail_url = "sales-inquiry-detail" if is_seller_context else "inquiry-detail"
        return redirect(detail_url, pk=inquiry.id)

    detail_url = "sales-inquiry-detail" if is_seller_context else "inquiry-detail"
    return redirect(detail_url, pk=inquiry.id)


@login_required
def resend_inquiry_view(request, pk):
    """
    Resends or sends a follow-up email for an existing inquiry.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Active company module permission required.")
        return redirect("dashboard")

    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])
    inquiry = get_object_or_404(Inquiry, pk=pk, company=company)

    if request.method == "POST":
        followup_note = request.POST.get("followup_note", "").strip()
        if followup_note:
            inquiry.message = f"{inquiry.message}\n\n[Follow-up]: {followup_note}"
            inquiry.save(update_fields=["message", "updated_at"])
            InquiryMessage.objects.create(
                inquiry=inquiry,
                sender=request.user,
                sender_name=request.user.get_full_name() or request.user.email,
                message_type=InquiryMessage.MessageType.OUTBOUND,
                subject=f"Follow-up: {inquiry.subject}",
                body=followup_note,
            )

        sent_ok, email_msg = send_inquiry_email(inquiry, user=request.user)
        if sent_ok:
            if inquiry.status == Inquiry.Status.FAILED:
                inquiry.status = Inquiry.Status.SENT
                inquiry.save(update_fields=["status"])
            messages.success(request, f"Follow-up dispatched successfully to {inquiry.sent_to_email}!")
        else:
            messages.warning(request, f"Follow-up dispatch failed: {email_msg}")

    detail_url = "sales-inquiry-detail" if is_seller_context else "inquiry-detail"
    return redirect(detail_url, pk=inquiry.id)


@login_required
def delete_inquiry_view(request, pk):
    """
    Deletes an inquiry record with company isolation.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Active company module permission required.")
        return redirect("dashboard")

    inquiry = get_object_or_404(Inquiry, pk=pk, company=company)
    inquiry.delete()

    messages.success(request, f"Inquiry #{pk} was removed from tracking.")
    redirect_target = "sales-inquiries-list" if (request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])) else "inquiries-list"
    return redirect(redirect_target)


@login_required
def export_inquiries_csv_view(request):
    """
    Dedicated 1-Click CSV Export for Commercial Inquiries & Quotes:
    Respects current company isolation, active product or requirement selection, date range, and status filters.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Inquiries module permission required.")
        return redirect("dashboard")

    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])

    qs = Inquiry.objects.filter(company=company).select_related(
        "search_result",
        "search_result__external_company",
        "search_result__search_job",
        "search_result__search_job__product",
        "search_result__search_job__product__category",
        "search_result__search_job__requirement",
        "search_result__search_job__requirement__category",
    ).annotate(messages_count=Count("messages")).order_by("-sent_at")

    # Filter by product or requirement
    item_id_str = request.GET.get("product_id", "").strip() or request.GET.get("requirement_id", "").strip()
    selected_item = None
    if is_seller_context:
        if item_id_str.isdigit():
            selected_item = Product.objects.filter(id=int(item_id_str), company=company, is_deleted=False).first()
            if selected_item:
                qs = qs.filter(search_result__search_job__product=selected_item)
        elif item_id_str == "unassigned":
            qs = qs.filter(search_result__search_job__product__isnull=True)
    else:
        if item_id_str.isdigit():
            selected_item = Requirement.objects.filter(id=int(item_id_str), company=company, is_deleted=False).first()
            if selected_item:
                selected_item.name = selected_item.item_name
                qs = qs.filter(search_result__search_job__requirement=selected_item)
        elif item_id_str == "unassigned":
            qs = qs.filter(search_result__search_job__requirement__isnull=True)

    # Filter by date range
    date_range = request.GET.get("date_range", "all").strip().lower()
    now = timezone.now()
    if date_range == "7d":
        qs = qs.filter(sent_at__gte=now - timedelta(days=7))
    elif date_range == "30d":
        qs = qs.filter(sent_at__gte=now - timedelta(days=30))
    elif date_range == "this_month":
        qs = qs.filter(sent_at__year=now.year, sent_at__month=now.month)

    # Filter by status
    status_filter = request.GET.get("status", "").strip()
    if status_filter and status_filter in dict(Inquiry.Status.choices):
        qs = qs.filter(status=status_filter)

    # Filter by search term
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(subject__icontains=q)
            | Q(sent_to_email__icontains=q)
            | Q(search_result__product_title__icontains=q)
            | Q(search_result__external_company__name__icontains=q)
            | Q(message__icontains=q)
        )

    item_slug = (selected_item.name if hasattr(selected_item, "name") else selected_item.item_name).replace(" ", "_") if selected_item else ("All_Products" if is_seller_context else "All_Requirements")
    context_prefix = "Sales_Inquiries" if is_seller_context else "Procurement_Inquiries"
    timestamp = now.strftime("%Y%m%d_%H%M")
    filename = f"{context_prefix}_{item_slug}_{timestamp}.csv"

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)

    if is_seller_context:
        writer.writerow([
            "Inquiry ID",
            "Counterparty Company",
            "Contact Email",
            "Catalog / Target Product",
            "Category",
            "Inquiry Subject",
            "Pipeline Status",
            "Quoted Price Amount",
            "Currency",
            "Commercial Delivery Terms",
            "Thread Messages Count",
            "Has Attachment",
            "Dispatched Date",
            "Last Updated",
        ])
        for inq in qs:
            ext = inq.search_result.external_company if inq.search_result else None
            prod_obj = inq.search_result.search_job.product if (inq.search_result and inq.search_result.search_job) else None
            prod_name = prod_obj.name if prod_obj else (inq.search_result.product_title if inq.search_result else "General")
            cat_name = prod_obj.category.name if (prod_obj and prod_obj.category) else "General"
            writer.writerow([
                inq.id,
                ext.name if ext else "Unknown",
                inq.sent_to_email or (ext.email if ext else ""),
                prod_name,
                cat_name,
                inq.subject or "",
                inq.get_status_display(),
                str(inq.quoted_price) if inq.quoted_price is not None else "",
                inq.quoted_currency or "INR",
                inq.delivery_terms or "",
                getattr(inq, "messages_count", inq.messages.count()),
                "Yes" if inq.attachment else "No",
                inq.sent_at.strftime("%Y-%m-%d %H:%M") if inq.sent_at else "",
                inq.updated_at.strftime("%Y-%m-%d %H:%M") if inq.updated_at else "",
            ])
    else:
        writer.writerow([
            "Inquiry ID",
            "Supplier Company Name",
            "Contact Email",
            "Procurement Requirement",
            "Category",
            "Inquiry Subject",
            "Pipeline Status",
            "Quoted Price Amount",
            "Currency",
            "Commercial Delivery Terms",
            "Thread Messages Count",
            "Has Attachment",
            "Dispatched Date",
            "Last Updated",
        ])
        for inq in qs:
            ext = inq.search_result.external_company if inq.search_result else None
            req_obj = inq.search_result.search_job.requirement if (inq.search_result and inq.search_result.search_job) else None
            req_name = req_obj.item_name if req_obj else (inq.search_result.product_title if inq.search_result else "General")
            cat_name = req_obj.category.name if (req_obj and req_obj.category) else "General"
            writer.writerow([
                inq.id,
                ext.name if ext else "Unknown",
                inq.sent_to_email or (ext.email if ext else ""),
                req_name,
                cat_name,
                inq.subject or "",
                inq.get_status_display(),
                str(inq.quoted_price) if inq.quoted_price is not None else "",
                inq.quoted_currency or "INR",
                inq.delivery_terms or "",
                getattr(inq, "messages_count", inq.messages.count()),
                "Yes" if inq.attachment else "No",
                inq.sent_at.strftime("%Y-%m-%d %H:%M") if inq.sent_at else "",
                inq.updated_at.strftime("%Y-%m-%d %H:%M") if inq.updated_at else "",
            ])

    return response


# ============================================================
# PRICE COMPARISON & DYNAMIC EXPORT REPORTS
# ============================================================

@login_required
def price_comparison_view(request):
    """
    Side-by-Side Price Comparison Matrix:
    Compares supplier prices for buyer requirements using verified available supplier prices,
    formal quotes from Inquiries, currency, quantity, MOQ, delivery terms, and source domain.
    Never invents prices — clearly marks missing or unverified quotes.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Price Comparison is only available for Buyer accounts.")
        return redirect("dashboard")

    # Load all company procurement requirements sorted strictly A to Z
    requirements = list(Requirement.objects.filter(company=company, is_deleted=False).order_by(Lower("item_name"))) if company else []

    req_id_str = request.GET.get("requirement_id", "").strip()
    selected_requirement = None
    if req_id_str and req_id_str.isdigit():
        selected_requirement = Requirement.objects.filter(id=int(req_id_str), company=company, is_deleted=False).first()
    elif requirements:
        selected_requirement = requirements[0]

    # Fetch candidate suppliers for this requirement
    candidates_qs = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.SUPPLIER,
    ).select_related(
        "external_company",
        "search_job",
        "search_job__requirement",
        "search_job__requirement__category",
    ).order_by("-match_score", "-created_at")

    if selected_requirement:
        candidates_qs = candidates_qs.filter(search_job__requirement=selected_requirement)

    # Fetch formal quotes recorded from Inquiry
    quotes_map = {}
    inquiries_with_quotes = Inquiry.objects.filter(
        company=company,
        quoted_price__isnull=False,
    ).select_related("search_result", "search_result__external_company")
    if selected_requirement:
        inquiries_with_quotes = inquiries_with_quotes.filter(search_result__search_job__requirement=selected_requirement)

    for inq in inquiries_with_quotes:
        if inq.search_result_id:
            quotes_map[inq.search_result_id] = inq
        if inq.search_result and inq.search_result.external_company_id:
            quotes_map[f"comp_{inq.search_result.external_company_id}"] = inq

    # Build structured side-by-side comparison items
    comparison_items = []
    seen_companies = set()
    valid_prices = []

    target_price = selected_requirement.target_price if selected_requirement else None
    target_currency = selected_requirement.currency if selected_requirement else "INR"

    for r in candidates_qs:
        cid = r.external_company_id
        if cid and cid in seen_companies:
            continue
        if cid:
            seen_companies.add(cid)

        # Check if there is a verified inquiry quote
        inq_quote = quotes_map.get(r.id) or (quotes_map.get(f"comp_{cid}") if cid else None)

        is_verified_quote = False
        price_val = None
        price_currency = target_currency
        delivery_terms = ""
        moq_val = r.moq or (selected_requirement.quantity if selected_requirement else 1)

        if inq_quote and inq_quote.quoted_price is not None:
            is_verified_quote = True
            price_val = inq_quote.quoted_price
            price_currency = inq_quote.quoted_currency or target_currency
            delivery_terms = inq_quote.delivery_terms or ""
        elif r.price is not None:
            price_val = r.price
            price_currency = r.price_currency or target_currency

        # Calculate variance and savings vs target price
        variance = None
        savings_percent = None
        is_cheaper = False
        if price_val is not None:
            valid_prices.append(price_val)
            if target_price and target_price > 0:
                variance = price_val - target_price
                if price_val < target_price:
                    is_cheaper = True
                    savings_percent = round(((target_price - price_val) / target_price) * 100, 1)

        comparison_items.append({
            "result": r,
            "company": r.external_company,
            "product_title": r.product_title or (selected_requirement.item_name if selected_requirement else "Requirement Item"),
            "price": price_val,
            "currency": price_currency,
            "is_verified_quote": is_verified_quote,
            "price_status": "Verified Formal Quote" if is_verified_quote else ("Web Discovered Price" if price_val is not None else "Quote on Request"),
            "delivery_terms": delivery_terms,
            "moq": moq_val,
            "match_score": r.match_score,
            "distance_km": r.distance_km,
            "variance": variance,
            "savings_percent": savings_percent,
            "is_cheaper": is_cheaper,
            "source_domain": r.external_company.domain if r.external_company else "",
            "last_updated": inq_quote.updated_at if inq_quote else r.created_at,
        })

    # Summary KPI calculation
    total_compared_count = len(comparison_items)
    verified_quotes_count = sum(1 for it in comparison_items if it["is_verified_quote"])
    lowest_price = min(valid_prices) if valid_prices else None
    avg_price = round(sum(valid_prices) / len(valid_prices), 2) if valid_prices else None
    cheaper_options_count = sum(1 for it in comparison_items if it["is_cheaper"])
    max_savings_pct = max([it["savings_percent"] for it in comparison_items if it["savings_percent"] is not None], default=0.0)

    return render(request, "leads/price_comparison.html", {
        "requirements": requirements,
        "selected_requirement": selected_requirement,
        "selected_requirement_id": selected_requirement.id if selected_requirement else None,
        "comparison_items": comparison_items,
        "results": [it["result"] for it in comparison_items],
        "total_compared_count": total_compared_count,
        "verified_quotes_count": verified_quotes_count,
        "lowest_price": lowest_price,
        "avg_price": avg_price,
        "cheaper_options_count": cheaper_options_count,
        "max_savings_pct": max_savings_pct,
        "target_price": target_price,
        "target_currency": target_currency,
        "company": company,
        "rbac": rbac,
        "page_title": f"Price Comparison — {selected_requirement.item_name}" if selected_requirement else "Price Comparison & Landed Cost Analysis",
    })


@login_required
def export_price_comparison_csv_view(request):
    """
    1-Click CSV Export for Price Comparison Matrix:
    Exports verified supplier quotes, discovered prices, MOQ, delivery terms,
    target price variance, savings %, and match scores.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Price Comparison permission required.")
        return redirect("dashboard")

    req_id_str = request.GET.get("requirement_id", "").strip()
    selected_requirement = None
    if req_id_str and req_id_str.isdigit():
        selected_requirement = Requirement.objects.filter(id=int(req_id_str), company=company, is_deleted=False).first()

    candidates_qs = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.SUPPLIER,
    ).select_related(
        "external_company",
        "search_job",
        "search_job__requirement",
        "search_job__requirement__category",
    ).order_by("-match_score")

    if selected_requirement:
        candidates_qs = candidates_qs.filter(search_job__requirement=selected_requirement)

    quotes_map = {}
    inquiries_with_quotes = Inquiry.objects.filter(company=company, quoted_price__isnull=False)
    if selected_requirement:
        inquiries_with_quotes = inquiries_with_quotes.filter(search_result__search_job__requirement=selected_requirement)
    for inq in inquiries_with_quotes:
        if inq.search_result_id:
            quotes_map[inq.search_result_id] = inq
        if inq.search_result and inq.search_result.external_company_id:
            quotes_map[f"comp_{inq.search_result.external_company_id}"] = inq

    target_price = selected_requirement.target_price if selected_requirement else None
    target_currency = selected_requirement.currency if selected_requirement else "INR"
    req_slug = selected_requirement.item_name.replace(" ", "_") if selected_requirement else "All_Requirements"

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="Price_Comparison_{req_slug}.csv"'
    writer = csv.writer(response)

    writer.writerow([
        "Supplier Company Name",
        "Country",
        "City",
        "Requirement Item",
        "Target Price",
        "Supplier Price",
        "Currency",
        "Price Status",
        "Target Price Variance",
        "Savings (%)",
        "MOQ",
        "Commercial Delivery Terms",
        "Match Score (%)",
        "Source Domain",
        "Last Updated",
    ])

    seen_companies = set()
    for r in candidates_qs:
        cid = r.external_company_id
        if cid and cid in seen_companies:
            continue
        if cid:
            seen_companies.add(cid)

        inq_quote = quotes_map.get(r.id) or (quotes_map.get(f"comp_{cid}") if cid else None)
        is_verified_quote = bool(inq_quote and inq_quote.quoted_price is not None)
        price_val = inq_quote.quoted_price if is_verified_quote else r.price
        price_currency = inq_quote.quoted_currency if is_verified_quote else (r.price_currency or target_currency)
        delivery_terms = inq_quote.delivery_terms if is_verified_quote else ""
        price_status = "Verified Formal Quote" if is_verified_quote else ("Web Discovered" if price_val is not None else "Quote on Request")

        variance_str = ""
        savings_str = ""
        if price_val is not None and target_price and target_price > 0:
            diff = price_val - target_price
            variance_str = f"+{diff}" if diff > 0 else str(diff)
            if price_val < target_price:
                savings_str = f"{round(((target_price - price_val) / target_price) * 100, 1)}%"

        writer.writerow([
            r.external_company.name if r.external_company else "Unknown",
            r.external_company.country or "India" if r.external_company else "India",
            r.external_company.city or "" if r.external_company else "",
            selected_requirement.item_name if selected_requirement else (r.product_title or "General"),
            str(target_price) if target_price else "Open Budget",
            str(price_val) if price_val is not None else "Quote on Request",
            price_currency,
            price_status,
            variance_str,
            savings_str,
            r.moq or 1,
            delivery_terms,
            r.match_score,
            r.external_company.domain or "" if r.external_company else "",
            (inq_quote.updated_at if inq_quote else r.created_at).strftime("%Y-%m-%d %H:%M"),
        ])

    return response


@login_required
def export_reports_view(request):
    """
    Interactive & Dynamic Export Reports Dashboard:
    Provides real-time interactive report previews, dynamic multi-dimension filtering,
    summary KPI metrics, and export engines in CSV, JSON, and printable format.
    Supports both Seller (Products, Leads, Inquiries) and Buyer (Requirements, Suppliers, Inquiries) workflows.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])

    # Active Report Type
    report_type = request.GET.get("type") or request.GET.get("report_type") or "leads"
    valid_types = ["leads", "inquiries", "catalog"]
    if report_type not in valid_types:
        report_type = "leads"

    # Fetch company catalog items (Products for Seller, Requirements for Buyer)
    if is_seller_context:
        items_list = list(Product.objects.filter(company=company, is_deleted=False).order_by(Lower("name"))) if company else []
        leads_base = SearchResult.objects.filter(search_job__company=company, result_type=SearchResult.ResultType.LEAD)
        leads_map = {item["search_job__product_id"]: item["total"] for item in leads_base.filter(search_job__product__isnull=False).values("search_job__product_id").annotate(total=Count("id"))}
        inquiries_base = Inquiry.objects.filter(company=company)
        inquiry_map = {item["search_result__search_job__product_id"]: item["total"] for item in inquiries_base.filter(search_result__search_job__product__isnull=False).values("search_result__search_job__product_id").annotate(total=Count("id"))}
        for p in items_list:
            p.lead_count = leads_map.get(p.id, 0)
            p.inquiry_count = inquiry_map.get(p.id, 0)
    else:
        items_list = list(Requirement.objects.filter(company=company, is_deleted=False).order_by(Lower("item_name"))) if company else []
        for r in items_list:
            r.name = r.item_name
        leads_base = SearchResult.objects.filter(search_job__company=company, result_type=SearchResult.ResultType.SUPPLIER)
        leads_map = {item["search_job__requirement_id"]: item["total"] for item in leads_base.filter(search_job__requirement__isnull=False).values("search_job__requirement_id").annotate(total=Count("id"))}
        inquiries_base = Inquiry.objects.filter(company=company)
        inquiry_map = {item["search_result__search_job__requirement_id"]: item["total"] for item in inquiries_base.filter(search_result__search_job__requirement__isnull=False).values("search_result__search_job__requirement_id").annotate(total=Count("id"))}
        for r in items_list:
            r.lead_count = leads_map.get(r.id, 0)
            r.inquiry_count = inquiry_map.get(r.id, 0)

    total_leads_count = leads_base.count()
    total_inquiry_count = inquiries_base.count()

    item_id_str = request.GET.get("product_id", "").strip() or request.GET.get("requirement_id", "").strip()
    selected_item = None
    if item_id_str and item_id_str.isdigit():
        if is_seller_context:
            selected_item = Product.objects.filter(id=int(item_id_str), company=company, is_deleted=False).first()
        else:
            selected_item = Requirement.objects.filter(id=int(item_id_str), company=company, is_deleted=False).first()
            if selected_item:
                selected_item.name = selected_item.item_name

    # Dynamic Filters
    date_range = request.GET.get("date_range", "all").strip().lower()
    now = timezone.now()
    date_cutoff = None
    if date_range == "7d":
        date_cutoff = now - timedelta(days=7)
    elif date_range == "30d":
        date_cutoff = now - timedelta(days=30)
    elif date_range == "this_month":
        date_cutoff = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    min_score_str = request.GET.get("min_score", "0").strip()
    min_score = int(min_score_str) if min_score_str.isdigit() else 0

    status_filter = request.GET.get("status", "").strip().lower()
    q_search = request.GET.get("q", "").strip()

    # Filtered QuerySets based on active controls
    if not selected_item:
        leads_filtered = leads_base.none()
        inquiries_filtered = inquiries_base.none()
        kpi_leads_count = 0
        kpi_inquiries_count = 0
        kpi_high_fit_count = 0
        kpi_high_fit_pct = 0.0
        kpi_contact_verified_pct = 0.0
        verified_leads = 0
        inq_replied_count = 0
        inq_discussion_count = 0
        inq_won_count = 0
        kpi_inquiry_response_rate = 0.0
        kpi_inquiry_won_rate = 0.0
        avg_match_score = 0.0
        preview_records = []
    else:
        if is_seller_context:
            leads_filtered = leads_base.filter(search_job__product=selected_item).select_related(
                "external_company", "search_job", "search_job__product", "search_job__product__category"
            )
            inquiries_filtered = inquiries_base.filter(search_result__search_job__product=selected_item).select_related(
                "search_result", "search_result__external_company", "search_result__search_job__product"
            )
        else:
            leads_filtered = leads_base.filter(search_job__requirement=selected_item).select_related(
                "external_company", "search_job", "search_job__requirement", "search_job__requirement__category"
            )
            inquiries_filtered = inquiries_base.filter(search_result__search_job__requirement=selected_item).select_related(
                "search_result", "search_result__external_company", "search_result__search_job__requirement"
            )

        avg_res = leads_filtered.aggregate(avg_score=Avg("match_score"))
        avg_match_score = round(float(avg_res["avg_score"]), 1) if avg_res["avg_score"] is not None else 0.0

        if date_cutoff:
            leads_filtered = leads_filtered.filter(created_at__gte=date_cutoff)
            inquiries_filtered = inquiries_filtered.filter(sent_at__gte=date_cutoff)

        if min_score > 0:
            leads_filtered = leads_filtered.filter(match_score__gte=min_score)

        if status_filter and status_filter != "all":
            inquiries_filtered = inquiries_filtered.filter(status=status_filter)

        if q_search:
            leads_filtered = leads_filtered.filter(
                Q(external_company__name__icontains=q_search)
                | Q(external_company__city__icontains=q_search)
                | Q(external_company__email__icontains=q_search)
                | Q(product_title__icontains=q_search)
                | Q(need_signal__icontains=q_search)
            )
            inquiries_filtered = inquiries_filtered.filter(
                Q(search_result__external_company__name__icontains=q_search)
                | Q(sent_to_email__icontains=q_search)
                | Q(subject__icontains=q_search)
            )

        kpi_leads_count = leads_filtered.count()
        kpi_inquiries_count = inquiries_filtered.count()

        kpi_high_fit_count = leads_filtered.filter(match_score__gte=80).count()
        kpi_high_fit_pct = round((kpi_high_fit_count / kpi_leads_count * 100), 1) if kpi_leads_count else 0.0
        verified_leads = leads_filtered.filter(
            Q(external_company__email__isnull=False) | Q(external_company__phone__isnull=False)
        ).exclude(external_company__email="").count()
        kpi_contact_verified_pct = round((verified_leads / kpi_leads_count * 100), 1) if kpi_leads_count else 0.0

        inq_replied_count = inquiries_filtered.filter(status=Inquiry.Status.REPLIED).count()
        inq_discussion_count = inquiries_filtered.filter(status=Inquiry.Status.IN_DISCUSSION).count()
        inq_won_count = inquiries_filtered.filter(status=Inquiry.Status.WON).count()
        kpi_inquiry_response_rate = round(((inq_replied_count + inq_won_count) / kpi_inquiries_count * 100), 1) if kpi_inquiries_count else 0.0
        kpi_inquiry_won_rate = round((inq_won_count / kpi_inquiries_count * 100), 1) if kpi_inquiries_count else 0.0

        if report_type == "leads":
            preview_records = list(leads_filtered.order_by("-match_score", "-created_at")[:100])
        elif report_type == "catalog":
            preview_records = [selected_item] if selected_item else []
        else:
            preview_records = list(inquiries_filtered.order_by("-sent_at")[:100])

    item_slug = (selected_item.name if hasattr(selected_item, "name") else selected_item.item_name).replace(" ", "_") if selected_item else "Selected_Item"

    # HANDLE EXPORT DOWNLOADS (CSV)
    if request.GET.get("download") == "1":
        if not selected_item:
            messages.warning(request, f"Please select a {'product' if is_seller_context else 'requirement'} first to generate and download the export.")
            return redirect(f"{request.path}?report_type={report_type}")

        # Credit reservation for report export
        usage_rec = None
        if company:
            from apps.billing.services.wallet import CreditWalletService
            ok, msg, usage_rec = CreditWalletService.reserve_credits(
                company=company,
                user=request.user,
                feature_code="export_reports",
                quantity=1,
                idempotency_key=f"export_report:{company.id}:{request.user.id}:{report_type}:{selected_item.id}:{timezone.now().strftime('%Y%m%d%H%M%S')}",
            )
            if not ok:
                messages.error(request, f"Cannot export report: {msg} Please top up your wallet.")
                return redirect(f"{request.path}?report_type={report_type}&product_id={selected_item.id}")

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        writer = csv.writer(response)

        if report_type == "leads":
            prefix = "Vendor_Report" if is_seller_context else "Supplier_Discovery_Report"
            response["Content-Disposition"] = f'attachment; filename="{prefix}_{item_slug}.csv"'
            writer.writerow(["Company Name", "Industry", "City", "Country", "Website", "Email", "Phone", "Target Item", "Category", "Fit Score (%)", "Fit Reason", "Intent Signal", "Discovered At"])
            for item in leads_filtered.order_by("-match_score", "-created_at")[:1000]:
                job = item.search_job
                target_name = (job.product.name if job.product else item.product_title) if (is_seller_context and job) else (job.requirement.item_name if (job and job.requirement) else item.product_title)
                cat_name = (job.product.category.name if (job.product and job.product.category) else "General") if (is_seller_context and job) else (job.requirement.category.name if (job and job.requirement and job.requirement.category) else "General")
                writer.writerow([
                    item.external_company.name if item.external_company else "Unknown",
                    item.external_company.industry or "" if item.external_company else "",
                    item.external_company.city or "" if item.external_company else "",
                    item.external_company.country or "India" if item.external_company else "India",
                    item.external_company.website or "" if item.external_company else "",
                    item.external_company.email or "" if item.external_company else "",
                    item.external_company.phone or "" if item.external_company else "",
                    target_name,
                    cat_name,
                    item.match_score,
                    item.match_reason or "",
                    item.need_signal or "",
                    item.created_at.strftime("%Y-%m-%d %H:%M"),
                ])

        elif report_type == "catalog":
            if is_seller_context:
                response["Content-Disposition"] = f'attachment; filename="Product_Catalog_{item_slug}.csv"'
                writer.writerow(["Product Name", "Category", "Type", "Price", "Currency", "MOQ", "Unit", "Availability", "Location", "Search Scope", "Description", "Specifications", "Created At"])
                prods_to_export = Product.objects.filter(company=company, is_deleted=False)
                if selected_item:
                    prods_to_export = prods_to_export.filter(id=selected_item.id)
                for p in prods_to_export.order_by("name"):
                    writer.writerow([
                        p.name,
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
            else:
                response["Content-Disposition"] = f'attachment; filename="Requirement_Specifications_{item_slug}.csv"'
                writer.writerow(["Requirement Item", "Category", "Quantity", "Unit", "Target Price", "Currency", "Delivery City", "Search Scope", "Status", "Description", "Specifications", "Created At"])
                reqs_to_export = Requirement.objects.filter(company=company, is_deleted=False)
                if selected_item:
                    reqs_to_export = reqs_to_export.filter(id=selected_item.id)
                for r in reqs_to_export.order_by("item_name"):
                    writer.writerow([
                        r.item_name,
                        r.category.name if r.category else "General",
                        r.quantity,
                        r.unit,
                        r.target_price or "",
                        r.currency,
                        r.delivery_city or (company.city if company else "India"),
                        r.get_search_scope_display(),
                        r.get_status_display(),
                        r.description or "",
                        r.specifications or "",
                        r.created_at.strftime("%Y-%m-%d %H:%M"),
                    ])

        else:  # inquiries
            prefix = "Inquiries_Audit" if is_seller_context else "Procurement_Inquiries_Audit"
            response["Content-Disposition"] = f'attachment; filename="{prefix}_{item_slug}.csv"'
            writer.writerow(["Inquiry ID", "Counterparty Company", "Contact Email", "Subject", "Item / Requirement", "Status", "Quoted Price", "Currency", "Delivery Terms", "Date Sent"])
            for inq in inquiries_filtered.order_by("-sent_at")[:1000]:
                job = inq.search_result.search_job if inq.search_result else None
                item_label = (job.product.name if job.product else inq.search_result.product_title) if (is_seller_context and job) else (job.requirement.item_name if (job and job.requirement) else (inq.search_result.product_title if inq.search_result else "General"))
                writer.writerow([
                    inq.id,
                    inq.search_result.external_company.name if inq.search_result else "External Vendor",
                    inq.sent_to_email,
                    inq.subject,
                    item_label,
                    inq.get_status_display(),
                    inq.quoted_price or "",
                    inq.quoted_currency or "INR",
                    inq.delivery_terms or "",
                    inq.sent_at.strftime("%Y-%m-%d %H:%M") if inq.sent_at else "",
                ])

        if usage_rec:
            from apps.billing.services.wallet import CreditWalletService
            CreditWalletService.commit_usage(usage_rec)

        return response

    checksum_seed = f"{company.id if company else 0}-{selected_item.id if selected_item else 0}-{timezone.now().strftime('%Y%m%d')}-{report_type}"
    report_checksum = hashlib.sha256(checksum_seed.encode("utf-8")).hexdigest()[:16].upper()

    return render(request, "leads/export_reports.html", {
        "products": items_list,
        "requirements": items_list if not is_seller_context else [],
        "selected_product": selected_item,
        "selected_requirement": selected_item if not is_seller_context else None,
        "selected_product_id": int(item_id_str) if item_id_str and item_id_str.isdigit() else None,
        "selected_requirement_id": int(item_id_str) if item_id_str and item_id_str.isdigit() else None,
        "report_type": report_type,
        "preview_records": preview_records,
        "total_leads_count": total_leads_count,
        "total_inquiry_count": total_inquiry_count,
        "total_catalog_count": len(items_list),
        "kpi_leads_count": kpi_leads_count,
        "kpi_inquiries_count": kpi_inquiries_count,
        "kpi_high_fit_count": kpi_high_fit_count,
        "kpi_high_fit_pct": kpi_high_fit_pct,
        "kpi_contact_verified_pct": kpi_contact_verified_pct,
        "verified_leads": verified_leads,
        "inq_replied_count": inq_replied_count,
        "inq_discussion_count": inq_discussion_count,
        "inq_won_count": inq_won_count,
        "kpi_inquiry_response_rate": kpi_inquiry_response_rate,
        "kpi_inquiry_won_rate": kpi_inquiry_won_rate,
        "avg_match_score": avg_match_score,
        "current_timestamp": timezone.now(),
        "report_ref": f"REP-{selected_item.id:04d}-{timezone.now().strftime('%Y%m%d%H%M')}" if selected_item else "",
        "report_checksum": report_checksum,
        "date_range": date_range,
        "min_score": min_score,
        "status_filter": status_filter,
        "q_search": q_search,
        "is_seller_context": is_seller_context,
        "is_print_mode": request.GET.get("format") == "print" or request.GET.get("print") == "1",
        "company": company,
        "rbac": rbac,
        "page_title": (
            (f"{'Vendor Report' if is_seller_context else 'Supplier Report'} - {selected_item.name}" if selected_item else ("Vendor Report & Analytics" if is_seller_context else "Supplier Intelligence Report"))
            if report_type == "leads" else
            (f"{'Catalog Specs' if is_seller_context else 'Requirement Specs'} - {selected_item.name}" if selected_item else ("Catalog Specifications Dossier" if is_seller_context else "Procurement Specifications Dossier"))
            if report_type == "catalog" else
            (f"Inquiries Audit - {selected_item.name}" if selected_item else "Inquiries & Outreach Audit Report")
        ),
    })

