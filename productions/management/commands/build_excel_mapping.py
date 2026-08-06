import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from productions.models import TemplateVersion
from productions.services.template_catalog import extract_cost_rates, extract_crews, extract_nuquera_catalog, extract_plate_positions, extract_production_products


PP_REFERENCE_RE = re.compile(r"(?:\+)?PP!\$?C\$?(\d+)", re.IGNORECASE)
CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def column_number(column):
    value = 0
    for character in column:
        value = value * 26 + ord(character) - 64
    return value


def column_letter(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


class Command(BaseCommand):
    help = "Construye y valida el mapa administrativo y de túneles desde el inventario real."

    def add_arguments(self, parser):
        parser.add_argument("--template-code", required=True)
        parser.add_argument("--report", required=True)
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        template = TemplateVersion.objects.get(code=options["template_code"])
        report = json.loads(Path(options["report"]).read_text(encoding="utf-8"))
        sheets = {sheet["name"]: sheet for sheet in report["sheets"]}
        products = extract_production_products(report)
        if not products:
            raise CommandError("No se detectaron las filas exactas de productos en PP.")

        formulas = {
            name: {item["cell"]: item.get("formula", "") for item in sheet.get("formulas", [])}
            for name, sheet in sheets.items()
        }
        physical = {
            name: {item["cell"] for item in sheet.get("non_formula_cells", [])}
            | {item["cell"] for item in sheet.get("formulas", [])}
            for name, sheet in sheets.items()
        }
        original_values = {
            name: {item["cell"]: item.get("value") for item in sheet.get("non_formula_cells", [])}
            for name, sheet in sheets.items()
        }
        mappings = []

        def target_rows(sheet_name):
            result = {}
            for cell, formula in formulas.get(sheet_name, {}).items():
                match = PP_REFERENCE_RE.fullmatch((formula or "").replace("'", ""))
                if match:
                    result[int(match.group(1))] = int(re.search(r"\d+$", cell).group())
            missing = sorted(set(product_by_pp_row) - set(result))
            if missing:
                raise CommandError(f"{sheet_name}: faltan referencias a filas PP {missing[:5]}.")
            return result

        def consecutive_input_columns(sheet_name, crew_column, first_data_row):
            columns = []
            for number in range(column_number(crew_column) + 1, column_number(crew_column) + 10):
                column = column_letter(number)
                cell = f"{column}{first_data_row}"
                if cell in formulas[sheet_name]:
                    break
                if cell not in physical[sheet_name]:
                    break
                columns.append(column)
            if not columns:
                raise CommandError(f"{sheet_name}: no se detectaron columnas de páginas de cuadrilla.")
            return columns

        def add(sheet, field, cell, module, data_type="text", clear_if_missing=None):
            if sheet not in sheets:
                raise CommandError(f"No existe la hoja {sheet}.")
            if cell not in physical[sheet]:
                raise CommandError(f"No existe físicamente {sheet}!{cell}.")
            if cell in formulas[sheet]:
                raise CommandError(f"La celda {sheet}!{cell} contiene fórmula y no puede autorizarse.")
            entry = {
                    "sheet": sheet,
                    "module": module,
                    "field": field,
                    "cell": cell,
                    "data_type": data_type,
                    "editable": True,
                    "contains_formula": False,
                }
            should_clear = not field.startswith("production.") if clear_if_missing is None else clear_if_missing
            should_clear = should_clear and original_values[sheet].get(cell) not in (None, "")
            if should_clear:
                entry["clear_if_missing"] = True
            mappings.append(entry)

        add("PP", "production.number_display", "K7", "administrative")
        add("PP", "production.reception_date", "E10", "administrative", "date")
        add("PP", "production.series", "E14", "administrative")
        add("PP", "production.vehicle_notes", "F14", "administrative", clear_if_missing=True)
        add("R.M", "production.customer", "E10", "reception")
        add("R.M", "production.process", "E11", "reception")
        add("EM-TUN", "production.plant_lot", "D2", "tunnel_packaging")

        add("RECEPCION (2)", "production.customer", "E7", "legacy_reception")
        add("RECEPCION (2)", "production.customer_lot", "T7", "legacy_reception", clear_if_missing=True)
        add("RECEPCION (2)", "production.process", "E8", "legacy_reception")
        add("RECEPCION (2)", "production.reception_date", "T8", "legacy_reception", "date")
        for row in (10, 11, 12):
            for number in range(column_number("C"), column_number("V") + 1):
                cell = f"{column_letter(number)}{row}"
                if cell in physical["RECEPCION (2)"] and cell not in formulas["RECEPCION (2)"] and original_values["RECEPCION (2)"].get(cell) not in (None, ""):
                    add(
                        "RECEPCION (2)",
                        f"template_clear.recepcion2.{cell}",
                        cell,
                        "legacy_reception",
                        clear_if_missing=True,
                    )

        layouts = template.rules.get("tunnel_racks", {})
        product_by_pp_row = {item["excel_row"]: item for item in products}
        for tunnel_code, fills in layouts.items():
            for fill_number, racks in fills.items():
                if not racks:
                    continue
                sheet_name = tunnel_code if fill_number == "1" else f"{tunnel_code} (2)"
                target_row_by_pp_row = target_rows(sheet_name)
                for rack in racks:
                    position_sheet, header_cell = rack["position_key"].split("!", 1)
                    if position_sheet != sheet_name:
                        raise CommandError(f"Posición incompatible: {rack['position_key']}.")
                    column = re.match(r"[A-Z]+", header_cell).group()
                    for pp_row, product in product_by_pp_row.items():
                        target = f"{column}{target_row_by_pp_row[pp_row]}"
                        field = f"tunnel.{tunnel_code}.fill{fill_number}.{rack['position_key']}.{product['code']}.trays"
                        add(sheet_name, field, target, "tunnel", "integer")

                lot_cell = "D2" if tunnel_code == "T6" else "E2"
                if lot_cell not in formulas.get(sheet_name, {}):
                    add(sheet_name, "production.plant_lot", lot_cell, "tunnel")

        plate_rows = target_rows("ENV. PLACAS")
        for position in extract_plate_positions(report):
            sheet_name, header_cell = position["position_key"].split("!", 1)
            column = CELL_RE.match(header_cell).group(1)
            for pp_row, product in product_by_pp_row.items():
                field = f"plates.{position['plate_rack']}.{position['position_key']}.{product['code']}.trays"
                add(sheet_name, field, f"{column}{plate_rows[pp_row]}", "plates", "integer")
        if "E2" not in formulas["ENV. PLACAS"]:
            add("ENV. PLACAS", "production.plant_lot", "E2", "plates")

        for sheet_name, prefix, module in (
            ("EM-TUN", "tunnel_packaging", "tunnel_packaging"),
            ("EM-PLA", "plate_packaging", "plate_packaging"),
        ):
            rows = target_rows(sheet_name)
            pallet_columns = {}
            for item in sheets[sheet_name].get("non_formula_cells", []):
                match = CELL_RE.match(item["cell"])
                pallet = re.fullmatch(r"P(\d+)", str(item.get("value") or ""), re.IGNORECASE)
                if not match or match.group(2) != "5" or not pallet:
                    continue
                number = int(pallet.group(1))
                column = match.group(1)
                if number not in pallet_columns or column_number(column) < column_number(pallet_columns[number]):
                    pallet_columns[number] = column
            expected_max = template.rules["tunnel_pallet_max" if sheet_name == "EM-TUN" else "plate_pallet_max"]
            if sorted(pallet_columns) != list(range(1, expected_max + 1)):
                raise CommandError(f"{sheet_name}: los pallets detectados no coinciden con 1..{expected_max}.")
            for pallet, column in pallet_columns.items():
                for pp_row, product in product_by_pp_row.items():
                    field = f"{prefix}.P{pallet}.{product['code']}.packages"
                    add(sheet_name, field, f"{column}{rows[pp_row]}", module, "integer")
            lot_cell = "D2" if sheet_name == "EM-TUN" else "E2"
            if not any(item["field"] == "production.plant_lot" and item["sheet"] == sheet_name for item in mappings):
                add(sheet_name, "production.plant_lot", lot_cell, module)

        for field, cell in {
            "materials.strech_film.quantity": "E77",
            "materials.rafia.quantity": "E81",
            "materials.plumones.quantity": "E95",
            "materials.hielo.quantity": "E94",
        }.items():
            add("PP", field, cell, "materials", "decimal")

        crews = extract_crews(report)
        for crew in crews:
            add(
                "T1",
                f"crew_roster.{crew['code']}.name",
                f"E{crew['excel_row']}",
                "tunnel_crews",
                clear_if_missing=True,
            )
        for tunnel_code in sorted(layouts):
            for fill_number in ("1", "2"):
                sheet_name = tunnel_code if fill_number == "1" else f"{tunnel_code} (2)"
                crew_column = "E" if sheet_name == "T1" else "H"
                page_columns = consecutive_input_columns(sheet_name, crew_column, 62)
                for crew in crews:
                    for page_number, column in enumerate(page_columns, start=1):
                        field = f"tunnel_crews.{tunnel_code}.fill{fill_number}.PAGINA {page_number}.{crew['code']}.trays"
                        add(sheet_name, field, f"{column}{crew['excel_row']}", "tunnel_crews", "integer")

        for crew in crews:
            for page_number, column in enumerate(consecutive_input_columns("ENV. PLACAS", "J", 61), start=1):
                field = f"plate_crews.PAGINA {page_number}.{crew['code']}.trays"
                add("ENV. PLACAS", field, f"{column}{crew['excel_row'] - 1}", "plate_crews", "integer")

        for vehicle_number in range(1, 11):
            first_column_number = 3 + (vehicle_number - 1) * 2
            first_column = column_letter(first_column_number)
            second_column = column_letter(first_column_number + 1)
            prefix = f"reception.vehicle{vehicle_number}"
            add("R.M", f"{prefix}.product.description", f"{first_column}13", "reception")
            add("R.M", f"{prefix}.plate", f"{first_column}14", "reception")
            add("R.M", f"{prefix}.car_number", f"{first_column}15", "reception")
            add("R.M", f"{prefix}.crew1.name", f"{first_column}16", "reception")
            add("R.M", f"{prefix}.crew2.name", f"{second_column}16", "reception")
            for dino in range(1, 68):
                excel_row = 16 + dino
                add("R.M", f"{prefix}.weight.row{dino}.crew1", f"{first_column}{excel_row}", "reception", "decimal")
                add("R.M", f"{prefix}.weight.row{dino}.crew2", f"{second_column}{excel_row}", "reception", "decimal")

        nuquera_catalog = extract_nuquera_catalog(report)
        for crew_code, sheet_name, time_column, process_cell in (
            ("NUQ-01", "NUQUERAS", "Q", "Q5"),
            ("NUQ-02", "NUQUERAS (2)", "P", "P5"),
        ):
            prefix = f"nuqueras.{crew_code}"
            add(sheet_name, f"{prefix}.day_name", "D5", "nuqueras")
            add(sheet_name, f"{prefix}.crew.name", "C6", "nuqueras")
            add(sheet_name, f"{prefix}.process", process_cell, "nuqueras")
            add(sheet_name, f"{prefix}.start_time", f"{time_column}2", "nuqueras", "time")
            add(sheet_name, f"{prefix}.end_time", f"{time_column}3", "nuqueras", "time")
            for worker in (item for item in nuquera_catalog["workers"] if item.get("block") == "upper"):
                add(sheet_name, f"{prefix}.worker.{worker['internal_code']}.name", f"{worker['column']}7", "nuqueras")
                for row_number in range(1, 36):
                    add(
                        sheet_name,
                        f"{prefix}.worker.{worker['internal_code']}.weight.row{row_number}",
                        f"{worker['column']}{7 + row_number}",
                        "nuqueras",
                        "decimal",
                    )

        paz_workers = [item for item in nuquera_catalog["workers"] if item.get("block") == "lower"]
        add("NUQUERAS", "nuqueras.NUQ-03.crew.name", "C48", "nuqueras")
        for worker in paz_workers:
            add("NUQUERAS", f"nuqueras.NUQ-03.worker.{worker['internal_code']}.name", f"{worker['column']}49", "nuqueras")
            for row_number in range(1, 37):
                add(
                    "NUQUERAS",
                    f"nuqueras.NUQ-03.worker.{worker['internal_code']}.weight.row{row_number}",
                    f"{worker['column']}{49 + row_number}",
                    "nuqueras",
                    "decimal",
                )
        for cell in ("C48", "C49", "D49"):
            add(
                "NUQUERAS (2)",
                f"template_clear.nuqueras2.{cell}",
                cell,
                "nuqueras",
                clear_if_missing=True,
            )

        for rate in extract_cost_rates(report):
            add("RE-COS", f"rates.RE-COS!{rate['cell']}.amount", rate["cell"], "costs", "decimal")

        payload = {
            "version": template.mapping_version,
            "status": "validated",
            "scope": "core_operations",
            "template": {
                "code": template.code,
                "filename": template.original_filename,
                "sha256": report["sha256"],
                "sheet_count": len(report["sheets"]),
            },
            "defaults": template.rules,
            "verified_modules": ["administrative", "reception", "legacy_reception", "nuqueras", "tunnel", "tunnel_crews", "plates", "plate_crews", "tunnel_packaging", "plate_packaging", "materials", "costs"],
            "mappings": mappings,
        }
        output = Path(options["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        # JSON es un subconjunto válido de YAML 1.2 y reduce drásticamente el tiempo de carga
        # de un mapa grande sin cambiar la extensión versionada solicitada.
        output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Mapa validado: {len(mappings)} celdas autorizadas en {output}"))
