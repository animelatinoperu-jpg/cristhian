from django.core.exceptions import ValidationError
from django.db import models, transaction

from productions.models import (
    Approval,
    AuditLog,
    CostEntry,
    GeneratedFile,
    MaterialUsage,
    NuqueraEntry,
    Observation,
    PlateCarryoverBalance,
    PlateCrewEntry,
    PlateEntry,
    PlatePackagingAllocation,
    PlatePackagingEntry,
    PlatePallet,
    PlatePalletConsumption,
    PlatePalletLine,
    PlatePositionTiming,
    ProductionOrder,
    ReceptionCarTiming,
    ReceptionEntry,
    TroqueladoEntry,
    TunnelCrewEntry,
    TunnelEntry,
    TunnelFill,
    TunnelPackagingEntry,
    TunnelRack,
)


@transaction.atomic
def permanently_delete_production(*, production_id, expected_version):
    production = ProductionOrder.objects.select_for_update().get(pk=production_id)
    if production.status != ProductionOrder.Status.VOID:
        raise ValidationError(
            "Solo se pueden eliminar definitivamente los partes anulados (VOID)."
        )
    if production.version != expected_version:
        raise ValidationError("La producción cambió en otra sesión. Recargue antes de continuar.")
    report = _collect_related_counts(production)
    pid = production.pk

    PlatePalletConsumption._base_manager.filter(line__production_id=pid).delete()
    PlatePalletLine._base_manager.filter(production_id=pid).delete()
    PlatePallet._base_manager.filter(production_id=pid).delete()
    PlateCarryoverBalance._base_manager.filter(
        models.Q(origin_production_id=pid) | models.Q(last_used_in_production_id=pid)
    ).delete()
    PlatePackagingAllocation._base_manager.filter(production_id=pid).delete()
    PlateEntry._base_manager.filter(production_id=pid).delete()
    TunnelCrewEntry._base_manager.filter(production_id=pid).delete()
    TunnelEntry._base_manager.filter(production_id=pid).delete()
    TunnelRack._base_manager.filter(fill__production_id=pid).delete()
    TunnelFill._base_manager.filter(production_id=pid).delete()
    PlatePositionTiming._base_manager.filter(production_id=pid).delete()
    PlateCrewEntry._base_manager.filter(production_id=pid).delete()
    TunnelPackagingEntry._base_manager.filter(production_id=pid).delete()
    PlatePackagingEntry._base_manager.filter(production_id=pid).delete()
    NuqueraEntry._base_manager.filter(production_id=pid).delete()
    TroqueladoEntry._base_manager.filter(production_id=pid).delete()
    ReceptionCarTiming._base_manager.filter(production_id=pid).delete()
    ReceptionEntry._base_manager.filter(production_id=pid).delete()
    MaterialUsage._base_manager.filter(production_id=pid).delete()
    CostEntry._base_manager.filter(production_id=pid).delete()
    Approval._base_manager.filter(production_id=pid).delete()
    Observation._base_manager.filter(production_id=pid).delete()
    AuditLog._base_manager.filter(production_id=pid).delete()
    for generated in GeneratedFile._base_manager.filter(production_id=pid):
        if generated.file:
            generated.file.delete(save=False)
    GeneratedFile._base_manager.filter(production_id=pid).delete()

    number = production.number
    label = str(production)
    production.delete()
    report["number"] = number
    report["label"] = label
    report["deleted"] = True
    return report


def _collect_related_counts(production):
    return {
        "reception_entries": production.receptionentry_set.count(),
        "reception_car_timings": production.reception_car_timings.count(),
        "nuquera_entries": production.nuqueraentry_set.count(),
        "troquelado_entries": production.troqueladoentry_set.count(),
        "tunnel_fills": production.tunnel_fills.count(),
        "tunnel_entries": production.tunnelentry_set.count(),
        "tunnel_crew_entries": production.tunnelcrewentry_set.count(),
        "plate_entries": production.plateentry_set.count(),
        "plate_position_timings": production.plate_position_timings.count(),
        "plate_crew_entries": production.platecrewentry_set.count(),
        "tunnel_packaging_entries": production.tunnelpackagingentry_set.count(),
        "plate_packaging_entries": production.platepackagingentry_set.count(),
        "plate_packaging_allocations": production.platepackagingallocation_set.count(),
        "plate_pallets": production.plate_pallets.count(),
        "plate_pallet_lines": production.platepalletline_set.count(),
        "generated_plate_balances": production.generated_plate_balances.count(),
        "used_plate_balances": production.used_plate_balances.count(),
        "material_usages": production.materialusage_set.count(),
        "cost_entries": production.costentry_set.count(),
        "approvals": production.approvals.count(),
        "observations": production.review_observations.count(),
        "audit_logs": production.audit_logs.count(),
        "generated_files": production.generated_files.count(),
    }
