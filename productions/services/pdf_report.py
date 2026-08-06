from io import BytesIO
from xml.sax.saxutils import escape

from django.db.models import Sum
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from productions.models import PlatePackagingEntry, ReceptionEntry, TunnelPackagingEntry
from .crew_control import reception_cone_pota_summary
from .reconciliation import plate_reconciliation, tunnel_reconciliation


FOREST = colors.HexColor("#124B3B")
MINT = colors.HexColor("#DCEBE5")
PAPER = colors.HexColor("#F3F5F4")
INK = colors.HexColor("#17211E")
MUTED = colors.HexColor("#64716D")
ALERT = colors.HexColor("#B23A33")


def _text(value):
    if value is None or value == "":
        return "-"
    return str(value).replace("—", "-").replace("–", "-")


def _page(canvas, doc):
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(FOREST)
    canvas.rect(0, height - 13 * mm, width, 13 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(18 * mm, height - 8.5 * mm, "PARTES DE PRODUCCION - CONTROL DE PLANTA")
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(18 * mm, 10 * mm, "Documento generado desde PostgreSQL")
    canvas.drawRightString(width - 18 * mm, 10 * mm, f"Pagina {doc.page}")
    canvas.restoreState()


def _section(title, styles):
    return Paragraph(escape(_text(title)), styles["Section"])


def _data_table(rows, widths=None, header=False):
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D6DEDB")),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, PAPER]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    if header:
        commands.extend([("BACKGROUND", (0, 0), (-1, 0), FOREST), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")])
    table.setStyle(TableStyle(commands))
    return table


def build_production_pdf(production):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=23 * mm, bottomMargin=18 * mm, title=f"PP {production.number} - {production.plant_lot}", author="Control de Produccion")
    sample = getSampleStyleSheet()
    styles = {
        "Title": ParagraphStyle("Title", parent=sample["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=INK, alignment=TA_LEFT, spaceAfter=3 * mm),
        "Meta": ParagraphStyle("Meta", parent=sample["BodyText"], fontName="Helvetica", fontSize=9, leading=13, textColor=MUTED, spaceAfter=5 * mm),
        "Section": ParagraphStyle("Section", parent=sample["Heading2"], fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=FOREST, spaceBefore=5 * mm, spaceAfter=2.5 * mm),
        "Right": ParagraphStyle("Right", parent=sample["BodyText"], fontName="Helvetica-Bold", fontSize=9, textColor=INK, alignment=TA_RIGHT),
    }
    story = [
        Paragraph(escape(f"PP {_text(production.number)} - {_text(production.plant_lot)}"), styles["Title"]),
        Paragraph(escape(f"{_text(production.customer.name)} | {_text(production.get_status_display())} | Plantilla {_text(production.template_version.code)}"), styles["Meta"]),
        _section("Datos generales", styles),
        _data_table(
            [
                ["Lote cliente", _text(production.customer_lot), "Turno", _text(production.get_shift_display())],
                ["Proceso", _text(production.process), "Producto", _text(production.main_product.description)],
                ["Recepcion", production.reception_date.strftime("%d/%m/%Y"), "Produccion", production.production_date.strftime("%d/%m/%Y")],
                ["Empaque", production.packaging_date.strftime("%d/%m/%Y") if production.packaging_date else "-", "Serie", _text(production.series)],
            ],
            widths=[31 * mm, 50 * mm, 27 * mm, 61 * mm],
        ),
    ]
    tunnel = tunnel_reconciliation(production)
    plates = plate_reconciliation(production)
    status_color = colors.HexColor("#155D44") if not tunnel.difference and not plates.difference else ALERT
    summary = Table(
        [
            ["Racks", "Cuadrillas tunel", "Dif. tuneles", "Dif. placas"],
            [str(tunnel.physical_total), str(tunnel.declared_total), str(tunnel.difference), str(plates.difference)],
        ],
        colWidths=[42.25 * mm] * 4,
    )
    summary.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), MINT), ("TEXTCOLOR", (0, 0), (-1, 0), FOREST), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, 0), 8), ("FONTSIZE", (0, 1), (-1, 1), 15), ("TEXTCOLOR", (2, 1), (-1, 1), status_color), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#B9CBC5")), ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9CBC5")), ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story.extend([_section("Conciliacion", styles), summary, _section("Tuneles y llenadas", styles)])
    fill_rows = [["Tunel", "Llenada", "Fecha", "Supervisor", "Racks", "Cuadrillas", "Estado"]]
    for fill in production.tunnel_fills.filter(is_active=True).select_related("tunnel", "supervisor").order_by("tunnel__code", "fill_number"):
        racks = fill.racks.filter(entries__is_active=True).aggregate(total=Sum("entries__tray_count"))["total"] or 0
        crews = fill.crew_entries.filter(is_active=True).aggregate(total=Sum("tray_count"))["total"] or 0
        fill_rows.append([fill.tunnel.code, fill.fill_number, fill.date.strftime("%d/%m/%Y"), _text(fill.supervisor.get_full_name() or fill.supervisor.username), racks, crews, _text(fill.get_status_display())])
    if len(fill_rows) == 1:
        fill_rows.append(["-", "-", "-", "Sin llenadas registradas", "0", "0", "-"])
    story.append(_data_table(fill_rows, widths=[14 * mm, 16 * mm, 23 * mm, 47 * mm, 18 * mm, 22 * mm, 29 * mm], header=True))

    reception_kg = ReceptionEntry.objects.filter(production=production, is_active=True).aggregate(total=Sum("weight_kg"))["total"] or 0
    reception_cone_pota = reception_cone_pota_summary(production)
    tunnel_packages = TunnelPackagingEntry.objects.filter(production=production, is_active=True).aggregate(total=Sum("package_count"))["total"] or 0
    plate_packages = PlatePackagingEntry.objects.filter(production=production, is_active=True).aggregate(total=Sum("package_count"))["total"] or 0
    story.extend([
        _section("Resumen operativo", styles),
        _data_table([["Recepcion total (kg)", f"{reception_kg:,.2f}"], ["Bultos empaque tuneles", f"{tunnel_packages:,}"], ["Bultos empaque placas", f"{plate_packages:,}"]], widths=[105 * mm, 64 * mm]),
        _section("Peso de limpieza de cono de pota por cuadrilla", styles),
    ])
    if reception_cone_pota["rows"]:
        reception_rows = [["Cuadrilla", "Dinos", "Peso kg"]]
        for row in reception_cone_pota["rows"]:
            reception_rows.append([row["crew_name"], str(row["dino_count"]), f'{row["total_weight"]:,.2f}'])
        story.append(_data_table(reception_rows, widths=[86 * mm, 24 * mm, 59 * mm], header=True))
    else:
        story.append(Paragraph("No hay registros de CONOS DE POTA en recepción.", styles["Meta"]))
    story.extend([
        _section("Observaciones", styles),
        Paragraph(escape(_text(production.observations or "Sin observaciones")), sample["BodyText"]),
        Spacer(1, 5 * mm),
        Paragraph("Este resumen no sustituye el archivo XLSM final. Los datos provienen de la base de datos y quedan sujetos a las conciliaciones y aprobaciones del sistema.", styles["Meta"]),
    ])
    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    return buffer.getvalue()
