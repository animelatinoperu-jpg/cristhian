from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import unicodedata

from django.db.models import Sum

from productions.models import Crew, PlateCrewEntry, PlateEntry, ReceptionEntry, TunnelCrewEntry, TunnelEntry, Worker


KG_QUANTUM = Decimal("0.01")
DEFAULT_TRAY_KG = Decimal("10.00")
STANDARD_TUNNELS = tuple(f"T{number}" for number in range(1, 7))


def _decimal_kg(value):
    return Decimal(str(value or 0)).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP)


def _tray_kg(production):
    rules = production.template_version.rules or {}
    configured = rules.get("tray_kg", DEFAULT_TRAY_KG)
    try:
        value = _decimal_kg(configured)
    except (InvalidOperation, TypeError, ValueError):
        value = DEFAULT_TRAY_KG
    return value if value > 0 else DEFAULT_TRAY_KG


def _kg(trays, tray_kg):
    return _decimal_kg(Decimal(trays or 0) * tray_kg)


def _normalized_crew_name(value):
    normalized = unicodedata.normalize("NFD", (value or "").strip().upper())
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    )
    return " ".join(normalized.split())


def _natural_code_key(value):
    parts = re.split(r"(\d+)", value or "")
    return tuple(int(part) if part.isdigit() else part.casefold() for part in parts)


def _product_is_cone_pota(product):
    if not product:
        return False
    haystack = " ".join(
        part
        for part in (
            getattr(product, "code", ""),
            getattr(product, "description", ""),
            getattr(product, "presentation", ""),
        )
        if part
    )
    normalized = _normalized_crew_name(haystack)
    return "CONO" in normalized and "POTA" in normalized


def reception_cone_pota_summary(production):
    """Agrupa el peso de limpieza de conos de pota por cuadrilla.

    La agrupación se hace por el nombre real registrado en cada movimiento,
    pero se normaliza para evitar duplicados por tildes o espacios.
    """

    entries = (
        ReceptionEntry.objects.filter(production=production, is_active=True)
        .select_related("crew", "product", "vehicle", "responsible")
        .order_by("crew__name", "vehicle__plate", "car_number", "container", "pk")
    )

    rows = {}
    total_weight = Decimal("0.00")
    total_entries = 0

    for entry in entries:
        if not _product_is_cone_pota(entry.product):
            continue
        total_entries += 1
        weight = _decimal_kg(entry.weight_kg)
        total_weight += weight
        crew_name = _normalized_crew_name(entry.crew.name) if entry.crew else "SIN CUADRILLA"
        row = rows.setdefault(
            crew_name,
            {
                "crew_id": entry.crew_id,
                "crew_name": crew_name,
                "dino_count": 0,
                "total_weight": Decimal("0.00"),
                "details": [],
            },
        )
        row["dino_count"] += 1
        row["total_weight"] += weight
        row["details"].append(
            {
                "vehicle": entry.vehicle.plate,
                "car_number": (entry.car_number or "").strip() or "—",
                "container": (entry.container or "").strip() or "—",
                "weight_kg": weight,
                "responsible": entry.responsible.get_full_name() or entry.responsible.username,
                "date": entry.date,
            }
        )

    reception_rows = sorted(
        rows.values(),
        key=lambda item: (-item["total_weight"], item["crew_name"].casefold()),
    )
    for row in reception_rows:
        row["details"].sort(
            key=lambda detail: (
                detail["vehicle"].casefold(),
                int(detail["car_number"]) if str(detail["car_number"]).isdigit() else 9999,
                int(detail["container"]) if str(detail["container"]).isdigit() else 9999,
                detail["container"].casefold(),
            )
        )

    return {
        "rows": reception_rows,
        "entry_count": total_entries,
        "total_weight": total_weight,
    }


def crew_control_summary(production):
    """Return a read-only consolidation of tunnel and plate crew work."""

    tray_kg = _tray_kg(production)
    rows = {}

    def crew_row(entry):
        crew = entry.crew
        normalized_name = _normalized_crew_name(crew.name)
        return rows.setdefault(
            normalized_name,
            {
                "crew_id": crew.pk,
                "crew_name": normalized_name,
                "tunnel_trays": 0,
                "plate_trays": 0,
                "total_trays": 0,
                "tunnel_kg": Decimal("0.00"),
                "plate_kg": Decimal("0.00"),
                "total_kg": Decimal("0.00"),
                "tunnel_counts": defaultdict(int),
                "plate_counts": defaultdict(int),
                "tunnel_details": [],
                "plate_details": [],
            },
        )

    tunnel_entries = (
        TunnelCrewEntry.objects.filter(fill__production=production, is_active=True)
        .select_related("crew", "fill__tunnel", "rack", "product", "responsible")
        .order_by("crew__name", "fill__tunnel__code", "fill__fill_number", "rack__code", "product__description", "pk")
    )
    for entry in tunnel_entries:
        row = crew_row(entry)
        tunnel_code = entry.fill.tunnel.code
        trays = entry.tray_count
        row["tunnel_trays"] += trays
        row["tunnel_counts"][tunnel_code] += trays
        row["tunnel_details"].append(
            {
                "tunnel": tunnel_code,
                "fill_number": entry.fill.fill_number,
                "location": entry.rack.code if entry.rack_id else entry.page_or_block,
                "product": entry.product.description if entry.product_id else "",
                "trays": trays,
                "kg": _kg(trays, tray_kg),
                "responsible": entry.responsible.get_full_name() or entry.responsible.username,
            }
        )

    plate_entries = (
        PlateCrewEntry.objects.filter(production=production, is_active=True)
        .select_related("crew", "position", "responsible")
        .order_by("crew__name", "position__plate_rack", "page", "position__display_name", "pk")
    )
    for entry in plate_entries:
        row = crew_row(entry)
        plate_rack = entry.position.plate_rack
        trays = entry.tray_count
        row["plate_trays"] += trays
        row["plate_counts"][plate_rack] += trays
        row["plate_details"].append(
            {
                "plate_rack": plate_rack,
                "plaquero_number": entry.position.plaquero_number,
                "batch_number": entry.position.batch_number,
                "page": entry.page,
                "position": entry.position.operational_label,
                "trays": trays,
                "kg": _kg(trays, tray_kg),
                "responsible": entry.responsible.get_full_name() or entry.responsible.username,
            }
        )

    crew_rows = []
    for row in rows.values():
        row["total_trays"] = row["tunnel_trays"] + row["plate_trays"]
        row["tunnel_kg"] = _kg(row["tunnel_trays"], tray_kg)
        row["plate_kg"] = _kg(row["plate_trays"], tray_kg)
        row["total_kg"] = _kg(row["total_trays"], tray_kg)
        row["tunnels"] = [
            {
                "code": code,
                "trays": row["tunnel_counts"].get(code, 0),
                "kg": _kg(row["tunnel_counts"].get(code, 0), tray_kg),
            }
            for code in STANDARD_TUNNELS
        ]
        row["plate_racks"] = [
            {
                "code": code,
                "label": f"Plaquero {code.removeprefix('P')}",
                "trays": row["plate_counts"].get(code, 0),
                "kg": _kg(row["plate_counts"].get(code, 0), tray_kg),
            }
            for code in ("P1", "P2", "P3")
        ]
        row["plate_details"].sort(
            key=lambda detail: (
                detail["batch_number"] or 9999,
                detail["plaquero_number"] or 9999,
                detail["page"],
            )
        )
        tunnel_detail_groups = []
        for tunnel in row["tunnels"]:
            details = [
                detail
                for detail in row["tunnel_details"]
                if detail["tunnel"] == tunnel["code"]
            ]
            if not details:
                continue
            details.sort(
                key=lambda detail: (
                    detail["fill_number"],
                    _natural_code_key(detail["location"]),
                    detail["product"].casefold(),
                )
            )
            tunnel_detail_groups.append(
                {
                    "code": tunnel["code"],
                    "trays": tunnel["trays"],
                    "kg": tunnel["kg"],
                    "details": details,
                }
            )
        row["tunnel_detail_groups"] = tunnel_detail_groups
        del row["tunnel_counts"]
        del row["plate_counts"]
        crew_rows.append(row)

    crew_rows.sort(key=lambda item: (-item["total_kg"], item["crew_name"].casefold()))
    tunnel_trays = sum(row["tunnel_trays"] for row in crew_rows)
    plate_trays = sum(row["plate_trays"] for row in crew_rows)
    total_trays = tunnel_trays + plate_trays

    tunnel_physical = (
        TunnelEntry.objects.filter(rack__fill__production=production, is_active=True)
        .aggregate(total=Sum("tray_count"))["total"]
        or 0
    )
    plate_physical = (
        PlateEntry.objects.filter(production=production, is_active=True)
        .aggregate(total=Sum("tray_count"))["total"]
        or 0
    )

    return {
        "rows": crew_rows,
        "tunnel_codes": STANDARD_TUNNELS,
        "tray_kg": tray_kg,
        "crew_count": len(crew_rows),
        "tunnel_trays": tunnel_trays,
        "tunnel_kg": _kg(tunnel_trays, tray_kg),
        "plate_trays": plate_trays,
        "plate_kg": _kg(plate_trays, tray_kg),
        "total_trays": total_trays,
        "total_kg": _kg(total_trays, tray_kg),
        "tunnel_physical": tunnel_physical,
        "tunnel_difference": tunnel_physical - tunnel_trays,
        "plate_physical": plate_physical,
        "plate_difference": plate_physical - plate_trays,
    }


def crew_tareo_summary(production, crew_pk):
    """Tareo de una sola cuadrilla dentro de un parte de produccion.

    Reutiliza el consolidado de crew_control_summary y le agrega lo propio
    del tareo: el objeto Crew y la lista de trabajadores del catalogo. Si la
    cuadrilla no tiene trabajo registrado en el parte devuelve la fila en
    cero, para que la pantalla igual muestre a su personal.
    """
    crew = Crew.objects.get(pk=crew_pk)
    summary = crew_control_summary(production)
    tray_kg = summary["tray_kg"]

    row = next(
        (item for item in summary["rows"] if item["crew_id"] == crew.pk),
        None,
    )
    if row is None:
        row = {
            "crew_id": crew.pk,
            "crew_name": _normalized_crew_name(crew.name),
            "tunnel_trays": 0,
            "plate_trays": 0,
            "total_trays": 0,
            "tunnel_kg": _decimal_kg(0),
            "plate_kg": _decimal_kg(0),
            "total_kg": _decimal_kg(0),
            "tunnel_details": [],
            "plate_details": [],
            "tunnel_detail_groups": [],
            "tunnels": [
                {"code": code, "trays": 0, "kg": _decimal_kg(0)}
                for code in STANDARD_TUNNELS
            ],
            "plate_racks": [
                {
                    "code": code,
                    "label": f"Plaquero {code.removeprefix('P')}",
                    "trays": 0,
                    "kg": _decimal_kg(0),
                }
                for code in ("P1", "P2", "P3")
            ],
        }

    workers = list(
        Worker.objects.filter(crew=crew, active=True).order_by("full_name", "pk")
    )
    return {
        **row,
        "crew": crew,
        "tray_kg": tray_kg,
        "workers": workers,
        "worker_count": len(workers),
    }
