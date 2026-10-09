import logging
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages

from apps.companies.context_processors import get_user_rbac_context
from apps.billing.models import Invoice
from apps.ai_search.models import SearchJob
from apps.billing.services.pdf_generator import PDFReportGenerator

logger = logging.getLogger(__name__)


@login_required
def invoice_pdf_view(request, invoice_id):
    """
    Renders and streams official GST Tax Invoice as a PDF download.
    """
    invoice = get_object_or_404(Invoice.objects.select_related("company", "subscription__plan_tier", "payment"), id=invoice_id)

    active_comp_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=active_comp_id)

    # Permission check: super admin or same company user
    if invoice.company != rbac["company"] and not rbac["is_super_admin"]:
        messages.error(request, "Permission denied: You do not have access to this invoice.")
        return redirect("subscription-billing-web")

    try:
        pdf_bytes = PDFReportGenerator.generate_tax_invoice_pdf(invoice)
        filename = f"Tax_Invoice_{invoice.invoice_number}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except Exception as e:
        logger.error("Error generating invoice PDF #%s: %s", invoice_id, e, exc_info=True)
        messages.error(request, f"Unable to generate invoice PDF: {str(e)}")
        return redirect("subscription-billing-web")


@login_required
def search_job_dossier_pdf_view(request, job_id):
    """
    Renders and streams executive procurement evaluation dossier for a SearchJob as a PDF.
    """
    job = get_object_or_404(
        SearchJob.objects.select_related("company", "requirement", "product").prefetch_related("results__external_company"),
        id=job_id
    )

    active_comp_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=active_comp_id)

    if job.company != rbac["company"] and not rbac["is_super_admin"]:
        messages.error(request, "Permission denied: You do not have access to this procurement search job.")
        return redirect("dashboard")

    try:
        pdf_bytes = PDFReportGenerator.generate_procurement_dossier_pdf(job)
        filename = f"Procurement_Dossier_Job_{job.id}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except Exception as e:
        logger.error("Error generating dossier PDF for Job #%s: %s", job_id, e, exc_info=True)
        messages.error(request, f"Unable to generate dossier PDF: {str(e)}")
        return redirect("dashboard")
