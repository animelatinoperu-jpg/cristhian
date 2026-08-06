from __future__ import annotations

import io
import re
import zipfile
from copy import deepcopy
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from django.conf import settings
from django.utils import timezone

from productions.models import (
    PlateCrewEntry,
    PlateEntry,
    PlatePositionTiming,
    ProductionOrder,
    ReceptionEntry,
)
from productions.services.excel.writer import SafeXlsmWriter, UnsafeWriteError


PLATE_REPORT_TEMPLATE = (
    Path(settings.BASE_DIR)
    / "reference_assets"
    / "PLANTILLA_ENVASADO_PLAQUEROS.xlsx"
)
DETAIL_ROWS = tuple(range(18, 49))
SUMMARY_ROWS = tuple(range(51, 62))
SUMMARY_TOTAL_ROW = 62
REPORT_LAST_ROW = 68
SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOCUMENT_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
EXTENDED_PROPERTIES_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
)
DOC_PROPS_VT_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
)
SUMMARY_FONT_SIZE = "8"
DETAIL_FONT_SIZE = "12.5"
SUMMARY_ROW_HEIGHT = "30"
CREW_FOOTER_ROW = 68
CREW_FOOTER_ROW_HEIGHT = "90"


class PlateReportError(ValueError):
    pass


@dataclass(frozen=True)
class PlateReportRow:
    schedule: str
    product: str
    summary_product: str
    code_or_weight: str
    position: str
    physical_trays: int | str
    crews: str
    crew_trays: str


def _local_time(value):
    if not value:
        return None
    return timezone.localtime(value).strftime("%H:%M")


def _position_label(position):
    return f"B{position.batch_number}-P{position.plaquero_number}"


WEIGHT_SPEC_PATTERN = re.compile(
    r"(?P<spec>\d+(?:[.,]\d+)?\s*(?:-\s*(?:\d+(?:[.,]\d+)?|UP)|\s+UP)?"
    r"\s*(?:KG|G)(?:\s*/\s*PZA)?)",
    re.IGNORECASE,
)
SIZE_CODE_PATTERN = re.compile(
    r"\s+(?P<spec>SM\s+ST(?:\s+MEDIANA)?)\s*$",
    re.IGNORECASE,
)


def _normalize_code_or_weight(value):
    normalized = value.replace(",", ".")
    normalized = re.sub(r"\s*-\s*", "-", normalized)
    normalized = re.sub(r"\s*/\s*PZA\b", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return re.sub(
        r"\b(?:kg|g)\b",
        lambda match: match.group(0).upper(),
        normalized,
        flags=re.IGNORECASE,
    )


def _split_product_description(description):
    """Separate the commercial product name from its weight or size suffix."""
    value = re.sub(r"\s+", " ", (description or "").strip())
    weight_matches = list(WEIGHT_SPEC_PATTERN.finditer(value))
    match = weight_matches[-1] if weight_matches else SIZE_CODE_PATTERN.search(value)
    if not match:
        return value, ""
    product = f"{value[:match.start()]} {value[match.end():]}"
    product = re.sub(r"\s+", " ", product).strip()
    return product, _normalize_code_or_weight(match.group("spec"))


def _report_rows(production):
    physical_entries = list(
        PlateEntry.objects.filter(production=production, is_active=True)
        .select_related("position", "product")
        .order_by("position__plate_rack", "position__position_key", "product__description", "pk")
    )
    if not physical_entries:
        raise PlateReportError("Todavía no hay productos envasados en plaqueros para generar el reporte.")

    timings = {
        timing.position_id: timing
        for timing in PlatePositionTiming.objects.filter(production=production)
    }
    crew_entries = list(
        PlateCrewEntry.objects.filter(production=production, is_active=True)
        .select_related("position", "product", "crew")
        .order_by("crew__name", "pk")
    )
    crews_by_product = defaultdict(list)
    legacy_crews_by_position = defaultdict(list)
    for entry in crew_entries:
        if entry.product_id:
            crews_by_product[(entry.position_id, entry.product_id)].append(entry)
        else:
            legacy_crews_by_position[entry.position_id].append(entry)

    products_per_position = defaultdict(int)
    for entry in physical_entries:
        products_per_position[entry.position_id] += 1

    first_position_row = set()
    rows = []
    for entry in sorted(
        physical_entries,
        key=lambda item: (
            item.position.batch_number or 9999,
            item.position.plaquero_number or 9999,
            item.product.description.casefold(),
            item.product.code.casefold(),
            item.pk,
        ),
    ):
        product_name, code_or_weight = _split_product_description(
            entry.product.description
        )
        timing = timings.get(entry.position_id)
        schedule = ""
        if entry.position_id not in first_position_row:
            started = _local_time(timing.load_started_at) if timing else None
            completed = _local_time(timing.load_completed_at) if timing else None
            if started or completed:
                schedule = f"{started or 'PEND.'} - {completed or 'PEND.'}"
            first_position_row.add(entry.position_id)

        assignments = list(crews_by_product.get((entry.position_id, entry.product_id), []))
        if products_per_position[entry.position_id] == 1:
            assignments.extend(legacy_crews_by_position.get(entry.position_id, []))
        assignments = assignments or [None]
        for index, assignment in enumerate(assignments):
            rows.append(
                PlateReportRow(
                    schedule=schedule if index == 0 else "",
                    product=product_name,
                    summary_product=entry.product.description,
                    code_or_weight=code_or_weight,
                    position=_position_label(entry.position),
                    physical_trays=entry.tray_count if index == 0 else "",
                    crews=assignment.crew.name if assignment else "PENDIENTE",
                    crew_trays=assignment.tray_count if assignment else 0,
                )
            )

    return rows, physical_entries, crew_entries, timings


def _first_reception_plate(production):
    receptions = (
        ReceptionEntry.objects.filter(production=production, is_active=True)
        .select_related("vehicle")
        .order_by("created_at", "pk")
    )
    first_car = receptions.filter(car_number="1").first() or receptions.first()
    return first_car.vehicle.plate if first_car else "SIN REGISTRO"


def _launch_range(timings):
    launches = sorted(
        timing.launched_at for timing in timings.values() if timing.launched_at
    )
    if not launches:
        return "PENDIENTE"
    return f"INICIO {_local_time(launches[0])} / FINAL {_local_time(launches[-1])}"


def _summary(items, key, value):
    totals = defaultdict(int)
    for item in items:
        totals[key(item)] += int(value(item))
    return sorted(totals.items(), key=lambda item: item[0].casefold())


def _crew_footer_updates(crew_entries):
    """Show crew/tray totals in the blank area below the signature boxes."""
    crew_summary = _summary(
        crew_entries,
        key=lambda item: item.crew.name,
        value=lambda item: item.tray_count,
    )
    if not crew_summary:
        return {"F68": "", "M68": ""}

    midpoint = (len(crew_summary) + 1) // 2
    left_block = crew_summary[:midpoint]
    right_block = crew_summary[midpoint:]
    left_lines = [
        f"{crew_name} - {tray_count}"
        for crew_name, tray_count in left_block
    ]
    right_lines = [
        f"{crew_name} - {tray_count}"
        for crew_name, tray_count in right_block
    ]
    return {
        "F68": "\n".join(left_lines),
        "M68": "\n".join(right_lines),
    }


def _cell_updates(production):
    _, physical_entries, crew_entries, timings = _report_rows(production)

    shifts = {entry.shift for entry in physical_entries}
    if len(shifts) == 1:
        shift = ProductionOrder.Shift(next(iter(shifts))).label
    else:
        shift = ProductionOrder.Shift.MIXED.label
    report_date = min(entry.date for entry in physical_entries)
    lot = production.customer_lot.strip() or production.plant_lot

    updates = {
        "S7": f"PP {production.number}",
        "U8": report_date.strftime("%d/%m/%Y"),
        "U9": shift,
        "E11": production.customer.name,
        "T11": production.customer.tax_id,
        "E12": production.process,
        "D14": lot,
        "Q14": _first_reception_plate(production),
        "Q15": _launch_range(timings),
        **_crew_footer_updates(crew_entries),
    }

    return updates


def _page_summary_updates(page_rows, page_number):
    """Build the product summary from only the detail lines on one sheet."""
    product_summary = _summary(
        page_rows,
        key=lambda item: item.summary_product,
        value=lambda item: item.physical_trays or 0,
    )
    summary_capacity = len(SUMMARY_ROWS) * 2
    if len(product_summary) > summary_capacity:
        raise PlateReportError(
            f"La hoja {page_number} tiene {len(product_summary)} productos y la "
            f"plantilla admite {summary_capacity} en su resumen."
        )

    first_product_block = product_summary[: len(SUMMARY_ROWS)]
    second_product_block = product_summary[len(SUMMARY_ROWS) :]
    updates = {}
    for sheet_row, (label, total) in zip(SUMMARY_ROWS, first_product_block):
        updates[f"E{sheet_row}"] = label
        updates[f"G{sheet_row}"] = total
    for sheet_row, (label, total) in zip(SUMMARY_ROWS, second_product_block):
        updates[f"M{sheet_row}"] = label
        updates[f"T{sheet_row}"] = total
    updates[f"G{SUMMARY_TOTAL_ROW}"] = sum(
        total for _, total in first_product_block
    )
    updates[f"T{SUMMARY_TOTAL_ROW}"] = (
        sum(total for _, total in second_product_block)
        if second_product_block
        else ""
    )
    return updates


def _page_cell_updates(production):
    """Split detail lines across as many copies of the report sheet as needed."""
    rows, _, _, _ = _report_rows(production)
    first_page_updates = _cell_updates(production)
    detail_columns = {"B", "D", "J", "N", "Q", "T", "V"}

    def is_detail_cell(ref):
        match = re.fullmatch(r"([A-Z]+)(\d+)", ref)
        return bool(
            match
            and match.group(1) in detail_columns
            and int(match.group(2)) in DETAIL_ROWS
        )

    common_updates = {
        ref: value
        for ref, value in first_page_updates.items()
        if not is_detail_cell(ref)
    }
    page_chunks = [
        rows[index : index + len(DETAIL_ROWS)]
        for index in range(0, len(rows), len(DETAIL_ROWS))
    ]
    page_count = len(page_chunks)
    pages = []
    for page_number, page_rows in enumerate(page_chunks, start=1):
        updates = {
            **common_updates,
            **_page_summary_updates(page_rows, page_number),
            "U2": f"{page_number} de {page_count}",
        }
        for sheet_row, item in zip(DETAIL_ROWS, page_rows):
            updates.update(
                {
                    f"B{sheet_row}": item.schedule,
                    f"D{sheet_row}": item.product,
                    f"J{sheet_row}": item.code_or_weight,
                    f"N{sheet_row}": item.position,
                    f"Q{sheet_row}": item.physical_trays,
                    f"T{sheet_row}": item.crews,
                    f"V{sheet_row}": item.crew_trays,
                }
            )
        pages.append(updates)
    return pages


def _sheet_path(package):
    candidates = [
        name
        for name in package.namelist()
        if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
    ]
    if len(candidates) != 1:
        raise PlateReportError("La plantilla del reporte debe contener una sola hoja.")
    return candidates[0]


def _xml_root(xml_bytes):
    """Parse OOXML while retaining the namespace prefixes used by the template."""
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(xml_bytes), events=("start-ns",)):
        ET.register_namespace(prefix or "", uri)
    return ET.fromstring(xml_bytes)


def _cell_tag_pattern(ref):
    return re.compile(
        rb'<c\b(?=[^>]*\br="' + re.escape(ref.encode("ascii")) + rb'")[^>]*>'
    )


def _cell_style_index(worksheet_xml, ref):
    match = _cell_tag_pattern(ref).search(worksheet_xml)
    if match is None:
        raise PlateReportError(f"No se encontrÃ³ la celda {ref} en la plantilla del reporte.")
    style = re.search(rb'\bs="(\d+)"', match.group(0))
    return int(style.group(1)) if style else 0


def _set_cell_style_index(worksheet_xml, ref, style_index):
    pattern = _cell_tag_pattern(ref)

    def replace(match):
        cell_tag = match.group(0)
        style_value = f's="{style_index}"'.encode("ascii")
        if re.search(rb'\bs="\d+"', cell_tag):
            return re.sub(rb'\bs="\d+"', style_value, cell_tag, count=1)
        ending = b"/>" if cell_tag.endswith(b"/>") else b">"
        return cell_tag[: -len(ending)] + b" " + style_value + ending

    updated, count = pattern.subn(replace, worksheet_xml, count=1)
    if count != 1:
        raise PlateReportError(f"No se pudo aplicar el formato a la celda {ref}.")
    return updated


def _set_row_height(worksheet_xml, row_number, height):
    pattern = re.compile(
        rb'<row\b(?=[^>]*\br="' + str(row_number).encode("ascii") + rb'")[^>]*>'
    )

    def replace(match):
        row_tag = match.group(0)
        height_value = f'ht="{height}"'.encode("ascii")
        if re.search(rb'\bht="[^"]*"', row_tag):
            row_tag = re.sub(rb'\bht="[^"]*"', height_value, row_tag, count=1)
        else:
            row_tag = row_tag[:-1] + b" " + height_value + b">"
        if re.search(rb'\bcustomHeight="[^"]*"', row_tag):
            return re.sub(
                rb'\bcustomHeight="[^"]*"',
                b'customHeight="1"',
                row_tag,
                count=1,
            )
        return row_tag[:-1] + b' customHeight="1">'

    updated, count = pattern.subn(replace, worksheet_xml, count=1)
    if count != 1:
        raise PlateReportError(
            f"No se pudo ajustar la altura de la fila {row_number}."
        )
    return updated


def _restore_ignorable_namespaces(original_xml, serialized_xml):
    """Keep namespace declarations referenced only by ``mc:Ignorable``.

    ``ElementTree`` drops unused namespace declarations while serializing. Excel
    still validates every prefix listed in ``mc:Ignorable``, so losing one of
    those declarations makes an otherwise valid workbook open in repair mode.
    """
    ignorable = re.search(rb'\bmc:Ignorable="([^"]+)"', original_xml)
    if ignorable is None:
        return serialized_xml

    declarations = []
    for prefix in ignorable.group(1).split():
        if re.search(rb'\bxmlns:' + re.escape(prefix) + rb'="', serialized_xml):
            continue
        declaration = re.search(
            rb'\bxmlns:' + re.escape(prefix) + rb'="[^"]+"',
            original_xml,
        )
        if declaration is None:
            raise PlateReportError(
                "La plantilla contiene un prefijo de compatibilidad sin declarar."
            )
        declarations.append(declaration.group(0))

    if not declarations:
        return serialized_xml
    root_tag = re.search(rb'<(?:[A-Za-z_][\w.-]*:)?styleSheet\b[^>]*>', serialized_xml)
    if root_tag is None:
        raise PlateReportError("No se encontró la raíz de estilos de la plantilla.")
    replacement = root_tag.group(0)[:-1] + b" " + b" ".join(declarations) + b">"
    return serialized_xml[: root_tag.start()] + replacement + serialized_xml[root_tag.end() :]


def _apply_report_typography(
    styles_xml,
    worksheet_xml,
    cell_refs,
    compact_cell_refs=(),
    detail_cell_refs=(),
    centered_cell_refs=(),
    footer_cell_refs=(),
):
    """Use Arial Black and keep populated report text inside its cells."""
    root = _xml_root(styles_xml)
    tag = lambda name: f"{{{SPREADSHEET_NS}}}{name}"
    compact_cell_refs = set(compact_cell_refs)
    detail_cell_refs = set(detail_cell_refs)
    centered_cell_refs = set(centered_cell_refs)
    footer_cell_refs = set(footer_cell_refs)

    fonts = root.find(tag("fonts"))
    if fonts is None:
        raise PlateReportError("La plantilla del reporte no contiene estilos de fuente.")
    for font in fonts.findall(tag("font")):
        name = font.find(tag("name"))
        if name is None:
            name = ET.Element(tag("name"), {"val": "Arial Black"})
            font.insert(0, name)
        else:
            name.set("val", "Arial Black")

    cell_xfs = root.find(tag("cellXfs"))
    if cell_xfs is None:
        raise PlateReportError("La plantilla del reporte no contiene formatos de celda.")
    original_formats = cell_xfs.findall(tag("xf"))
    base_styles = {
        ref: _cell_style_index(worksheet_xml, ref)
        for ref in cell_refs
    }
    sized_fonts = {}

    def sized_font_id(base_font_id, font_size):
        key = (base_font_id, font_size)
        if key in sized_fonts:
            return sized_fonts[key]
        font = deepcopy(fonts.findall(tag("font"))[base_font_id])
        size = font.find(tag("sz"))
        if size is None:
            size = ET.Element(tag("sz"), {"val": font_size})
            font.insert(0, size)
        else:
            size.set("val", font_size)
        sized_fonts[key] = len(fonts)
        fonts.append(font)
        fonts.set("count", str(len(fonts)))
        return sized_fonts[key]

    derived_styles = {}
    style_variants = {
        (
            base_style,
            ref in compact_cell_refs,
            ref in detail_cell_refs,
            ref in centered_cell_refs,
            ref in footer_cell_refs,
        )
        for ref, base_style in base_styles.items()
    }
    for base_style, compact, detail, centered, footer in sorted(style_variants):
        if base_style >= len(original_formats):
            raise PlateReportError("La plantilla contiene un formato de celda invÃ¡lido.")
        cell_format = deepcopy(original_formats[base_style])
        if compact or detail or footer:
            base_font_id = int(cell_format.attrib.get("fontId", "0"))
            font_size = (
                SUMMARY_FONT_SIZE
                if compact or footer
                else DETAIL_FONT_SIZE
            )
            cell_format.set("fontId", str(sized_font_id(base_font_id, font_size)))
            cell_format.set("applyFont", "1")
        alignment = cell_format.find(tag("alignment"))
        if alignment is None:
            alignment = ET.Element(tag("alignment"))
            protection = cell_format.find(tag("protection"))
            if protection is None:
                cell_format.append(alignment)
            else:
                cell_format.insert(list(cell_format).index(protection), alignment)
        alignment.set("wrapText", "1")
        if compact or centered or footer:
            alignment.set("horizontal", "center")
            alignment.set("vertical", "center")
        cell_format.set("applyAlignment", "1")
        derived_styles[(base_style, compact, detail, centered, footer)] = len(cell_xfs)
        cell_xfs.append(cell_format)

    cell_xfs.set("count", str(len(cell_xfs)))
    for ref, base_style in base_styles.items():
        worksheet_xml = _set_cell_style_index(
            worksheet_xml,
            ref,
            derived_styles[
                (
                    base_style,
                    ref in compact_cell_refs,
                    ref in detail_cell_refs,
                    ref in centered_cell_refs,
                    ref in footer_cell_refs,
                )
            ],
        )

    for row_number in sorted(
        {int(re.search(r"\d+", ref).group()) for ref in compact_cell_refs}
    ):
        worksheet_xml = _set_row_height(
            worksheet_xml,
            row_number,
            SUMMARY_ROW_HEIGHT,
        )

    serialized_styles = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    serialized_styles = _restore_ignorable_namespaces(styles_xml, serialized_styles)
    return serialized_styles, worksheet_xml


def _apply_center_alignment(styles_xml, worksheet_xml, cell_refs):
    """Center specific cells without changing fonts or other template styling."""
    cell_refs = set(cell_refs)
    if not cell_refs:
        return styles_xml, worksheet_xml

    root = _xml_root(styles_xml)
    tag = lambda name: f"{{{SPREADSHEET_NS}}}{name}"
    cell_xfs = root.find(tag("cellXfs"))
    if cell_xfs is None:
        raise PlateReportError("La plantilla del reporte no contiene formatos de celda.")

    original_formats = cell_xfs.findall(tag("xf"))
    derived_styles = {}
    for ref in sorted(cell_refs):
        base_style = _cell_style_index(worksheet_xml, ref)
        if base_style >= len(original_formats):
            raise PlateReportError("La plantilla contiene un formato de celda invalido.")
        if base_style not in derived_styles:
            cell_format = deepcopy(original_formats[base_style])
            alignment = cell_format.find(tag("alignment"))
            if alignment is None:
                alignment = ET.Element(tag("alignment"))
                protection = cell_format.find(tag("protection"))
                if protection is None:
                    cell_format.append(alignment)
                else:
                    cell_format.insert(list(cell_format).index(protection), alignment)
            alignment.set("horizontal", "center")
            alignment.set("vertical", "center")
            alignment.set("wrapText", "1")
            cell_format.set("applyAlignment", "1")
            derived_styles[base_style] = len(cell_xfs)
            cell_xfs.append(cell_format)
        worksheet_xml = _set_cell_style_index(
            worksheet_xml,
            ref,
            derived_styles[base_style],
        )

    cell_xfs.set("count", str(len(cell_xfs)))
    serialized_styles = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    serialized_styles = _restore_ignorable_namespaces(styles_xml, serialized_styles)
    return serialized_styles, worksheet_xml


def _prepare_crew_footer(worksheet_xml):
    """Reserve the blank footer below signatures without changing their boxes."""
    missing_merges = [
        ref
        for ref in ("F68:K68", "M68:T68")
        if f'ref="{ref}"'.encode("ascii") not in worksheet_xml
    ]
    if missing_merges:
        merge_open = re.search(rb"<mergeCells\b[^>]*>", worksheet_xml)
        merge_close = worksheet_xml.find(b"</mergeCells>")
        if merge_open is None or merge_close < 0:
            raise PlateReportError(
                "La plantilla no contiene la estructura para el resumen de cuadrillas."
            )
        opening_tag = merge_open.group(0)
        count_match = re.search(rb'\bcount="(\d+)"', opening_tag)
        if count_match:
            new_count = int(count_match.group(1)) + len(missing_merges)
            updated_tag = (
                opening_tag[: count_match.start(1)]
                + str(new_count).encode("ascii")
                + opening_tag[count_match.end(1) :]
            )
            worksheet_xml = (
                worksheet_xml[: merge_open.start()]
                + updated_tag
                + worksheet_xml[merge_open.end() :]
            )
            merge_close += len(updated_tag) - len(opening_tag)
        payload = b"".join(
            f'<mergeCell ref="{ref}"/>'.encode("ascii")
            for ref in missing_merges
        )
        worksheet_xml = (
            worksheet_xml[:merge_close]
            + payload
            + worksheet_xml[merge_close:]
        )
    return _set_row_height(
        worksheet_xml,
        CREW_FOOTER_ROW,
        CREW_FOOTER_ROW_HEIGHT,
    )


def _report_sheet_name(page_number):
    return "ENVASADO" if page_number == 1 else f"ENVASADO {page_number}"


def _add_report_sheets(contents, worksheet_pages, report_last_row=REPORT_LAST_ROW):
    """Add cloned template sheets and their drawing/print relationships."""
    page_count = len(worksheet_pages)
    contents["xl/worksheets/sheet1.xml"] = worksheet_pages[0]

    workbook_original = contents["xl/workbook.xml"]
    workbook_root = _xml_root(workbook_original)
    workbook_tag = lambda name: f"{{{SPREADSHEET_NS}}}{name}"
    sheets = workbook_root.find(workbook_tag("sheets"))
    defined_names = workbook_root.find(workbook_tag("definedNames"))
    if sheets is None or defined_names is None:
        raise PlateReportError(
            "La plantilla no contiene la estructura necesaria para agregar hojas."
        )

    defined_name_tag = workbook_tag("definedName")
    for defined_name in defined_names.findall(defined_name_tag):
        if defined_name.attrib.get("name") == "_xlnm.Print_Area":
            local_sheet_id = defined_name.attrib.get("localSheetId")
            if local_sheet_id in (None, "0"):
                defined_name.text = f"'{_report_sheet_name(1)}'!$B$1:$V${report_last_row}"

    if page_count == 1:
        workbook_xml = ET.tostring(
            workbook_root,
            encoding="utf-8",
            xml_declaration=True,
        )
        contents["xl/workbook.xml"] = _restore_ignorable_namespaces(
            workbook_original,
            workbook_xml,
        )
        return contents

    ET.register_namespace("", PACKAGE_REL_NS)
    relationships_root = ET.fromstring(contents["xl/_rels/workbook.xml.rels"])
    relationship_tag = f"{{{PACKAGE_REL_NS}}}Relationship"
    existing_relationship_ids = {
        int(match.group(1))
        for relation in relationships_root.findall(relationship_tag)
        if (match := re.fullmatch(r"rId(\d+)", relation.attrib.get("Id", "")))
    }
    next_relationship_id = max(existing_relationship_ids, default=0) + 1

    for page_number in range(2, page_count + 1):
        sheet_name = _report_sheet_name(page_number)
        relationship_id = f"rId{next_relationship_id}"
        next_relationship_id += 1
        ET.SubElement(
            sheets,
            workbook_tag("sheet"),
            {
                "name": sheet_name,
                "sheetId": str(page_number),
                f"{{{DOCUMENT_REL_NS}}}id": relationship_id,
            },
        )
        ET.SubElement(
            relationships_root,
            relationship_tag,
            {
                "Id": relationship_id,
                "Type": (
                    "http://schemas.openxmlformats.org/officeDocument/2006/"
                    "relationships/worksheet"
                ),
                "Target": f"worksheets/sheet{page_number}.xml",
            },
        )
        print_area = ET.SubElement(
            defined_names,
            workbook_tag("definedName"),
            {
                "name": "_xlnm.Print_Area",
                "localSheetId": str(page_number - 1),
            },
        )
        print_area.text = f"'{sheet_name}'!$B$1:$V${report_last_row}"

        contents[f"xl/worksheets/sheet{page_number}.xml"] = worksheet_pages[
            page_number - 1
        ]
        sheet_relationships = contents[
            "xl/worksheets/_rels/sheet1.xml.rels"
        ].replace(
            b"../drawings/drawing1.xml",
            f"../drawings/drawing{page_number}.xml".encode("ascii"),
        ).replace(
            b"../printerSettings/printerSettings1.bin",
            (
                f"../printerSettings/printerSettings{page_number}.bin"
            ).encode("ascii"),
        )
        contents[
            f"xl/worksheets/_rels/sheet{page_number}.xml.rels"
        ] = sheet_relationships
        contents[f"xl/drawings/drawing{page_number}.xml"] = contents[
            "xl/drawings/drawing1.xml"
        ]
        contents[
            f"xl/drawings/_rels/drawing{page_number}.xml.rels"
        ] = contents["xl/drawings/_rels/drawing1.xml.rels"]
        contents[
            f"xl/printerSettings/printerSettings{page_number}.bin"
        ] = contents["xl/printerSettings/printerSettings1.bin"]

    workbook_xml = ET.tostring(
        workbook_root,
        encoding="utf-8",
        xml_declaration=True,
    )
    contents["xl/workbook.xml"] = _restore_ignorable_namespaces(
        workbook_original,
        workbook_xml,
    )
    contents["xl/_rels/workbook.xml.rels"] = ET.tostring(
        relationships_root,
        encoding="utf-8",
        xml_declaration=True,
    )

    ET.register_namespace("", CONTENT_TYPES_NS)
    content_types_root = ET.fromstring(contents["[Content_Types].xml"])
    override_tag = f"{{{CONTENT_TYPES_NS}}}Override"
    for page_number in range(2, page_count + 1):
        ET.SubElement(
            content_types_root,
            override_tag,
            {
                "PartName": f"/xl/worksheets/sheet{page_number}.xml",
                "ContentType": (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.worksheet+xml"
                ),
            },
        )
        ET.SubElement(
            content_types_root,
            override_tag,
            {
                "PartName": f"/xl/drawings/drawing{page_number}.xml",
                "ContentType": (
                    "application/vnd.openxmlformats-officedocument.drawing+xml"
                ),
            },
        )
    contents["[Content_Types].xml"] = ET.tostring(
        content_types_root,
        encoding="utf-8",
        xml_declaration=True,
    )

    ET.register_namespace("", EXTENDED_PROPERTIES_NS)
    ET.register_namespace("vt", DOC_PROPS_VT_NS)
    app_root = ET.fromstring(contents["docProps/app.xml"])
    vt_tag = lambda name: f"{{{DOC_PROPS_VT_NS}}}{name}"
    heading_vector = app_root.find(
        f"{{{EXTENDED_PROPERTIES_NS}}}HeadingPairs/{vt_tag('vector')}"
    )
    if heading_vector is not None:
        for count_node in heading_vector.findall(f".//{vt_tag('i4')}"):
            count_node.text = str(page_count)
    titles_vector = app_root.find(
        f"{{{EXTENDED_PROPERTIES_NS}}}TitlesOfParts/{vt_tag('vector')}"
    )
    if titles_vector is not None:
        titles_vector.clear()
        titles_vector.set("size", str(page_count * 2))
        titles_vector.set("baseType", "lpstr")
        for page_number in range(1, page_count + 1):
            title = ET.SubElement(titles_vector, vt_tag("lpstr"))
            title.text = _report_sheet_name(page_number)
        for page_number in range(1, page_count + 1):
            title = ET.SubElement(titles_vector, vt_tag("lpstr"))
            title.text = (
                f"{_report_sheet_name(page_number)}!Área_de_impresión"
            )
    contents["docProps/app.xml"] = ET.tostring(
        app_root,
        encoding="utf-8",
        xml_declaration=True,
    )
    return contents


def build_plate_report_xlsx(
    production,
    *,
    template_path=None,
    page_updates=None,
    apply_typography=True,
    centered_cell_refs=(),
    footer_cell_refs=("F68", "M68"),
    report_last_row=REPORT_LAST_ROW,
    worksheet_preparer=None,
):
    if not PLATE_REPORT_TEMPLATE.is_file():
        raise PlateReportError("No se encontró la plantilla oficial de envasado en plaqueros.")
    page_updates = page_updates or _page_cell_updates(production)
    source_bytes = (template_path or PLATE_REPORT_TEMPLATE).read_bytes()
    source_buffer = io.BytesIO(source_bytes)
    output_buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(source_buffer, "r") as source, zipfile.ZipFile(
            output_buffer, "w"
        ) as target:
            worksheet_path = _sheet_path(source)
            contents = {
                info.filename: source.read(info.filename)
                for info in source.infolist()
            }
            worksheet_template = _prepare_crew_footer(contents[worksheet_path])
            if worksheet_preparer is not None:
                worksheet_template = worksheet_preparer(worksheet_template)
            styles_xml = contents["xl/styles.xml"]
            worksheet_pages = []
            for updates in page_updates:
                worksheet_xml = worksheet_template
                for ref, value in updates.items():
                    worksheet_xml = SafeXlsmWriter._replace_cell_payload(
                        worksheet_xml,
                        ref,
                        value,
                    )
                if apply_typography:
                    styles_xml, worksheet_xml = _apply_report_typography(
                        styles_xml,
                        worksheet_xml,
                        updates.keys(),
                        compact_cell_refs={
                            ref
                            for ref in updates
                            if ref[0] in {"E", "M"}
                            and int(ref[1:]) in SUMMARY_ROWS
                        },
                        detail_cell_refs={
                            ref
                            for ref in updates
                            if (match := re.fullmatch(r"[A-Z]+(\d+)", ref))
                            and int(match.group(1)) in DETAIL_ROWS
                        },
                        centered_cell_refs={
                            ref
                            for ref in updates
                            if (match := re.fullmatch(r"V(\d+)", ref))
                            and int(match.group(1)) in DETAIL_ROWS
                        },
                        footer_cell_refs=footer_cell_refs,
                    )
                else:
                    styles_xml, worksheet_xml = _apply_center_alignment(
                        styles_xml,
                        worksheet_xml,
                        centered_cell_refs,
                    )
                worksheet_pages.append(worksheet_xml)
            contents["xl/styles.xml"] = styles_xml
            contents = _add_report_sheets(contents, worksheet_pages, report_last_row=report_last_row)
            written = set()
            for info in source.infolist():
                target.writestr(info, contents[info.filename])
                written.add(info.filename)
            for filename, payload in contents.items():
                if filename not in written:
                    target.writestr(
                        filename,
                        payload,
                        compress_type=zipfile.ZIP_DEFLATED,
                    )
    except (
        ET.ParseError,
        IndexError,
        KeyError,
        zipfile.BadZipFile,
        UnsafeWriteError,
    ) as exc:
        raise PlateReportError(f"No se pudo completar la plantilla del reporte: {exc}") from exc
    return output_buffer.getvalue()
