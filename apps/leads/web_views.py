import csv
from decimal import Decimal
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from apps.leads.models import SavedItem, Inquiry, PriceHistory
from apps.leads.services.email_service import send_inquiry_email
from apps.ai_search.models import SearchResult, ExternalCompany
from apps.catalog.models import Product
from apps.companies.rbac import get_user_rbac_context


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

    result = get_object_or_404(SearchResult, id=result_id)

    saved_item, created = SavedItem.objects.get_or_create(
        company=company,
        search_result=result,
        defaults={"status": SavedItem.Status.NEW},
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "created": created,
            "message": f"Lead '{result.external_company.name}' saved to pipeline!" if created else "Lead is already saved.",
        })

    if created:
        messages.success(request, f"Lead '{result.external_company.name}' saved to your sales pipeline!")
    else:
        messages.info(request, f"Lead '{result.external_company.name}' is already in your saved leads.")

    redirect_to = request.META.get("HTTP_REFERER") or reverse("find-buyers")
    return redirect(redirect_to)


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
    ).select_related(
        "search_result",
        "search_result__external_company",
        "search_result__search_job",
        "search_result__search_job__product",
        "search_result__search_job__product__category",
        "assigned_to",
    ).order_by("-created_at")

    # Calculate saved lead counts per product
    product_saved_counts = (
        base_qs
        .filter(search_result__search_job__product__isnull=False)
        .values("search_result__search_job__product_id")
        .annotate(total=Count("id"))
    )
    counts_map = {item["search_result__search_job__product_id"]: item["total"] for item in product_saved_counts}
    for p in products:
        p.saved_count = counts_map.get(p.id, 0)

    total_saved_count = base_qs.count()
    unassigned_count = base_qs.filter(search_result__search_job__product__isnull=True).count()

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
            saved_items = list(base_qs.filter(search_result__search_job__product=selected_product))
    elif product_id_str == "unassigned":
        is_unassigned_view = True
        is_single_product = True
        saved_items = list(base_qs.filter(search_result__search_job__product__isnull=True))
    else:
        # Group by product so it is NOT "all-in-one"
        for p in products:
            if p.saved_count > 0:
                p_items = list(base_qs.filter(search_result__search_job__product=p))
                product_groups.append({
                    "product": p,
                    "items": p_items,
                    "count": len(p_items),
                    "new_count": sum(1 for i in p_items if i.status == "new"),
                    "contacted_count": sum(1 for i in p_items if i.status == "contacted"),
                    "negotiating_count": sum(1 for i in p_items if i.status == "negotiating"),
                    "won_count": sum(1 for i in p_items if i.status == "won"),
                })
        if unassigned_count > 0:
            unassigned_items = list(base_qs.filter(search_result__search_job__product__isnull=True))
            unassigned_group = {
                "title": "Other Saved Leads",
                "items": unassigned_items,
                "count": len(unassigned_items),
            }

    active_items = saved_items if is_single_product else list(base_qs)
    stage_counts = {
        "new": sum(1 for i in active_items if i.status == "new"),
        "contacted": sum(1 for i in active_items if i.status == "contacted"),
        "negotiating": sum(1 for i in active_items if i.status == "negotiating"),
        "interested": sum(1 for i in active_items if i.status == "interested"),
        "won": sum(1 for i in active_items if i.status == "won"),
        "lost": sum(1 for i in active_items if i.status == "lost"),
    }

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
        "company": company,
        "rbac": rbac,
        "team_members": team_members,
        "status_choices": SavedItem.Status.choices,
        "page_title": f"Saved Buyers - {selected_product.name}" if selected_product else "Saved Buyers (Product-Wise)",
    })


@login_required
def update_saved_lead_view(request, item_id):
    """
    Updates status pipeline, private notes, or assigned team member for a saved lead.
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
        notes = request.POST.get("notes", "").strip()
        assigned_to_id = request.POST.get("assigned_to_id")

        if status_val in [s[0] for s in SavedItem.Status.choices]:
            item.status = status_val

        if "notes" in request.POST:
            item.notes = notes

        if assigned_to_id is not None:
            if assigned_to_id == "":
                item.assigned_to = None
            else:
                item.assigned_to_id = assigned_to_id

        item.save()

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "status": item.status,
                "status_display": item.get_status_display(),
                "notes": item.notes,
            })

        messages.success(request, f"Lead '{item.search_result.external_company.name}' updated successfully.")

    return redirect("saved-leads")


@login_required
def delete_saved_lead_view(request, item_id):
    """
    Removes lead from the saved pipeline.
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

    messages.success(request, f"Lead '{lead_name}' removed from your pipeline.")
    return redirect("saved-leads")


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
        messages.error(request, "Access restricted: Sales Leads is only available for Seller accounts.")
        return redirect("dashboard")

    # Fetch company products sorted strictly alphabetically A to Z
    products = list(Product.objects.filter(company=company, is_deleted=False).order_by(Lower("name")))

    # Base query of all qualified buyer leads for this company
    base_leads_qs = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.LEAD,
    ).select_related(
        "external_company",
        "search_job",
        "search_job__product",
        "search_job__product__category",
    ).order_by("-match_score", "-created_at")

    # Lead counts by product
    lead_counts = (
        base_leads_qs
        .filter(search_job__product__isnull=False)
        .values("search_job__product_id")
        .annotate(total=Count("id"))
    )
    counts_map = {item["search_job__product_id"]: item["total"] for item in lead_counts}
    for p in products:
        p.lead_count = counts_map.get(p.id, 0)

    unassigned_count = base_leads_qs.filter(search_job__product__isnull=True).count()
    total_leads_count = base_leads_qs.count()

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
            leads = list(base_leads_qs.filter(search_job__product=selected_product))
    elif product_id_str == "unassigned":
        is_unassigned_view = True
        is_single_product = True
        leads = list(base_leads_qs.filter(search_job__product__isnull=True))
    else:
        # Group by product so it is NOT "all-in-one"
        for p in products:
            if p.lead_count > 0:
                p_leads = list(base_leads_qs.filter(search_job__product=p))
                product_groups.append({
                    "product": p,
                    "leads": p_leads,
                    "count": len(p_leads),
                })

        if unassigned_count > 0:
            unassigned_leads = list(base_leads_qs.filter(search_job__product__isnull=True))
            unassigned_group = {
                "title": "Other / Custom Search Leads",
                "leads": unassigned_leads,
                "count": len(unassigned_leads),
            }

    saved_result_ids = set(SavedItem.objects.filter(company=company).values_list("search_result_id", flat=True)) if company else set()

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
        "company": company,
        "rbac": rbac,
        "page_title": f"Buyer Leads - {selected_product.name}" if selected_product else "Sales Leads (Product-Wise)",
    })


# ============================================================
# BUYER SIDE: SUPPLIER SHORTLIST & RFQ INQUIRY WORKFLOW
# ============================================================

@login_required
def save_supplier_toggle_view(request, result_id):
    """
    Shortlist Supplier Action:
    Allows buyers to bookmark a qualified supplier for procurement.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Procurement module permission required.")
        return redirect("dashboard")

    result = get_object_or_404(SearchResult, id=result_id)

    saved_item, created = SavedItem.objects.get_or_create(
        company=company,
        search_result=result,
        defaults={"status": SavedItem.Status.INTERESTED},
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "created": created,
            "message": f"Supplier '{result.external_company.name}' shortlisted!" if created else "Supplier is already shortlisted.",
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
    Saved / Shortlisted Suppliers View:
    Displays all bookmarked suppliers with pricing, MOQ, and direct RFQ action.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Saved Suppliers is only available for Buyer accounts.")
        return redirect("dashboard")

    saved_suppliers = SavedItem.objects.filter(
        company=company,
        search_result__result_type=SearchResult.ResultType.SUPPLIER,
    ).select_related(
        "search_result",
        "search_result__external_company",
    ).order_by("-created_at")

    return render(request, "leads/saved_suppliers.html", {
        "saved_suppliers": saved_suppliers,
        "company": company,
        "rbac": rbac,
        "page_title": "Shortlisted Suppliers",
    })


@login_required
def delete_saved_supplier_view(request, item_id):
    """
    Removes supplier from shortlisted suppliers.
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

    messages.success(request, f"Supplier '{name}' removed from shortlist.")
    return redirect("saved-suppliers")


@login_required
def send_rfq_view(request, result_id):
    """
    Send RFQ / Inquiry View:
    Processes dispatch of official Request For Quotation to supplier,
    dispatches email, records inquiry in DB, and redirects to inquiries tracking.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Active company module permission required.")
        return redirect("dashboard")

    result = get_object_or_404(SearchResult, id=result_id)

    if request.method == "POST":
        subject = request.POST.get("subject", "").strip() or f"Request for Quotation: {result.product_title}"
        message = request.POST.get("message", "").strip()
        sent_to_email = request.POST.get("sent_to_email", "").strip() or result.external_company.email

        if not sent_to_email:
            sent_to_email = f"sales@{result.external_company.domain or 'company.com'}"

        inquiry = Inquiry.objects.create(
            company=company,
            search_result=result,
            subject=subject,
            message=message,
            sent_to_email=sent_to_email,
            status=Inquiry.Status.SENT,
        )

        # Dispatch real email
        sent_ok, email_msg = send_inquiry_email(inquiry, user=request.user)
        if not sent_ok:
            inquiry.status = Inquiry.Status.FAILED
            inquiry.save(update_fields=["status"])
            messages.warning(
                request,
                f"RFQ #{inquiry.id} recorded, but email dispatch failed ({email_msg}). You can follow up or retry."
            )
        else:
            messages.success(
                request,
                f"RFQ inquiry #{inquiry.id} successfully recorded and dispatched to {result.external_company.name} ({sent_to_email})!"
            )

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "inquiry_id": inquiry.id,
                "status": inquiry.status,
                "message": f"RFQ dispatched to {sent_to_email}",
            })

        return redirect("inquiries-list")

    redirect_to = request.META.get("HTTP_REFERER") or reverse("find-suppliers")
    return redirect(redirect_to)


@login_required
def inquiries_list_view(request):
    """
    RFQs & Inquiries Tracking View (Product-Wise):
    Lists quotation requests, recipient contacts, status, and dates.
    Supports:
    1. Product-wise grouping and filtering (?product_id=<id>).
    2. Searchable product combobox (A to Z) & quick tabs.
    3. Status filtering (all, sent, replied, failed) and text search.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Active company module permission required.")
        return redirect("dashboard")

    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])

    # Fetch company products sorted strictly A to Z
    products = list(Product.objects.filter(company=company, is_deleted=False).order_by(Lower("name"))) if company else []

    base_qs = Inquiry.objects.filter(company=company).select_related(
        "search_result",
        "search_result__external_company",
        "search_result__search_job",
        "search_result__search_job__product",
        "search_result__search_job__product__category",
    )

    # Filter by search term
    q = request.GET.get("q", "").strip()
    if q:
        base_qs = base_qs.filter(
            Q(subject__icontains=q)
            | Q(sent_to_email__icontains=q)
            | Q(search_result__product_title__icontains=q)
            | Q(search_result__external_company__name__icontains=q)
        )

    # Filter by status
    status_filter = request.GET.get("status", "").strip()
    if status_filter and status_filter in [s[0] for s in Inquiry.Status.choices]:
        base_qs = base_qs.filter(status=status_filter)

    # Count inquiries per product
    inquiry_counts = (
        base_qs
        .filter(search_result__search_job__product__isnull=False)
        .values("search_result__search_job__product_id")
        .annotate(total=Count("id"))
    )
    counts_map = {item["search_result__search_job__product_id"]: item["total"] for item in inquiry_counts}
    for p in products:
        p.inquiry_count = counts_map.get(p.id, 0)

    total_inquiries_count = base_qs.count()
    unassigned_count = base_qs.filter(search_result__search_job__product__isnull=True).count()

    product_id_str = request.GET.get("product_id", "").strip()
    selected_product = None
    is_unassigned_view = False
    is_single_product = False
    inquiries = []
    product_groups = []
    unassigned_group = None

    if product_id_str and product_id_str.isdigit():
        selected_product = Product.objects.filter(id=int(product_id_str), company=company, is_deleted=False).first()
        if selected_product:
            is_single_product = True
            inquiries = list(base_qs.filter(search_result__search_job__product=selected_product).order_by("-sent_at"))
    elif product_id_str == "unassigned":
        is_unassigned_view = True
        is_single_product = True
        inquiries = list(base_qs.filter(search_result__search_job__product__isnull=True).order_by("-sent_at"))
    else:
        # Group by product so it is NOT "all-in-one"
        for p in products:
            if p.inquiry_count > 0:
                p_inqs = list(base_qs.filter(search_result__search_job__product=p).order_by("-sent_at"))
                product_groups.append({
                    "product": p,
                    "inquiries": p_inqs,
                    "count": len(p_inqs),
                    "sent_count": sum(1 for i in p_inqs if i.status == "sent"),
                    "replied_count": sum(1 for i in p_inqs if i.status == "replied"),
                })
        if unassigned_count > 0:
            unassigned_inqs = list(base_qs.filter(search_result__search_job__product__isnull=True).order_by("-sent_at"))
            unassigned_group = {
                "title": "Other Inquiries",
                "inquiries": unassigned_inqs,
                "count": len(unassigned_inqs),
            }

    active_inqs = inquiries if is_single_product else list(base_qs)
    total_count = len(active_inqs)
    sent_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.SENT)
    replied_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.REPLIED)
    failed_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.FAILED)

    page_title = (
        (f"Buyer Inquiries - {selected_product.name}" if selected_product else "Buyer Inquiries (Product-Wise)")
        if is_seller_context else
        (f"Inquiries - {selected_product.name}" if selected_product else "Inquiries & RFQs (Product-Wise)")
    )

    return render(request, "leads/inquiries.html", {
        "products": products,
        "selected_product": selected_product,
        "selected_product_id": int(product_id_str) if product_id_str and product_id_str.isdigit() else None,
        "is_unassigned_view": is_unassigned_view,
        "is_single_product": is_single_product,
        "product_groups": product_groups,
        "unassigned_group": unassigned_group,
        "unassigned_count": unassigned_count,
        "total_inquiries_count": total_inquiries_count,
        "inquiries": inquiries,
        "total_count": total_count,
        "sent_count": sent_count,
        "replied_count": replied_count,
        "failed_count": failed_count,
        "current_status": status_filter,
        "search_query": q,
        "is_seller_context": is_seller_context,
        "company": company,
        "rbac": rbac,
        "page_title": page_title,
    })


@login_required
def inquiry_detail_view(request, pk):
    """
    Detailed RFQ & Commercial Inquiry Dossier:
    Displays sent specifications, supplier details, delivery status,
    and history of quotes recorded from this vendor.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Active company module permission required.")
        return redirect("dashboard")

    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])

    inquiry = get_object_or_404(Inquiry, pk=pk, company=company)
    ext_company = inquiry.search_result.external_company

    price_history = PriceHistory.objects.filter(
        external_company=ext_company
    ).order_by("-recorded_at")[:10]

    return render(request, "leads/inquiry_detail.html", {
        "inquiry": inquiry,
        "ext_company": ext_company,
        "price_history": price_history,
        "is_seller_context": is_seller_context,
        "company": company,
        "rbac": rbac,
        "page_title": f"Inquiry Dossier #{inquiry.id} — {ext_company.name}",
    })


@login_required
def record_inquiry_quote_view(request, pk):
    """
    Records a formal quotation received from the supplier in response to an RFQ.
    Updates inquiry status to 'REPLIED' and creates a PriceHistory record.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Active company module permission required.")
        return redirect("dashboard")

    inquiry = get_object_or_404(Inquiry, pk=pk, company=company)

    if request.method == "POST":
        quoted_price_str = request.POST.get("quoted_price", "").strip()
        currency = request.POST.get("currency", "INR").strip().upper()
        notes = request.POST.get("notes", "").strip()

        if quoted_price_str:
            try:
                price_val = Decimal(quoted_price_str)
                PriceHistory.objects.create(
                    external_company=inquiry.search_result.external_company,
                    item_name=inquiry.search_result.product_title,
                    price=price_val,
                    currency=currency,
                )
            except Exception:
                pass

        inquiry.status = Inquiry.Status.REPLIED
        if notes:
            inquiry.message = f"{inquiry.message}\n\n--- [SUPPLIER QUOTE RECORDED: {currency} {quoted_price_str}] ---\nNotes: {notes}"
        inquiry.save()

        messages.success(
            request,
            f"Supplier quote recorded successfully! Inquiry #{inquiry.id} marked as 'Replied'."
        )

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": True, "status": inquiry.status})

        return redirect("inquiry-detail", pk=inquiry.id)

    return redirect("inquiry-detail", pk=inquiry.id)


@login_required
def resend_inquiry_view(request, pk):
    """
    Resends or sends a follow-up email for an existing RFQ inquiry.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Active company module permission required.")
        return redirect("dashboard")

    inquiry = get_object_or_404(Inquiry, pk=pk, company=company)

    if request.method == "POST":
        followup_note = request.POST.get("followup_note", "").strip()
        if followup_note:
            inquiry.message = f"{inquiry.message}\n\n[FOLLOW-UP DISPATCHED]:\n{followup_note}"

        sent_ok, email_msg = send_inquiry_email(inquiry, user=request.user)
        if sent_ok:
            inquiry.status = Inquiry.Status.SENT
            inquiry.save()
            messages.success(request, f"Follow-up dispatched successfully to {inquiry.sent_to_email}!")
        else:
            messages.warning(request, f"Follow-up dispatch failed: {email_msg}")

    return redirect("inquiry-detail", pk=inquiry.id)


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


# ============================================================
# PRICE COMPARISON & REPORTS
# ============================================================

@login_required
def price_comparison_view(request):
    """
    Side-by-Side Price Comparison:
    Compares suppliers for buyer requirements.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Price Comparison is only available for Buyer accounts.")
        return redirect("dashboard")

    results = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.SUPPLIER,
    ).select_related("external_company", "search_job").order_by("price")[:30]

    return render(request, "leads/price_comparison.html", {
        "results": results,
        "company": company,
        "rbac": rbac,
        "page_title": "Price Comparison",
    })


@login_required
def export_reports_view(request):
    """
    Export Reports View (Product-Wise):
    Provides product-by-product and consolidated exports in CSV / Excel format.
    Supports downloading:
    - Buyer Leads CSV (by product or all)
    - Saved CRM Pipeline CSV (by product or all)
    - Inquiries & RFQs CSV (by product or all)
    - Product Performance Consolidated CSV
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])

    # Fetch company products sorted strictly A to Z
    products = list(Product.objects.filter(company=company, is_deleted=False).order_by(Lower("name"))) if company else []

    # Map product stats
    leads_base = SearchResult.objects.filter(search_job__company=company, result_type=SearchResult.ResultType.LEAD)
    saved_base = SavedItem.objects.filter(company=company, search_result__result_type=SearchResult.ResultType.LEAD)
    inquiries_base = Inquiry.objects.filter(company=company)

    leads_map = {item["search_job__product_id"]: item["total"] for item in leads_base.filter(search_job__product__isnull=False).values("search_job__product_id").annotate(total=Count("id"))}
    saved_map = {item["search_result__search_job__product_id"]: item["total"] for item in saved_base.filter(search_result__search_job__product__isnull=False).values("search_result__search_job__product_id").annotate(total=Count("id"))}
    inquiry_map = {item["search_result__search_job__product_id"]: item["total"] for item in inquiries_base.filter(search_result__search_job__product__isnull=False).values("search_result__search_job__product_id").annotate(total=Count("id"))}

    for p in products:
        p.lead_count = leads_map.get(p.id, 0)
        p.saved_count = saved_map.get(p.id, 0)
        p.inquiry_count = inquiry_map.get(p.id, 0)

    total_leads_count = leads_base.count()
    total_saved_count = saved_base.count()
    total_inquiry_count = inquiries_base.count()

    product_id_str = request.GET.get("product_id", "").strip()
    selected_product = None
    if product_id_str and product_id_str.isdigit():
        selected_product = Product.objects.filter(id=int(product_id_str), company=company, is_deleted=False).first()

    # Handle file download if requested (?download=1)
    if request.GET.get("download") == "1":
        export_type = request.GET.get("type", "leads")
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        writer = csv.writer(response)

        prod_slug = selected_product.name.replace(" ", "_") if selected_product else "All_Products"

        if export_type == "leads":
            response["Content-Disposition"] = f'attachment; filename="Buyer_Leads_{prod_slug}.csv"'
            writer.writerow(["Company Name", "Industry", "City", "Country", "Website", "Email", "Phone", "Target Product", "Category", "Fit Score (%)", "Fit Reason", "Signal", "Discovered At"])
            
            qs = leads_base.select_related("external_company", "search_job", "search_job__product", "search_job__product__category")
            if selected_product:
                qs = qs.filter(search_job__product=selected_product)
            
            for item in qs.order_by("-match_score", "-created_at"):
                writer.writerow([
                    item.external_company.name,
                    item.external_company.industry or "",
                    item.external_company.city or "",
                    item.external_company.country or "India",
                    item.external_company.website or "",
                    item.external_company.email or "",
                    item.external_company.phone or "",
                    item.search_job.product.name if item.search_job and item.search_job.product else item.product_title,
                    item.search_job.product.category.name if (item.search_job and item.search_job.product and item.search_job.product.category) else "General",
                    item.match_score,
                    item.match_reason or "",
                    item.need_signal or "",
                    item.created_at.strftime("%Y-%m-%d %H:%M"),
                ])
            return response

        elif export_type == "saved":
            response["Content-Disposition"] = f'attachment; filename="Saved_CRM_Pipeline_{prod_slug}.csv"'
            writer.writerow(["Buyer Company", "City", "Country", "Email", "Phone", "Target Product", "Pipeline Stage", "Assigned Sales Rep", "Private Notes", "Date Saved"])
            
            qs = saved_base.select_related("search_result", "search_result__external_company", "search_result__search_job__product", "assigned_to")
            if selected_product:
                qs = qs.filter(search_result__search_job__product=selected_product)

            for item in qs.order_by("-created_at"):
                writer.writerow([
                    item.search_result.external_company.name,
                    item.search_result.external_company.city or "",
                    item.search_result.external_company.country or "India",
                    item.search_result.external_company.email or "",
                    item.search_result.external_company.phone or "",
                    item.search_result.search_job.product.name if item.search_result.search_job and item.search_result.search_job.product else item.search_result.product_title,
                    item.get_status_display(),
                    item.assigned_to.get_full_name() if item.assigned_to else "Unassigned",
                    item.notes or "",
                    item.created_at.strftime("%Y-%m-%d %H:%M"),
                ])
            return response

        elif export_type == "inquiries":
            response["Content-Disposition"] = f'attachment; filename="Inquiries_Report_{prod_slug}.csv"'
            writer.writerow(["Inquiry ID", "Recipient Company", "Recipient Email", "Subject", "Product", "Status", "Date Sent", "Message / Note"])
            
            qs = inquiries_base.select_related("search_result", "search_result__external_company", "search_result__search_job__product")
            if selected_product:
                qs = qs.filter(search_result__search_job__product=selected_product)

            for inq in qs.order_by("-sent_at"):
                writer.writerow([
                    inq.id,
                    inq.search_result.external_company.name if inq.search_result else "External Vendor",
                    inq.sent_to_email,
                    inq.subject,
                    inq.search_result.search_job.product.name if (inq.search_result and inq.search_result.search_job and inq.search_result.search_job.product) else (inq.search_result.product_title if inq.search_result else "General"),
                    inq.get_status_display(),
                    inq.sent_at.strftime("%Y-%m-%d %H:%M") if inq.sent_at else "",
                    inq.message or "",
                ])
            return response

        elif export_type == "consolidated":
            response["Content-Disposition"] = f'attachment; filename="Consolidated_Product_Report.csv"'
            writer.writerow(["Product Name", "Category", "MOQ", "Unit", "Discovered Leads", "Saved in CRM", "Inquiries Count", "Availability", "Location"])
            for p in products:
                writer.writerow([
                    p.name,
                    p.category.name if p.category else "General",
                    p.minimum_order_quantity or p.moq or 1,
                    p.unit or "pcs",
                    p.lead_count,
                    p.saved_count,
                    p.inquiry_count,
                    p.get_availability_display() if hasattr(p, "get_availability_display") else p.availability,
                    p.location or "",
                ])
            return response

    return render(request, "leads/export_reports.html", {
        "products": products,
        "selected_product": selected_product,
        "selected_product_id": int(product_id_str) if product_id_str and product_id_str.isdigit() else None,
        "total_leads_count": total_leads_count,
        "total_saved_count": total_saved_count,
        "total_inquiry_count": total_inquiry_count,
        "is_seller_context": is_seller_context,
        "company": company,
        "rbac": rbac,
        "page_title": f"Export Reports - {selected_product.name}" if selected_product else "Export Reports (Product-Wise)",
    })
