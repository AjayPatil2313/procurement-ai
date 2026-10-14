import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect

from apps.billing.services.pdf_generator import PDFReportGenerator
from apps.companies.rbac import get_user_rbac_context
from apps.leads.models import Inquiry

logger = logging.getLogger(__name__)


@login_required
def inquiry_quotation_pdf_view(request, pk):
    """
    Renders and streams official B2B Commercial Quotation / Proforma PDF for an inquiry.
    Enforces multi-tenant data isolation and RBAC.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    inquiry = get_object_or_404(
        Inquiry.objects.select_related(
            "company",
            "search_result",
            "search_result__external_company",
            "search_result__search_job",
            "search_result__search_job__requirement",
            "search_result__search_job__product",
        ),
        id=pk,
    )

    # Permission check: super admin or same company
    if inquiry.company != company and not rbac["is_super_admin"]:
        messages.error(request, "Permission denied: You do not have access to this commercial inquiry.")
        return redirect("dashboard")

    try:
        pdf_bytes = PDFReportGenerator.generate_inquiry_quotation_pdf(inquiry)
        clean_company_name = "".join(c for c in (inquiry.company.name or "Company") if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
        filename = f"Quotation_QTN_{inquiry.id:05d}_{clean_company_name}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except Exception as e:
        logger.error("Error generating quotation PDF for inquiry #%s: %s", pk, e, exc_info=True)
        messages.error(request, f"Unable to generate quotation PDF: {str(e)}")
        redirect_to = request.META.get("HTTP_REFERER") or "inquiries-list"
        return redirect(redirect_to)
