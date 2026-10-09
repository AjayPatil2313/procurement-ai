import io
import logging
from decimal import Decimal
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)


class PDFReportGenerator:
    """
    High-performance, server-side vector PDF generator for:
    1. Official GST Compliant Tax Invoices
    2. Executive Procurement Evaluation Dossiers
    """

    @classmethod
    def generate_tax_invoice_pdf(cls, invoice) -> bytes:
        """
        Generates a formal, GST-compliant Tax Invoice PDF.
        Returns raw PDF bytes suitable for HTTP streaming or email attachment.
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()
        normal_style = styles["Normal"]

        title_style = ParagraphStyle(
            "InvoiceTitle",
            parent=normal_style,
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=colors.HexColor("#0f172a"),
        )
        subtitle_style = ParagraphStyle(
            "InvoiceSubtitle",
            parent=normal_style,
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#64748b"),
        )
        heading_style = ParagraphStyle(
            "SectionHeading",
            parent=normal_style,
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#1e293b"),
        )
        body_style = ParagraphStyle(
            "BodyText",
            parent=normal_style,
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#334155"),
        )
        bold_body = ParagraphStyle(
            "BoldBody",
            parent=normal_style,
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#0f172a"),
        )
        status_style = ParagraphStyle(
            "StatusStamp",
            parent=normal_style,
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            textColor=colors.HexColor("#16a34a") if invoice.status == "paid" else colors.HexColor("#ea580c"),
            alignment=2,
        )

        story = []

        # 1. Header: Platform Branding & Invoice Status
        header_table_data = [
            [
                Paragraph("<b>PROCUREMENT AI</b>", title_style),
                Paragraph(f"<b>STATUS: {invoice.get_status_display().upper()}</b>", status_style),
            ],
            [
                Paragraph("Next-Gen Autonomous B2B Sourcing Platform", subtitle_style),
                Paragraph(f"Invoice #: <b>{invoice.invoice_number}</b>", subtitle_style),
            ],
        ]
        header_table = Table(header_table_data, colWidths=[300, 220])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 10))
        story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#2563eb"), spaceAfter=14))

        # 2. Issuer & Customer Details (Bill From / Bill To)
        company = invoice.company
        issuer_info = (
            "<b>ISSUER / VENDOR:</b><br/>"
            "<b>Procurement AI Technologies India Pvt. Ltd.</b><br/>"
            "Cyber Gateway, HITEC City, Phase II<br/>"
            "Hyderabad, Telangana 500081, India<br/>"
            "GSTIN: <b>36AABCP9983K1Z8</b><br/>"
            "PAN: <b>AABCP9983K</b> | SAC Code: <b>998313</b><br/>"
            "Support: billing@procurement-ai.com"
        )

        customer_address = company.address or f"{company.address_line1} {company.address_line2}".strip() or "Registered Business Premises"
        customer_city = f"{company.city}, {company.state} {company.postal_code}".strip(", ") or "India"
        customer_gstin = company.gst_vat_no or "Unregistered / B2B Consumer"

        customer_info = (
            "<b>BILLED TO (CUSTOMER):</b><br/>"
            f"<b>{company.name}</b><br/>"
            f"{customer_address}<br/>"
            f"{customer_city}<br/>"
            f"Country: {company.country}<br/>"
            f"GSTIN / Tax ID: <b>{customer_gstin}</b><br/>"
            f"Contact: {company.email or 'N/A'}"
        )

        info_table = Table(
            [[Paragraph(issuer_info, body_style), Paragraph(customer_info, body_style)]],
            colWidths=[260, 260],
        )
        info_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
            ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#f1f5f9")),
            ("BOX", (0, 0), (0, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("BOX", (1, 0), (1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(info_table)
        story.append(Spacer(1, 14))

        # 3. Invoice Metadata (Issue Date, Due Date, Billing Period)
        meta_data = [
            [
                Paragraph(f"<b>Issue Date:</b> {invoice.issue_date}", body_style),
                Paragraph(f"<b>Due Date:</b> {invoice.due_date or invoice.issue_date}", body_style),
                Paragraph(f"<b>Period:</b> {invoice.billing_period_start} to {invoice.billing_period_end}", body_style),
                Paragraph(f"<b>Currency:</b> {invoice.currency}", body_style),
            ]
        ]
        meta_table = Table(meta_data, colWidths=[130, 130, 170, 90])
        meta_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 14))

        # 4. Itemized Charges Table
        item_title = "Subscription Service Allocation"
        if invoice.subscription and invoice.subscription.plan_tier:
            item_title = f"Procurement AI - {invoice.subscription.plan_tier.name} Plan ({invoice.subscription.plan_tier.get_billing_cycle_display()})"
        elif "topup" in str(invoice.invoice_number).lower() or (invoice.payment and "TOPUP" in invoice.payment.provider_reference):
            item_title = "Procurement AI - Credit Top-Up Pack Recharge"

        subtotal = invoice.subtotal
        cgst_rate = Decimal("9.00")
        sgst_rate = Decimal("9.00")
        half_tax = round(invoice.tax / Decimal("2"), 2)

        items_data = [
            [
                Paragraph("<b>Description of Services</b>", bold_body),
                Paragraph("<b>SAC Code</b>", bold_body),
                Paragraph("<b>Qty</b>", bold_body),
                Paragraph("<b>Rate</b>", bold_body),
                Paragraph("<b>Amount</b>", bold_body),
            ],
            [
                Paragraph(f"{item_title}<br/><font color='#64748b' size=7.5>IT Software Services (Cloud/SaaS B2B Procurement License)</font>", body_style),
                Paragraph("998313", body_style),
                Paragraph("1", body_style),
                Paragraph(f"{invoice.currency} {subtotal:.2f}", body_style),
                Paragraph(f"{invoice.currency} {subtotal:.2f}", bold_body),
            ],
        ]

        items_table = Table(items_data, colWidths=[240, 65, 45, 85, 85])
        items_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        # Change header text to white
        for cell in items_data[0]:
            cell.style.textColor = colors.white

        story.append(items_table)
        story.append(Spacer(1, 10))

        # 5. Tax Breakdown & Total Summary
        summary_data = [
            ["Taxable Subtotal:", f"{invoice.currency} {subtotal:.2f}"],
            ["CGST (9.0%):", f"{invoice.currency} {half_tax:.2f}"],
            ["SGST (9.0%):", f"{invoice.currency} {half_tax:.2f}"],
            ["Total GST (18.0%):", f"{invoice.currency} {invoice.tax:.2f}"],
            ["Grand Total:", f"{invoice.currency} {invoice.total_amount:.2f}"],
        ]
        summary_table = Table(summary_data, colWidths=[380, 140])
        summary_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("FONTNAME", (0, 0), (-1, -2), "Helvetica"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TEXTCOLOR", (0, -1), (-1, -1), colors.HexColor("#0f172a")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f1f5f9")),
            ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#0f172a")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 20))

        # 6. Payment Receipt & Signatory Box
        payment = invoice.payment
        pay_ref = payment.provider_reference if payment else "Direct SaaS Order"
        paid_stamp = f"Paid via {payment.provider if payment else 'Card'} | Ref: {pay_ref}"

        footer_data = [
            [
                Paragraph(f"<b>Payment Settlement:</b><br/>{paid_stamp}<br/><font color='#64748b'>This is a system generated tax invoice with electronic signature.</font>", subtitle_style),
                Paragraph("<b>For PROCUREMENT AI TECHNOLOGIES PVT. LTD.</b><br/><br/><br/><i>Authorized Signatory</i>", subtitle_style),
            ]
        ]
        footer_table = Table(footer_data, colWidths=[340, 180])
        footer_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ]))
        story.append(footer_table)

        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes

    @classmethod
    def generate_procurement_dossier_pdf(cls, search_job) -> bytes:
        """
        Generates an Executive Procurement Evaluation Dossier for a completed AI Search Job.
        Summarizes candidate suppliers, fit scores, MOQ, pricing, and AI matching rationale.
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "DossierTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#0f172a"),
        )
        subtitle_style = ParagraphStyle(
            "DossierSubtitle",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#64748b"),
        )
        h2_style = ParagraphStyle(
            "DossierH2",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=colors.HexColor("#1e293b"),
        )
        body_style = ParagraphStyle(
            "DossierBody",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#334155"),
        )
        badge_style = ParagraphStyle(
            "BadgeStyle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
            textColor=colors.HexColor("#2563eb"),
            alignment=2,
        )

        story = []

        # 1. Header
        target_name = (
            search_job.requirement.item_name if search_job.requirement
            else (search_job.product.name if search_job.product else search_job.search_query or "Sourcing Target")
        )
        job_type_label = search_job.get_job_type_display()

        header_table = Table([
            [
                Paragraph("<b>PROCUREMENT AI — EVALUATION DOSSIER</b>", title_style),
                Paragraph(f"<b>STATUS: {search_job.get_status_display().upper()}</b>", badge_style),
            ],
            [
                Paragraph(f"Executive Candidate Analysis for <b>{target_name}</b>", subtitle_style),
                Paragraph(f"Job ID: <b>#{search_job.id}</b> | {search_job.created_at.strftime('%Y-%m-%d %H:%M')}", subtitle_style),
            ],
        ], colWidths=[350, 170])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 8))
        story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0284c7"), spaceAfter=12))

        # 2. Executive Scope & Parameters
        req = search_job.requirement
        scope_lines = [
            f"<b>Initiating Company:</b> {search_job.company.name}",
            f"<b>Procurement Target:</b> {target_name}",
            f"<b>Target Category:</b> {req.category if req else 'Industrial / Commercial'}",
            f"<b>Target Budget:</b> {req.currency} {req.budget_max or req.target_price or 'Market Pricing'}" if req else "<b>Budget:</b> Flexible",
            f"<b>Required Quantity:</b> {req.quantity} {req.unit}" if req else "<b>Quantity:</b> Negotiable",
            f"<b>Destination Location:</b> {req.delivery_location or search_job.company.city or 'India'}" if req else "<b>Location:</b> Domestic / Global",
        ]
        scope_data = [
            [Paragraph(scope_lines[0], body_style), Paragraph(scope_lines[3], body_style)],
            [Paragraph(scope_lines[1], body_style), Paragraph(scope_lines[4], body_style)],
            [Paragraph(scope_lines[2], body_style), Paragraph(scope_lines[5], body_style)],
        ]
        scope_table = Table(scope_data, colWidths=[260, 260])
        scope_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#f1f5f9")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(scope_table)
        story.append(Spacer(1, 14))

        # 3. Candidate Summary Table
        story.append(Paragraph("<b>Discovered Commercial Candidates & AI Fit Ranking:</b>", h2_style))
        story.append(Spacer(1, 6))

        results = search_job.results.select_related("external_company").order_by("-match_score")[:15]
        candidate_rows = [
            [
                Paragraph("<b>Rank</b>", body_style),
                Paragraph("<b>Candidate Enterprise</b>", body_style),
                Paragraph("<b>Location</b>", body_style),
                Paragraph("<b>Offered Product</b>", body_style),
                Paragraph("<b>Price / MOQ</b>", body_style),
                Paragraph("<b>Fit Score</b>", body_style),
            ]
        ]

        rank = 1
        for res in results:
            comp = res.external_company
            loc = f"{comp.city or ''}, {comp.country or 'Global'}".strip(", ")
            price_str = f"{res.price_currency or ''} {res.price:.2f}" if res.price else "On Quotation"
            moq_str = f"MOQ: {res.moq}" if res.moq else ""
            score_color = "#16a34a" if res.match_score >= 80 else ("#2563eb" if res.match_score >= 60 else "#ea580c")
            score_p = Paragraph(f"<font color='{score_color}'><b>{res.match_score}%</b></font>", body_style)

            candidate_rows.append([
                Paragraph(f"#{rank}", body_style),
                Paragraph(f"<b>{comp.name}</b><br/><font color='#64748b' size=7>{comp.phone or comp.email or comp.domain}</font>", body_style),
                Paragraph(loc, body_style),
                Paragraph(res.product_title[:45], body_style),
                Paragraph(f"{price_str}<br/><font color='#64748b' size=7>{moq_str}</font>", body_style),
                score_p,
            ])
            rank += 1

        if len(candidate_rows) == 1:
            candidate_rows.append([Paragraph("No candidate records found for this search job.", body_style)] * 6)

        table = Table(candidate_rows, colWidths=[35, 145, 95, 120, 85, 40])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        for cell in candidate_rows[0]:
            cell.style.textColor = colors.white

        story.append(table)
        story.append(Spacer(1, 16))

        # 4. Committee Sign-off Section
        sign_table = Table([
            [
                Paragraph("<b>Prepared By:</b> Procurement AI Autonomous Pipeline", subtitle_style),
                Paragraph("<b>Procurement Committee Approval:</b> ___________________________", subtitle_style),
            ]
        ], colWidths=[260, 260])
        sign_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(sign_table)

        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes
