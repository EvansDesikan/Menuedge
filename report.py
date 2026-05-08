"""
MenuEdge PDF Report Generator
Clean, minimal design — ReportLab
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, PageBreak
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from datetime import datetime

# ── Palette ───────────────────────────────────────────────────────────────────
DARK     = colors.HexColor("#1C1816")
TERRA    = colors.HexColor("#C9622A")
GOLD     = colors.HexColor("#C9963E")
CREAM    = colors.HexColor("#F5EDD8")
MUTED    = colors.HexColor("#9A8A78")
PAGE_BG  = colors.HexColor("#FAFAF8")
BORDER   = colors.HexColor("#E5DDD0")
WHITE    = colors.white

# Classification
CLS_COLOR = {
    "Star":      colors.HexColor("#1A7A3C"),
    "Plowhorse": colors.HexColor("#1A5FA0"),
    "Puzzle":    colors.HexColor("#9A6200"),
    "Dog":       colors.HexColor("#A02020"),
}
CLS_BG = {
    "Star":      colors.HexColor("#EDF7F1"),
    "Plowhorse": colors.HexColor("#E8F0FA"),
    "Puzzle":    colors.HexColor("#FEF6E7"),
    "Dog":       colors.HexColor("#FDEAEA"),
}
# Text symbols that render correctly in Helvetica
CLS_ICON = {
    "Star":      "*",
    "Plowhorse": "~",
    "Puzzle":    "?",
    "Dog":       "x",
}

ACTION_COLOR = {
    "Keep & Promote":      colors.HexColor("#1A7A3C"),
    "Reprice":             colors.HexColor("#9A6200"),
    "Rewrite Description": colors.HexColor("#1A5FA0"),
    "Reposition on Menu":  colors.HexColor("#6A3A9A"),
    "Remove or Redesign":  colors.HexColor("#A02020"),
}
PRIORITY_COLOR = {
    "High":   colors.HexColor("#A02020"),
    "Medium": colors.HexColor("#9A6200"),
    "Low":    colors.HexColor("#1A5FA0"),
}


def _fmt(val, sym="$"):
    try:
        return f"{sym}{float(val):.2f}"
    except Exception:
        return str(val)


# ── Header / footer ───────────────────────────────────────────────────────────
class PageDecorator:
    def __init__(self, restaurant="", date_str=""):
        self.restaurant = restaurant
        self.date_str   = date_str or datetime.now().strftime("%d %b %Y")

    def __call__(self, canv, doc):
        if doc.page == 1:
            return
        canv.saveState()
        w, h = A4

        # Header — thin gold rule with brand
        canv.setStrokeColor(GOLD)
        canv.setLineWidth(1)
        canv.line(16*mm, h - 12*mm, w - 16*mm, h - 12*mm)

        canv.setFont("Helvetica-Bold", 9)
        canv.setFillColor(TERRA)
        canv.drawString(16*mm, h - 9.5*mm, "MenuEdge")
        canv.setFont("Helvetica", 8)
        canv.setFillColor(MUTED)
        canv.drawString(40*mm, h - 9.5*mm, "AI Menu Intelligence")

        if self.restaurant:
            canv.setFont("Helvetica", 8)
            canv.setFillColor(MUTED)
            canv.drawRightString(w - 16*mm, h - 9.5*mm, self.restaurant)

        # Footer — thin rule with page number
        canv.setStrokeColor(BORDER)
        canv.setLineWidth(0.5)
        canv.line(16*mm, 13*mm, w - 16*mm, 13*mm)

        canv.setFont("Helvetica", 7)
        canv.setFillColor(MUTED)
        canv.drawString(16*mm, 9*mm, f"Confidential  ·  menuedge.app  ·  {self.date_str}")
        canv.drawRightString(w - 16*mm, 9*mm, f"Page {doc.page}")

        canv.restoreState()


# ── Cover page ────────────────────────────────────────────────────────────────
def draw_cover(canv, doc, data):
    canv.saveState()
    w, h = A4
    summary   = data.get("restaurant_summary", {})
    rest_name = summary.get("restaurant_name", "") or "Your Restaurant"
    date_str  = datetime.now().strftime("%d %B %Y")

    # Dark background
    canv.setFillColor(DARK)
    canv.rect(0, 0, w, h, fill=1, stroke=0)

    # Terra left accent strip
    canv.setFillColor(TERRA)
    canv.rect(0, 0, 5*mm, h, fill=1, stroke=0)

    # Gold top rule
    canv.setStrokeColor(GOLD)
    canv.setLineWidth(1)
    canv.line(16*mm, h - 24*mm, w - 16*mm, h - 24*mm)

    # Brand
    canv.setFont("Helvetica-Bold", 14)
    canv.setFillColor(GOLD)
    canv.drawString(16*mm, h - 18*mm, "MenuEdge")
    canv.setFont("Helvetica", 9)
    canv.setFillColor(MUTED)
    canv.drawString(46*mm, h - 18*mm, "AI Menu Intelligence")
    canv.setFillColor(MUTED)
    canv.drawRightString(w - 16*mm, h - 18*mm, date_str)

    # Title
    canv.setFont("Helvetica-Bold", 38)
    canv.setFillColor(CREAM)
    canv.drawString(16*mm, h * 0.58, "Menu")
    canv.drawString(16*mm, h * 0.58 - 14*mm, "Optimisation")
    canv.setFillColor(TERRA)
    canv.drawString(16*mm, h * 0.58 - 28*mm, "Report")

    # Divider
    canv.setStrokeColor(TERRA)
    canv.setLineWidth(2)
    canv.line(16*mm, h * 0.58 - 32*mm, 70*mm, h * 0.58 - 32*mm)

    # Prepared for
    canv.setFont("Helvetica", 10)
    canv.setFillColor(MUTED)
    canv.drawString(16*mm, h * 0.58 - 40*mm, "Prepared for")
    canv.setFont("Helvetica-Bold", 15)
    canv.setFillColor(CREAM)
    canv.drawString(16*mm, h * 0.58 - 50*mm, rest_name)

    # Stat pills — bottom
    uplift = data.get("estimated_total_monthly_uplift", "—")
    total  = str(summary.get("total_items", "—"))
    health = summary.get("overall_health", "—")
    pills  = [("Est. Monthly Uplift", uplift), ("Total Dishes", total), ("Menu Health", health)]

    pw   = 54*mm
    px   = 16*mm
    py   = 22*mm

    for label, val in pills:
        canv.setFillColor(colors.HexColor("#252018"))
        canv.roundRect(px, py, pw, 16*mm, 2.5*mm, fill=1, stroke=0)
        canv.setStrokeColor(GOLD)
        canv.setLineWidth(1)
        canv.line(px + 3*mm, py + 14.5*mm, px + pw - 3*mm, py + 14.5*mm)
        canv.setFont("Helvetica-Bold", 13)
        canv.setFillColor(GOLD)
        canv.drawCentredString(px + pw / 2, py + 7*mm, str(val))
        canv.setFont("Helvetica", 7)
        canv.setFillColor(MUTED)
        canv.drawCentredString(px + pw / 2, py + 2.5*mm, label.upper())
        px += pw + 4*mm

    # Confidential note
    canv.setFont("Helvetica", 7)
    canv.setFillColor(colors.HexColor("#4A3E35"))
    canv.drawCentredString(w / 2, 10*mm, "CONFIDENTIAL  ·  menuedge.app")

    canv.restoreState()


# ── Style helpers ─────────────────────────────────────────────────────────────
W = A4[0] - 32*mm   # usable width (16mm margins each side)

def _h1(text):
    return Paragraph(text, ParagraphStyle(
        "H1", fontName="Helvetica-Bold", fontSize=16,
        textColor=DARK, spaceBefore=7*mm, spaceAfter=1*mm
    ))

def _h2(text):
    return Paragraph(text, ParagraphStyle(
        "H2", fontName="Helvetica-Bold", fontSize=11,
        textColor=DARK, spaceBefore=5*mm, spaceAfter=1*mm
    ))

def _cat(text):
    """Category label above a table."""
    return Paragraph(text, ParagraphStyle(
        "CAT", fontName="Helvetica-Bold", fontSize=9.5,
        textColor=GOLD, spaceBefore=5*mm, spaceAfter=1*mm
    ))

def _body(text, size=9.5, color=DARK, indent=0):
    return Paragraph(text, ParagraphStyle(
        "BD", fontName="Helvetica", fontSize=size,
        textColor=color, spaceBefore=0, spaceAfter=2*mm,
        leftIndent=indent, leading=13
    ))

def _caption(text):
    return Paragraph(text, ParagraphStyle(
        "CP", fontName="Helvetica-Oblique", fontSize=8,
        textColor=MUTED, spaceBefore=0, spaceAfter=3*mm, leading=11
    ))

def _rule(color=BORDER, thick=0.5):
    return HRFlowable(width="100%", thickness=thick, color=color,
                      spaceAfter=4*mm, spaceBefore=4*mm)

def _section_title(text):
    """Clean section title — bold text + gold underline."""
    p = Paragraph(text, ParagraphStyle(
        "ST", fontName="Helvetica-Bold", fontSize=13,
        textColor=DARK, spaceBefore=7*mm, spaceAfter=0
    ))
    rule = HRFlowable(width="100%", thickness=1.5, color=GOLD,
                      spaceAfter=4*mm, spaceBefore=1.5*mm)
    return [p, rule]


# ── Main generator ────────────────────────────────────────────────────────────
def generate_pdf(data: dict, output_path: str, tier: str = "pro"):
    """
    tier="pro"     → full report (all sections, AI-rewritten descriptions)
    tier="starter" → Top Revenue Opportunities + Full Item Analysis only,
                     original menu descriptions shown (no AI rewrites)
    """
    is_pro = (tier != "starter")

    summary   = data.get("restaurant_summary", {})
    rest_name = summary.get("restaurant_name", "") or "Your Restaurant"
    curr_sym  = summary.get("currency_symbol", "$")
    date_str  = datetime.now().strftime("%d %B %Y")

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=16*mm, rightMargin=16*mm,
        topMargin=20*mm, bottomMargin=18*mm,
        title="MenuEdge Report"
    )

    decorator = PageDecorator(restaurant=rest_name, date_str=date_str)
    story     = []

    # Cover spacer (drawn via onFirstPage callback)
    story.append(Spacer(1, 1))
    story.append(PageBreak())

    # ── EXECUTIVE SUMMARY (pro only) ──────────────────────────────────────────
    if not is_pro:
        # Starter: skip to Top Revenue Opportunities directly
        pass
    else:
        story.extend(_section_title("Executive Summary"))

    if is_pro:
        insight = summary.get("headline_insight", "")
        if insight:
            quote_tbl = Table([[_body(
                f'<i>"{insight}"</i>', size=10, color=DARK
            )]], colWidths=[W])
            quote_tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (-1,-1), colors.HexColor("#FBF6EE")),
                ("LINEBEFORE",    (0,0), (0,-1),  3, GOLD),
                ("TOPPADDING",    (0,0), (-1,-1), 4*mm),
                ("BOTTOMPADDING", (0,0), (-1,-1), 4*mm),
                ("LEFTPADDING",   (0,0), (-1,-1), 5*mm),
                ("RIGHTPADDING",  (0,0), (-1,-1), 5*mm),
            ]))
            story.append(quote_tbl)
            story.append(Spacer(1, 4*mm))

        # Stat grid — 3 columns, 2 rows
        stats = [
            ("Total Items",         str(summary.get("total_items", "—"))),
            ("Price Range",         summary.get("price_range", "—")),
            ("Avg Price",           _fmt(summary.get("avg_price", 0), curr_sym)),
            ("Est. Food Cost",      f"{summary.get('estimated_avg_food_cost_pct','—')}%"),
            ("Overall Health",      summary.get("overall_health", "—")),
            ("Est. Monthly Uplift", data.get("estimated_total_monthly_uplift", "—")),
        ]

        def _stat_cell(label, val, accent=False):
            val_hex   = "#C9622A" if accent else "#1C1816"
            muted_hex = "#9A8A78"
            return Paragraph(
                f'<font size="15" color="{val_hex}"><b>{val}</b></font>'
                f'<br/><font size="7" color="{muted_hex}">{label.upper()}</font>',
                ParagraphStyle("S", fontName="Helvetica", fontSize=15,
                               textColor=TERRA if accent else DARK,
                               alignment=TA_CENTER, leading=21)
            )

        col = W / 3
        grid = Table(
            [[_stat_cell(k, v, accent=(i == 5)) for i, (k, v) in enumerate(stats[:3])],
             [_stat_cell(k, v, accent=(i+3 == 5)) for i, (k, v) in enumerate(stats[3:])]],
            colWidths=[col, col, col]
        )
        grid.setStyle(TableStyle([
            ("BOX",           (0,0), (-1,-1), 0.5, BORDER),
            ("INNERGRID",     (0,0), (-1,-1), 0.5, BORDER),
            ("TOPPADDING",    (0,0), (-1,-1), 4.5*mm),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4.5*mm),
            ("ALIGN",         (0,0), (-1,-1), "CENTER"),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
            ("BACKGROUND",    (2,1), (2,1),   colors.HexColor("#FBF6EE")),
            ("LINEABOVE",     (2,1), (2,1),   1.5, TERRA),
        ]))
        story.append(grid)
        story.append(Spacer(1, 6*mm))

    # ── TOP OPPORTUNITIES ─────────────────────────────────────────────────────
    top_wins = data.get("top_wins", [])
    if top_wins:
        story.extend(_section_title("Top Revenue Opportunities"))

        for i, win in enumerate(top_wins[:5], 1):
            uplift = win.get("estimated_monthly_uplift", "")
            num = Paragraph(
                f'<b>{i}</b>',
                ParagraphStyle("N", fontName="Helvetica-Bold", fontSize=20,
                               alignment=TA_CENTER, textColor=TERRA)
            )
            content = [
                Paragraph(win.get("title", ""), ParagraphStyle(
                    "WT", fontName="Helvetica-Bold", fontSize=10,
                    textColor=DARK, spaceAfter=1*mm, leading=13)),
                _body(win.get("detail", ""), size=9, color=MUTED),
            ]
            if uplift:
                content.append(Paragraph(
                    f'<font color="#1A7A3C"><b>{uplift}</b></font>',
                    ParagraphStyle("WU", fontName="Helvetica-Bold", fontSize=8.5,
                                   spaceBefore=1*mm)
                ))
            row_tbl = Table([[num, content]], colWidths=[16*mm, W - 16*mm])
            row_tbl.setStyle(TableStyle([
                ("LINEBEFORE",    (0,0), (0,-1),  2.5, TERRA),
                ("LINEBELOW",     (0,-1),(-1,-1),  0.4, BORDER),
                ("TOPPADDING",    (0,0), (-1,-1), 3.5*mm),
                ("BOTTOMPADDING", (0,0), (-1,-1), 3.5*mm),
                ("LEFTPADDING",   (0,0), (-1,-1), 3.5*mm),
                ("RIGHTPADDING",  (0,0), (-1,-1), 3.5*mm),
                ("VALIGN",        (0,0), (-1,-1), "TOP"),
            ]))
            story.append(row_tbl)
            story.append(Spacer(1, 1.5*mm))
        story.append(Spacer(1, 4*mm))

    # ── ACTION SUMMARY (pro only) ─────────────────────────────────────────────
    if is_pro:
        promote = data.get("items_to_promote", [])
        remove  = data.get("items_to_remove",  [])
        if promote or remove:
            story.extend(_section_title("Action Summary"))

            def _list_col(items, label, label_color, bg, accent_col):
                rows = [Paragraph(label, ParagraphStyle(
                    "LH", fontName="Helvetica-Bold", fontSize=9,
                    textColor=label_color, spaceAfter=2*mm))]
                for it in items:
                    rows.append(_body(f"  {it}", size=9, color=DARK))
                return rows

            action_tbl = Table([[
                _list_col(promote, "Promote",           CLS_COLOR["Star"], CLS_BG["Star"], colors.HexColor("#4ADE80")),
                _list_col(remove,  "Consider Removing", CLS_COLOR["Dog"],  CLS_BG["Dog"],  colors.HexColor("#FC8181")),
            ]], colWidths=[W / 2 - 2*mm, W / 2 - 2*mm])
            action_tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (0,0), CLS_BG["Star"]),
                ("BACKGROUND",    (1,0), (1,0), CLS_BG["Dog"]),
                ("BOX",           (0,0), (-1,-1), 0.5, BORDER),
                ("LINEAFTER",     (0,0), (0,-1),  0.5, BORDER),
                ("TOPPADDING",    (0,0), (-1,-1), 4*mm),
                ("BOTTOMPADDING", (0,0), (-1,-1), 4*mm),
                ("LEFTPADDING",   (0,0), (-1,-1), 5*mm),
                ("VALIGN",        (0,0), (-1,-1), "TOP"),
            ]))
            story.append(action_tbl)
            story.append(Spacer(1, 4*mm))

    # ── LAYOUT RECOMMENDATIONS (pro only) ────────────────────────────────────
    if is_pro:
        layout_recs = data.get("layout_recommendations", [])
        if layout_recs:
            story.extend(_section_title("Menu Layout Recommendations"))
            for rec in layout_recs:
                t = Table([[
                    Paragraph("•", ParagraphStyle("AR", fontName="Helvetica-Bold",
                        fontSize=12, textColor=TERRA, alignment=TA_CENTER)),
                    _body(rec, size=9, color=DARK),
                ]], colWidths=[7*mm, W - 7*mm])
                t.setStyle(TableStyle([
                    ("VALIGN",        (0,0), (-1,-1), "TOP"),
                    ("TOPPADDING",    (0,0), (-1,-1), 1.5*mm),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 1.5*mm),
                    ("LEFTPADDING",   (0,0), (-1,-1), 2*mm),
                ]))
                story.append(t)
            story.append(Spacer(1, 4*mm))

    # ── SEASONAL SUGGESTIONS (pro only) ──────────────────────────────────────
    if is_pro:
        seasonal = data.get("seasonal_suggestions", {})
        if seasonal:
            story.extend(_section_title("Seasonal Menu Suggestions"))

            season   = seasonal.get("season", "")
            loc_ctx  = seasonal.get("location_context", "")
            opp      = seasonal.get("seasonal_opportunity", "")
            add_now  = seasonal.get("add_now", [])
            rot_out  = seasonal.get("rotate_out", [])

            # Season + context banner
            if season or loc_ctx:
                banner_text = f'<b>{season}</b>'
                if loc_ctx:
                    banner_text += f'  —  {loc_ctx}'
                banner = Table([[_body(banner_text, size=9.5, color=DARK)]], colWidths=[W])
                banner.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0), (-1,-1), colors.HexColor("#FBF6EE")),
                    ("LINEBEFORE",    (0,0), (0,-1),  3, GOLD),
                    ("TOPPADDING",    (0,0), (-1,-1), 3.5*mm),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 3.5*mm),
                    ("LEFTPADDING",   (0,0), (-1,-1), 5*mm),
                    ("RIGHTPADDING",  (0,0), (-1,-1), 5*mm),
                ]))
                story.append(banner)
                story.append(Spacer(1, 4*mm))

            # Add Now + Rotate Out — side by side
            if add_now or rot_out:
                def _season_col(items, label, label_color, bg, key_field, extra_field=None):
                    rows = [Paragraph(label, ParagraphStyle(
                        "SLH", fontName="Helvetica-Bold", fontSize=9,
                        textColor=label_color, spaceAfter=2*mm))]
                    for it in items:
                        dish_line = f"<b>{it.get('dish', '')}</b>"
                        if extra_field and it.get(extra_field):
                            dish_line += f"  <font size='8' color='#9A8A78'>{it[extra_field]}</font>"
                        rows.append(Paragraph(dish_line, ParagraphStyle(
                            "SD", fontName="Helvetica", fontSize=9,
                            textColor=DARK, spaceAfter=0.5*mm, leading=12)))
                        rows.append(_body(it.get("reason", ""), size=8.5, color=MUTED))
                    return rows

                season_tbl = Table([[
                    _season_col(add_now, "Add to Menu Now",   CLS_COLOR["Star"], CLS_BG["Star"], "dish", "suggested_price"),
                    _season_col(rot_out, "Consider Rotating Out", CLS_COLOR["Dog"],  CLS_BG["Dog"],  "dish"),
                ]], colWidths=[W / 2 - 2*mm, W / 2 - 2*mm])
                season_tbl.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0), (0,0), CLS_BG["Star"]),
                    ("BACKGROUND",    (1,0), (1,0), CLS_BG["Dog"]),
                    ("BOX",           (0,0), (-1,-1), 0.5, BORDER),
                    ("LINEAFTER",     (0,0), (0,-1),  0.5, BORDER),
                    ("TOPPADDING",    (0,0), (-1,-1), 4*mm),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 4*mm),
                    ("LEFTPADDING",   (0,0), (-1,-1), 5*mm),
                    ("VALIGN",        (0,0), (-1,-1), "TOP"),
                ]))
                story.append(season_tbl)
                story.append(Spacer(1, 4*mm))

            # Seasonal opportunity callout
            if opp:
                opp_tbl = Table([[
                    Paragraph("Opportunity", ParagraphStyle(
                        "OL", fontName="Helvetica-Bold", fontSize=8,
                        textColor=TERRA, spaceAfter=1*mm)),
                    _body(opp, size=9, color=DARK),
                ]], colWidths=[24*mm, W - 24*mm])
                opp_tbl.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0), (-1,-1), colors.HexColor("#FDF3E3")),
                    ("LINEBEFORE",    (0,0), (0,-1),  3, TERRA),
                    ("TOPPADDING",    (0,0), (-1,-1), 3.5*mm),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 3.5*mm),
                    ("LEFTPADDING",   (0,0), (-1,-1), 4*mm),
                    ("RIGHTPADDING",  (0,0), (-1,-1), 4*mm),
                    ("VALIGN",        (0,0), (-1,-1), "TOP"),
                ]))
                story.append(opp_tbl)
                story.append(Spacer(1, 5*mm))

    # ── FULL ITEM ANALYSIS ────────────────────────────────────────────────────
    story.append(_rule(BORDER))
    story.extend(_section_title("Full Item Analysis"))
    story.append(_caption("Every dish classified, repriced, and actioned by MenuEdge AI."))

    items      = data.get("items", [])
    categories = {}
    for item in items:
        cat = item.get("category", "Other")
        categories.setdefault(cat, []).append(item)

    # Col widths: Dish | Curr | Rec | Change | Priority | Action  (CLASS removed)
    CW = [W*0.32, W*0.10, W*0.10, W*0.10, W*0.10, W*0.28]

    S_HL = ParagraphStyle("HL", fontName="Helvetica-Bold", fontSize=7,
                          textColor=CREAM, alignment=TA_LEFT,   leading=9)
    S_HC = ParagraphStyle("HC", fontName="Helvetica-Bold", fontSize=7,
                          textColor=CREAM, alignment=TA_CENTER, leading=9)
    S_L  = ParagraphStyle("SL", fontName="Helvetica",      fontSize=8.5,
                          textColor=DARK,  alignment=TA_LEFT,   leading=11)
    S_C  = ParagraphStyle("SC", fontName="Helvetica",      fontSize=8.5,
                          textColor=DARK,  alignment=TA_CENTER, leading=11)
    S_R  = ParagraphStyle("SR", fontName="Helvetica",      fontSize=8.5,
                          textColor=DARK,  alignment=TA_RIGHT,  leading=11)

    for cat, cat_items in categories.items():
        story.append(_cat(cat.upper()))

        headers = [
            Paragraph("DISH",     S_HL),
            Paragraph("CURRENT",  S_HC),
            Paragraph("REC.",     S_HC),
            Paragraph("CHANGE",   S_HC),
            Paragraph("PRIORITY", S_HC),
            Paragraph("ACTION",   S_HL),
        ]
        rows   = [headers]
        styles = [
            ("BACKGROUND",    (0,0), (-1,0),  DARK),
            ("LINEABOVE",     (0,0), (-1,0),  1.5, TERRA),
            ("BOX",           (0,0), (-1,-1), 0.5, BORDER),
            ("INNERGRID",     (0,0), (-1,-1), 0.3, BORDER),
            ("TOPPADDING",    (0,0), (-1,-1), 2.5*mm),
            ("BOTTOMPADDING", (0,0), (-1,-1), 2.5*mm),
            ("LEFTPADDING",   (0,0), (-1,-1), 2*mm),
            ("RIGHTPADDING",  (0,0), (-1,-1), 2*mm),
            ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ]

        for ri, item in enumerate(cat_items, 1):
            cls     = item.get("classification", "—")
            cls_col = CLS_COLOR.get(cls, MUTED)
            cls_bg  = CLS_BG.get(cls, PAGE_BG)
            pri     = item.get("priority", "—")
            act     = item.get("action", "—")

            try:    orig = float(item.get("original_price")    or 0)
            except: orig = 0.0
            try:    rec  = float(item.get("recommended_price") or 0)
            except: rec  = 0.0

            up_col = (colors.HexColor("#1A7A3C") if rec > orig + 0.01
                      else colors.HexColor("#A02020") if rec < orig - 0.01
                      else DARK)

            # Pro: show AI-rewritten description. Starter: show original menu description.
            if is_pro:
                desc = (item.get("new_description") or "").strip()
            else:
                desc = (item.get("original_description") or item.get("description") or "").strip()
            dish_html = f"<b>{item.get('name','')}</b>"
            if desc:
                snippet = desc[:70] + ("..." if len(desc) > 70 else "")
                dish_html += (f'<br/><font size="6.5" color="#9A8A78">'
                              f'<i>{snippet}</i></font>')

            row_bg = PAGE_BG if ri % 2 == 0 else colors.HexColor("#F5F1EB")

            row = [
                Paragraph(dish_html, S_L),
                Paragraph(_fmt(orig, curr_sym), S_R),
                Paragraph(f'<b>{_fmt(rec, curr_sym)}</b>', ParagraphStyle(
                    "RC", fontName="Helvetica-Bold", fontSize=8.5,
                    textColor=up_col, alignment=TA_RIGHT, leading=11)),
                Paragraph(item.get("price_change", "—"), S_C),
                Paragraph(f'<b>{pri}</b>', ParagraphStyle(
                    "PR", fontName="Helvetica-Bold", fontSize=7.5,
                    textColor=PRIORITY_COLOR.get(pri, MUTED),
                    alignment=TA_CENTER, leading=10)),
                Paragraph(act, ParagraphStyle(
                    "AC", fontName="Helvetica", fontSize=8,
                    textColor=ACTION_COLOR.get(act, MUTED),
                    alignment=TA_LEFT, leading=10)),
            ]
            rows.append(row)
            styles.append(("BACKGROUND", (0, ri), (-1, ri), row_bg))
            styles.append(("LINEBEFORE", (0, ri), (0,  ri), 2.5, cls_col))

        t = Table(rows, colWidths=CW, repeatRows=1)
        t.setStyle(TableStyle(styles))
        story.append(t)
        story.append(Spacer(1, 4*mm))

    # ── METHODOLOGY (pro only) ────────────────────────────────────────────────
    if not is_pro:
        pass
    else:
        story.append(_rule(BORDER))
        story.extend(_section_title("Methodology"))
        story.append(_body(
            "This report applies the <b>Menu Engineering Matrix</b> (Kasavana & Smith, Cornell University) "
            "to classify every dish into one of four categories based on estimated popularity and margin.",
            size=9
        ))
        story.append(Spacer(1, 2*mm))

        matrix = [
            ["Class",      "Popularity", "Margin", "Action"],
            ["Star",       "High",       "High",   "Feature prominently. Protect price."],
            ["Plowhorse",  "High",       "Low",    "Raise price slightly or cut costs."],
            ["Puzzle",     "Low",        "High",   "Rename, reposition, improve description."],
            ["Dog",        "Low",        "Low",    "Remove or completely redesign."],
        ]
        S_MH = ParagraphStyle("MH", fontName="Helvetica-Bold", fontSize=8, textColor=CREAM, leading=10)
        S_MC = ParagraphStyle("MC", fontName="Helvetica",      fontSize=8, textColor=DARK,  leading=10)

        m_rows = [[Paragraph(cell, S_MH if ri == 0 else S_MC) for cell in row]
                   for ri, row in enumerate(matrix)]
        mt = Table(m_rows, colWidths=[W*0.18, W*0.14, W*0.12, W*0.56])
        mt_styles = [
            ("BACKGROUND",    (0,0), (-1,0),  DARK),
            ("LINEABOVE",     (0,0), (-1,0),  1.5, TERRA),
            ("BOX",           (0,0), (-1,-1), 0.5, BORDER),
            ("INNERGRID",     (0,0), (-1,-1), 0.3, BORDER),
            ("TOPPADDING",    (0,0), (-1,-1), 2.5*mm),
            ("BOTTOMPADDING", (0,0), (-1,-1), 2.5*mm),
            ("LEFTPADDING",   (0,0), (-1,-1), 3*mm),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ]
        for ri, cls in enumerate(["Star", "Plowhorse", "Puzzle", "Dog"], 1):
            mt_styles.append(("BACKGROUND", (0, ri), (0, ri), CLS_BG[cls]))
            mt_styles.append(("LINEBEFORE", (0, ri), (0, ri), 2.5, CLS_COLOR[cls]))
            mt_styles.append(("TEXTCOLOR",  (0, ri), (0, ri), CLS_COLOR[cls]))
            mt_styles.append(("FONTNAME",   (0, ri), (0, ri), "Helvetica-Bold"))
        mt.setStyle(TableStyle(mt_styles))
        story.append(mt)
        story.append(Spacer(1, 4*mm))
        story.append(_caption(
            "AI analysis by Claude (Anthropic). Recommendations are estimates. "
            "Actual results depend on location, customer base, and implementation."
        ))

    # ── UPGRADE PROMPT (starter only) ────────────────────────────────────────
    if not is_pro:
        story.append(Spacer(1, 6*mm))
        story.append(_rule(GOLD, thick=1))

        # Locked section previews — faint placeholder rows
        locked_sections = [
            "Executive Summary  —  Menu health score, avg price, est. food cost & monthly uplift",
            "Action Summary  —  Which dishes to promote and which to remove",
            "Menu Layout Recommendations  —  Where to place items for maximum orders",
            "Seasonal Menu Suggestions  —  What to add, rotate out & capitalise on this season",
            "AI Description Rewrites  —  Claude-powered descriptions for every dish",
            "Methodology  —  Full Menu Engineering Matrix breakdown",
        ]

        # Header
        story.append(Paragraph(
            "Unlock the Full Report",
            ParagraphStyle("UH", fontName="Helvetica-Bold", fontSize=13,
                           textColor=DARK, spaceBefore=2*mm, spaceAfter=1*mm)
        ))
        story.append(Paragraph(
            "Your Starter report includes Top Revenue Opportunities and Full Item Analysis. "
            "Upgrade to Pro to unlock everything below.",
            ParagraphStyle("US", fontName="Helvetica", fontSize=9,
                           textColor=MUTED, spaceAfter=4*mm, leading=13)
        ))

        # Locked rows with a grey "redacted" look
        for section in locked_sections:
            locked_row = Table([[
                Paragraph("[  ]", ParagraphStyle("LK", fontName="Helvetica-Bold",
                    fontSize=9, textColor=colors.HexColor("#CCBBAA"),
                    alignment=TA_CENTER)),
                Paragraph(section, ParagraphStyle("LS", fontName="Helvetica",
                    fontSize=9, textColor=colors.HexColor("#BBAA99"), leading=12)),
            ]], colWidths=[10*mm, W - 10*mm])
            locked_row.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (-1,-1), colors.HexColor("#F5F0EA")),
                ("LINEBELOW",     (0,0), (-1,-1), 0.3, colors.HexColor("#DDD5C8")),
                ("TOPPADDING",    (0,0), (-1,-1), 2.5*mm),
                ("BOTTOMPADDING", (0,0), (-1,-1), 2.5*mm),
                ("LEFTPADDING",   (0,0), (-1,-1), 3*mm),
                ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
            ]))
            story.append(locked_row)

        story.append(Spacer(1, 5*mm))

        # CTA box
        cta_content = [
            Paragraph("Upgrade to MenuEdge Pro", ParagraphStyle(
                "CA", fontName="Helvetica-Bold", fontSize=13,
                textColor=CREAM, spaceAfter=2*mm, alignment=TA_CENTER)),
            Paragraph(
                "Get the full report — Executive Summary, Action Summary, Layout Recommendations, "
                "AI-rewritten descriptions, and Methodology.",
                ParagraphStyle("CB", fontName="Helvetica", fontSize=9,
                               textColor=colors.HexColor("#D0C0B0"),
                               leading=13, spaceAfter=4*mm, alignment=TA_CENTER)),
            Paragraph("menuedge.app / upgrade", ParagraphStyle(
                "CC", fontName="Helvetica-Bold", fontSize=10,
                textColor=GOLD, alignment=TA_CENTER)),
        ]
        cta_tbl = Table([cta_content], colWidths=[W])
        cta_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), DARK),
            ("LINEBEFORE",    (0,0), (0,-1),  3.5, TERRA),
            ("TOPPADDING",    (0,0), (-1,-1), 6*mm),
            ("BOTTOMPADDING", (0,0), (-1,-1), 6*mm),
            ("LEFTPADDING",   (0,0), (-1,-1), 6*mm),
            ("RIGHTPADDING",  (0,0), (-1,-1), 6*mm),
        ]))
        story.append(cta_tbl)

    # Build
    def _first(canv, doc):
        draw_cover(canv, doc, data)

    doc.build(story, onFirstPage=_first, onLaterPages=decorator)
    print(f"  PDF saved -> {output_path}")
