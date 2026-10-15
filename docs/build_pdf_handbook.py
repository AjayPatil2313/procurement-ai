"""
TradeConnect Master Handbook PDF Generator
Compiles all docs/*.md chapters into a publication-quality PDF handbook
using ReportLab 5.0 with custom running headers, footers, page numbering,
syntax styling, callout boxes, and formatted data tables.
"""

import os
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    KeepTogether,
    HRFlowable,
)
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas to dynamically compute and display running headers and
    footers with exact 'Page X of Y' page numbers.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages):
        # Skip header and footer on the cover page (Page 1)
        if self._pageNumber == 1:
            return

        self.saveState()
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#64748b"))

        # Top Running Header
        self.drawString(54, letter[1] - 36, "TradeConnect B2B AI Platform | Engineering & Architecture Handbook")
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.75)
        self.line(54, letter[1] - 42, letter[0] - 54, letter[1] - 42)

        # Bottom Running Footer
        self.setFont("Helvetica", 8)
        self.drawString(54, 34, "Confidential & Proprietary — For Internal Engineering & Technical Evaluation")
        page_str = f"Page {self._pageNumber} of {total_pages}"
        self.drawRightString(letter[0] - 54, 34, page_str)
        self.line(54, 44, letter[0] - 54, 44)

        self.restoreState()


def clean_markdown_inline(text):
    """Sanitizes inline markdown elements for ReportLab Paragraph rendering."""
    # Normalize br tags
    text = re.sub(r'<br\s*/?>', '__BR_TAG__', text, flags=re.IGNORECASE)
    # Replace markdown links [text](url) -> <u>text</u>
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'<u>\1</u>', text)
    # Bold **text** -> <b>text</b>
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    # Italic *text* -> <i>text</i>
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    # Inline code `code` -> __CODE_START__code__CODE_END__
    text = re.sub(r'`([^`]+)`', r'__CODE_START__\1__CODE_END__', text)

    # Escape ampersands
    text = re.sub(r'&(?!(?:amp|lt|gt|quot|apos);)', '&amp;', text)

    # Protect valid HTML tags
    valid_tags = ['b', 'i', 'u', '/b', '/i', '/u']
    for tag in valid_tags:
        text = text.replace(f'<{tag}>', f'__TAG_{tag.replace("/", "SLASH_")}__')

    # Escape raw angle brackets
    text = text.replace('<', '&lt;').replace('>', '&gt;')

    # Restore valid tags
    for tag in valid_tags:
        text = text.replace(f'__TAG_{tag.replace("/", "SLASH_")}__', f'<{tag}>')

    text = text.replace('__CODE_START__', '<font name="Courier" color="#0369a1"><b>').replace('__CODE_END__', '</b></font>')
    text = text.replace('__BR_TAG__', '<br/>')
    return text


def build_pdf():
    docs_dir = Path(__file__).resolve().parent
    output_pdf_path = docs_dir / "TradeConnect_Master_Handbook.pdf"

    doc = SimpleDocTemplate(
        str(output_pdf_path),
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
    )

    styles = getSampleStyleSheet()

    # Custom Typography Styles
    title_style = ParagraphStyle(
        "CoverTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=28,
        leading=34,
        textColor=colors.HexColor("#0f172a"),
        alignment=0,
    )
    subtitle_style = ParagraphStyle(
        "CoverSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=13,
        leading=18,
        textColor=colors.HexColor("#0284c7"),
        alignment=0,
    )
    cover_meta_style = ParagraphStyle(
        "CoverMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=15,
        textColor=colors.HexColor("#475569"),
    )
    h1_style = ParagraphStyle(
        "Heading1_Custom",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=14,
        spaceAfter=8,
        keepWithNext=True,
    )
    h2_style = ParagraphStyle(
        "Heading2_Custom",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#0369a1"),
        spaceBefore=12,
        spaceAfter=6,
        keepWithNext=True,
    )
    h3_style = ParagraphStyle(
        "Heading3_Custom",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#334155"),
        spaceBefore=8,
        spaceAfter=4,
        keepWithNext=True,
    )
    body_style = ParagraphStyle(
        "Body_Custom",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13.5,
        textColor=colors.HexColor("#1e293b"),
        spaceAfter=6,
    )
    bullet_style = ParagraphStyle(
        "Bullet_Custom",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#1e293b"),
        leftIndent=14,
        spaceAfter=3,
    )
    code_style = ParagraphStyle(
        "Code_Custom",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#0f172a"),
    )
    table_cell_style = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#1e293b"),
    )
    table_header_style = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#ffffff"),
    )
    callout_style = ParagraphStyle(
        "CalloutText",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=12.5,
        textColor=colors.HexColor("#0f172a"),
    )

    story = []

    # =========================================================================
    # 1. COVER PAGE
    # =========================================================================
    story.append(Spacer(1, 40))
    story.append(Paragraph("TRADE<font color='#0284c7'>CONNECT</font>", ParagraphStyle("Logo", fontName="Helvetica-Bold", fontSize=18, textColor=colors.HexColor("#0f172a"))))
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=3, color=colors.HexColor("#0284c7"), spaceAfter=20, spaceBefore=0))
    story.append(Spacer(1, 25))
    story.append(Paragraph("TradeConnect B2B AI Platform", title_style))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Comprehensive Engineering Architecture, System Design & Technical Interview Handbook", subtitle_style))
    story.append(Spacer(1, 35))

    overview_box_data = [[
        Paragraph(
            "<b>Executive Documentation Package:</b><br/>"
            "• Full-Stack Architecture (Django 5.2, DRF, MySQL 8.0, Redis)<br/>"
            "• Dual-Engine Cognitive AI Gateway (Google Gemini 2.5 Flash + OpenAI GPT-4o-mini Failover)<br/>"
            "• 49 Database Models with Complete Relational Schema Specifications<br/>"
            "• Multi-Tenant RBAC & Instant Company Suspension Middleware<br/>"
            "• 21-Event Centralized Notification Management Engine (RFC 8058 Compliant)<br/>"
            "• 25+ Technical Interview Deep-Dive Questions & Production Solutions",
            cover_meta_style
        )
    ]]
    t_cover = Table(overview_box_data, colWidths=[letter[0] - 108])
    t_cover.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f8fafc")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#cbd5e1")),
        ('LEFTPADDING', (0,0), (-1,-1), 16),
        ('RIGHTPADDING', (0,0), (-1,-1), 16),
        ('TOPPADDING', (0,0), (-1,-1), 14),
        ('BOTTOMPADDING', (0,0), (-1,-1), 14),
    ]))
    story.append(t_cover)

    story.append(Spacer(1, 80))

    meta_text = (
        "<b>Document Version:</b> 1.0 (Production Verified)<br/>"
        "<b>Published Date:</b> October 2026<br/>"
        "<b>Target Platforms:</b> Windows 11 / Linux Ubuntu 22.04 LTS / Docker<br/>"
        "<b>Test Verification Status:</b> 43/43 Automated Tests Passing (100% OK)<br/>"
        "<b>Author / Organization:</b> TradeConnect Core Engineering Team"
    )
    story.append(Paragraph(meta_text, cover_meta_style))
    story.append(PageBreak())

    # =========================================================================
    # 2. CHAPTER PARSER & INCLUSION
    # =========================================================================
    chapter_files = [
        "01_PROJECT_OVERVIEW.md",
        "02_DEVELOPMENT_ROADMAP.md",
        "03_SYSTEM_ARCHITECTURE.md",
        "04_DATABASE_DESIGN_AND_ERD.md",
        "05_CODE_EXPLANATION_AND_MODULES.md",
        "06_API_DOCUMENTATION.md",
        "07_SETUP_AND_INSTALLATION_GUIDE.md",
        "08_TESTING_AND_QUALITY_ASSURANCE.md",
        "09_SECURITY_AND_PERFORMANCE.md",
        "10_DEPLOYMENT_GUIDE.md",
        "11_TROUBLESHOOTING_AND_FAQ.md",
        "12_INTERVIEW_PREPARATION_HANDBOOK.md",
        "13_PROGRESS_TRACKER_AND_CHECKLIST.md",
    ]

    for fname in chapter_files:
        fpath = docs_dir / fname
        if not fpath.exists():
            continue

        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()

        lines = content.splitlines()
        in_code_block = False
        code_lines = []
        table_lines = []
        in_table = False

        for line in lines:
            # Code block toggles
            if line.strip().startswith("```"):
                if in_code_block:
                    in_code_block = False
                    code_text = "\n".join(code_lines)
                    code_lines = []
                    # Render code block box
                    c_para = Paragraph(
                        code_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>").replace(" ", "&nbsp;"),
                        code_style
                    )
                    t_box = Table([[c_para]], colWidths=[letter[0] - 108])
                    t_box.setStyle(TableStyle([
                        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f1f5f9")),
                        ('BOX', (0,0), (-1,-1), 0.75, colors.HexColor("#cbd5e1")),
                        ('LEFTPADDING', (0,0), (-1,-1), 10),
                        ('RIGHTPADDING', (0,0), (-1,-1), 10),
                        ('TOPPADDING', (0,0), (-1,-1), 8),
                        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
                    ]))
                    story.append(Spacer(1, 4))
                    story.append(t_box)
                    story.append(Spacer(1, 6))
                else:
                    in_code_block = True
                    code_lines = []
                continue

            if in_code_block:
                code_lines.append(line)
                continue

            # Table handling
            if "|" in line and "-|-" in line:
                # Separator line, continue
                continue
            elif line.strip().startswith("|") and line.strip().endswith("|"):
                in_table = True
                table_lines.append(line)
                continue
            else:
                if in_table:
                    # End of table, process collected lines
                    in_table = False
                    if table_lines:
                        raw_rows = []
                        for tl in table_lines:
                            cells = [c.strip() for c in tl.strip().strip("|").split("|")]
                            raw_rows.append(cells)
                        table_lines = []

                        max_cols = max(len(r) for r in raw_rows)
                        norm_rows = []
                        is_header = True
                        for r in raw_rows:
                            while len(r) < max_cols:
                                r.append("")
                            r_paras = []
                            for c in r:
                                p_style = table_header_style if is_header else table_cell_style
                                r_paras.append(Paragraph(clean_markdown_inline(c), p_style))
                            norm_rows.append(r_paras)
                            is_header = False

                        avail_width = letter[0] - 108
                        col_w = avail_width / max_cols
                        t_comp = Table(norm_rows, colWidths=[col_w] * max_cols)
                        t_comp.setStyle(TableStyle([
                            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#0f172a")),
                            ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor("#ffffff")),
                            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
                            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
                            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor("#ffffff"), colors.HexColor("#f8fafc")]),
                            ('TOPPADDING', (0,0), (-1,-1), 5),
                            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
                            ('LEFTPADDING', (0,0), (-1,-1), 6),
                            ('RIGHTPADDING', (0,0), (-1,-1), 6),
                        ]))
                        story.append(Spacer(1, 4))
                        story.append(t_comp)
                        story.append(Spacer(1, 6))

            stripped = line.strip()
            if not stripped:
                continue

            # Section Dividers
            if stripped == "---":
                story.append(Spacer(1, 4))
                story.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#e2e8f0"), spaceAfter=10, spaceBefore=4))
                continue

            # Headings
            if stripped.startswith("# "):
                h_text = clean_markdown_inline(stripped[2:].strip())
                story.append(PageBreak())
                story.append(Paragraph(h_text, h1_style))
                story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0284c7"), spaceAfter=12, spaceBefore=2))
            elif stripped.startswith("## "):
                h_text = clean_markdown_inline(stripped[3:].strip())
                story.append(Paragraph(h_text, h2_style))
            elif stripped.startswith("### "):
                h_text = clean_markdown_inline(stripped[4:].strip())
                story.append(Paragraph(h_text, h3_style))
            elif stripped.startswith("> [!"):
                # Callout block
                c_text = clean_markdown_inline(stripped)
                call_table = Table([[Paragraph(c_text, callout_style)]], colWidths=[letter[0] - 108])
                call_table.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f0fdf4")),
                    ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#86efac")),
                    ('LINEBEFORE', (0,0), (0,0), 3, colors.HexColor("#16a34a")),
                    ('LEFTPADDING', (0,0), (-1,-1), 10),
                    ('RIGHTPADDING', (0,0), (-1,-1), 10),
                    ('TOPPADDING', (0,0), (-1,-1), 6),
                    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                ]))
                story.append(call_table)
                story.append(Spacer(1, 4))
            elif stripped.startswith("- ") or stripped.startswith("* "):
                b_text = clean_markdown_inline(stripped[2:].strip())
                story.append(Paragraph(f"• {b_text}", bullet_style))
            elif re.match(r'^\d+\.\s', stripped):
                num_text = clean_markdown_inline(stripped)
                story.append(Paragraph(num_text, bullet_style))
            else:
                # Plain Body text
                story.append(Paragraph(clean_markdown_inline(stripped), body_style))

    # Build PDF with two-pass canvas
    print(f"Compiling PDF Handbook to {output_pdf_path}...")
    doc.build(story, canvasmaker=NumberedCanvas)
    print("PDF compilation completed successfully!")


if __name__ == "__main__":
    build_pdf()
