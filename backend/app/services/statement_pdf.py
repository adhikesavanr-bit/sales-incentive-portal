"""One employee's monthly incentive statement, as a PDF.

Pure: takes the rows the dashboard already reads and returns bytes, so it can
be tested without BigQuery. Figures are the stored ones from
`v_incentive_current` — nothing is recalculated here.

Amounts are written "Rs" rather than with the rupee sign: the PDF base fonts
have no glyph for it, and embedding a font for one character is not worth it.
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
_QUALIFIED = colors.HexColor("#2F7D5E")
_DISQUALIFIED = colors.HexColor("#B4412F")


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


def _day(v: Any) -> str:
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    return str(v or "")[:10]


def month_label(period: str) -> str:
    y, m = period.split("-")
    return date(int(y), int(m), 1).strftime("%B %Y")


def build_statement(
    *,
    period: str,
    employee: dict,
    breakdown: dict,
    transactions: list[dict],
    generated_by: str,
) -> bytes:
    """Render the statement. `employee` needs employee_id and full_name."""
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9,
                          leading=12, textColor=_INK)
    muted = ParagraphStyle("muted", parent=body, textColor=_MUTED, fontSize=8)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=16,
                        leading=20, textColor=_INK, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11,
                        leading=14, textColor=_INK, spaceBefore=10, spaceAfter=6)
    cell = ParagraphStyle("cell", parent=body, fontSize=8, leading=10)

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
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
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
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), _MUTED),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, _RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(calc_table)
    if b.get("arpu_rule_applied"):
        story += [Spacer(1, 4), Paragraph(str(b["arpu_rule_applied"]), muted)]

    qualified = sum(1 for t in transactions if t.get("status") == "QUALIFIED")
    story.append(Paragraph(
        f"Sales ({len(transactions)} · {qualified} qualified, "
        f"{len(transactions) - qualified} disqualified)", h2,
    ))
    if transactions:
        rows = [["Date", "Plan", "College", "Coupon", "Net", "Status"]]
        for t in transactions:
            ok = t.get("status") == "QUALIFIED"
            rows.append([
                _day(t.get("payment_date_ist")),
                Paragraph(str(t.get("plan_title") or "—"), cell),
                Paragraph(str(t.get("college_name") or "—"), cell),
                Paragraph(str(t.get("coupon") or "—"), cell),
                rupees(t.get("net_amount")),
                Paragraph(
                    "Qualified" if ok
                    else str(t.get("reason_detail") or "Disqualified"),
                    ParagraphStyle("st", parent=cell,
                                   textColor=_QUALIFIED if ok else _DISQUALIFIED),
                ),
            ])
        sales = Table(
            rows,
            colWidths=[20 * mm, 35 * mm, 45 * mm, 27 * mm, 22 * mm, 31 * mm],
            repeatRows=1,
        )
        sales.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), _WASH),
            ("TEXTCOLOR", (0, 0), (-1, 0), _MUTED),
            ("ALIGN", (4, 0), (4, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, _RULE),
        ]))
        story.append(sales)
    else:
        story.append(Paragraph("No sales recorded for this month.", muted))

    stamp = datetime.now().strftime("%d %b %Y %H:%M")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
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
