from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import re

from django.conf import settings

from productions.models import ProductionOrder, ReceptionEntry, TunnelCrewEntry, TunnelEntry
from productions.services.plate_report import (
    DETAIL_ROWS,
    PlateReportError,
    _split_product_description,
    build_plate_report_xlsx,
)

TUNNEL_REPORT_TEMPLATE = Path(settings.BASE_DIR) / "reference_assets" / "PLANTILLA_ENVASADO_TUNELES.xlsx"
TUNNEL_SUMMARY_ROWS = tuple(range(52, 63))
TUNNEL_SUMMARY_TOTAL_ROW = 63
TUNNEL_FOOTER_LEFT_ROWS = tuple(range(70, 77))
TUNNEL_FOOTER_RIGHT_ROWS = tuple(range(70, 77))
TUNNEL_REPORT_LAST_ROW = 76


class TunnelReportError(PlateReportError):
    pass


@dataclass(frozen=True)
class TunnelReportRow:
    rack: str
    product: str
    summary_product: str
    code_or_weight: str
    tunnel: str
    physical_trays: int | str
    crew: str
    crew_trays: int | str


def _time(value):
    return value.strftime("%H:%M") if value else "PEND."


def _natural_rack_key(code):
    text = (code or "").strip().upper()
    match = re.search(r"(\d+)", text)
    if match:
        return (text[:match.start()], int(match.group(1)), text[match.end():])
    return (text, 0, "")


def _first_reception_plate(production):
    entries = ReceptionEntry.objects.filter(production=production, is_active=True).select_related("vehicle").order_by("created_at", "pk")
    first = entries.filter(car_number="1").first() or entries.first()
    return first.vehicle.plate if first else "SIN REGISTRO"


def _report_rows(production, tunnel=None):
    entry_query = TunnelEntry.objects.filter(production=production, is_active=True)
    crew_query = TunnelCrewEntry.objects.filter(production=production, is_active=True)
    if tunnel is not None:
        entry_query = entry_query.filter(rack__fill__tunnel=tunnel)
        crew_query = crew_query.filter(fill__tunnel=tunnel)
    entries = list(
        entry_query
        .select_related("rack__fill__tunnel", "product")
        .order_by("rack__fill__tunnel__code", "rack__fill__fill_number", "rack__code", "product__description", "pk")
    )
    if not entries:
        raise TunnelReportError("Todavía no hay bandejas guardadas en túneles para generar el reporte.")
    crews = list(
        crew_query
        .select_related("fill__tunnel", "rack", "product", "crew")
        .order_by("fill__tunnel__code", "fill__fill_number", "rack__code", "product__description", "crew__name", "pk")
    )
    products_by_rack = defaultdict(list)
    crews_by_rack = defaultdict(list)
    crews_by_rack_product = defaultdict(list)
    for entry in entries:
        products_by_rack[entry.rack_id].append(entry)
    for entry in crews:
        if entry.rack_id:
            if entry.product_id:
                crews_by_rack_product[(entry.rack_id, entry.product_id)].append(entry)
            else:
                crews_by_rack[entry.rack_id].append(entry)

    rows = []
    for rack_id, products in sorted(products_by_rack.items(), key=lambda item: _natural_rack_key(item[1][0].rack.code)):
        rack = products[0].rack
        legacy_rack_crews = list(crews_by_rack.get(rack_id, []))
        for physical in products:
            product_crews = crews_by_rack_product.get((rack_id, physical.product_id), [])
            row_crews = product_crews or ([legacy_rack_crews.pop(0)] if legacy_rack_crews else [None])
            for index, crew in enumerate(row_crews):
                product_name, weight = _split_product_description(physical.product.description)
                rows.append(TunnelReportRow(
                    rack=rack.code if index == 0 else "",
                    product=product_name if index == 0 else "",
                    summary_product=physical.product.description if index == 0 else "",
                    code_or_weight=weight if index == 0 else "",
                    tunnel=rack.fill.tunnel.code if index == 0 else "",
                    physical_trays=physical.tray_count if index == 0 else "",
                    crew=crew.crew.name if crew else ("PENDIENTE" if index == 0 else ""),
                    crew_trays=crew.tray_count if crew else (0 if index == 0 else ""),
                ))
        for crew in legacy_rack_crews:
            rows.append(TunnelReportRow(
                rack=rack.code,
                product="",
                summary_product="",
                code_or_weight="",
                tunnel=rack.fill.tunnel.code,
                physical_trays="",
                crew=crew.crew.name,
                crew_trays=crew.tray_count,
            ))
    return rows, entries, crews


def _summary(rows):
    totals = defaultdict(int)
    for row in rows:
        if row.summary_product and row.physical_trays != "":
            totals[row.summary_product] += int(row.physical_trays)
    return sorted(totals.items(), key=lambda item: item[0].casefold())


def _crew_footer(rows):
    totals = defaultdict(int)
    for row in rows:
        crew_name = (row.crew or "").strip()
        if not crew_name or crew_name.upper() == "PENDIENTE" or row.crew_trays in {"", None}:
            continue
        totals[crew_name] += int(row.crew_trays)
    values = sorted(totals.items(), key=lambda item: item[0].casefold())
    midpoint = (len(values) + 1) // 2
    left = values[:midpoint]
    right = values[midpoint:]
    updates = {}
    for row_number, (name, trays) in zip(TUNNEL_FOOTER_LEFT_ROWS, left):
        updates[f"F{row_number}"] = f"{name} - {trays} bandejas - {trays * 10:.2f} kg"
    for row_number, (name, trays) in zip(TUNNEL_FOOTER_RIGHT_ROWS, right):
        updates[f"M{row_number}"] = f"{name} - {trays} bandejas - {trays * 10:.2f} kg"
    return updates


def _prepare_tunnel_footer(worksheet_xml):
    if b'r="F70"' in worksheet_xml and b'r="M70"' in worksheet_xml:
        return worksheet_xml

    row_payload = []
    for row_number in range(70, TUNNEL_REPORT_LAST_ROW + 1):
        row_payload.append(
            (
                f'<row r="{row_number}">'
                f'<c r="F{row_number}" t="inlineStr"><is><t xml:space="preserve"></t></is></c>'
                f'<c r="M{row_number}" t="inlineStr"><is><t xml:space="preserve"></t></is></c>'
                f"</row>"
            ).encode("utf-8")
        )
    sheetdata_close = worksheet_xml.find(b"</sheetData>")
    if sheetdata_close < 0:
        raise TunnelReportError("La plantilla de túneles no contiene la estructura de filas esperada.")
    worksheet_xml = (
        worksheet_xml[:sheetdata_close]
        + b"".join(row_payload)
        + worksheet_xml[sheetdata_close:]
    )
    return worksheet_xml


def _page_updates(production, tunnel=None):
    rows, entries, crews = _report_rows(production, tunnel=tunnel)
    fills = {entry.rack.fill for entry in entries}
    launches = sorted(fill.launch_time for fill in fills if fill.launch_time)
    common = {
        "S7": f"PP {production.number}", "U8": min(entry.date for entry in entries).strftime("%d/%m/%Y"),
        "U9": ProductionOrder.Shift(production.shift).label, "E11": production.customer.name,
        "T11": production.customer.tax_id, "E12": production.process,
        "D14": production.customer_lot.strip() or production.plant_lot, "Q14": _first_reception_plate(production),
        "K15": "HORA DE LANZAMIENTO DE TÚNELES (INICIO Y FINAL)",
        "Q15": f"INICIO {_time(launches[0])} / FINAL {_time(launches[-1])}" if launches else "PENDIENTE",
    }
    chunks = [rows[index:index + len(DETAIL_ROWS)] for index in range(0, len(rows), len(DETAIL_ROWS))]
    pages = []
    for page_number, page_rows in enumerate(chunks, 1):
        updates = {**common, "U2": f"{page_number} de {len(chunks)}"}
        updates.update(_crew_footer(page_rows))
        summary = _summary(page_rows)
        if len(summary) > len(TUNNEL_SUMMARY_ROWS) * 2:
            raise TunnelReportError(f"La hoja {page_number} supera la capacidad del resumen de productos.")
        left, right = summary[:len(TUNNEL_SUMMARY_ROWS)], summary[len(TUNNEL_SUMMARY_ROWS):]
        for row_number, (label, total) in zip(TUNNEL_SUMMARY_ROWS, left):
            updates[f"E{row_number}"] = label
            updates[f"G{row_number}"] = total
        for row_number, (label, total) in zip(TUNNEL_SUMMARY_ROWS, right):
            updates[f"M{row_number}"] = label
            updates[f"T{row_number}"] = total
        updates[f"G{TUNNEL_SUMMARY_TOTAL_ROW}"] = sum(total for _, total in left)
        updates[f"T{TUNNEL_SUMMARY_TOTAL_ROW}"] = sum(total for _, total in right) if right else ""
        for row_number, item in zip(DETAIL_ROWS, page_rows):
            updates.update({f"B{row_number}": item.rack, f"D{row_number}": item.product,
                            f"J{row_number}": item.code_or_weight, f"N{row_number}": item.tunnel,
                            f"Q{row_number}": item.physical_trays, f"T{row_number}": item.crew,
                            f"V{row_number}": item.crew_trays})
        pages.append(updates)
    return pages


def build_tunnel_report_xlsx(production, tunnel=None):
    if not TUNNEL_REPORT_TEMPLATE.is_file():
        raise TunnelReportError("No se encontró la plantilla oficial de envasado en túneles.")
    try:
        return build_plate_report_xlsx(
            production,
            template_path=TUNNEL_REPORT_TEMPLATE,
            page_updates=_page_updates(production, tunnel=tunnel),
            apply_typography=False,
            centered_cell_refs={f"V{row}" for row in DETAIL_ROWS},
            footer_cell_refs={*(f"F{row}" for row in TUNNEL_FOOTER_LEFT_ROWS), *(f"M{row}" for row in TUNNEL_FOOTER_RIGHT_ROWS)},
            report_last_row=TUNNEL_REPORT_LAST_ROW,
            worksheet_preparer=_prepare_tunnel_footer,
        )
    except PlateReportError as exc:
        raise TunnelReportError(str(exc)) from exc
