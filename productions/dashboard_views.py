"""Dashboard ejecutivo con datos reales de la base de datos.

Agrega registros operativos activos de los partes de producción no anulados:

- Recepción (kg directos de ``ReceptionEntry.weight_kg``).
- Túneles y placas (bandejas convertidas a kg con el ``tray_kg`` de la
  plantilla de cada parte, por defecto 10 kg por bandeja).
- Nuqueras y troquelado (kg directos de sus respectivos registros).
"""

import json
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q, Sum
from django.utils import timezone
from django.views.generic import TemplateView

from productions.models import (
    Crew,
    NuqueraEntry,
    PlateCrewEntry,
    PlateEntry,
    ProductionOrder,
    ReceptionEntry,
    TroqueladoEntry,
    TunnelCrewEntry,
    TunnelEntry,
)

DEFAULT_TRAY_KG = Decimal("10.00")
TREND_DAYS = 14
TOP_CREWS = 8
RECENT_PP_LIMIT = 8

BAR_PALETTE = [
    "#6366f1",
    "#ec4899",
    "#4ECDC4",
    "#45B7D1",
    "#96CEB4",
    "#FFEAA7",
    "#DDA0DD",
    "#FF6B6B",
]
AREA_COLORS = ["#FF6B6B", "#45B7D1", "#96CEB4", "#FFEAA7", "#DDA0DD"]


def _tray_kg_from_rules(rules):
    """Kg por bandeja configurado en la plantilla (10.00 por defecto)."""
    configured = (rules or {}).get("tray_kg", DEFAULT_TRAY_KG)
    try:
        value = Decimal(str(configured))
    except (InvalidOperation, TypeError, ValueError):
        value = DEFAULT_TRAY_KG
    return value if value > 0 else DEFAULT_TRAY_KG


def _as_float(value):
    return float(value or 0)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "productions/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        live = ProductionOrder.objects.exclude(
            status=ProductionOrder.Status.VOID
        ).select_related("template_version", "customer")
        tray_kg_by_pp = {
            production.pk: _tray_kg_from_rules(
                production.template_version.rules if production.template_version_id else {}
            )
            for production in live
        }

        def to_kg(production_id, trays):
            return Decimal(trays or 0) * tray_kg_by_pp.get(
                production_id, DEFAULT_TRAY_KG
            )

        not_void = ~Q(production__status=ProductionOrder.Status.VOID)

        # ---- Recepción: kg totales y serie diaria ----
        reception_qs = ReceptionEntry.objects.filter(is_active=True).filter(not_void)
        reception_kg = reception_qs.aggregate(total=Sum("weight_kg"))["total"] or Decimal("0")

        today = timezone.localdate()
        days = [today - timedelta(days=offset) for offset in range(TREND_DAYS - 1, -1, -1)]
        labels = [day.strftime("%d/%m") for day in days]
        reception_by_day = defaultdict(Decimal)
        for row in (
            reception_qs.filter(date__gte=days[0], date__lte=today)
            .values("date")
            .annotate(total=Sum("weight_kg"))
        ):
            reception_by_day[row["date"]] = row["total"] or Decimal("0")

        # ---- Túneles: bandejas físicas -> kg (serie diaria incluida) ----
        tunnel_qs = TunnelEntry.objects.filter(is_active=True).exclude(
            rack__fill__production__status=ProductionOrder.Status.VOID
        )
        tunnel_kg = Decimal("0")
        tunnel_by_day = defaultdict(Decimal)
        for row in tunnel_qs.values("rack__fill__production", "date").annotate(
            trays=Sum("tray_count")
        ):
            kg = to_kg(row["rack__fill__production"], row["trays"])
            tunnel_kg += kg
            if days[0] <= row["date"] <= today:
                tunnel_by_day[row["date"]] += kg

        # ---- Placas: bandejas físicas -> kg (serie diaria incluida) ----
        plate_qs = PlateEntry.objects.filter(is_active=True).filter(not_void)
        plate_kg = Decimal("0")
        plate_by_day = defaultdict(Decimal)
        for row in plate_qs.values("production", "date").annotate(
            trays=Sum("tray_count")
        ):
            kg = to_kg(row["production"], row["trays"])
            plate_kg += kg
            if days[0] <= row["date"] <= today:
                plate_by_day[row["date"]] += kg

        # ---- Nuqueras y troquelado: kg directos ----
        nuquera_qs = NuqueraEntry.objects.filter(is_active=True).filter(not_void)
        nuquera_kg = nuquera_qs.aggregate(total=Sum("weight_kg"))["total"] or Decimal("0")
        troquelado_qs = TroqueladoEntry.objects.filter(is_active=True).filter(not_void)
        troquelado_kg = (
            troquelado_qs.aggregate(total=Sum("weight_kg"))["total"] or Decimal("0")
        )

        # ---- Rendimiento por cuadrilla (top) ----
        crew_kg = defaultdict(Decimal)
        crew_names = {}
        for row in (
            nuquera_qs.values("crew", "crew__name").annotate(total=Sum("weight_kg"))
        ):
            crew_names[row["crew"]] = row["crew__name"]
            crew_kg[row["crew"]] += row["total"] or Decimal("0")
        for row in (
            troquelado_qs.values("crew", "crew__name").annotate(total=Sum("weight_kg"))
        ):
            crew_names[row["crew"]] = row["crew__name"]
            crew_kg[row["crew"]] += row["total"] or Decimal("0")
        for row in (
            TunnelCrewEntry.objects.filter(is_active=True)
            .exclude(fill__production__status=ProductionOrder.Status.VOID)
            .values("crew", "crew__name", "fill__production")
            .annotate(trays=Sum("tray_count"))
        ):
            crew_names[row["crew"]] = row["crew__name"]
            crew_kg[row["crew"]] += to_kg(row["fill__production"], row["trays"])
        for row in (
            PlateCrewEntry.objects.filter(is_active=True)
            .filter(not_void)
            .values("crew", "crew__name", "production")
            .annotate(trays=Sum("tray_count"))
        ):
            crew_names[row["crew"]] = row["crew__name"]
            crew_kg[row["crew"]] += to_kg(row["production"], row["trays"])
        top_crews = sorted(crew_kg.items(), key=lambda item: item[1], reverse=True)[
            :TOP_CREWS
        ]

        # ---- Contadores ----
        active_pp = live.filter(
            status__in=[
                ProductionOrder.Status.OPEN,
                ProductionOrder.Status.IN_PROGRESS,
            ]
        ).count()
        active_crews = Crew.objects.filter(active=True).count()

        # ---- Partes recientes con su recepción ----
        recent_pp = list(
            live.order_by("-production_date", "-number")
            .annotate(
                reception_total=Sum(
                    "receptionentry__weight_kg",
                    filter=Q(receptionentry__is_active=True),
                )
            )
            .select_related("customer")[:RECENT_PP_LIMIT]
        )

        total_kg = reception_kg
        has_data = any(
            [
                total_kg,
                tunnel_kg,
                plate_kg,
                nuquera_kg,
                troquelado_kg,
                live.exists(),
            ]
        )

        context["kpis"] = {
            "total_kg": _as_float(total_kg),
            "tunnel_kg": _as_float(tunnel_kg),
            "plate_kg": _as_float(plate_kg),
            "nuquera_kg": _as_float(nuquera_kg),
            "troquelado_kg": _as_float(troquelado_kg),
            "active_pp": active_pp,
            "active_crews": active_crews,
        }
        context["crew_data"] = json.dumps(
            {
                "labels": [crew_names[crew_id] for crew_id, _ in top_crews],
                "datasets": [
                    {
                        "label": "Kg",
                        "data": [_as_float(kg) for _, kg in top_crews],
                        "backgroundColor": [
                            BAR_PALETTE[index % len(BAR_PALETTE)]
                            for index in range(len(top_crews))
                        ],
                    }
                ],
            }
        )
        context["area_data"] = json.dumps(
            {
                "labels": ["Recepción", "Túneles", "Placas", "Nuqueras", "Troquelado"],
                "datasets": [
                    {
                        "data": [
                            _as_float(total_kg),
                            _as_float(tunnel_kg),
                            _as_float(plate_kg),
                            _as_float(nuquera_kg),
                            _as_float(troquelado_kg),
                        ],
                        "backgroundColor": AREA_COLORS,
                        "borderColor": "#1a1a2e",
                        "borderWidth": 3,
                    }
                ],
            }
        )
        context["trend_data"] = json.dumps(
            {
                "labels": labels,
                "datasets": [
                    {
                        "label": "Recepción (kg)",
                        "data": [_as_float(reception_by_day[day]) for day in days],
                        "borderColor": "#4ECDC4",
                        "backgroundColor": "rgba(78, 205, 196, 0.1)",
                        "borderWidth": 3,
                        "fill": True,
                        "tension": 0.4,
                    }
                ],
            }
        )
        context["comparison_data"] = json.dumps(
            {
                "labels": labels,
                "datasets": [
                    {
                        "label": "Túneles (kg)",
                        "data": [_as_float(tunnel_by_day[day]) for day in days],
                        "borderColor": "#45B7D1",
                        "borderWidth": 3,
                        "tension": 0.4,
                    },
                    {
                        "label": "Placas (kg)",
                        "data": [_as_float(plate_by_day[day]) for day in days],
                        "borderColor": "#96CEB4",
                        "borderWidth": 3,
                        "tension": 0.4,
                    },
                ],
            }
        )
        context["recent_pp"] = recent_pp
        context["has_data"] = has_data
        context["trend_days"] = TREND_DAYS
        context["top_crew_count"] = len(top_crews)
        return context
