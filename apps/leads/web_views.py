import csv
import json
from datetime import timedelta
from decimal import Decimal
from django.db.models import Count, Q
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

    result = get_object_or_404(SearchResult, id=result_id)
    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])

    if request.method == "POST":
        default_subj = f"Commercial Proposal: {result.product_title}" if is_seller_context else f"Request for Quotation: {result.product_title}"
        subject = request.POST.get("subject", "").strip() or default_subj
        message = request.POST.get("message", "").strip()
        sent_to_email = request.POST.get("sent_to_email", "").strip() or result.external_company.email

        if not sent_to_email:
            sent_to_email = f"sales@{result.external_company.domain or 'company.com'}"

        target_price_str = request.POST.get("target_price", "").strip()
        delivery_terms = request.POST.get("delivery_terms", "").strip()

        quoted_price = None
        if target_price_str:
            try:
                quoted_price = Decimal(target_price_str)
            except Exception:
                pass

        inquiry = Inquiry.objects.create(
            company=company,
            search_result=result,
            subject=subject,
            message=message,
            sent_to_email=sent_to_email,
            status=Inquiry.Status.SENT,
            quoted_price=quoted_price,
            delivery_terms=delivery_terms,
        )

        # Log initial outbound message in conversation thread
        InquiryMessage.objects.create(
            inquiry=inquiry,
            sender=request.user,
            sender_name=request.user.get_full_name() or request.user.email,
            message_type=InquiryMessage.MessageType.OUTBOUND,
            subject=subject,
            body=message,
        )

        # Dispatch real email
        sent_ok, email_msg = send_inquiry_email(inquiry, user=request.user)
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

    # Fetch company products sorted strictly A to Z
    products = list(Product.objects.filter(company=company, is_deleted=False).order_by(Lower("name"))) if company else []

    base_qs = Inquiry.objects.filter(company=company).select_related(
        "search_result",
        "search_result__external_company",
        "search_result__search_job",
        "search_result__search_job__product",
        "search_result__search_job__product__category",
    )

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

    active_inqs = inquiries if is_single_product else list(base_qs)
    total_count = len(active_inqs)
    sent_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.SENT)
    discussion_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.IN_DISCUSSION)
    replied_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.REPLIED)
    won_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.WON)
    lost_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.LOST)
    failed_count = sum(1 for i in active_inqs if i.status == Inquiry.Status.FAILED)
    conversion_rate = round(((won_count + replied_count) / total_count * 100), 1) if total_count else 0.0

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

        if body:
            InquiryMessage.objects.create(
                inquiry=inquiry,
                sender=request.user,
                sender_name=sender_name,
                message_type=msg_type,
                subject=f"Re: {inquiry.subject}",
                body=body,
            )

            if new_status and new_status in [s[0] for s in Inquiry.Status.choices]:
                inquiry.status = new_status
            elif inquiry.status == Inquiry.Status.SENT and msg_type == InquiryMessage.MessageType.OUTBOUND:
                inquiry.status = Inquiry.Status.IN_DISCUSSION
            inquiry.save(update_fields=["status", "updated_at"])

            if send_email_copy:
                send_inquiry_email(inquiry, user=request.user)

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

            # Log system event in messages
            InquiryMessage.objects.create(
                inquiry=inquiry,
                sender=request.user,
                sender_name="System",
                message_type=InquiryMessage.MessageType.SYSTEM,
                subject=f"Status Changed to {inquiry.get_status_display()}",
                body=f"Commercial status transitioned from '{old_status}' to '{inquiry.get_status_display()}' by {request.user.get_full_name() or request.user.email}.",
            )

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

        # Log quote into conversation thread
        quote_body = f"Quoted Terms Recorded:\n• Price: {currency} {quoted_price_str}\n"
        if delivery_terms:
            quote_body += f"• Delivery/Lead time: {delivery_terms}\n"
        if notes:
            quote_body += f"• Notes: {notes}"

        InquiryMessage.objects.create(
            inquiry=inquiry,
            sender=request.user,
            sender_name=inquiry.search_result.external_company.name,
            message_type=InquiryMessage.MessageType.INBOUND,
            subject=f"Quotation Submitted: {currency} {quoted_price_str}",
            body=quote_body,
        )

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


# ============================================================
# PRICE COMPARISON & DYNAMIC EXPORT REPORTS
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
    Interactive & Dynamic Export Reports Dashboard (Product-Wise):
    Provides real-time interactive report previews, dynamic multi-dimension filtering,
    summary KPI metrics, and export engines in CSV, JSON, and printable format.
    Supports:
    1. Report Types:
       - 'leads': Buyer Leads & Market Discovery Intelligence
       - 'inquiries': Commercial Inquiries & Outreach Audit
    2. Dynamic Dimension Filters:
       - product_id: Filter by product or consolidated catalog
       - date_range: all, 7d, 30d, this_month
       - min_score: 0, 70, 85 (for leads)
       - status: inquiry status (for inquiries)
       - q: real-time keyword search
    3. Live On-Page Preview Table with sorting and KPI metrics.
    4. Downloads in CSV, JSON, or Printable dossier.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    is_seller_context = request.path.startswith("/sales/") or (rbac["can_view_seller"] and not rbac["can_view_buyer"])

    # Fetch company products sorted strictly A to Z
    products = list(Product.objects.filter(company=company, is_deleted=False).order_by(Lower("name"))) if company else []

    # Active Report Type
    report_type = request.GET.get("type") or request.GET.get("report_type") or "leads"
    valid_types = ["leads", "inquiries"]
    if report_type not in valid_types:
        report_type = "leads"

    # Base QuerySets
    leads_base = SearchResult.objects.filter(search_job__company=company, result_type=SearchResult.ResultType.LEAD)
    inquiries_base = Inquiry.objects.filter(company=company)

    # Calculate catalog overview stats for products
    leads_map = {item["search_job__product_id"]: item["total"] for item in leads_base.filter(search_job__product__isnull=False).values("search_job__product_id").annotate(total=Count("id"))}
    inquiry_map = {item["search_result__search_job__product_id"]: item["total"] for item in inquiries_base.filter(search_result__search_job__product__isnull=False).values("search_result__search_job__product_id").annotate(total=Count("id"))}

    for p in products:
        p.lead_count = leads_map.get(p.id, 0)
        p.inquiry_count = inquiry_map.get(p.id, 0)

    total_leads_count = leads_base.count()
    total_inquiry_count = inquiries_base.count()

    product_id_str = request.GET.get("product_id", "").strip()
    selected_product = None
    if product_id_str and product_id_str.isdigit():
        selected_product = Product.objects.filter(id=int(product_id_str), company=company, is_deleted=False).first()

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
    leads_filtered = leads_base.select_related("external_company", "search_job", "search_job__product", "search_job__product__category")
    inquiries_filtered = inquiries_base.select_related("search_result", "search_result__external_company", "search_result__search_job__product")

    if selected_product:
        leads_filtered = leads_filtered.filter(search_job__product=selected_product)
        inquiries_filtered = inquiries_filtered.filter(search_result__search_job__product=selected_product)

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

    # Dynamic KPI Calculations for current scope
    kpi_leads_count = leads_filtered.count()
    kpi_inquiries_count = inquiries_filtered.count()

    # Leads KPIs
    kpi_high_fit_count = leads_filtered.filter(match_score__gte=80).count()
    kpi_high_fit_pct = round((kpi_high_fit_count / kpi_leads_count * 100), 1) if kpi_leads_count else 0.0
    verified_leads = leads_filtered.filter(Q(external_company__email__isnull=False) | Q(external_company__phone__isnull=False)).exclude(external_company__email="").count()
    kpi_contact_verified_pct = round((verified_leads / kpi_leads_count * 100), 1) if kpi_leads_count else 0.0

    # Inquiries KPIs
    inq_replied_count = inquiries_filtered.filter(status=Inquiry.Status.REPLIED).count()
    inq_discussion_count = inquiries_filtered.filter(status=Inquiry.Status.IN_DISCUSSION).count()
    inq_won_count = inquiries_filtered.filter(status=Inquiry.Status.WON).count()
    kpi_inquiry_response_rate = round(((inq_replied_count + inq_won_count) / kpi_inquiries_count * 100), 1) if kpi_inquiries_count else 0.0
    kpi_inquiry_won_rate = round((inq_won_count / kpi_inquiries_count * 100), 1) if kpi_inquiries_count else 0.0

    prod_slug = selected_product.name.replace(" ", "_") if selected_product else "All_Products"

    # HANDLE EXPORT DOWNLOADS (CSV, JSON, or PRINT)
    if request.GET.get("download") == "1":
        export_format = request.GET.get("format", "csv").lower()

        # 1. JSON Export
        if export_format == "json":
            json_data = []
            if report_type == "leads":
                for item in leads_filtered.order_by("-match_score", "-created_at")[:500]:
                    json_data.append({
                        "company_name": item.external_company.name,
                        "industry": item.external_company.industry or "",
                        "city": item.external_company.city or "",
                        "country": item.external_company.country or "India",
                        "website": item.external_company.website or "",
                        "email": item.external_company.email or "",
                        "phone": item.external_company.phone or "",
                        "target_product": item.search_job.product.name if (item.search_job and item.search_job.product) else item.product_title,
                        "fit_score": item.match_score,
                        "fit_reason": item.match_reason or "",
                        "intent_signal": item.need_signal or "",
                        "discovered_at": item.created_at.isoformat(),
                    })
            else:  # inquiries
                for inq in inquiries_filtered.order_by("-sent_at")[:500]:
                    json_data.append({
                        "inquiry_id": inq.id,
                        "recipient_company": inq.search_result.external_company.name if inq.search_result else "",
                        "recipient_email": inq.sent_to_email,
                        "subject": inq.subject,
                        "status": inq.status,
                        "quoted_price": str(inq.quoted_price) if inq.quoted_price else "",
                        "quoted_currency": inq.quoted_currency or "INR",
                        "delivery_terms": inq.delivery_terms or "",
                        "sent_at": inq.sent_at.isoformat() if inq.sent_at else "",
                    })

            response = HttpResponse(json.dumps(json_data, indent=2), content_type="application/json")
            response["Content-Disposition"] = f'attachment; filename="Report_{report_type}_{prod_slug}.json"'
            return response

        # 2. CSV Export
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        writer = csv.writer(response)

        if report_type == "leads":
            response["Content-Disposition"] = f'attachment; filename="Buyer_Leads_{prod_slug}.csv"'
            writer.writerow(["Company Name", "Industry", "City", "Country", "Website", "Email", "Phone", "Target Product", "Category", "Fit Score (%)", "Fit Reason", "Intent Signal", "Discovered At"])
            for item in leads_filtered.order_by("-match_score", "-created_at")[:1000]:
                writer.writerow([
                    item.external_company.name,
                    item.external_company.industry or "",
                    item.external_company.city or "",
                    item.external_company.country or "India",
                    item.external_company.website or "",
                    item.external_company.email or "",
                    item.external_company.phone or "",
                    item.search_job.product.name if (item.search_job and item.search_job.product) else item.product_title,
                    item.search_job.product.category.name if (item.search_job and item.search_job.product and item.search_job.product.category) else "General",
                    item.match_score,
                    item.match_reason or "",
                    item.need_signal or "",
                    item.created_at.strftime("%Y-%m-%d %H:%M"),
                ])
            return response

        else:  # inquiries
            response["Content-Disposition"] = f'attachment; filename="Inquiries_Audit_{prod_slug}.csv"'
            writer.writerow(["Inquiry ID", "Recipient Company", "Recipient Email", "Subject", "Product", "Status", "Quoted Price", "Currency", "Delivery Terms", "Date Sent"])
            for inq in inquiries_filtered.order_by("-sent_at")[:1000]:
                writer.writerow([
                    inq.id,
                    inq.search_result.external_company.name if inq.search_result else "External Vendor",
                    inq.sent_to_email,
                    inq.subject,
                    inq.search_result.search_job.product.name if (inq.search_result and inq.search_result.search_job and inq.search_result.search_job.product) else (inq.search_result.product_title if inq.search_result else "General"),
                    inq.get_status_display(),
                    inq.quoted_price or "",
                    inq.quoted_currency or "INR",
                    inq.delivery_terms or "",
                    inq.sent_at.strftime("%Y-%m-%d %H:%M") if inq.sent_at else "",
                ])
            return response

    # BUILD LIVE PREVIEW RECORDS FOR ON-SCREEN DISPLAY
    if report_type == "leads":
        preview_records = list(leads_filtered.order_by("-match_score", "-created_at")[:40])
    else:  # inquiries
        preview_records = list(inquiries_filtered.order_by("-sent_at")[:40])

    return render(request, "leads/export_reports.html", {
        "products": products,
        "selected_product": selected_product,
        "selected_product_id": int(product_id_str) if product_id_str and product_id_str.isdigit() else None,
        "report_type": report_type,
        "preview_records": preview_records,
        "total_leads_count": total_leads_count,
        "total_inquiry_count": total_inquiry_count,
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
        "date_range": date_range,
        "min_score": min_score,
        "status_filter": status_filter,
        "q_search": q_search,
        "is_seller_context": is_seller_context,
        "is_print_mode": request.GET.get("format") == "print" or request.GET.get("print") == "1",
        "company": company,
        "rbac": rbac,
        "page_title": (
            (f"Buyer Leads - {selected_product.name}" if selected_product else "Buyer Leads Discovery Report")
            if report_type == "leads" else
            (f"Inquiries Audit - {selected_product.name}" if selected_product else "Inquiries & Outreach Audit Report")
        ),
    })

