from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
import json


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'productions/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # KPIs con valores quemados
        context['kpis'] = {
            'total_kg': 1000.0,
            'tunnel_kg': 300.0,
            'plate_kg': 400.0,
            'nuquera_kg': 200.0,
            'troquelado_kg': 100.0,
            'active_pp': 5,
            'active_crews': 3,
        }

        # Gráficas con datos de ejemplo
        context['crew_data'] = json.dumps({
            'labels': ['Cuadrilla A', 'Cuadrilla B', 'Cuadrilla C'],
            'datasets': [{'label': 'Kg', 'data': [100, 200, 150], 'backgroundColor': ['#FF6B6B', '#4ECDC4', '#45B7D1']}]
        })

        context['area_data'] = json.dumps({
            'labels': ['Túneles', 'Placas', 'Nuqueras', 'Troquelado'],
            'datasets': [{'data': [300, 400, 200, 100], 'backgroundColor': ['#45B7D1', '#96CEB4', '#FFEAA7', '#DDA0DD'], 'borderColor': '#1a1a2e', 'borderWidth': 3}]
        })

        context['trend_data'] = json.dumps({
            'labels': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
            'datasets': [{'label': 'Producción', 'data': [100, 120, 110, 130, 140, 120, 150], 'borderColor': '#4ECDC4', 'backgroundColor': 'rgba(78, 205, 196, 0.1)', 'borderWidth': 3, 'fill': True, 'tension': 0.4}]
        })

        context['comparison_data'] = json.dumps({
            'labels': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
            'datasets': [
                {'label': 'Túneles', 'data': [150, 160, 155, 165, 170, 160, 180], 'borderColor': '#45B7D1', 'borderWidth': 3, 'tension': 0.4},
                {'label': 'Placas', 'data': [200, 210, 215, 225, 220, 215, 230], 'borderColor': '#96CEB4', 'borderWidth': 3, 'tension': 0.4}
            ]
        })

        return context
