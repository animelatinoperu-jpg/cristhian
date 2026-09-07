from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from django.utils import timezone
from django.db.models import Sum
from django.db.models.functions import Coalesce
from datetime import timedelta
import json

from .models import ProductionOrder, Crew


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'productions/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=14)

        context['kpis'] = {
            'total_kg': 0,
            'tunnel_kg': 0,
            'plate_kg': 0,
            'nuquera_kg': 0,
            'troquelado_kg': 0,
            'active_pp': ProductionOrder.objects.filter(status__in=['OPEN', 'IN_PROGRESS']).count(),
            'active_crews': Crew.objects.filter(active=True).count(),
        }

        context['crew_performance'] = json.dumps({'labels': [], 'datasets': []})
        context['area_performance'] = json.dumps({'labels': [], 'datasets': []})
        context['shift_performance'] = json.dumps({'labels': [], 'datasets': []})
        context['worker_top10'] = json.dumps({'labels': [], 'datasets': []})
        context['daily_trend'] = json.dumps({'labels': [], 'datasets': []})
        context['tunnel_vs_plates'] = json.dumps({'labels': [], 'datasets': []})

        return context
