from __future__ import annotations

import io

from reportlab.pdfgen import canvas

from productions.services.plate_report import DETAIL_ROWS, PlateReportError
from productions.services.plate_report_pdf import (
    ACCENT_FILL,
    MARGIN,
    PAGE_SIZE,
    HEADER_FILL,
    _cell,
    _convert_official_xlsx_to_pdf,
    _label_value,
)
from productions.services.tunnel_report import (
    TunnelReportError,
    TUNNEL_SUMMARY_ROWS,
    TUNNEL_SUMMARY_TOTAL_ROW,
    _page_updates,
    build_tunnel_report_xlsx,
)


def _draw_tunnel_detail_table(pdf, updates, page_width, top):
    x = MARGIN
    usable = page_width - (2 * MARGIN)
    ratios = (0.08, 0.36, 0.13, 0.10, 0.10, 0.13, 0.10)
    widths = [usable * ratio for ratio in ratios]
    headers = (
        "RACK",
        "PRODUCTO",
        "CODIGO / PESOS",
        "N TUNEL",
        "CANTIDAD BANDEJAS",
        "CUADRILLA",
        "BANDEJA (ENVASAS)",
    )
    columns = ("B", "D", "J", "N", "Q", "T", "V")
    header_height = 9 * 2.834645669
    row_height = 5 * 2.834645669
    y = top - header_height
    cursor = x
    for width, header in zip(widths, headers):
        _cell(pdf, cursor, y, width, header_height, header, fill=HEADER_FILL, font="Helvetica-Bold", font_size=6.1, align="center")
        cursor += width

    for row in DETAIL_ROWS:
        y -= row_height
        cursor = x
        for width, column in zip(widths, columns):
            value = updates.get(f"{column}{row}", "")
            align = "left" if column == "D" else "center"
            font = "Helvetica-Bold" if value and column in {"Q", "V"} else "Helvetica"
            _cell(pdf, cursor, y, width, row_height, value, font=font, font_size=5.8, align=align, padding=2)
            cursor += width
    return y - 2.2 * 2.834645669


def _draw_tunnel_header(pdf, updates, page_number, page_count, page_width, top):
    x = MARGIN
    usable = page_width - (2 * MARGIN)
    mm = 2.834645669

    title_height = 13 * mm
    title_y = top - title_height
    _cell(
        pdf,
        x,
        title_y,
        usable * 0.69,
        title_height,
        "REGISTRO DE ENVASADO (TUNEL)",
        font="Helvetica-Bold",
        font_size=15,
        align="center",
        line_width=0.8,
    )
    meta_x = x + usable * 0.69
    meta_width = usable * 0.31
    _cell(pdf, meta_x, title_y + title_height * 0.5, meta_width * 0.45, title_height * 0.5, "PAGINA", fill=HEADER_FILL, font="Helvetica-Bold", align="center")
    _cell(pdf, meta_x + meta_width * 0.45, title_y + title_height * 0.5, meta_width * 0.55, title_height * 0.5, f"{page_number} de {page_count}", font="Helvetica-Bold", align="center")
    _cell(pdf, meta_x, title_y, meta_width * 0.45, title_height * 0.5, "PP", fill=HEADER_FILL, font="Helvetica-Bold", align="center")
    _cell(pdf, meta_x + meta_width * 0.45, title_y, meta_width * 0.55, title_height * 0.5, updates.get("S7", ""), fill=ACCENT_FILL, font="Helvetica-Bold", align="center")

    row_height = 5.5 * mm
    row_y = title_y - row_height
    _label_value(pdf, x, row_y, 20 * mm, 76 * mm, row_height, "EMPRESA", "PPFISH S.A.C.")
    _label_value(pdf, x + 96 * mm, row_y, 14 * mm, 32 * mm, row_height, "RUC", "20516770300")
    _label_value(pdf, x + 142 * mm, row_y, 14 * mm, usable - 156 * mm, row_height, "FECHA", updates.get("U8", ""))

    row_y -= row_height
    _label_value(pdf, x, row_y, 20 * mm, 110 * mm, row_height, "CLIENTE", updates.get("E11", ""))
    _label_value(pdf, x + 130 * mm, row_y, 14 * mm, usable - 144 * mm, row_height, "RUC", updates.get("T11", ""))

    row_y -= row_height
    _label_value(pdf, x, row_y, 20 * mm, 110 * mm, row_height, "PROCESO", updates.get("E12", ""))
    _label_value(pdf, x + 130 * mm, row_y, 14 * mm, usable - 144 * mm, row_height, "TURNO", updates.get("U9", ""))

    row_y -= 7.5 * mm
    block_gap = 3 * mm
    block_width = (usable - block_gap) / 2
    _label_value(pdf, x, row_y, 20 * mm, block_width - 20 * mm, 7 * mm, "LOTE", updates.get("D14", ""))
    _label_value(pdf, x + block_width + block_gap, row_y, 42 * mm, block_width - 42 * mm, 3.5 * mm, "PLACA (VEHICULO)", updates.get("Q14", ""))
    _label_value(pdf, x + block_width + block_gap, row_y - 3.5 * mm, 42 * mm, block_width - 42 * mm, 3.5 * mm, "HORA DE LANZAMIENTO", updates.get("Q15", ""))
    return row_y - 5 * mm


def _draw_tunnel_summary(pdf, updates, page_width, top):
    x = MARGIN
    usable = page_width - (2 * MARGIN)
    gap = 3 * 2.834645669
    block_width = (usable - gap) / 2
    label_width = block_width * 0.77
    total_width = block_width - label_width
    header_height = 7 * 2.834645669
    row_height = 4.5 * 2.834645669
    total_height = 5.5 * 2.834645669

    for block_x in (x, x + block_width + gap):
        _cell(pdf, block_x, top - header_height, label_width, header_height, "RESUMEN DEL PRODUCTO", fill=HEADER_FILL, font="Helvetica-Bold", font_size=7, align="center")
        _cell(pdf, block_x + label_width, top - header_height, total_width, header_height, "BANDEJAS", fill=HEADER_FILL, font="Helvetica-Bold", font_size=7, align="center")

    y = top - header_height
    for row in TUNNEL_SUMMARY_ROWS:
        y -= row_height
        _cell(pdf, x, y, label_width, row_height, updates.get(f"E{row}", ""), font="Helvetica-Bold", font_size=5.4, align="center", padding=2)
        _cell(pdf, x + label_width, y, total_width, row_height, updates.get(f"G{row}", ""), font="Helvetica-Bold", font_size=6.2, align="center")
        right_x = x + block_width + gap
        _cell(pdf, right_x, y, label_width, row_height, updates.get(f"M{row}", ""), font="Helvetica-Bold", font_size=5.4, align="center", padding=2)
        _cell(pdf, right_x + label_width, y, total_width, row_height, updates.get(f"T{row}", ""), font="Helvetica-Bold", font_size=6.2, align="center")

    y -= total_height
    for block_x, value in ((x, updates.get(f"G{TUNNEL_SUMMARY_TOTAL_ROW}", "")), (x + block_width + gap, updates.get(f"T{TUNNEL_SUMMARY_TOTAL_ROW}", ""))):
        _cell(pdf, block_x, y, label_width, total_height, "TOTAL", font="Helvetica-Bold", font_size=7, align="center")
        _cell(pdf, block_x + label_width, y, total_width, total_height, value, font="Helvetica-Bold", font_size=7, align="center")
    return y


def _draw_tunnel_crew_totals(pdf, updates, page_width, top):
    """Muestra cuadrilla, bandejas y kilos al pie del resumen."""
    mm = 2.834645669
    x = MARGIN
    usable = page_width - (2 * MARGIN)
    gap = 3 * mm
    block_width = (usable - gap) / 2
    row_height = 4.5 * mm
    y = top - 3 * mm
    left_values = [
        updates.get(f"F{row}", "")
        for row in range(70, 77)
        if updates.get(f"F{row}", "")
    ]
    right_values = [
        updates.get(f"M{row}", "")
        for row in range(70, 77)
        if updates.get(f"M{row}", "")
    ]
    for index in range(max(len(left_values), len(right_values))):
        y -= row_height
        _cell(
            pdf,
            x,
            y,
            block_width,
            row_height,
            left_values[index] if index < len(left_values) else "",
            font="Helvetica-Bold",
            font_size=6.2,
            align="left",
            padding=3,
        )
        _cell(
            pdf,
            x + block_width + gap,
            y,
            block_width,
            row_height,
            right_values[index] if index < len(right_values) else "",
            font="Helvetica-Bold",
            font_size=6.2,
            align="left",
            padding=3,
        )


def _build_tunnel_report_pdf_fallback(production, tunnel=None):
    pages = _page_updates(production, tunnel=tunnel)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=PAGE_SIZE, pageCompression=1)
    tunnel_label = tunnel.code if tunnel else "Tuneles"
    pdf.setTitle(f"Envasado en tunel {tunnel_label} PP {production.number}")
    pdf.setAuthor("Partes de Produccion")
    page_width, page_height = PAGE_SIZE

    for page_number, updates in enumerate(pages, start=1):
        top = page_height - MARGIN
        updates = {**updates, "U2": f"{page_number} de {len(pages)}"}
        detail_top = _draw_tunnel_header(pdf, updates, page_number, len(pages), page_width, top)
        summary_top = _draw_tunnel_detail_table(pdf, updates, page_width, detail_top)
        crew_top = _draw_tunnel_summary(pdf, updates, page_width, summary_top)
        _draw_tunnel_crew_totals(pdf, updates, page_width, crew_top)
        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


def build_tunnel_report_pdf(production, tunnel=None):
    try:
        xlsx_payload = build_tunnel_report_xlsx(production, tunnel=tunnel)
        converted = _convert_official_xlsx_to_pdf(xlsx_payload)
        if converted is not None:
            return converted
        return _build_tunnel_report_pdf_fallback(production, tunnel=tunnel)
    except PlateReportError as exc:
        raise TunnelReportError(str(exc)) from exc
