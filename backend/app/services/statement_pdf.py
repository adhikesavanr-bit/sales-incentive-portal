"""One employee's monthly incentive statement, as a PDF.

Pure: takes the rows the dashboard already reads and returns bytes, so it can
be tested without BigQuery. Figures are the stored ones from
`v_incentive_current` — nothing is recalculated here.

Amounts are written "Rs" rather than with the rupee sign: the PDF base fonts
have no glyph for it, and embedding a font for one character is not worth it.
The statement is set in Times, the base-14 Times New Roman equivalent, so no
font file ships with the backend.

Only the incentive itself is included: the headline and how it was
calculated. Coupon analysis and sale-level detail stay on the dashboard.
"""
from __future__ import annotations

import io
from datetime import date, datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_INK = colors.HexColor("#1F2933")
_MUTED = colors.HexColor("#5F6B7A")
_RULE = colors.HexColor("#DDE1E6")
_WASH = colors.HexColor("#F4F6F8")

# Times New Roman, as the PDF base fonts carry it.
_FONT = "Times-Roman"
_BOLD = "Times-Bold"


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def indian_grouping(n: float, decimals: int = 0) -> str:
    """12345678.9 -> '1,23,45,679' — lakh/crore grouping, as Finance reads it."""
    neg = n < 0
    whole, _, frac = f"{abs(n):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    out = whole + (f".{frac}" if frac else "")
    return f"-{out}" if neg else out


def rupees(v: Any) -> str:
    return f"Rs {indian_grouping(_num(v))}"


def pct(v: Any, decimals: int = 1) -> str:
    return f"{_num(v) * 100:.{decimals}f}%"


def month_label(period: str) -> str:
    y, m = period.split("-")
    return date(int(y), int(m), 1).strftime("%B %Y")


def build_statement(
    *,
    period: str,
    employee: dict,
    breakdown: dict,
    generated_by: str,
) -> bytes:
    """Render the statement. `employee` needs employee_id and full_name."""
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName=_FONT, fontSize=9,
                          leading=12, textColor=_INK)
    muted = ParagraphStyle("muted", parent=body, textColor=_MUTED, fontSize=8)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName=_BOLD, fontSize=16,
                        leading=20, textColor=_INK, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName=_BOLD, fontSize=11,
                        leading=14, textColor=_INK, spaceBefore=10, spaceAfter=6)

    b = breakdown
    story: list = [
        Paragraph("Incentive statement", h1),
        Paragraph(
            f"{employee.get('full_name') or employee['employee_id']} · "
            f"{employee['employee_id']} · {month_label(period)}",
            body,
        ),
        Paragraph(
            " · ".join(
                x for x in (
                    b.get("designation") or employee.get("designation"),
                    b.get("region") or employee.get("region"),
                    employee.get("zone"),
                ) if x
            ) or "&nbsp;",
            muted,
        ),
        Spacer(1, 8),
    ]

    headline = Table(
        [
            ["Incentive earned", "Payable this month", "Carried forward"],
            [rupees(b.get("total_incentive")), rupees(b.get("net_payable")),
             rupees(b.get("accumulation"))],
        ],
        colWidths=[60 * mm] * 3,
    )
    headline.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _WASH),
        ("TEXTCOLOR", (0, 0), (-1, 0), _MUTED),
        ("FONTNAME", (0, 0), (-1, -1), _FONT),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (-1, 1), _BOLD),
        ("FONTSIZE", (0, 1), (-1, 1), 13),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("LINEAFTER", (0, 0), (1, -1), 0.5, _RULE),
    ]))
    story.append(headline)

    story.append(Paragraph("How this was calculated", h2))
    calc = [
        ("Target units", indian_grouping(_num(b.get("target_units")))),
        ("Units sold", indian_grouping(_num(b.get("gross_units")))),
        ("Units counted (after disqualification)",
         indian_grouping(_num(b.get("achieved_units")))),
        ("Target revenue", rupees(b.get("target_revenue"))),
        ("Qualified revenue (excl. GST)", rupees(b.get("qualified_revenue"))),
        ("Disqualified revenue", rupees(b.get("disqualified_revenue"))),
        ("Revenue achievement", pct(b.get("revenue_pct"))),
        ("Unit achievement", pct(b.get("unit_pct"))),
        ("ARPU", rupees(b.get("arpu"))),
        ("Achievement used", pct(b.get("base_pct"))),
        ("Incentive rate", pct(b.get("bde_rate"), 2)),
        ("BDE incentive", rupees(b.get("bde_incentive"))),
    ]
    if _num(b.get("submanager_incentive")):
        calc.append(("Sub-manager incentive", rupees(b.get("submanager_incentive"))))
    calc_table = Table(calc, colWidths=[110 * mm, 70 * mm])
    calc_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), _MUTED),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (1, 0), (1, -1), _BOLD),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, _RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(calc_table)
    if b.get("arpu_rule_applied"):
        story += [Spacer(1, 4), Paragraph(str(b["arpu_rule_applied"]), muted)]

    stamp = datetime.now().strftime("%d %b %Y %H:%M")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(_FONT, 7)
        canvas.setFillColor(_MUTED)
        canvas.drawString(
            15 * mm, 10 * mm,
            f"Generated {stamp} by {generated_by}. Figures as stored for "
            f"{month_label(period)}; amounts exclude GST.",
        )
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=18 * mm,
        title=f"Incentive statement {employee['employee_id']} {period}",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
