from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from django.utils import timezone
from django.db.models import Sum, Count
from django.db.models.functions import Coalesce
from datetime import timedelta
import json

from .models import (
    ProductionOrder, TunnelCrewEntry, PlateCrewEntry, NuqueraEntry,
    TroqueladoEntry, Crew
)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'productions/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=14)

        # KPIs
        tunnel_kg = float(TunnelCrewEntry.objects.filter(
            date__range=[start_date, end_date], is_active=True
        ).aggregate(total=Coalesce(Sum('tray_count'), 0))['total'] or 0) * 10

        plate_kg = float(PlateCrewEntry.objects.filter(
            date__range=[start_date, end_date], is_active=True
        ).aggregate(total=Coalesce(Sum('tray_count'), 0))['total'] or 0) * 10

        nuquera_kg = float(NuqueraEntry.objects.filter(
            date__range=[start_date, end_date], is_active=True
        ).aggregate(total=Coalesce(Sum('weight_kg'), 0))['total'] or 0)

        troquelado_kg = float(TroqueladoEntry.objects.filter(
            date__range=[start_date, end_date], is_active=True
        ).aggregate(total=Coalesce(Sum('weight_kg'), 0))['total'] or 0)

        total = tunnel_kg + plate_kg + nuquera_kg + troquelado_kg
        active_pp = ProductionOrder.objects.filter(status__in=['OPEN', 'IN_PROGRESS']).count()
        active_crews = Crew.objects.filter(active=True).count()

        context['kpis'] = {
            'total_kg': total,
            'tunnel_kg': tunnel_kg,
            'plate_kg': plate_kg,
            'nuquera_kg': nuquera_kg,
            'troquelado_kg': troquelado_kg,
            'active_pp': active_pp,
            'active_crews': active_crews,
        }

        # Datos para gráficas
        context['crew_data'] = json.dumps({
            'labels': ['Cuadrilla A', 'Cuadrilla B', 'Cuadrilla C'],
            'datasets': [{'label': 'Kg', 'data': [100, 200, 150], 'backgroundColor': ['#FF6B6B', '#4ECDC4', '#45B7D1']}]
        })

        context['area_data'] = json.dumps({
            'labels': ['Túneles', 'Placas', 'Nuqueras', 'Troquelado'],
            'datasets': [{'data': [tunnel_kg, plate_kg, nuquera_kg, troquelado_kg], 'backgroundColor': ['#45B7D1', '#96CEB4', '#FFEAA7', '#DDA0DD'], 'borderColor': '#1a1a2e', 'borderWidth': 3}]
        })

        context['trend_data'] = json.dumps({
            'labels': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
            'datasets': [{'label': 'Producción', 'data': [100, 120, 110, 130, 140], 'borderColor': '#4ECDC4', 'backgroundColor': 'rgba(78, 205, 196, 0.1)', 'borderWidth': 3, 'fill': True, 'tension': 0.4}]
        })

        context['comparison_data'] = json.dumps({
            'labels': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
            'datasets': [
                {'label': 'Túneles', 'data': [50, 60, 55, 65, 70], 'borderColor': '#45B7D1', 'borderWidth': 3, 'tension': 0.4},
                {'label': 'Placas', 'data': [50, 60, 55, 65, 70], 'borderColor': '#96CEB4', 'borderWidth': 3, 'tension': 0.4}
            ]
        })

        return context
