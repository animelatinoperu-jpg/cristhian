"""Dashboard ejecutivo con datos reales de la base de datos.

Agrega registros operativos activos de los partes de producción no anulados:

- Recepción (kg directos de ``ReceptionEntry.weight_kg``).
- Túneles y placas (bandejas convertidas a kg con el ``tray_kg`` de la
  plantilla de cada parte, por defecto 10 kg por bandeja).
- Nuqueras y troquelado (kg directos de sus respectivos registros).
- Empaque (bultos convertidos a kg con el ``package_kg`` de la plantilla
  de cada parte, por defecto 20 kg por bulto).
- Costos (cantidad x costo unitario de ``CostEntry``).

Acepta ``?pp=<id>`` para filtrar todos los indicadores y gráficas a un
solo parte de producción.
"""

import json
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F, Q, Sum
from django.utils import timezone
from django.views.generic import TemplateView

from productions.models import (
    CostEntry,
    Crew,
    NuqueraEntry,
    PlateCrewEntry,
    PlateEntry,
    PlatePackagingEntry,
    ProductionOrder,
    ReceptionEntry,
    TroqueladoEntry,
    TunnelCrewEntry,
    TunnelEntry,
    TunnelPackagingEntry,
)

DEFAULT_TRAY_KG = Decimal("10.00")
DEFAULT_PACKAGE_KG = Decimal("20.00")
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


def _rule_decimal(rules, key, default):
    """Lee una regla numérica de la plantilla con valor por defecto seguro."""
    configured = (rules or {}).get(key, default)
    try:
        value = Decimal(str(configured))
    except (InvalidOperation, TypeError, ValueError):
        value = default
    return value if value > 0 else default


def _as_float(value):
    return float(value or 0)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "productions/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        live = ProductionOrder.objects.exclude(
            status=ProductionOrder.Status.VOID
        ).select_related("template_version", "customer")

        selected_pp = None
        pp_param = (self.request.GET.get("pp") or "").strip()
        if pp_param.isdigit():
            selected_pp = live.filter(pk=int(pp_param)).first()

        tray_kg_by_pp = {}
        package_kg_by_pp = {}
        for production in live:
            rules = (
                production.template_version.rules
                if production.template_version_id
                else {}
            )
            tray_kg_by_pp[production.pk] = _rule_decimal(
                rules, "tray_kg", DEFAULT_TRAY_KG
            )
            package_kg_by_pp[production.pk] = _rule_decimal(
                rules, "package_kg", DEFAULT_PACKAGE_KG
            )

        def to_tray_kg(production_id, trays):
            return Decimal(trays or 0) * tray_kg_by_pp.get(
                production_id, DEFAULT_TRAY_KG
            )

        def to_pack_kg(production_id, packages):
            return Decimal(packages or 0) * package_kg_by_pp.get(
                production_id, DEFAULT_PACKAGE_KG
            )

        not_void = ~Q(production__status=ProductionOrder.Status.VOID)

        def in_scope(queryset, production_field="production_id"):
            if selected_pp is not None:
                return queryset.filter(**{production_field: selected_pp.pk})
            return queryset

        # ---- Recepción: kg totales y serie diaria ----
        reception_qs = in_scope(
            ReceptionEntry.objects.filter(is_active=True).filter(not_void)
        )
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
        tunnel_qs = in_scope(
            TunnelEntry.objects.filter(is_active=True).exclude(
                rack__fill__production__status=ProductionOrder.Status.VOID
            ),
            production_field="rack__fill__production_id",
        )
        tunnel_kg = Decimal("0")
        tunnel_by_day = defaultdict(Decimal)
        for row in tunnel_qs.values("rack__fill__production", "date").annotate(
            trays=Sum("tray_count")
        ):
            kg = to_tray_kg(row["rack__fill__production"], row["trays"])
            tunnel_kg += kg
            if days[0] <= row["date"] <= today:
                tunnel_by_day[row["date"]] += kg

        # ---- Placas: bandejas físicas -> kg (serie diaria incluida) ----
        plate_qs = in_scope(
            PlateEntry.objects.filter(is_active=True).filter(not_void)
        )
        plate_kg = Decimal("0")
        plate_by_day = defaultdict(Decimal)
        for row in plate_qs.values("production", "date").annotate(
            trays=Sum("tray_count")
        ):
            kg = to_tray_kg(row["production"], row["trays"])
            plate_kg += kg
            if days[0] <= row["date"] <= today:
                plate_by_day[row["date"]] += kg

        # ---- Nuqueras y troquelado: kg directos ----
        nuquera_qs = in_scope(
            NuqueraEntry.objects.filter(is_active=True).filter(not_void)
        )
        nuquera_kg = nuquera_qs.aggregate(total=Sum("weight_kg"))["total"] or Decimal("0")
        troquelado_qs = in_scope(
            TroqueladoEntry.objects.filter(is_active=True).filter(not_void)
        )
        troquelado_kg = (
            troquelado_qs.aggregate(total=Sum("weight_kg"))["total"] or Decimal("0")
        )

        # ---- Empaque: bultos -> kg ----
        pack_kg = Decimal("0")
        for model in (TunnelPackagingEntry, PlatePackagingEntry):
            pack_qs = in_scope(
                model.objects.filter(is_active=True).filter(not_void)
            )
            for row in pack_qs.values("production").annotate(
                packages=Sum("package_count")
            ):
                pack_kg += to_pack_kg(row["production"], row["packages"])

        # ---- Costos: cantidad x costo unitario ----
        cost_qs = in_scope(
            CostEntry.objects.filter(is_active=True).filter(not_void)
        )
        cost_total = (
            cost_qs.annotate(line_total=F("quantity") * F("unit_cost")).aggregate(
                total=Sum("line_total")
            )["total"]
            or Decimal("0")
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
        tunnel_crew_qs = TunnelCrewEntry.objects.filter(is_active=True).exclude(
            fill__production__status=ProductionOrder.Status.VOID
        )
        if selected_pp is not None:
            tunnel_crew_qs = tunnel_crew_qs.filter(fill__production_id=selected_pp.pk)
        for row in tunnel_crew_qs.values(
            "crew", "crew__name", "fill__production"
        ).annotate(trays=Sum("tray_count")):
            crew_names[row["crew"]] = row["crew__name"]
            crew_kg[row["crew"]] += to_tray_kg(row["fill__production"], row["trays"])
        plate_crew_qs = PlateCrewEntry.objects.filter(is_active=True).filter(not_void)
        if selected_pp is not None:
            plate_crew_qs = plate_crew_qs.filter(production_id=selected_pp.pk)
        for row in plate_crew_qs.values("crew", "crew__name", "production").annotate(
            trays=Sum("tray_count")
        ):
            crew_names[row["crew"]] = row["crew__name"]
            crew_kg[row["crew"]] += to_tray_kg(row["production"], row["trays"])
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
        if selected_pp is None:
            active_crews = Crew.objects.filter(active=True).count()
            crews_label = "Cuadrillas"
        else:
            active_crews = len(crew_kg)
            crews_label = f"Cuadrillas en PP {selected_pp.number}"

        # ---- Opciones del filtro y partes recientes ----
        pp_options = list(
            live.order_by("-production_date", "-number").values(
                "id", "number", "production_date", "customer__name"
            )
        )
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
                pack_kg,
                live.exists(),
            ]
        )

        context["kpis"] = {
            "total_kg": _as_float(total_kg),
            "tunnel_kg": _as_float(tunnel_kg),
            "plate_kg": _as_float(plate_kg),
            "nuquera_kg": _as_float(nuquera_kg),
            "troquelado_kg": _as_float(troquelado_kg),
            "pack_kg": _as_float(pack_kg),
            "cost_total": _as_float(cost_total),
            "active_pp": active_pp,
            "active_crews": active_crews,
        }
        context["crews_label"] = crews_label
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
        context["pp_options"] = pp_options
        context["selected_pp"] = selected_pp
        context["has_data"] = has_data
        context["trend_days"] = TREND_DAYS
        context["top_crew_count"] = len(top_crews)
        context["updated_at"] = timezone.localtime(timezone.now()).strftime(
            "%d/%m/%Y %H:%M:%S"
        )
        return context
