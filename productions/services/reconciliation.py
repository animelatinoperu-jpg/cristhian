from dataclasses import asdict, dataclass

from django.db.models import Sum

from productions.models import PlateCrewEntry, PlateEntry, TunnelCrewEntry, TunnelEntry


@dataclass(frozen=True)
class Reconciliation:
    physical_total: int
    declared_total: int

    @property
    def difference(self):
        return self.physical_total - self.declared_total

    @property
    def status(self):
        return "CONFORME" if self.difference == 0 else "DIFERENCIA"

    def to_dict(self):
        return {**asdict(self), "difference": self.difference, "status": self.status}


def tunnel_reconciliation(production, fill=None):
    physical = TunnelEntry.objects.filter(production=production, is_active=True)
    declared = TunnelCrewEntry.objects.filter(fill__production=production, is_active=True)
    if fill is not None:
        physical = physical.filter(rack__fill=fill)
        declared = declared.filter(fill=fill)
    return Reconciliation(
        physical.aggregate(total=Sum("tray_count"))["total"] or 0,
        declared.aggregate(total=Sum("tray_count"))["total"] or 0,
    )


def plate_reconciliation(production):
    return Reconciliation(
        PlateEntry.objects.filter(production=production, is_active=True).aggregate(total=Sum("tray_count"))["total"] or 0,
        PlateCrewEntry.objects.filter(production=production, is_active=True).aggregate(total=Sum("tray_count"))["total"] or 0,
    )
