from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from productions.models import (
    PlateCarryoverBalance,
    PlateEntry,
    PlatePackagingAllocation,
    PlatePackagingEntry,
    PlatePallet,
    PlatePalletConsumption,
    PlatePalletLine,
    Product,
    ProductionOrder,
)
from productions.request_context import suppress_automatic_audit


TRAY_KG = Decimal("10.00")


def _package_trays(production):
    return int(production.template_version.rules.get("package_trays", 2))


def _package_kg(production):
    return Decimal(str(production.template_version.rules.get("package_kg", 20)))


def _pallet_capacity(production):
    return int(
        production.template_version.rules.get(
            "plate_pallet_package_capacity",
            56,
        )
    )


def _older_production_filter(production):
    return Q(origin_production__production_date__lt=production.production_date) | Q(
        origin_production__production_date=production.production_date,
        origin_production__number__lt=production.number,
    )


def compatible_carryover_queryset(production, *, product=None, lock=False):
    queryset = PlateCarryoverBalance.objects.filter(
        is_active=True,
        status=PlateCarryoverBalance.Status.AVAILABLE,
        available_trays__gt=0,
        origin_production__customer_id=production.customer_id,
    ).filter(
        Q(origin_production=production, source_entry__isnull=True)
        | Q(
            origin_production__status=ProductionOrder.Status.CLOSED,
        )
        & _older_production_filter(production)
    )
    if product is not None:
        queryset = queryset.filter(product=product)
    if lock:
        queryset = queryset.select_for_update()
    return queryset.select_related(
        "origin_production",
        "product",
        "source_entry__position",
        "generated_by",
    ).order_by(
        "origin_production__production_date",
        "origin_production__number",
        "generated_at",
        "pk",
    )


def _source_remaining_rows(
    production,
    *,
    product=None,
    lock=False,
    require_unloaded=True,
):
    sources = PlateEntry.objects.filter(
        production=production,
        is_active=True,
    ).select_related("product", "position")
    if product is not None:
        sources = sources.filter(product=product)
    if require_unloaded:
        sources = sources.filter(
            position__production_timings__production=production,
            position__production_timings__unloaded_at__isnull=False,
        ).distinct()
    if lock:
        sources = sources.select_for_update()
    sources = list(
        sources.order_by(
            "position__plate_rack",
            "position__position_key",
            "product__code",
            "pk",
        )
    )
    if not sources:
        return []

    package_trays = _package_trays(production)
    source_ids = [source.pk for source in sources]
    manual_packages = dict(
        PlatePackagingAllocation.objects.filter(
            source_entry_id__in=source_ids,
            is_active=True,
        )
        .values("source_entry_id")
        .annotate(total=Sum("package_count"))
        .values_list("source_entry_id", "total")
    )
    automatic_trays = dict(
        PlatePalletConsumption.objects.filter(
            source_entry_id__in=source_ids,
            line__production=production,
            line__is_active=True,
        )
        .values("source_entry_id")
        .annotate(total=Sum("tray_count"))
        .values_list("source_entry_id", "total")
    )
    balance_trays = dict(
        PlateCarryoverBalance.objects.filter(
            source_entry_id__in=source_ids,
            is_active=True,
        )
        .exclude(status=PlateCarryoverBalance.Status.CANCELLED)
        .values("source_entry_id")
        .annotate(total=Sum("initial_trays"))
        .values_list("source_entry_id", "total")
    )
    rows = []
    for source in sources:
        attributed = (
            int(manual_packages.get(source.pk, 0) or 0) * package_trays
            + int(automatic_trays.get(source.pk, 0) or 0)
            + int(balance_trays.get(source.pk, 0) or 0)
        )
        rows.append(
            {
                "source": source,
                "remaining_trays": max(source.tray_count - attributed, 0),
            }
        )

    legacy_by_product = dict(
        PlatePackagingEntry.objects.filter(
            production=production,
            is_active=True,
            product_id__in={row["source"].product_id for row in rows},
        )
        .values("product_id")
        .annotate(total=Sum("package_count"))
        .values_list("product_id", "total")
    )
    legacy_trays_by_product = {
        product_id: int(packages or 0) * package_trays
        for product_id, packages in legacy_by_product.items()
    }
    for row in rows:
        product_id = row["source"].product_id
        unattributed = legacy_trays_by_product.get(product_id, 0)
        if unattributed <= 0:
            continue
        deducted = min(row["remaining_trays"], unattributed)
        row["remaining_trays"] -= deducted
        legacy_trays_by_product[product_id] -= deducted
    return rows


def plate_product_availability(production):
    summaries = {}
    source_rows = _source_remaining_rows(production)
    for row in source_rows:
        if row["remaining_trays"] <= 0:
            continue
        source = row["source"]
        item = summaries.setdefault(
            source.product_id,
            {
                "product": source.product,
                "current_trays": 0,
                "carryover_trays": 0,
                "source_count": 0,
                "carryover_count": 0,
                "origins": [],
            },
        )
        item["current_trays"] += row["remaining_trays"]
        item["source_count"] += 1
        if row["remaining_trays"]:
            item["origins"].append(
                {
                    "label": source.position.operational_label,
                    "trays": row["remaining_trays"],
                    "kind": "current",
                }
            )

    for balance in compatible_carryover_queryset(production):
        item = summaries.setdefault(
            balance.product_id,
            {
                "product": balance.product,
                "current_trays": 0,
                "carryover_trays": 0,
                "source_count": 0,
                "carryover_count": 0,
                "origins": [],
            },
        )
        item["carryover_trays"] += balance.available_trays
        item["carryover_count"] += 1
        item["origins"].append(
            {
                "label": (
                    f"PP {balance.origin_production.number} · "
                    f"{balance.source_entry.position.operational_label if balance.source_entry_id else 'Saldo inicial manual'}"
                ),
                "trays": balance.available_trays,
                "kind": "carryover",
            }
        )

    package_trays = _package_trays(production)
    result = []
    for item in summaries.values():
        available = item["current_trays"] + item["carryover_trays"]
        item["available_trays"] = available
        item["available_kg"] = Decimal(available) * TRAY_KG
        item["possible_packages"] = available // package_trays
        item["possible_package_kg"] = (
            Decimal(item["possible_packages"]) * _package_kg(production)
        )
        item["residual_trays"] = available % package_trays
        item["residual_kg"] = Decimal(item["residual_trays"]) * TRAY_KG
        result.append(item)
    return sorted(
        result,
        key=lambda item: (
            item["product"].code.casefold(),
            item["product"].description.casefold(),
        ),
    )


def _pallet_existing_packages(production, pallet_number):
    legacy = (
        PlatePackagingEntry.objects.filter(
            production=production,
            pallet_number=pallet_number,
            is_active=True,
        ).aggregate(total=Sum("package_count"))["total"]
        or 0
    )
    traced_manual = (
        PlatePackagingAllocation.objects.filter(
            production=production,
            pallet_number=pallet_number,
            is_active=True,
        ).aggregate(total=Sum("package_count"))["total"]
        or 0
    )
    automatic = (
        PlatePalletLine.objects.filter(
            production=production,
            pallet__pallet_number=pallet_number,
            is_active=True,
        ).aggregate(total=Sum("package_count"))["total"]
        or 0
    )
    return int(legacy + traced_manual + automatic)


def plate_pallet_dashboard(production):
    capacity = _pallet_capacity(production)
    pallet_records = {
        pallet.pallet_number: pallet
        for pallet in PlatePallet.objects.filter(
            production=production,
            is_active=True,
        ).select_related("closed_by")
    }
    numbers = set(pallet_records)
    numbers.update(
        PlatePackagingEntry.objects.filter(
            production=production,
            is_active=True,
        ).values_list("pallet_number", flat=True)
    )
    numbers.update(
        PlatePackagingAllocation.objects.filter(
            production=production,
            is_active=True,
        ).values_list("pallet_number", flat=True)
    )
    lines = list(
        PlatePalletLine.objects.filter(
            production=production,
            is_active=True,
        )
        .select_related("pallet", "product", "responsible")
        .prefetch_related(
            "consumptions__source_entry__position",
            "consumptions__carryover_balance__origin_production",
        )
        .order_by("pallet__pallet_number", "created_at", "pk")
    )
    numbers.update(line.pallet.pallet_number for line in lines)

    products_by_pallet = defaultdict(
        lambda: defaultdict(
            lambda: {"product": None, "package_count": 0, "movements": []}
        )
    )
    for entry in PlatePackagingEntry.objects.filter(
        production=production,
        is_active=True,
    ).select_related("product"):
        item = products_by_pallet[entry.pallet_number][entry.product_id]
        item["product"] = entry.product
        item["package_count"] += entry.package_count
    for allocation in PlatePackagingAllocation.objects.filter(
        production=production,
        is_active=True,
    ).select_related("source_entry__product"):
        product = allocation.source_entry.product
        item = products_by_pallet[allocation.pallet_number][product.pk]
        item["product"] = product
        item["package_count"] += allocation.package_count
    for line in lines:
        item = products_by_pallet[line.pallet.pallet_number][line.product_id]
        item["product"] = line.product
        item["package_count"] += line.package_count
        item["movements"].append(line)

    result = []
    for pallet_number in sorted(numbers):
        total = _pallet_existing_packages(production, pallet_number)
        product_rows = []
        for item in products_by_pallet[pallet_number].values():
            item["tray_count"] = item["package_count"] * _package_trays(production)
            item["kg"] = Decimal(item["package_count"]) * _package_kg(production)
            product_rows.append(item)
        product_rows.sort(key=lambda item: item["product"].code.casefold())
        pallet = pallet_records.get(pallet_number)
        result.append(
            {
                "pallet": pallet,
                "pallet_number": pallet_number,
                "status": (
                    pallet.status if pallet is not None else PlatePallet.Status.OPEN
                ),
                "package_count": total,
                "capacity": capacity,
                "available_packages": max(capacity - total, 0),
                "kg": Decimal(total) * _package_kg(production),
                "is_full": total >= capacity,
                "products": product_rows,
            }
        )
    return result


@transaction.atomic
def auto_pack_product(*, production, product_id, pallet_number, user):
    if production.status in {
        ProductionOrder.Status.APPROVED,
        ProductionOrder.Status.CLOSED,
        ProductionOrder.Status.VOID,
    }:
        raise ValidationError("El PP no admite nuevos empaques en su estado actual.")
    product = Product.objects.get(pk=product_id, active=True)
    maximum_pallet = production.template_version.rules.get("plate_pallet_max")
    if pallet_number < 1 or (maximum_pallet and pallet_number > maximum_pallet):
        raise ValidationError(
            f"Ingrese un pallet entre P1 y P{maximum_pallet or 50}."
        )
    pallet, _ = PlatePallet.objects.select_for_update().get_or_create(
        production=production,
        pallet_number=pallet_number,
        is_active=True,
        defaults={"status": PlatePallet.Status.OPEN},
    )
    if pallet.status == PlatePallet.Status.CLOSED:
        raise ValidationError(
            f"El pallet P{pallet_number} está cerrado. Reábralo para agregar productos."
        )
    capacity = _pallet_capacity(production)
    used_packages = _pallet_existing_packages(production, pallet_number)
    available_capacity = max(capacity - used_packages, 0)
    if available_capacity <= 0:
        raise ValidationError(f"El pallet P{pallet_number} ya alcanzó {capacity} bultos.")

    balances = list(
        compatible_carryover_queryset(production, product=product, lock=True)
    )
    source_rows = _source_remaining_rows(
        production,
        product=product,
        lock=True,
    )
    carryover_trays = sum(balance.available_trays for balance in balances)
    current_trays = sum(row["remaining_trays"] for row in source_rows)
    total_available = carryover_trays + current_trays
    package_trays = _package_trays(production)
    package_count = min(total_available // package_trays, available_capacity)
    if package_count <= 0:
        raise ValidationError(
            f"{product.code} todavía no reúne {package_trays} bandejas para formar un bulto completo."
        )
    trays_to_consume = package_count * package_trays
    line = PlatePalletLine(
        production=production,
        responsible=user,
        observation="Cálculo automático de bultos por producto",
        date=production.packaging_date
        or production.production_date
        or production.reception_date,
        pallet=pallet,
        product=product,
        package_count=package_count,
    )
    line.full_clean()
    with suppress_automatic_audit():
        line.save()

    remaining = trays_to_consume
    carryover_used = 0
    for balance in balances:
        if remaining <= 0:
            break
        used = min(balance.available_trays, remaining)
        if used <= 0:
            continue
        consumption = PlatePalletConsumption(
            line=line,
            carryover_balance=balance,
            tray_count=used,
        )
        consumption.full_clean()
        consumption.save()
        balance.available_trays -= used
        balance.status = (
            PlateCarryoverBalance.Status.CONSUMED
            if balance.available_trays == 0
            else PlateCarryoverBalance.Status.AVAILABLE
        )
        balance.last_used_in_production = production
        balance.last_used_by = user
        balance.last_used_at = timezone.now()
        balance.full_clean()
        balance.save(
            update_fields=[
                "available_trays",
                "status",
                "last_used_in_production",
                "last_used_by",
                "last_used_at",
                "version",
                "updated_at",
            ]
        )
        carryover_used += used
        remaining -= used

    current_used = 0
    for row in source_rows:
        if remaining <= 0:
            break
        used = min(row["remaining_trays"], remaining)
        if used <= 0:
            continue
        consumption = PlatePalletConsumption(
            line=line,
            source_entry=row["source"],
            tray_count=used,
        )
        consumption.full_clean()
        consumption.save()
        current_used += used
        remaining -= used
    if remaining:
        raise ValidationError("No fue posible conciliar el origen de todas las bandejas.")

    return {
        "line": line,
        "pallet": pallet,
        "product": product,
        "package_count": package_count,
        "tray_count": trays_to_consume,
        "kg": Decimal(package_count) * _package_kg(production),
        "carryover_used": carryover_used,
        "current_used": current_used,
        "residual_trays": total_available - trays_to_consume,
        "pallet_total": used_packages + package_count,
        "pallet_capacity": capacity,
    }


@transaction.atomic
def register_manual_plate_balance(*, production, source_entry_id, tray_count, observation, user):
    if production.status in {
        ProductionOrder.Status.APPROVED,
        ProductionOrder.Status.CLOSED,
        ProductionOrder.Status.VOID,
    }:
        raise ValidationError("El PP no admite nuevos saldos en su estado actual.")
    try:
        tray_count = int(tray_count)
    except (TypeError, ValueError):
        raise ValidationError("Ingrese una cantidad válida de bandejas para saldo.")
    if tray_count < 1:
        raise ValidationError("Ingrese al menos 1 bandeja para saldo.")
    source = (
        PlateEntry.objects.select_for_update()
        .select_related("product", "position")
        .get(pk=source_entry_id, production=production, is_active=True)
    )
    source_rows = _source_remaining_rows(
        production,
        product=source.product,
        lock=True,
    )
    current_remaining = next(
        (row["remaining_trays"] for row in source_rows if row["source"].pk == source.pk),
        0,
    )
    balance = PlateCarryoverBalance.objects.select_for_update().filter(
        source_entry=source,
        is_active=True,
    ).first()
    existing_initial = balance.initial_trays if balance is not None else 0
    if balance is not None and (
        balance.available_trays < balance.initial_trays
        or balance.status == PlateCarryoverBalance.Status.CONSUMED
    ):
        raise ValidationError("Este saldo ya fue utilizado; no se puede modificar desde empaque.")
    maximum = current_remaining + existing_initial
    if tray_count > maximum:
        raise ValidationError(
            f"Solo quedan {maximum} bandeja(s) disponibles para enviar a saldo."
        )
    if balance is None:
        balance = PlateCarryoverBalance(
            origin_production=production,
            source_entry=source,
            product=source.product,
            generated_by=user,
        )
    balance.origin_production = production
    balance.product = source.product
    balance.initial_trays = tray_count
    balance.available_trays = tray_count
    balance.status = PlateCarryoverBalance.Status.AVAILABLE
    balance.generated_by = user
    balance.generated_at = timezone.now()
    balance.observation = observation or "Saldo registrado manualmente desde empaque de placas"
    balance.full_clean()
    balance.save()
    return balance


@transaction.atomic
def register_initial_plate_balance(*, production, product_id, tray_count, observation, user):
    if production.status in {
        ProductionOrder.Status.APPROVED,
        ProductionOrder.Status.CLOSED,
        ProductionOrder.Status.VOID,
    }:
        raise ValidationError("El PP no admite nuevos saldos en su estado actual.")
    try:
        tray_count = int(tray_count)
    except (TypeError, ValueError):
        raise ValidationError("Ingrese una cantidad válida de bandejas para saldo.")
    if tray_count < 1:
        raise ValidationError("Ingrese al menos 1 bandeja para saldo.")
    product = Product.objects.get(pk=product_id, active=True)
    balance = PlateCarryoverBalance(
        origin_production=production,
        source_entry=None,
        product=product,
        initial_trays=tray_count,
        available_trays=tray_count,
        status=PlateCarryoverBalance.Status.AVAILABLE,
        generated_by=user,
        generated_at=timezone.now(),
        observation=observation or "Saldo inicial manual cargado sin historial anterior",
    )
    balance.full_clean()
    balance.save()
    return balance


@transaction.atomic
def void_auto_pack_line(*, line_id, production, user):
    line = (
        PlatePalletLine.objects.select_for_update()
        .select_related("pallet", "product")
        .get(pk=line_id, production=production, is_active=True)
    )
    if line.pallet.status == PlatePallet.Status.CLOSED:
        raise ValidationError("Reabra el pallet antes de eliminar este movimiento.")
    consumptions = list(
        PlatePalletConsumption.objects.select_for_update()
        .filter(line=line)
        .select_related("carryover_balance")
    )
    for consumption in consumptions:
        if consumption.carryover_balance_id:
            balance = consumption.carryover_balance
            balance.available_trays += consumption.tray_count
            balance.status = PlateCarryoverBalance.Status.AVAILABLE
            balance.last_used_in_production = None
            balance.last_used_by = None
            balance.last_used_at = None
            balance.full_clean()
            balance.save(
                update_fields=[
                    "available_trays",
                    "status",
                    "last_used_in_production",
                    "last_used_by",
                    "last_used_at",
                    "version",
                    "updated_at",
                ]
            )
    with suppress_automatic_audit():
        line.delete(user=user, reason="Corrección de empaque automático")
    return line


@transaction.atomic
def set_plate_pallet_status(*, pallet_id, production, target_status, user):
    pallet = PlatePallet.objects.select_for_update().get(
        pk=pallet_id,
        production=production,
        is_active=True,
    )
    if target_status == PlatePallet.Status.CLOSED:
        if _pallet_existing_packages(production, pallet.pallet_number) <= 0:
            raise ValidationError("No puede cerrar un pallet vacío.")
        pallet.status = PlatePallet.Status.CLOSED
        pallet.closed_at = timezone.now()
        pallet.closed_by = user
    elif target_status == PlatePallet.Status.OPEN:
        pallet.status = PlatePallet.Status.OPEN
        pallet.closed_at = None
        pallet.closed_by = None
    else:
        raise ValidationError("Estado de pallet no permitido.")
    pallet.full_clean()
    pallet.save(
        update_fields=[
            "status",
            "closed_at",
            "closed_by",
            "version",
            "updated_at",
        ]
    )
    return pallet


@transaction.atomic
def sync_production_carryover_balances(*, production, user):
    source_rows = _source_remaining_rows(
        production,
        lock=True,
        require_unloaded=False,
    )
    active_source_ids = set()
    created_or_updated = []
    for row in source_rows:
        source = row["source"]
        remaining = row["remaining_trays"]
        active_source_ids.add(source.pk)
        balance = PlateCarryoverBalance.objects.select_for_update().filter(
            source_entry=source
        ).first()
        if balance is None:
            balance = PlateCarryoverBalance(
                origin_production=production,
                source_entry=source,
                product=source.product,
                initial_trays=remaining,
                available_trays=remaining,
                generated_by=user,
            )
        elif balance.available_trays < balance.initial_trays and balance.status in {
            PlateCarryoverBalance.Status.AVAILABLE,
            PlateCarryoverBalance.Status.CONSUMED,
        }:
            raise ValidationError(
                f"El saldo {source.product.code} de este PP ya fue utilizado en otra producción."
            )
        else:
            balance.origin_production = production
            balance.product = source.product
            balance.initial_trays = remaining
            balance.available_trays = remaining
            balance.generated_by = user
            balance.generated_at = timezone.now()
        balance.status = (
            PlateCarryoverBalance.Status.AVAILABLE
            if remaining > 0
            else PlateCarryoverBalance.Status.CANCELLED
        )
        balance.full_clean()
        balance.save()
        if remaining:
            created_or_updated.append(balance)

    obsolete = PlateCarryoverBalance.objects.select_for_update().filter(
        origin_production=production,
        is_active=True,
        source_entry__isnull=False,
    ).exclude(source_entry_id__in=active_source_ids)
    if obsolete.filter(
        Q(status=PlateCarryoverBalance.Status.CONSUMED)
        | Q(available_trays__lt=F("initial_trays"))
    ).exists():
        raise ValidationError("Existen saldos de este PP que ya fueron utilizados.")
    obsolete.update(
        status=PlateCarryoverBalance.Status.CANCELLED,
        available_trays=0,
        updated_at=timezone.now(),
    )
    return created_or_updated


@transaction.atomic
def cancel_production_balances_for_reopen(*, production):
    balances = PlateCarryoverBalance.objects.select_for_update().filter(
        origin_production=production,
        is_active=True,
    )
    if balances.filter(
        Q(status=PlateCarryoverBalance.Status.CONSUMED)
        | Q(available_trays__lt=F("initial_trays"))
    ).exists():
        raise ValidationError(
            "No se puede reabrir este PP porque uno de sus saldos ya fue utilizado "
            "en una producción posterior."
        )
    balances.update(
        status=PlateCarryoverBalance.Status.CANCELLED,
        available_trays=0,
        updated_at=timezone.now(),
    )


def plate_balance_dashboard(production):
    all_available = list(
        PlateCarryoverBalance.objects.filter(
            is_active=True,
            status=PlateCarryoverBalance.Status.AVAILABLE,
            available_trays__gt=0,
        )
        .filter(
            Q(origin_production=production, source_entry__isnull=True)
            | Q(origin_production__status=ProductionOrder.Status.CLOSED)
        )
        .select_related(
            "origin_production__customer",
            "product",
            "source_entry__position",
            "generated_by",
        )
        .order_by(
            "product__code",
            "origin_production__production_date",
            "origin_production__number",
        )
    )
    for balance in all_available:
        balance.compatible_with_current = (
            balance.origin_production_id == production.pk
            and balance.source_entry_id is None
        ) or (
            balance.origin_production.customer_id == production.customer_id
            and (
                balance.origin_production.production_date
                < production.production_date
                or (
                    balance.origin_production.production_date
                    == production.production_date
                    and balance.origin_production.number < production.number
                )
            )
        )
    generated = list(
        PlateCarryoverBalance.objects.filter(
            origin_production=production,
            is_active=True,
        )
        .select_related("product", "source_entry__position", "generated_by")
        .order_by("product__code", "source_entry__position__plate_rack")
    )
    received_consumptions = list(
        PlatePalletConsumption.objects.filter(
            line__production=production,
            line__is_active=True,
            carryover_balance__isnull=False,
        )
        .select_related(
            "line__pallet",
            "line__product",
            "carryover_balance__origin_production",
            "carryover_balance__source_entry__position",
        )
        .order_by("line__pallet__pallet_number", "line__created_at")
    )
    return {
        "product_availability": plate_product_availability(production),
        "all_available": all_available,
        "generated": generated,
        "received_consumptions": received_consumptions,
        "available_trays": sum(
            balance.available_trays for balance in all_available
        ),
        "compatible_trays": sum(
            balance.available_trays
            for balance in all_available
            if balance.compatible_with_current
        ),
        "generated_trays": sum(
            balance.available_trays
            for balance in generated
            if balance.status == PlateCarryoverBalance.Status.AVAILABLE
        ),
        "received_trays": sum(
            consumption.tray_count for consumption in received_consumptions
        ),
    }
