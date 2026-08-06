from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from django.db import transaction

from productions.models import Crew, Material, PlatePosition, Product, Rate, Worker


CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")
RACK_RE = re.compile(r"^R0?\d+$", re.IGNORECASE)
PALLET_RE = re.compile(r"^P(\d+)$", re.IGNORECASE)
KG_QUANTUM = Decimal("0.01")


def _column_number(column):
    value = 0
    for character in column:
        value = value * 26 + ord(character) - 64
    return value


def _sheet(report, name):
    return next((sheet for sheet in report["sheets"] if sheet["name"] == name), None)


def _cell_map(sheet):
    return {cell["cell"]: cell.get("value") for cell in sheet.get("non_formula_cells", [])}


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _kg_decimal(value):
    parsed = _decimal(value)
    return parsed.quantize(KG_QUANTUM, rounding=ROUND_HALF_UP) if parsed is not None else None


def extract_products(report):
    sheet = _sheet(report, "COLORES")
    if not sheet:
        return []
    cells = _cell_map(sheet)
    products = []
    for row in range(1, 1000):
        description = str(cells.get(f"H{row}") or "").strip()
        code = str(cells.get(f"L{row}") or "").strip()
        standard = _kg_decimal(cells.get(f"I{row}"))
        plus = _kg_decimal(cells.get(f"J{row}"))
        packaging = _kg_decimal(cells.get(f"K{row}"))
        if not description or not code or standard is None or plus is None or packaging is None:
            continue
        products.append(
            {
                "description": description,
                "code": code,
                "color": str(cells.get(f"M{row}") or "").strip(),
                "presentation": code,
                "standard_weight_kg": standard,
                "plus_weight_kg": plus,
                "packaging_weight_kg": packaging,
            }
        )
    return products


def extract_production_products(report):
    """Productos exactos de las filas operativas del PP, sin alterar sus descripciones."""

    sheet = _sheet(report, "PP")
    if not sheet:
        return []
    cells = _cell_map(sheet)
    products = []
    for row in range(1, 1000):
        ordinal = _decimal(cells.get(f"B{row}"))
        description = str(cells.get(f"C{row}") or "").strip()
        if ordinal is None or ordinal != ordinal.to_integral_value() or not description:
            continue
        number = int(ordinal)
        if number < 1 or number > 500:
            continue
        products.append({"description": description, "code": f"PP-{number:03d}", "excel_row": row})
    return products


def extract_crews(report):
    sheet = _sheet(report, "T1")
    if not sheet:
        return []
    cells = _cell_map(sheet)
    crews = []
    for row in range(62, 73):
        name = str(cells.get(f"E{row}") or "").strip()
        if name:
            crews.append({"code": f"CUAD-{len(crews) + 1:02d}", "name": name, "excel_row": row})
    return crews


def extract_reception_products(report):
    sheet = _sheet(report, "R.M")
    if not sheet:
        return []
    cells = _cell_map(sheet)
    seen = set()
    products = []
    for column in ("C", "E", "G", "I", "K", "M", "O", "Q", "S", "U"):
        description = str(cells.get(f"{column}13") or "").strip()
        if description and description not in seen:
            seen.add(description)
            products.append({"code": f"RM-{len(products) + 1:02d}", "description": description})
    return products


def extract_reception_crews(report):
    """Cuadrillas de la lista validada por R.M!C16:V16."""

    sheet = _sheet(report, "R.M")
    if not sheet:
        return []
    cells = _cell_map(sheet)
    crews = []
    for index, row in enumerate(range(2, 5), start=1):
        name = str(cells.get(f"Z{row}") or "").strip()
        if name:
            crews.append({"code": f"RM-CUAD-{index:02d}", "name": name})
    return crews


def extract_nuquera_catalog(report):
    crews = []
    workers = []
    for index, sheet_name in enumerate(("NUQUERAS", "NUQUERAS (2)"), start=1):
        sheet = _sheet(report, sheet_name)
        if not sheet:
            continue
        cells = _cell_map(sheet)
        name = str(cells.get("C6") or "").strip()
        if name:
            crews.append({"code": f"NUQ-{index:02d}", "name": name})
        if index == 1:
            for column_number in range(3, 16):
                column = ""
                number = column_number
                while number:
                    number, remainder = divmod(number - 1, 26)
                    column = chr(65 + remainder) + column
                full_name = str(cells.get(f"{column}7") or "").strip()
                if full_name:
                    workers.append(
                        {
                            "internal_code": f"NUQ-W{len(workers) + 1:02d}",
                            "full_name": full_name,
                            "column": column,
                            "block": "upper",
                            "crew_code": "NUQ-01",
                            "crew_name": crews[0]["name"] if crews else "",
                        }
                    )

    primary_sheet = _sheet(report, "NUQUERAS")
    if primary_sheet:
        cells = _cell_map(primary_sheet)
        paz_name = str(cells.get("C48") or "").strip()
        if paz_name:
            crews.append({"code": "NUQ-03", "name": paz_name})
            for column_number in range(3, 16):
                column = ""
                number = column_number
                while number:
                    number, remainder = divmod(number - 1, 26)
                    column = chr(65 + remainder) + column
                full_name = str(cells.get(f"{column}49") or "").strip()
                if full_name:
                    workers.append(
                        {
                            "internal_code": f"NUQ-W{len(workers) + 1:02d}",
                            "full_name": full_name,
                            "column": column,
                            "block": "lower",
                            "crew_code": "NUQ-03",
                            "crew_name": paz_name,
                        }
                    )
    return {"crews": crews, "workers": workers}


def extract_cost_rates(report):
    sheet = _sheet(report, "RE-COS")
    if not sheet:
        return []
    cells = _cell_map(sheet)
    rates = []
    for cell, value in cells.items():
        match = CELL_RE.match(cell)
        amount = _decimal(value)
        if not match or amount is None or amount <= 0:
            continue
        column, row = match.group(1), int(match.group(2))
        header_row = None
        for candidate in range(row - 1, max(0, row - 16), -1):
            label = str(cells.get(f"{column}{candidate}") or "").upper().replace(" ", "")
            if "MONTOS/." in label:
                header_row = candidate
                break
        if header_row is None:
            continue
        row_labels = []
        for candidate_column in range(2, _column_number(column)):
            candidate = ""
            number = candidate_column
            while number:
                number, remainder = divmod(number - 1, 26)
                candidate = chr(65 + remainder) + candidate
            label = str(cells.get(f"{candidate}{row}") or "").strip()
            if label:
                row_labels.append(label)
        context = row_labels[0] if row_labels else f"fila {row}"
        rates.append(
            {
                "cell": cell,
                "amount": amount,
                "process": f"RE-COS!{cell} · {context}",
                "unit": "por 1000 kg",
            }
        )
    return rates


def extract_tunnel_racks(report):
    result = {}
    for tunnel_number in range(1, 7):
        tunnel_code = f"T{tunnel_number}"
        result[tunnel_code] = {}
        for fill_number in (1, 2):
            sheet_name = tunnel_code if fill_number == 1 else f"{tunnel_code} (2)"
            sheet = _sheet(report, sheet_name)
            candidates = []
            if sheet:
                for cell in sheet.get("non_formula_cells", []):
                    match = CELL_RE.match(cell["cell"])
                    value = str(cell.get("value") or "").strip()
                    if match and match.group(2) == "5" and RACK_RE.match(value):
                        candidates.append((_column_number(match.group(1)), value, cell["cell"]))
            seen = set()
            racks = []
            for _, code, cell in sorted(candidates):
                normalized = code.upper()
                if normalized in seen:
                    continue
                seen.add(normalized)
                racks.append({"code": code, "position_key": f"{sheet_name}!{cell}"})
            result[tunnel_code][str(fill_number)] = racks
    return result


def extract_plate_positions(report):
    sheet = _sheet(report, "ENV. PLACAS")
    if not sheet:
        return []
    counters = {"P1": 0, "P2": 0, "P3": 0}
    positions = []
    for cell in sheet.get("non_formula_cells", []):
        match = CELL_RE.match(cell["cell"])
        value = str(cell.get("value") or "").strip().upper()
        if not match or match.group(2) != "5" or value not in counters:
            continue
        column = _column_number(match.group(1))
        if not 5 <= column <= 28:  # E:AB, bloque real de ingreso
            continue
        counters[value] += 1
        positions.append(
            {
                "plate_rack": value,
                "position_key": f"ENV. PLACAS!{cell['cell']}",
                "display_name": (
                    f"Bachada {counters[value]} · Plaquero {int(value.removeprefix('P'))}"
                ),
            }
        )
    return positions


def _maximum_pallet(report, sheet_name):
    sheet = _sheet(report, sheet_name)
    maximum = None
    if sheet:
        for cell in sheet.get("non_formula_cells", []):
            match = PALLET_RE.match(str(cell.get("value") or "").strip())
            if match:
                maximum = max(maximum or 0, int(match.group(1)))
    return maximum


@transaction.atomic
def sync_template_catalog(template_version, report_path):
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    products_created = 0
    for item in extract_products(report):
        _, created = Product.objects.update_or_create(
            description=item["description"],
            code=item["code"],
            defaults={
                "color": item["color"],
                "presentation": item["presentation"],
                "standard_weight_kg": item["standard_weight_kg"],
                "plus_weight_kg": item["plus_weight_kg"],
                "packaging_weight_kg": item["packaging_weight_kg"],
                "active": True,
            },
        )
        products_created += int(created)

    for item in extract_production_products(report):
        _, created = Product.objects.update_or_create(
            description=item["description"],
            code=item["code"],
            defaults={"presentation": "Producto exacto de la plantilla", "active": True},
        )
        products_created += int(created)

    for item in extract_reception_products(report):
        _, created = Product.objects.update_or_create(
            description=item["description"],
            code=item["code"],
            defaults={"presentation": "Materia prima de recepción", "active": True},
        )
        products_created += int(created)

    reception_crews_created = 0
    for item in extract_reception_crews(report):
        _, created = Crew.objects.update_or_create(
            name=item["name"],
            defaults={"code": item["code"], "active": True},
        )
        reception_crews_created += int(created)

    crews_created = 0
    for item in extract_crews(report):
        _, created = Crew.objects.update_or_create(
            code=item["code"],
            defaults={"name": item["name"], "active": True},
        )
        crews_created += int(created)

    nuquera_catalog = extract_nuquera_catalog(report)
    for item in nuquera_catalog["crews"]:
        _, created = Crew.objects.get_or_create(
            name=item["name"],
            defaults={"code": item["code"], "active": True},
        )
        crews_created += int(created)

    workers_created = 0
    nuquera_crews = {crew.name: crew for crew in Crew.objects.filter(name__in=[item["name"] for item in nuquera_catalog["crews"]])}
    for item in nuquera_catalog["workers"]:
        _, created = Worker.objects.update_or_create(
            internal_code=item["internal_code"],
            defaults={
                "full_name": item["full_name"],
                "crew": nuquera_crews.get(item["crew_name"]),
                "position": "Nuquera",
                "active": True,
            },
        )
        workers_created += int(created)

    rates_created = 0
    for item in extract_cost_rates(report):
        _, created = Rate.objects.update_or_create(
            process=item["process"],
            effective_from=date(2000, 1, 1),
            defaults={"amount": item["amount"], "unit": item["unit"], "active": True},
        )
        rates_created += int(created)

    _, hielo_created = Material.objects.update_or_create(
        name="Hielo",
        defaults={"unit": "kg", "active": True},
    )

    positions_created = 0
    for item in extract_plate_positions(report):
        _, created = PlatePosition.objects.update_or_create(
            template_version=template_version,
            position_key=item["position_key"],
            defaults={
                "plate_rack": item["plate_rack"],
                "display_name": item["display_name"],
                "max_trays": template_version.rules.get("plate_rack_max_trays", 189),
                "active": True,
            },
        )
        positions_created += int(created)

    rules = {
        **template_version.rules,
        "tunnel_racks": extract_tunnel_racks(report),
        "tunnel_pallet_max": _maximum_pallet(report, "EM-TUN"),
        "plate_pallet_max": _maximum_pallet(report, "EM-PLA"),
    }
    template_version.rules = rules
    template_version.save(update_fields=["rules"])
    return {
        "products": len(extract_products(report)) + len(extract_production_products(report)) + len(extract_reception_products(report)),
        "products_created": products_created,
        "crews": len(extract_crews(report)),
        "crews_created": crews_created,
        "reception_crews": len(extract_reception_crews(report)),
        "reception_crews_created": reception_crews_created,
        "workers": len(nuquera_catalog["workers"]),
        "workers_created": workers_created,
        "rates": len(extract_cost_rates(report)),
        "rates_created": rates_created,
        "materials_created": int(hielo_created),
        "positions": len(extract_plate_positions(report)),
        "positions_created": positions_created,
    }
