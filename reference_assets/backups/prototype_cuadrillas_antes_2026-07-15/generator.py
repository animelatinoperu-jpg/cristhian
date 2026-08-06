from __future__ import annotations

import hashlib
import re
import tempfile
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.db.models import Q

from productions.models import AuditLog, GeneratedFile, MaterialUsage, ProductionOrder, Rate, Role
from productions.services.permissions import require_roles
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


def _kg_value(value):
    return Decimal(str(value)).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP)


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
    values = {
        "production.number": production.number,
        "production.number_display": f"N° {production.number}",
        "production.plant_lot": production.plant_lot,
        "production.customer_lot": production.customer_lot,
        "production.customer": production.customer.name,
        "production.process": production.process,
        "production.main_product.code": production.main_product.code,
        "production.main_product.description": production.main_product.description,
        "production.reception_date": production.reception_date,
        "production.production_date": production.production_date,
        "production.packaging_date": production.packaging_date,
        "production.shift": production.get_shift_display(),
        "production.series": production.series,
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
    if len(vehicles) > 10:
        raise ValueError("R.M admite como máximo 10 vehículos en la plantilla asignada.")
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
    for fill in production.tunnel_fills.prefetch_related("racks__entries__product"):
        for rack in fill.racks.all():
            for entry in rack.entries.filter(is_active=True):
                values[f"tunnel.{fill.tunnel.code}.fill{fill.fill_number}.{rack.position_key}.{entry.product.code}.trays"] = entry.tray_count
    for entry in production.plateentry_set.filter(is_active=True).select_related("position", "product"):
        values[f"plates.{entry.position.plate_rack}.{entry.position.position_key}.{entry.product.code}.trays"] = entry.tray_count
    for entry in production.tunnelcrewentry_set.filter(is_active=True).select_related("fill__tunnel", "crew"):
        page = _page_key(entry.page_or_block)
        values[f"tunnel_crews.{entry.fill.tunnel.code}.fill{entry.fill.fill_number}.{page}.{entry.crew.code}.trays"] = entry.tray_count
    plate_crew_totals = {}
    for entry in production.platecrewentry_set.filter(is_active=True).select_related("position", "crew"):
        values[f"plate_crews.{entry.position.plate_rack}.{entry.position.position_key}.{entry.page}.{entry.crew.code}.trays"] = entry.tray_count
        key = f"plate_crews.{_page_key(entry.page)}.{entry.crew.code}.trays"
        plate_crew_totals[key] = plate_crew_totals.get(key, 0) + entry.tray_count
    values.update(plate_crew_totals)
    for entry in production.tunnelpackagingentry_set.filter(is_active=True).select_related("product"):
        prefix = f"tunnel_packaging.P{entry.pallet_number}.{entry.product.code}"
        values.update({f"{prefix}.packages": entry.package_count, f"{prefix}.trays": entry.tray_count, f"{prefix}.kg": _kg_value(entry.kilos), f"{prefix}.date": entry.date})
    for entry in production.platepackagingentry_set.filter(is_active=True).select_related("product"):
        prefix = f"plate_packaging.P{entry.pallet_number}.{entry.product.code}"
        values.update({f"{prefix}.packages": entry.package_count, f"{prefix}.trays": entry.tray_count, f"{prefix}.kg": _kg_value(entry.kilos), f"{prefix}.date": entry.date})
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
        try:
            _, authorized = SafeXlsmWriter(template_path, mapping_path).write(output, production_values(production))
        except Exception as exc:
            raise GenerationError(str(exc)) from exc
        if output_issues := validate_output_file(output):
            raise GenerationError("Archivo generado inválido: " + "; ".join(output_issues))
        integrity = IntegrityChecker(template_path, output, authorized).check()
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
