from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .plate_report import (
    DETAIL_ROWS,
    SUMMARY_ROWS,
    PlateReportError,
    _page_cell_updates,
    build_plate_report_xlsx,
)


PAGE_SIZE = A4
MARGIN = 7 * mm
INK = colors.HexColor("#101820")
HEADER_FILL = colors.HexColor("#B7B7B7")
ACCENT_FILL = colors.HexColor("#DCE7F7")


def _text(value):
    if value is None:
        return ""
    return str(value).replace("\u00a0", " ").strip()


def _fit_font(value, font_name, preferred_size, available_width, minimum=4.5):
    value = _text(value)
    size = preferred_size
    while size > minimum and stringWidth(value, font_name, size) > available_width:
        size -= 0.25
    return size


def _cell(
    pdf,
    x,
    y,
    width,
    height,
    value="",
    *,
    fill=None,
    font="Helvetica",
    font_size=6.5,
    align="left",
    padding=3,
    line_width=0.55,
):
    if fill:
        pdf.setFillColor(fill)
        pdf.rect(x, y, width, height, stroke=0, fill=1)
    pdf.setStrokeColor(colors.black)
    pdf.setLineWidth(line_width)
    pdf.rect(x, y, width, height, stroke=1, fill=0)

    label = _text(value)
    if not label:
        return
    size = _fit_font(label, font, font_size, max(width - (padding * 2), 1))
    pdf.setFont(font, size)
    pdf.setFillColor(INK)
    baseline = y + (height - size) / 2 + 1
    if align == "center":
        pdf.drawCentredString(x + width / 2, baseline, label)
    elif align == "right":
        pdf.drawRightString(x + width - padding, baseline, label)
    else:
        pdf.drawString(x + padding, baseline, label)


def _label_value(pdf, x, y, label_width, value_width, height, label, value):
    _cell(
        pdf,
        x,
        y,
        label_width,
        height,
        label,
        fill=HEADER_FILL,
        font="Helvetica-Bold",
        font_size=6.2,
    )
    _cell(pdf, x + label_width, y, value_width, height, value, font_size=6.2)


def _draw_header(pdf, updates, page_number, page_count, page_width, top):
    x = MARGIN
    usable = page_width - (2 * MARGIN)

    title_height = 13 * mm
    title_y = top - title_height
    _cell(
        pdf,
        x,
        title_y,
        usable * 0.69,
        title_height,
        "REGISTRO DE ENVASADO (PLAQUERO)",
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
    _cell(pdf, meta_x + meta_width * 0.45, title_y, meta_width * 0.55, title_height * 0.5, updates.get("S7", ""), font="Helvetica-Bold", align="center")

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


def _draw_detail_table(pdf, updates, page_width, top):
    x = MARGIN
    usable = page_width - (2 * MARGIN)
    ratios = (0.11, 0.26, 0.13, 0.11, 0.12, 0.12, 0.15)
    widths = [usable * ratio for ratio in ratios]
    headers = (
        "INICIO / FIN DE CARGA",
        "PRODUCTO",
        "CODIGO / PESOS",
        "N DE PLAQUERO",
        "CANTIDAD BANDEJAS",
        "CUADRILLA",
        "BANDEJA (ENVASAS)",
    )
    columns = ("B", "D", "J", "N", "Q", "T", "V")
    header_height = 9 * mm
    row_height = 5 * mm
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
            align = "center" if column in {"B", "J", "N", "Q", "T", "V"} else "left"
            font = "Helvetica-Bold" if value and column in {"Q", "V"} else "Helvetica"
            _cell(pdf, cursor, y, width, row_height, value, font=font, font_size=5.8, align=align, padding=2)
            cursor += width
    return y - 2.2 * mm


def _draw_summary(pdf, updates, page_width, top):
    x = MARGIN
    usable = page_width - (2 * MARGIN)
    gap = 3 * mm
    block_width = (usable - gap) / 2
    label_width = block_width * 0.77
    total_width = block_width - label_width
    header_height = 7 * mm
    row_height = 4.5 * mm
    total_height = 5.5 * mm

    for block_x in (x, x + block_width + gap):
        _cell(pdf, block_x, top - header_height, label_width, header_height, "RESUMEN DEL PRODUCTO", fill=HEADER_FILL, font="Helvetica-Bold", font_size=7, align="center")
        _cell(pdf, block_x + label_width, top - header_height, total_width, header_height, "BANDEJAS", fill=HEADER_FILL, font="Helvetica-Bold", font_size=7, align="center")

    y = top - header_height
    for row in SUMMARY_ROWS:
        y -= row_height
        _cell(pdf, x, y, label_width, row_height, updates.get(f"E{row}", ""), font="Helvetica-Bold", font_size=5.4, align="center", padding=2)
        _cell(pdf, x + label_width, y, total_width, row_height, updates.get(f"G{row}", ""), font="Helvetica-Bold", font_size=6.2, align="center")
        right_x = x + block_width + gap
        _cell(pdf, right_x, y, label_width, row_height, updates.get(f"M{row}", ""), font="Helvetica-Bold", font_size=5.4, align="center", padding=2)
        _cell(pdf, right_x + label_width, y, total_width, row_height, updates.get(f"T{row}", ""), font="Helvetica-Bold", font_size=6.2, align="center")

    y -= total_height
    for block_x, value in ((x, updates.get("G54", "")), (x + block_width + gap, updates.get("T54", ""))):
        _cell(pdf, block_x, y, label_width, total_height, "TOTAL", font="Helvetica-Bold", font_size=7, align="center")
        _cell(pdf, block_x + label_width, y, total_width, total_height, value, font="Helvetica-Bold", font_size=7, align="center")


def _build_plate_report_pdf_fallback(production):
    """Portable fallback used when LibreOffice is unavailable (mainly tests)."""
    pages = _page_cell_updates(production)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=PAGE_SIZE, pageCompression=1)
    pdf.setTitle(f"Envasado en plaqueros PP {production.number}")
    pdf.setAuthor("Partes de Produccion")
    page_width, page_height = PAGE_SIZE

    for page_number, updates in enumerate(pages, start=1):
        top = page_height - MARGIN
        detail_top = _draw_header(pdf, updates, page_number, len(pages), page_width, top)
        summary_top = _draw_detail_table(pdf, updates, page_width, detail_top)
        _draw_summary(pdf, updates, page_width, summary_top)
        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


def _libreoffice_binary():
    return shutil.which("libreoffice") or shutil.which("soffice")


def _convert_official_xlsx_to_pdf(xlsx_payload):
    office = _libreoffice_binary()
    if not office:
        return None

    with tempfile.TemporaryDirectory(prefix="plate-report-") as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / "ENVASADO_PLAQUEROS.xlsx"
        output_path = source_path.with_suffix(".pdf")
        profile_path = temp_path / "libreoffice-profile"
        profile_path.mkdir()
        source_path.write_bytes(xlsx_payload)

        environment = os.environ.copy()
        environment["HOME"] = temp_dir
        command = [
            office,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile_path.as_uri()}",
            "--convert-to",
            "pdf:calc_pdf_Export",
            "--outdir",
            temp_dir,
            str(source_path),
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PlateReportError(
                "No se pudo convertir la plantilla oficial a PDF. Intente nuevamente."
            ) from exc

        if result.returncode != 0 or not output_path.exists():
            detail = (result.stderr or result.stdout or "").strip()
            raise PlateReportError(
                "No se pudo convertir la plantilla oficial a PDF. "
                f"{detail[:240]}"
            )
        return output_path.read_bytes()


def build_plate_report_pdf(production):
    """Export the populated official workbook to its native portrait A4 PDF."""
    xlsx_payload = build_plate_report_xlsx(production)
    converted = _convert_official_xlsx_to_pdf(xlsx_payload)
    if converted is not None:
        return converted
    return _build_plate_report_pdf_fallback(production)
