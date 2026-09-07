from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from django.utils import timezone
from django.db.models import Sum, Count, Q, F
from django.db.models.functions import Coalesce
from datetime import timedelta
import json

from .models import (
    ProductionOrder, TunnelCrewEntry, PlateCrewEntry, NuqueraEntry,
    TroqueladoEntry, Crew, Worker
)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'productions/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=14)

        context['kpis'] = self.get_kpis(start_date, end_date)
        context['crew_data'] = json.dumps(self.get_crew_data(start_date, end_date))
        context['area_data'] = json.dumps(self.get_area_data(start_date, end_date))
        context['trend_data'] = json.dumps(self.get_trend_data(start_date, end_date))
        context['comparison_data'] = json.dumps(self.get_comparison_data(start_date, end_date))

        return context

    def get_kpis(self, start_date, end_date):
        tunnel_kg = (TunnelCrewEntry.objects.filter(
            date__range=[start_date, end_date], is_active=True
        ).aggregate(total=Coalesce(Sum('tray_count'), 0))['total'] or 0) * 10

        plate_kg = (PlateCrewEntry.objects.filter(
            date__range=[start_date, end_date], is_active=True
        ).aggregate(total=Coalesce(Sum('tray_count'), 0))['total'] or 0) * 10

        nuquera_kg = NuqueraEntry.objects.filter(
            date__range=[start_date, end_date], is_active=True
        ).aggregate(total=Coalesce(Sum('weight_kg'), 0))['total'] or 0

        troquelado_kg = TroqueladoEntry.objects.filter(
            date__range=[start_date, end_date], is_active=True
        ).aggregate(total=Coalesce(Sum('weight_kg'), 0))['total'] or 0

        total = tunnel_kg + plate_kg + nuquera_kg + troquelado_kg

        return {
            'total_kg': float(total),
            'tunnel_kg': float(tunnel_kg),
            'plate_kg': float(plate_kg),
            'nuquera_kg': float(nuquera_kg),
            'troquelado_kg': float(troquelado_kg),
            'active_pp': ProductionOrder.objects.filter(status__in=['OPEN', 'IN_PROGRESS']).count(),
            'active_crews': Crew.objects.filter(active=True).count(),
        }

    def get_crew_data(self, start_date, end_date):
        crews_data = []
        for crew in Crew.objects.filter(active=True):
            tunnel = TunnelCrewEntry.objects.filter(
                crew=crew, date__range=[start_date, end_date], is_active=True
            ).aggregate(total=Coalesce(Sum('tray_count'), 0))['total'] or 0

            plate = PlateCrewEntry.objects.filter(
                crew=crew, date__range=[start_date, end_date], is_active=True
            ).aggregate(total=Coalesce(Sum('tray_count'), 0))['total'] or 0

            total_kg = (tunnel + plate) * 10
            if total_kg > 0:
                crews_data.append({'name': crew.name, 'kg': float(total_kg)})

        crews_data.sort(key=lambda x: x['kg'], reverse=True)
        top_5 = crews_data[:5]

        return {
            'labels': [c['name'] for c in top_5],
            'datasets': [{
                'label': 'Kg Procesados',
                'data': [c['kg'] for c in top_5],
                'backgroundColor': ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7']
            }]
        }

    def get_area_data(self, start_date, end_date):
        tunnel_kg = (TunnelCrewEntry.objects.filter(
            date__range=[start_date, end_date], is_active=True
        ).aggregate(total=Coalesce(Sum('tray_count'), 0))['total'] or 0) * 10

        plate_kg = (PlateCrewEntry.objects.filter(
            date__range=[start_date, end_date], is_active=True
        ).aggregate(total=Coalesce(Sum('tray_count'), 0))['total'] or 0) * 10

        nuquera_kg = NuqueraEntry.objects.filter(
            date__range=[start_date, end_date], is_active=True
        ).aggregate(total=Coalesce(Sum('weight_kg'), 0))['total'] or 0

        troquelado_kg = TroqueladoEntry.objects.filter(
            date__range=[start_date, end_date], is_active=True
        ).aggregate(total=Coalesce(Sum('weight_kg'), 0))['total'] or 0

        total = tunnel_kg + plate_kg + nuquera_kg + troquelado_kg

        if total > 0:
            tunnel_pct = (tunnel_kg / total) * 100
            plate_pct = (plate_kg / total) * 100
            nuquera_pct = (nuquera_kg / total) * 100
            troquelado_pct = (troquelado_kg / total) * 100
        else:
            tunnel_pct = plate_pct = nuquera_pct = troquelado_pct = 0

        return {
            'labels': ['Túneles', 'Placas', 'Nuqueras', 'Troquelado'],
            'datasets': [{
                'data': [float(tunnel_kg), float(plate_kg), float(nuquera_kg), float(troquelado_kg)],
                'backgroundColor': ['#45B7D1', '#96CEB4', '#FFEAA7', '#DDA0DD'],
                'borderColor': '#1a1a2e',
                'borderWidth': 3
            }]
        }

    def get_trend_data(self, start_date, end_date):
        days = (end_date - start_date).days + 1
        data = []
        labels = []

        for i in range(days):
            date = start_date + timedelta(days=i)

            tunnel = TunnelCrewEntry.objects.filter(
                date=date, is_active=True
            ).aggregate(total=Coalesce(Sum('tray_count'), 0))['total'] or 0

            plate = PlateCrewEntry.objects.filter(
                date=date, is_active=True
            ).aggregate(total=Coalesce(Sum('tray_count'), 0))['total'] or 0

            total_kg = (tunnel + plate) * 10
            data.append(float(total_kg))
            labels.append(date.strftime('%a'))

        return {
            'labels': labels,
            'datasets': [{
                'label': 'Producción',
                'data': data,
                'borderColor': '#4ECDC4',
                'backgroundColor': 'rgba(78, 205, 196, 0.1)',
                'borderWidth': 3,
                'fill': True,
                'tension': 0.4
            }]
        }

    def get_comparison_data(self, start_date, end_date):
        days = (end_date - start_date).days + 1
        tunnel_data = []
        plate_data = []
        labels = []

        for i in range(days):
            date = start_date + timedelta(days=i)

            tunnel = TunnelCrewEntry.objects.filter(
                date=date, is_active=True
            ).aggregate(total=Coalesce(Sum('tray_count'), 0))['total'] or 0

            plate = PlateCrewEntry.objects.filter(
                date=date, is_active=True
            ).aggregate(total=Coalesce(Sum('tray_count'), 0))['total'] or 0

            tunnel_data.append(float(tunnel * 10))
            plate_data.append(float(plate * 10))
            labels.append(date.strftime('%a'))

        return {
            'labels': labels,
            'datasets': [
                {
                    'label': 'Túneles',
                    'data': tunnel_data,
                    'borderColor': '#45B7D1',
                    'borderWidth': 3,
                    'tension': 0.4
                },
                {
                    'label': 'Placas',
                    'data': plate_data,
                    'borderColor': '#96CEB4',
                    'borderWidth': 3,
                    'tension': 0.4
                }
            ]
        }
