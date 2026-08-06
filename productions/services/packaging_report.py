from __future__ import annotations

import io
import re
import zipfile
from collections import defaultdict
from copy import copy
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from openpyxl import load_workbook

from productions.models import (
    PlatePackagingAllocation,
    PlatePackagingEntry,
    PlatePalletLine,
    ProductionOrder,
    TunnelPackagingEntry,
)
from productions.services.excel.writer import SafeXlsmWriter, UnsafeWriteError
from productions.services.plate_report import _apply_report_typography, _split_product_description


PACKAGING_REPORT_TEMPLATE = (
    Path(settings.BASE_DIR) / "reference_assets" / "EMPAQUE_EN_TUNEL.xlsx"
)
DETAIL_ROWS = tuple(range(19, 44))
TOTAL_ROW = 44


class PackagingReportError(ValueError):
    pass


@dataclass(frozen=True)
class PackagingReportRow:
    pallet: str
    product: str
    code_or_weight: str
    packages: int
    kilos: int


def _sheet_path(package):
    candidates = [
        name
        for name in package.namelist()
        if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
    ]
    if len(candidates) != 1:
        raise PackagingReportError("La plantilla de empaque debe contener una sola hoja.")
    return candidates[0]


def _first_date(entries):
    dates = [entry.date for entry in entries if getattr(entry, "date", None)]
    return min(dates) if dates else None


def _format_shift(production):
    return ProductionOrder.Shift(production.shift).label


def _base_updates(production, entries, *, title):
    report_date = _first_date(entries) or production.production_date
    lot = (production.customer_lot or "").strip() or production.plant_lot
    return {
        "F2": f"                    REGISTRO DE EMPAQUE ({title})",
        "S8": f"PP {production.number}",
        "T9": report_date,
        "T10": _format_shift(production),
        "D12": production.customer.name,
        "S12": production.customer.tax_id,
        "D13": production.process,
        "E15": lot,
    }


def _rows_for_tunnel(production):
    entries = list(
        TunnelPackagingEntry.objects.filter(production=production, is_active=True)
        .select_related("product")
        .order_by("pallet_number", "product__description", "pk")
    )
    if not entries:
        raise PackagingReportError("Todavía no hay empaque de túneles para generar el reporte.")
    rows = []
    for entry in entries:
        product, code_or_weight = _split_product_description(entry.product.description)
        rows.append(
            PackagingReportRow(
                pallet=f"P{entry.pallet_number}",
                product=product,
                code_or_weight=code_or_weight,
                packages=entry.package_count,
                kilos=entry.kilos,
            )
        )
    return rows, entries


def _rows_for_plates(production):
    automatic_lines = list(
        PlatePalletLine.objects.filter(production=production, is_active=True)
        .select_related("pallet", "product")
        .order_by("pallet__pallet_number", "product__description", "pk")
    )
    allocations = list(
        PlatePackagingAllocation.objects.filter(production=production, is_active=True)
        .select_related("source_entry__product")
        .order_by("pallet_number", "source_entry__product__description", "pk")
    )
    legacy_entries = list(
        PlatePackagingEntry.objects.filter(production=production, is_active=True)
        .select_related("product")
        .order_by("pallet_number", "product__description", "pk")
    )
    entries = [*automatic_lines, *allocations, *legacy_entries]
    if not entries:
        raise PackagingReportError("Todavía no hay empaque de plaqueros para generar el reporte.")

    grouped = defaultdict(lambda: {"packages": 0, "kilos": 0})
    for line in automatic_lines:
        product = line.product
        key = (line.pallet.pallet_number, product.pk, product.description, product.code)
        grouped[key]["packages"] += line.package_count
        grouped[key]["kilos"] += line.kilos
    for entry in entries:
        if isinstance(entry, PlatePalletLine):
            continue
        product = entry.product
        key = (entry.pallet_number, product.pk, product.description, product.code)
        grouped[key]["packages"] += entry.package_count
        grouped[key]["kilos"] += entry.kilos

    rows = []
    for (pallet, _product_id, description, code), values in sorted(
        grouped.items(), key=lambda item: (item[0][0], item[0][2].casefold(), item[0][3])
    ):
        product, code_or_weight = _split_product_description(description)
        rows.append(
            PackagingReportRow(
                pallet=f"P{pallet}",
                product=product,
                code_or_weight=code_or_weight,
                packages=values["packages"],
                kilos=values["kilos"],
            )
        )
    return rows, entries


def _updates_for_rows(production, rows, entries, *, title):
    updates = _base_updates(production, entries, title=title)
    for sheet_row, row in zip(DETAIL_ROWS, rows):
        updates.update(
            {
                f"B{sheet_row}": row.pallet,
                f"D{sheet_row}": row.product,
                f"G{sheet_row}": row.code_or_weight,
                f"N{sheet_row}": row.packages,
                f"R{sheet_row}": row.kilos,
            }
        )
    updates[f"N{TOTAL_ROW}"] = sum(row.packages for row in rows)
    updates[f"R{TOTAL_ROW}"] = sum(row.kilos for row in rows)
    return updates


def _row_chunks(rows):
    return [
        rows[index : index + len(DETAIL_ROWS)]
        for index in range(0, len(rows), len(DETAIL_ROWS))
    ] or [[]]


def _style_written_cell(cell):
    font = copy(cell.font)
    font.name = "Arial Black"
    cell.font = font
    alignment = copy(cell.alignment)
    alignment.horizontal = "center"
    alignment.vertical = "center"
    cell.alignment = alignment


def _write_cell(worksheet, ref, value):
    cell = worksheet[ref]
    cell.value = value
    _style_written_cell(cell)


def _clear_detail_rows(worksheet):
    for sheet_row in DETAIL_ROWS:
        for column in ("B", "D", "G", "N", "R"):
            _write_cell(worksheet, f"{column}{sheet_row}", "")


def _build_paginated_packaging_report(production, rows, entries, *, title):
    source_buffer = io.BytesIO(PACKAGING_REPORT_TEMPLATE.read_bytes())
    output_buffer = io.BytesIO()
    try:
        workbook = load_workbook(source_buffer)
        if len(workbook.worksheets) != 1:
            raise PackagingReportError("La plantilla de empaque debe contener una sola hoja.")
        template_sheet = workbook.worksheets[0]
        chunks = _row_chunks(rows)
        template_sheet.title = "Página 1"
        for page_index in range(2, len(chunks) + 1):
            clone = workbook.copy_worksheet(template_sheet)
            clone.title = f"Página {page_index}"

        for page_index, page_rows in enumerate(chunks, start=1):
            worksheet = workbook[f"Página {page_index}"]
            updates = _updates_for_rows(production, page_rows, entries, title=title)
            _clear_detail_rows(worksheet)
            for ref, value in updates.items():
                _write_cell(worksheet, ref, value)
        workbook.save(output_buffer)
    except PackagingReportError:
        raise
    except Exception as exc:
        raise PackagingReportError(f"No se pudo paginar la plantilla de empaque: {exc}") from exc
    return output_buffer.getvalue()


def _build_packaging_report(production, rows, entries, *, title):
    if not PACKAGING_REPORT_TEMPLATE.is_file():
        raise PackagingReportError("No se encontró la plantilla oficial de empaque.")
    if len(rows) > len(DETAIL_ROWS):
        return _build_paginated_packaging_report(production, rows, entries, title=title)
    source_buffer = io.BytesIO(PACKAGING_REPORT_TEMPLATE.read_bytes())
    output_buffer = io.BytesIO()
    updates = _updates_for_rows(production, rows, entries, title=title)
    try:
        with zipfile.ZipFile(source_buffer, "r") as source, zipfile.ZipFile(output_buffer, "w") as target:
            worksheet_path = _sheet_path(source)
            contents = {
                info.filename: source.read(info.filename)
                for info in source.infolist()
            }
            worksheet_xml = contents[worksheet_path]
            styles_xml = contents["xl/styles.xml"]
            for ref, value in updates.items():
                worksheet_xml = SafeXlsmWriter._replace_cell_payload(worksheet_xml, ref, value)
            styles_xml, worksheet_xml = _apply_report_typography(
                styles_xml,
                worksheet_xml,
                updates.keys(),
                detail_cell_refs=updates.keys(),
                centered_cell_refs=updates.keys(),
            )
            contents[worksheet_path] = worksheet_xml
            contents["xl/styles.xml"] = styles_xml
            for info in source.infolist():
                target.writestr(info, contents[info.filename])
    except (KeyError, zipfile.BadZipFile, UnsafeWriteError) as exc:
        raise PackagingReportError(f"No se pudo completar la plantilla de empaque: {exc}") from exc
    return output_buffer.getvalue()


def build_tunnel_packaging_report_xlsx(production):
    rows, entries = _rows_for_tunnel(production)
    return _build_packaging_report(production, rows, entries, title="TUNEL")


def build_plate_packaging_report_xlsx(production):
    rows, entries = _rows_for_plates(production)
    return _build_packaging_report(production, rows, entries, title="PLAQUEROS")
