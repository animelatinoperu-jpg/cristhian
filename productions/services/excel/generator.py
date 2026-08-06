from __future__ import annotations

import hashlib
import datetime as dt
import re
import tempfile
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.db.models import Q

from productions.models import (
    AuditLog,
    GeneratedFile,
    MaterialUsage,
    PlatePackagingAllocation,
    PlatePalletLine,
    ProductionOrder,
    Rate,
    Role,
    TunnelCrewEntry,
)
from productions.services.permissions import require_roles
from .analyzer import XlsmAnalyzer
from .integrity_checker import IntegrityChecker
from .mapper import load_mapping
from .writer import SafeXlsmWriter
from .validator import validate_output_file


class GenerationError(RuntimeError):
    pass


FINAL_MAPPING_MODULES = frozenset(
    {
        "administrative",
        "reception",
        "legacy_reception",
        "nuqueras",
        "tunnel",
        "tunnel_crews",
        "plates",
        "plate_crews",
        "tunnel_packaging",
        "plate_packaging",
        "materials",
        "costs",
    }
)

KG_QUANTUM = Decimal("0.01")
TUNNEL_CREW_SLOTS = tuple(f"CUAD-{number:02d}" for number in range(1, 12))
TUNNEL_PAYMENT_SHEET = "T.EMV"
TUNNEL_PAYMENT_KG_CELL_RE = re.compile(r"^[CHM]\d+$")
TUNNEL_PAYMENT_SOURCE_RE = re.compile(
    r"'(?P<sheet>T[1-6](?: \(2\))?)'!\$?(?P<cell>[A-Z]+\$?\d+)"
)
TUNNEL_PAYMENT_HEADER_RE = re.compile(r"^\+'T1'!\$?E\$?(?P<row>6[2-9]|7[0-2])$")
TUNNEL_PAYMENT_MIRROR_HEADER_RE = re.compile(
    r"^\+(?P<source>[BGL](?:6|30|55|79))$"
)
PAYMENT_ITEM_COLUMN = {"C": "B", "H": "G", "M": "L"}
PAYMENT_HEADER_SLOT_BY_CELL = {
    cell: slot_number
    for slot_number, cell in enumerate(
        ("B6", "G6", "L6", "B30", "G30", "L30", "B55", "G55", "L55", "B79", "G79"),
        start=1,
    )
}


def _kg_value(value):
    return Decimal(str(value)).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP)


def _tunnel_payment_overrides(template_path, mapping, values):
    """Detalla bandejas y kg por cuadrilla dentro del cuadro T.EMV.

    Las hojas T1-T6 deben conservar sus cantidades en bandejas para que la
    conciliación de racks siga funcionando. T.EMV referencia directamente las
    celdas de bandejas, las multiplica por 10 y guarda el resultado calculado
    para que sea visible incluso antes del primer recálculo de Excel.
    """
    report = XlsmAnalyzer(template_path).analyze()
    payment_sheet = next(
        (sheet for sheet in report["sheets"] if sheet["name"] == TUNNEL_PAYMENT_SHEET),
        None,
    )
    if payment_sheet is None:
        return {}, {}
    template_values = {
        item["cell"]: item["value"]
        for item in payment_sheet.get("non_formula_cells", [])
    }
    tunnel_mappings = [
        item for item in mapping.get("mappings", [])
        if item.get("module") == "tunnel_crews"
    ]
    formula_overrides = {}
    cell_overrides = {}
    for item in payment_sheet.get("formulas", []):
        cell = item["cell"].replace("$", "")
        formula = item["formula"].strip()
        header_match = TUNNEL_PAYMENT_HEADER_RE.fullmatch(formula)
        if header_match:
            slot_number = int(header_match.group("row")) - 61
            crew_name = values.get(
                f"crew_roster.CUAD-{slot_number:02d}.name",
                "",
            )
            source = f"'T1'!E{header_match.group('row')}"
            formula_overrides[(TUNNEL_PAYMENT_SHEET, cell)] = {
                "formula": f'IF({source}<>"",{source},"DISPONIBLE")',
                "cached_value": str(crew_name or "DISPONIBLE"),
            }
            continue
        mirror_header_match = TUNNEL_PAYMENT_MIRROR_HEADER_RE.fullmatch(formula)
        if mirror_header_match:
            source = mirror_header_match.group("source")
            slot_number = PAYMENT_HEADER_SLOT_BY_CELL[source]
            crew_name = values.get(
                f"crew_roster.CUAD-{slot_number:02d}.name",
                "",
            )
            formula_overrides[(TUNNEL_PAYMENT_SHEET, cell)] = {
                "formula": f'IF({source}<>"",{source},"DISPONIBLE")',
                "cached_value": str(crew_name or "DISPONIBLE"),
            }
            continue
        if not TUNNEL_PAYMENT_KG_CELL_RE.fullmatch(cell):
            continue
        source_refs = list(TUNNEL_PAYMENT_SOURCE_RE.finditer(formula))
        if not source_refs:
            continue
        item_cell = f"{PAYMENT_ITEM_COLUMN[cell[0]]}{re.search(r'\d+', cell).group()}"
        base_label = str(template_values.get(item_cell) or "").upper()
        wants_second_fill = "SEGUNDA" in base_label
        chosen_source = next(
            (
                source
                for source in source_refs
                if (" (2)" in source.group("sheet")) == wants_second_fill
            ),
            source_refs[0],
        )
        source_sheet = chosen_source.group("sheet")
        source_row = int(re.search(r"\d+", chosen_source.group("cell")).group())
        slot_number = source_row - 61
        if not 1 <= slot_number <= len(TUNNEL_CREW_SLOTS):
            continue
        tunnel_code = source_sheet.split()[0]
        fill_number = 2 if source_sheet.endswith("(2)") else 1
        slot = f"CUAD-{slot_number:02d}"
        field_prefix = f"tunnel_crews.{tunnel_code}.fill{fill_number}."
        field_suffix = f".{slot}.trays"
        targets = sorted(
            (
                target
                for target in tunnel_mappings
                if target["field"].startswith(field_prefix)
                and target["field"].endswith(field_suffix)
            ),
            key=lambda target: (target["sheet"], target["cell"]),
        )
        if not targets:
            continue
        tray_total = sum(
            Decimal(str(values.get(target["field"], 0) or 0))
            for target in targets
        )
        references = ",".join(
            f"'{target['sheet']}'!{target['cell'].replace('$', '')}"
            for target in targets
        )
        formula_overrides[(TUNNEL_PAYMENT_SHEET, cell)] = {
            "formula": f"SUM({references})*10",
            "cached_value": _kg_value(tray_total * Decimal("10")),
        }
        if tray_total:
            detail_label = f"{tunnel_code}{'-2' if fill_number == 2 else ''} · {tray_total:0.0f} BDJ"
            cell_overrides[(TUNNEL_PAYMENT_SHEET, item_cell)] = detail_label
    return formula_overrides, cell_overrides


def _effective_mapping_scope(mapping):
    """Considera completo un mapa validado que cubre todos los modulos editables.

    La plantilla PP-V2 tiene 28 hojas. Siete son hojas de resumen, formulas o
    catalogos y no reciben escrituras directas; sus formulas consumen las 21
    hojas operativas mapeadas. Por eso la cobertura se valida por modulos y no
    por la presencia de escrituras directas en cada hoja.
    """
    declared_scope = mapping.get("scope", "full")
    verified_modules = set(mapping.get("verified_modules") or ())
    template_sheet_count = (mapping.get("template") or {}).get("sheet_count")
    if (
        declared_scope == "full"
        or (
            mapping.get("status") == "validated"
            and template_sheet_count == 28
            and FINAL_MAPPING_MODULES.issubset(verified_modules)
        )
    ):
        return "full"
    return declared_scope


def _safe_lot(value):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-") or "SIN-LOTE"


def _automatic_customer_lot(production):
    return f"PPF{production.reception_date:%d%m%Y}"


def _automatic_packaging_date(production):
    return production.reception_date + dt.timedelta(days=1)


def _page_key(value):
    match = re.search(r"\d+", str(value or ""))
    return f"PAGINA {int(match.group())}" if match else str(value or "").strip().upper()


def mapping_path_for_template(template_version):
    return Path(settings.BASE_DIR) / "config" / f"excel_mapping_{template_version.mapping_version}.yaml"


def mapping_capabilities(template_version):
    path = mapping_path_for_template(template_version)
    try:
        mapping = load_mapping(path)
    except Exception as exc:
        return {"ready": False, "scope": "none", "path": path, "error": str(exc)}
    return {"ready": True, "scope": _effective_mapping_scope(mapping), "path": path, "error": ""}


def production_values(production):
    customer_lot = _automatic_customer_lot(production)
    packaging_date = _automatic_packaging_date(production)
    values = {
        "production.number": production.number,
        "production.number_display": f"N° {production.number}",
        "production.plant_lot": production.plant_lot,
        "production.customer_lot": customer_lot,
        "production.customer": production.customer.name,
        "production.process": production.process,
        "production.main_product.code": production.main_product.code,
        "production.main_product.description": production.main_product.description,
        "production.reception_date": production.reception_date,
        "production.production_date": production.production_date,
        "production.packaging_date": packaging_date,
        "production.shift": production.get_shift_display(),
        "production.series": "001",
        "production.vehicle_notes": production.vehicle_notes,
        "production.plate_notes": production.plate_notes,
        "production.observations": production.observations,
    }
    reception_entries = list(production.receptionentry_set.filter(is_active=True).select_related("vehicle", "product", "crew").order_by("date", "time", "pk"))
    for index, entry in enumerate(reception_entries, start=1):
        prefix = f"reception.row{index}"
        values.update({f"{prefix}.date": entry.date, f"{prefix}.vehicle": entry.vehicle.plate, f"{prefix}.car_number": entry.car_number, f"{prefix}.product": entry.product.code, f"{prefix}.crew": entry.crew.code if entry.crew else None, f"{prefix}.container": entry.container, f"{prefix}.weight_kg": _kg_value(entry.weight_kg), f"{prefix}.time": entry.time})
    vehicles = {}
    for entry in reception_entries:
        vehicles.setdefault(entry.vehicle_id, []).append(entry)
    if len(vehicles) > 9:
        raise ValueError("R.M admite como máximo 9 vehículos en la plantilla asignada.")
    for vehicle_index, entries in enumerate(vehicles.values(), start=1):
        products = {entry.product_id: entry.product for entry in entries}
        if len(products) > 1:
            raise ValueError(f"El vehículo {entries[0].vehicle.plate} tiene más de un producto; R.M solo admite uno por vehículo.")
        car_numbers = {entry.car_number.strip() for entry in entries if entry.car_number.strip()}
        if len(car_numbers) > 1:
            raise ValueError(f"El vehículo {entries[0].vehicle.plate} tiene números de carro distintos.")
        crew_ids = []
        for entry in entries:
            crew_id = entry.crew_id or 0
            if crew_id not in crew_ids:
                crew_ids.append(crew_id)
        if len(crew_ids) > 2:
            raise ValueError(f"El vehículo {entries[0].vehicle.plate} supera las dos cuadrillas permitidas por R.M.")
        prefix = f"reception.vehicle{vehicle_index}"
        first = entries[0]
        values.update(
            {
                f"{prefix}.product.description": first.product.description,
                f"{prefix}.plate": first.vehicle.plate,
                f"{prefix}.car_number": next(iter(car_numbers), ""),
            }
        )
        for slot, crew_id in enumerate(crew_ids, start=1):
            crew = next((entry.crew for entry in entries if (entry.crew_id or 0) == crew_id), None)
            values[f"{prefix}.crew{slot}.name"] = crew.name if crew else ""
        occupied = set()
        for entry in entries:
            slot = crew_ids.index(entry.crew_id or 0) + 1
            row = int(entry.container)
            key = (row, slot)
            if key in occupied:
                raise ValueError(f"Dino {row} duplicado para la misma cuadrilla y vehículo {entry.vehicle.plate}.")
            occupied.add(key)
            values[f"{prefix}.weight.row{row}.crew{slot}"] = _kg_value(entry.weight_kg)
    nuquera_entries = list(production.nuqueraentry_set.filter(is_active=True).select_related("crew", "worker").order_by("date", "start_time", "pk"))
    for index, entry in enumerate(nuquera_entries, start=1):
        prefix = f"nuqueras.row{index}"
        values.update({f"{prefix}.date": entry.date, f"{prefix}.shift": entry.get_shift_display(), f"{prefix}.crew": entry.crew.code, f"{prefix}.worker": entry.worker.internal_code, f"{prefix}.process": entry.process, f"{prefix}.weight_kg": _kg_value(entry.weight_kg), f"{prefix}.start_time": entry.start_time, f"{prefix}.end_time": entry.end_time})
    nuquera_groups = {}
    for entry in nuquera_entries:
        slot = entry.crew.code if entry.crew.code in {"NUQ-01", "NUQ-02", "NUQ-03"} else "NUQ-01"
        nuquera_groups.setdefault(slot, []).append(entry)
    day_names = ("LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO")
    for crew_code, entries in nuquera_groups.items():
        processes = {entry.process.strip() for entry in entries}
        starts = {entry.start_time for entry in entries}
        ends = {entry.end_time for entry in entries}
        dates = {entry.date for entry in entries}
        if any(len(items) > 1 for items in (processes, starts, ends, dates)):
            raise ValueError(f"Los registros de {entries[0].crew.name} deben compartir fecha, proceso y horario.")
        first = entries[0]
        prefix = f"nuqueras.{crew_code}"
        values.update(
            {
                f"{prefix}.day_name": day_names[first.date.weekday()],
                f"{prefix}.crew.name": first.crew.name,
                f"{prefix}.process": first.process,
                f"{prefix}.start_time": first.start_time,
                f"{prefix}.end_time": first.end_time,
            }
        )
        by_worker = {}
        for entry in entries:
            by_worker.setdefault(entry.worker.internal_code, []).append(entry)
        for worker_code, worker_entries in by_worker.items():
            if not worker_code.startswith("NUQ-W"):
                raise ValueError(f"El trabajador {worker_entries[0].worker.full_name} no tiene posición en la hoja NUQUERAS.")
            available_rows = 36 if crew_code == "NUQ-03" else 35
            if len(worker_entries) > available_rows:
                raise ValueError(
                    f"El trabajador {worker_entries[0].worker.full_name} supera las {available_rows} filas disponibles."
                )
            values[f"{prefix}.worker.{worker_code}.name"] = worker_entries[0].worker.full_name
            for row_number, entry in enumerate(worker_entries, start=1):
                values[f"{prefix}.worker.{worker_code}.weight.row{row_number}"] = _kg_value(entry.weight_kg)
    for fill in production.tunnel_fills.filter(is_active=True).prefetch_related("racks__entries__product"):
        for rack in fill.racks.all():
            for entry in rack.entries.filter(is_active=True):
                values[f"tunnel.{fill.tunnel.code}.fill{fill.fill_number}.{rack.position_key}.{entry.product.code}.trays"] = entry.tray_count
    for entry in production.plateentry_set.filter(is_active=True).select_related("position", "product"):
        values[f"plates.{entry.position.plate_rack}.{entry.position.position_key}.{entry.product.code}.trays"] = entry.tray_count
    # The fill is the authoritative owner of a tunnel crew record.  Querying
    # through it also recovers historical rows whose duplicated production_id
    # was stored incorrectly and would otherwise disappear from the Excel.
    tunnel_crew_entries = list(
        TunnelCrewEntry.objects.filter(fill__production=production, fill__is_active=True, is_active=True)
        .select_related("fill__tunnel", "crew")
    )
    plate_crew_entries = list(
        production.platecrewentry_set.filter(is_active=True).select_related("position", "crew")
    )
    participating_crews = {
        entry.crew_id: entry.crew
        for entry in [*tunnel_crew_entries, *plate_crew_entries]
    }
    ordered_crews = sorted(participating_crews.values(), key=lambda crew: (crew.name.casefold(), crew.pk))
    if len(ordered_crews) > len(TUNNEL_CREW_SLOTS):
        raise ValueError(
            f"El PP tiene {len(ordered_crews)} cuadrillas participantes, pero la plantilla actual admite "
            f"{len(TUNNEL_CREW_SLOTS)}. Desactive o consolide las cuadrillas excedentes antes de generar el Excel."
        )
    crew_slot_by_id = {}
    for slot, crew in zip(TUNNEL_CREW_SLOTS, ordered_crews):
        crew_slot_by_id[crew.pk] = slot
        values[f"crew_roster.{slot}.name"] = crew.name

    tunnel_crew_totals = defaultdict(int)
    for entry in tunnel_crew_entries:
        page = _page_key(entry.page_or_block)
        slot = crew_slot_by_id[entry.crew_id]
        key = f"tunnel_crews.{entry.fill.tunnel.code}.fill{entry.fill.fill_number}.{page}.{slot}.trays"
        tunnel_crew_totals[key] += entry.tray_count
    values.update(tunnel_crew_totals)

    plate_crew_totals = defaultdict(int)
    for entry in plate_crew_entries:
        slot = crew_slot_by_id[entry.crew_id]
        detail_key = f"plate_crews.{entry.position.plate_rack}.{entry.position.position_key}.{entry.page}.{slot}.trays"
        values[detail_key] = entry.tray_count
        total_key = f"plate_crews.{_page_key(entry.page)}.{slot}.trays"
        plate_crew_totals[total_key] += entry.tray_count
    values.update(plate_crew_totals)
    for entry in production.tunnelpackagingentry_set.filter(is_active=True).select_related("product"):
        prefix = f"tunnel_packaging.P{entry.pallet_number}.{entry.product.code}"
        values.update({f"{prefix}.packages": entry.package_count, f"{prefix}.trays": entry.tray_count, f"{prefix}.kg": _kg_value(entry.kilos), f"{prefix}.date": entry.date})
    plate_packaging_totals = defaultdict(lambda: {"packages": 0, "date": None})
    for entry in production.platepackagingentry_set.filter(
        is_active=True
    ).select_related("product"):
        item = plate_packaging_totals[
            (entry.pallet_number, entry.product.code)
        ]
        item["packages"] += entry.package_count
        if item["date"] is None or entry.date > item["date"]:
            item["date"] = entry.date
    for allocation in PlatePackagingAllocation.objects.filter(
        production=production,
        is_active=True,
    ).select_related("source_entry__product"):
        item = plate_packaging_totals[
            (
                allocation.pallet_number,
                allocation.source_entry.product.code,
            )
        ]
        item["packages"] += allocation.package_count
        if item["date"] is None or allocation.date > item["date"]:
            item["date"] = allocation.date
    for line in PlatePalletLine.objects.filter(
        production=production,
        is_active=True,
    ).select_related("pallet", "product"):
        item = plate_packaging_totals[
            (line.pallet.pallet_number, line.product.code)
        ]
        item["packages"] += line.package_count
        if item["date"] is None or line.date > item["date"]:
            item["date"] = line.date
    package_trays = production.template_version.rules.get("package_trays", 2)
    package_kg = production.template_version.rules.get("package_kg", 20)
    for (pallet_number, product_code), item in plate_packaging_totals.items():
        prefix = f"plate_packaging.P{pallet_number}.{product_code}"
        values.update(
            {
                f"{prefix}.packages": item["packages"],
                f"{prefix}.trays": item["packages"] * package_trays,
                f"{prefix}.kg": _kg_value(item["packages"] * package_kg),
                f"{prefix}.date": item["date"],
            }
        )
    for entry in production.materialusage_set.filter(
        is_active=True,
        material__name__in=MaterialUsage.EXCEL_INPUT_MATERIAL_NAMES,
    ).select_related("material"):
        key = re.sub(r"[^a-z0-9]+", "_", entry.material.name.lower()).strip("_")
        values[f"materials.{key}.quantity"] = (
            _kg_value(entry.quantity)
            if entry.material.unit.strip().lower() == "kg"
            else entry.quantity
        )
    for index, entry in enumerate(production.costentry_set.filter(is_active=True).order_by("pk"), start=1):
        values.update({f"costs.row{index}.concept": entry.concept, f"costs.row{index}.quantity": entry.quantity, f"costs.row{index}.unit_cost": entry.unit_cost, f"costs.row{index}.total": entry.total})
    seen_rates = set()
    rates = Rate.objects.filter(active=True, effective_from__lte=production.production_date).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=production.production_date)
    ).order_by("process", "-effective_from", "-pk")
    for rate in rates:
        match = re.match(r"^(RE-COS![A-Z]+\d+)", rate.process)
        if match and match.group(1) not in seen_rates:
            seen_rates.add(match.group(1))
            values[f"rates.{match.group(1)}.amount"] = rate.amount
    return values


@transaction.atomic
def generate_production_workbook(*, production_id, user, kind, mapping_path=None):
    require_roles(user, Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER)
    production = ProductionOrder.objects.select_for_update().select_related("template_version", "customer", "main_product").get(pk=production_id)
    mapping_path = Path(mapping_path) if mapping_path else mapping_path_for_template(production.template_version)
    try:
        mapping = load_mapping(mapping_path)
    except Exception as exc:
        raise GenerationError(str(exc)) from exc
    if kind == GeneratedFile.Kind.FINAL and production.status not in {ProductionOrder.Status.APPROVED, ProductionOrder.Status.CLOSED}:
        raise GenerationError("El Excel final solo se genera para una producción aprobada o cerrada.")
    if kind == GeneratedFile.Kind.FINAL and _effective_mapping_scope(mapping) != "full":
        raise GenerationError("El Excel final todavía no está habilitado: falta validar el mapa completo de las 28 hojas. Puede generar el preliminar con los campos ya verificados.")
    template_path = Path(production.template_version.file.path)
    if not template_path.is_file():
        raise GenerationError("No está disponible la copia privada de la plantilla asignada.")
    sequence = (production.generated_files.filter(kind=kind).order_by("-sequence").values_list("sequence", flat=True).first() or 0) + 1
    suffix = "FINAL" if kind == GeneratedFile.Kind.FINAL else "PRELIMINAR"
    filename = f"PP_{production.number}_{_safe_lot(production.plant_lot)}_{suffix}_v{sequence}.xlsm"
    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir) / filename
        values = production_values(production)
        formula_overrides, cell_overrides = _tunnel_payment_overrides(
            template_path,
            mapping,
            values,
        )
        try:
            _, authorized = SafeXlsmWriter(template_path, mapping_path).write(
                output,
                values,
                formula_overrides=formula_overrides,
                cell_overrides=cell_overrides,
            )
        except Exception as exc:
            raise GenerationError(str(exc)) from exc
        if output_issues := validate_output_file(output):
            raise GenerationError("Archivo generado inválido: " + "; ".join(output_issues))
        integrity = IntegrityChecker(
            template_path,
            output,
            authorized,
            formula_overrides=formula_overrides,
        ).check()
        if not integrity["valid"]:
            raise GenerationError("Falló el control de integridad: " + "; ".join(integrity["issues"][:5]))
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        record = GeneratedFile(
            production=production,
            template_version=production.template_version,
            kind=kind,
            sequence=sequence,
            filename=filename,
            sha256=digest,
            generated_by=user,
            integrity_report=integrity,
            valid=True,
        )
        with output.open("rb") as handle:
            record.file.save(filename, File(handle), save=False)
        record.save()
    AuditLog.objects.create(user=user, production=production, module="excel", model_name=record._meta.label, record_pk=str(record.pk), action=AuditLog.Action.GENERATE, new_value={"filename": filename, "sha256": digest})
    return record
