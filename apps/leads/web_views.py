from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from apps.leads.models import SavedItem, Inquiry, PriceHistory
from apps.ai_search.models import SearchResult, ExternalCompany
from apps.companies.rbac import get_user_rbac_context


@login_required
def saved_suppliers_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Saved Suppliers is only available for Buyer accounts.")
        return redirect("dashboard")

    # Top suppliers or saved items
    suppliers = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.SUPPLIER,
    ).select_related("external_company", "search_job").order_by("-match_score")

    return render(request, "leads/saved_suppliers.html", {
        "suppliers": suppliers,
        "company": company,
        "rbac": rbac,
        "page_title": "Saved Suppliers",
    })


@login_required
def saved_leads_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Saved Leads is only available for Seller accounts.")
        return redirect("dashboard")

    saved_items = SavedItem.objects.filter(company=company).select_related("search_result", "search_result__external_company")

    return render(request, "leads/saved_leads.html", {
        "saved_items": saved_items,
        "company": company,
        "rbac": rbac,
        "page_title": "Saved Leads",
    })


@login_required
def inquiries_list_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    inquiries = Inquiry.objects.filter(company=company).select_related("search_result")

    return render(request, "leads/inquiries.html", {
        "inquiries": inquiries,
        "company": company,
        "rbac": rbac,
        "page_title": "RFQs & Inquiries",
    })


@login_required
def price_comparison_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Price Comparison is only available for Buyer accounts.")
        return redirect("dashboard")

    results = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.SUPPLIER,
    ).select_related("external_company").order_by("price")

    return render(request, "leads/price_comparison.html", {
        "results": results,
        "company": company,
        "rbac": rbac,
        "page_title": "Price Comparison",
    })


@login_required
def leads_list_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Sales Leads is only available for Seller accounts.")
        return redirect("dashboard")

    leads = SearchResult.objects.filter(
        search_job__company=company,
        result_type=SearchResult.ResultType.LEAD,
    ).select_related("external_company", "search_job").order_by("-match_score")

    return render(request, "leads/leads_list.html", {
        "leads": leads,
        "company": company,
        "rbac": rbac,
        "page_title": "Sales Leads",
    })


@login_required
def export_reports_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    return render(request, "leads/export_reports.html", {
        "company": company,
        "rbac": rbac,
        "page_title": "Export Reports",
    })
