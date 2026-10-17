import os
import re
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
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
            self.draw_page_number(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # Header (pages > 1)
        if self._pageNumber > 1:
            self.drawString(54, 750, "AI Procurement & Sales Platform — Notification System Verification Report")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(54, 742, 558, 742)
            
        # Footer
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(54, 45, 558, 45)
        
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 32, page_str)
        self.drawString(54, 32, "CONFIDENTIAL — FOR INTERNAL QA & RELEASE GOVERNANCE ONLY")
        self.restoreState()


def build_pdf():
    md_path = os.path.join("docs", "notification_testing_report.md")
    pdf_path = os.path.join("docs", "notification_testing_report.pdf")

    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=6,
    )
    meta_style = ParagraphStyle(
        "DocMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#475569"),
        spaceAfter=14,
    )
    h1_style = ParagraphStyle(
        "H1",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#1E293B"),
        spaceBefore=14,
        spaceAfter=8,
        keepWithNext=True,
    )
    h2_style = ParagraphStyle(
        "H2",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=15,
        textColor=colors.HexColor("#2563EB"),
        spaceBefore=10,
        spaceAfter=6,
        keepWithNext=True,
    )
    h3_style = ParagraphStyle(
        "H3",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#334155"),
        spaceBefore=8,
        spaceAfter=4,
        keepWithNext=True,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#334155"),
        spaceAfter=6,
    )
    bullet_style = ParagraphStyle(
        "Bullet",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#334155"),
        leftIndent=14,
        spaceAfter=3,
    )
    code_style = ParagraphStyle(
        "CodeBlock",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=6,
    )
    table_cell = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#1E293B"),
    )
    table_cell_bold = ParagraphStyle(
        "TableCellBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#1E293B"),
    )
    table_hdr = ParagraphStyle(
        "TableHdr",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=10,
        textColor=colors.white,
    )

    story = []

    lines = md_text.split("\n")
    i = 0
    in_code = False
    code_lines = []

    while i < len(lines):
        line = lines[i]

        # Code block toggle
        if line.strip().startswith("```"):
            if in_code:
                # end code
                code_text = "<br/>".join([c.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(" ", "&nbsp;") for c in code_lines])
                code_tbl = Table(
                    [[Paragraph(code_text, code_style)]],
                    colWidths=[504],
                )
                code_tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F5F9")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]))
                story.append(code_tbl)
                story.append(Spacer(1, 6))
                code_lines = []
                in_code = False
            else:
                in_code = True
                code_lines = []
            i += 1
            continue

        if in_code:
            code_lines.append(line)
            i += 1
            continue

        # Markdown tables
        if line.strip().startswith("|") and line.strip().endswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            
            # parse table
            raw_rows = []
            for tline in table_lines:
                cells = [c.strip() for c in tline.split("|")[1:-1]]
                # skip separator row
                if all(re.match(r"^:?-+:?$", c) for c in cells if c):
                    continue
                raw_rows.append(cells)

            if raw_rows:
                num_cols = len(raw_rows[0])
                table_data = []
                for r_idx, row in enumerate(raw_rows):
                    row_data = []
                    for c_idx, cell in enumerate(row):
                        # clean markdown bold
                        cleaned = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", cell)
                        cleaned = re.sub(r"`(.*?)`", r"<font name='Courier'>\1</font>", cleaned)
                        if r_idx == 0:
                            p = Paragraph(cleaned, table_hdr)
                        elif c_idx == 0 or "PASS" in cleaned or "GO" in cleaned:
                            p = Paragraph(cleaned, table_cell_bold)
                        else:
                            p = Paragraph(cleaned, table_cell)
                        row_data.append(p)
                    table_data.append(row_data)

                # auto width distribution
                col_width = 504.0 / num_cols
                if num_cols == 9:
                    # Specific widths for verification matrix
                    col_widths = [65, 45, 55, 30, 30, 95, 65, 35, 84]
                elif num_cols == 3:
                    col_widths = [140, 100, 264]
                elif num_cols == 6:
                    col_widths = [55, 45, 55, 120, 110, 119]
                else:
                    col_widths = [col_width] * num_cols

                t = Table(table_data, colWidths=col_widths, repeatRows=1)
                t_style = [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
                for r_i in range(1, len(table_data)):
                    if r_i % 2 == 0:
                        t_style.append(("BACKGROUND", (0, r_i), (-1, r_i), colors.HexColor("#F8FAFC")))
                t.setStyle(TableStyle(t_style))
                story.append(t)
                story.append(Spacer(1, 8))
            continue

        stripped = line.strip()

        # Headers
        if stripped.startswith("# "):
            title_text = stripped[2:].strip()
            story.append(Paragraph(title_text, title_style))
            story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#2563EB"), spaceAfter=10))
        elif stripped.startswith("## "):
            h1_text = stripped[3:].strip()
            story.append(Paragraph(h1_text, h1_style))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceAfter=6))
        elif stripped.startswith("### "):
            h2_text = stripped[4:].strip()
            story.append(Paragraph(h2_text, h2_style))
        elif stripped.startswith("#### "):
            h3_text = stripped[5:].strip()
            story.append(Paragraph(h3_text, h3_style))
        elif stripped.startswith("- ") or stripped.startswith("* "):
            cleaned = stripped[2:].strip()
            cleaned = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", cleaned)
            cleaned = re.sub(r"`(.*?)`", r"<font name='Courier'>\1</font>", cleaned)
            story.append(Paragraph(f"• {cleaned}", bullet_style))
        elif re.match(r"^\d+\.\s", stripped):
            num_match = re.match(r"^(\d+\.\s)(.*)", stripped)
            prefix = num_match.group(1)
            cleaned = num_match.group(2)
            cleaned = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", cleaned)
            cleaned = re.sub(r"`(.*?)`", r"<font name='Courier'>\1</font>", cleaned)
            story.append(Paragraph(f"<b>{prefix}</b>{cleaned}", bullet_style))
        elif stripped == "---":
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#E2E8F0"), spaceAfter=6))
        elif stripped:
            cleaned = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", stripped)
            cleaned = re.sub(r"`(.*?)`", r"<font name='Courier'>\1</font>", cleaned)
            if cleaned.startswith("<b>Document ID:</b>") or "Final Release Recommendation:" in cleaned:
                story.append(Paragraph(cleaned, meta_style))
            else:
                story.append(Paragraph(cleaned, body_style))
        else:
            story.append(Spacer(1, 3))

        i += 1

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"PDF successfully built: {pdf_path}")

if __name__ == "__main__":
    build_pdf()
