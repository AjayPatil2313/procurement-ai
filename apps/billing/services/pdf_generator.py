import io
import logging
from decimal import Decimal
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
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
        if invoice.payment and "AUTOPAY" in invoice.payment.provider_reference:
            plan_name = invoice.subscription.plan_tier.name if (invoice.subscription and invoice.subscription.plan_tier) else "Pro"
            item_title = f"Procurement AI - Auto-Pay Subscription Renewal ({plan_name} Plan)"
        elif invoice.payment and ("RCH" in invoice.payment.provider_reference or "recharge" in invoice.payment.provider_reference.lower()) or "recharge" in str(invoice.invoice_number).lower():
            item_title = "Procurement AI - Prepaid Company Wallet Recharge (INR Balance)"
        elif "topup" in str(invoice.invoice_number).lower() or (invoice.payment and "TOPUP" in invoice.payment.provider_reference):
            item_title = "Procurement AI - Credit Top-Up Pack Recharge"
        elif invoice.subscription and invoice.subscription.plan_tier:
            item_title = f"Procurement AI - {invoice.subscription.plan_tier.name} Plan ({invoice.subscription.plan_tier.get_billing_cycle_display()})"

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

    @classmethod
    def generate_inquiry_quotation_pdf(cls, inquiry) -> bytes:
        """
        Generates an official, professional B2B Commercial Quotation / Proforma PDF.
        Suitable for customer dispatch, internal audit, and commercial contract negotiations.
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
            "QuoteTitle",
            parent=normal_style,
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#0f172a"),
        )
        subtitle_style = ParagraphStyle(
            "QuoteSubtitle",
            parent=normal_style,
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#64748b"),
        )
        heading_style = ParagraphStyle(
            "QuoteHeading",
            parent=normal_style,
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#1e293b"),
        )
        body_style = ParagraphStyle(
            "QuoteBody",
            parent=normal_style,
            fontName="Helvetica",
            fontSize=8.5,
            leading=11.5,
            textColor=colors.HexColor("#334155"),
        )
        bold_body = ParagraphStyle(
            "QuoteBoldBody",
            parent=normal_style,
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11.5,
            textColor=colors.HexColor("#0f172a"),
        )
        status_style = ParagraphStyle(
            "QuoteStatus",
            parent=normal_style,
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12,
            textColor=colors.HexColor("#16a34a") if inquiry.status in ["won", "replied"] else colors.HexColor("#2563eb"),
            alignment=2,
        )

        story = []

        # 1. Header with Company Branding & Quotation Metadata
        company = inquiry.company
        quote_no = f"QTN-{inquiry.id:05d}"
        quote_date = inquiry.sent_at.strftime("%d-%b-%Y") if inquiry.sent_at else "Immediate"

        header_data = [
            [
                Paragraph(f"<b>{company.name.upper()}</b>", title_style),
                Paragraph(f"<b>COMMERCIAL QUOTATION</b><br/><font color='#2563eb'>#{quote_no}</font>", status_style),
            ],
            [
                Paragraph("Commercial Sales & Procurement Operations", subtitle_style),
                Paragraph(f"Date: <b>{quote_date}</b> &bull; Valid: <b>30 Days</b>", subtitle_style),
            ],
        ]
        header_table = Table(header_data, colWidths=[310, 210])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 8))
        story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#2563eb"), spaceAfter=12))

        # 2. Issuer (Vendor/Seller) and Recipient (Customer/Buyer)
        issuer_addr = company.address or f"{company.city or ''}, {company.state or ''} {company.country or 'India'}".strip(", ") or "Corporate Office"
        issuer_gstin = company.gst_vat_no or "Registered B2B Enterprise"

        ext_comp = None
        if inquiry.search_result and inquiry.search_result.external_company:
            ext_comp = inquiry.search_result.external_company

        recip_name = ext_comp.name if ext_comp else "Prospective Commercial Partner"
        recip_addr = f"{ext_comp.city or ''}, {ext_comp.country or 'India'}".strip(", ") if ext_comp else "Verified Trade Entity"
        recip_contact = inquiry.sent_to_email or (ext_comp.email if ext_comp else "Direct Commercial Channel")

        issuer_html = (
            "<b>ISSUED BY (SUPPLIER / VENDOR):</b><br/>"
            f"<b>{company.name}</b><br/>"
            f"{issuer_addr}<br/>"
            f"Tax / GSTIN: <b>{issuer_gstin}</b><br/>"
            f"Email: {company.email or 'sales@enterprise.com'}<br/>"
            f"Phone: {company.phone or 'Direct Commercial Line'}"
        )

        customer_html = (
            "<b>PREPARED FOR (PROSPECTIVE BUYER):</b><br/>"
            f"<b>{recip_name}</b><br/>"
            f"{recip_addr}<br/>"
            f"Attention: Procurement & Purchasing Desk<br/>"
            f"Email: <b>{recip_contact}</b><br/>"
            f"Commercial Intent: Sourcing & Supply Contract"
        )

        info_table = Table(
            [[Paragraph(issuer_html, body_style), Paragraph(customer_html, body_style)]],
            colWidths=[260, 260],
        )
        info_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(info_table)
        story.append(Spacer(1, 14))

        # 3. Item & Commercial Specifications Table
        story.append(Paragraph("<b>1. SCOPE OF SUPPLY & COMMERCIAL LINE ITEMS</b>", heading_style))
        story.append(Spacer(1, 6))

        item_title = inquiry.subject
        specs = inquiry.message
        qty = Decimal("1.00")
        unit = "Lot"

        if inquiry.search_result:
            if inquiry.search_result.product_title:
                item_title = inquiry.search_result.product_title
            if inquiry.search_result.search_job:
                req = inquiry.search_result.search_job.requirement
                prod = inquiry.search_result.search_job.product
                if req:
                    qty = req.quantity
                    unit = req.unit or "pcs"
                    if req.specifications:
                        specs = req.specifications
                elif prod:
                    qty = prod.minimum_order_quantity if prod.minimum_order_quantity else Decimal("1.00")
                    unit = prod.unit or "pcs"
                    if prod.specifications:
                        specs = prod.specifications

        currency = inquiry.quoted_currency or "INR"
        unit_price = inquiry.quoted_price if inquiry.quoted_price else Decimal("0.00")
        subtotal = unit_price * qty
        tax_pct = Decimal("18.00")  # Standard GST
        tax_amount = (subtotal * tax_pct / Decimal("100.00")).quantize(Decimal("0.01"))
        total_amount = subtotal + tax_amount

        items_table_data = [
            [
                Paragraph("<b>Item #</b>", bold_body),
                Paragraph("<b>Description & Specifications</b>", bold_body),
                Paragraph("<b>Qty / Unit</b>", bold_body),
                Paragraph(f"<b>Unit Price ({currency})</b>", bold_body),
                Paragraph(f"<b>Total ({currency})</b>", bold_body),
            ],
            [
                Paragraph("01", body_style),
                Paragraph(f"<b>{item_title}</b><br/><font color='#64748b' size=7.5>{specs[:220]}...</font>", body_style),
                Paragraph(f"{qty:,.2f} {unit}", body_style),
                Paragraph(f"{unit_price:,.2f}", body_style),
                Paragraph(f"{subtotal:,.2f}", bold_body),
            ],
            [
                Paragraph("", body_style),
                Paragraph("<b>Subtotal (Net Commercial Value)</b>", bold_body),
                Paragraph("", body_style),
                Paragraph("", body_style),
                Paragraph(f"{currency} {subtotal:,.2f}", bold_body),
            ],
            [
                Paragraph("", body_style),
                Paragraph(f"Applicable Tax (GST @ {tax_pct}%)", body_style),
                Paragraph("", body_style),
                Paragraph("", body_style),
                Paragraph(f"{currency} {tax_amount:,.2f}", body_style),
            ],
            [
                Paragraph("", body_style),
                Paragraph("<b>TOTAL CONTRACT VALUE (INCL. TAX)</b>", bold_body),
                Paragraph("", body_style),
                Paragraph("", body_style),
                Paragraph(f"<b>{currency} {total_amount:,.2f}</b>", bold_body),
            ],
        ]

        items_table = Table(items_table_data, colWidths=[40, 240, 75, 80, 85])
        items_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#f1f5f9")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#f8fafc")),
            ("BACKGROUND", (0, 4), (-1, 4), colors.HexColor("#e2e8f0")),
        ]))
        for cell in items_table_data[0]:
            cell.style.textColor = colors.white

        story.append(items_table)
        story.append(Spacer(1, 14))

        # 4. Commercial Terms & Conditions
        story.append(Paragraph("<b>2. COMMERCIAL TERMS & FULFILLMENT CONDITIONS</b>", heading_style))
        story.append(Spacer(1, 6))

        deliv_terms = inquiry.delivery_terms or "Ex-Works / Door Delivery as agreed in contract"
        terms_html = (
            f"&bull; <b>Delivery / Logistics:</b> {deliv_terms}<br/>"
            "&bull; <b>Payment Terms:</b> 100% advance against Proforma Invoice / Confirmed Irrevocable L/C / Net 30 days as mutually finalized.<br/>"
            "&bull; <b>Inspection & Quality:</b> Factory standard test certificates and batch inspection reports provided prior to dispatch.<br/>"
            "&bull; <b>Quotation Validity:</b> Valid for 30 calendar days from the date of issue. Prices subject to raw material escalation thereafter.<br/>"
            "&bull; <b>Governing Law & Jurisdiction:</b> Governed under the commercial jurisdiction of the registered supplier headquarters."
        )

        terms_table = Table([[Paragraph(terms_html, body_style)]], colWidths=[520])
        terms_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(terms_table)
        story.append(Spacer(1, 18))

        # 5. Signatory & Digital Verification Block
        sign_data = [
            [
                Paragraph("<b>Customer Acceptance:</b><br/><br/><br/>___________________________________<br/>Authorized Customer Representative", subtitle_style),
                Paragraph(f"<b>For {company.name}:</b><br/><br/><br/>___________________________________<br/>Authorized Commercial Signatory", subtitle_style),
            ]
        ]
        sign_table = Table(sign_data, colWidths=[260, 260])
        sign_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(sign_table)
        story.append(Spacer(1, 10))
        story.append(Paragraph("<i>Note: This is a verified electronic commercial quotation generated via Procurement AI Platform.</i>", subtitle_style))

        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes

