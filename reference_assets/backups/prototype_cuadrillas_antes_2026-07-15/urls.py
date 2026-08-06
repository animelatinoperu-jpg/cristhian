from django.urls import path

from . import views


app_name = "productions"
urlpatterns = [
    path("", views.ProductionListView.as_view(), name="list"),
    path("cuentas/registro/", views.UserRegistrationView.as_view(), name="register"),
    path("cuentas/registro/listo/", views.UserRegistrationDoneView.as_view(), name="register_done"),
    path("usuarios/", views.UserListView.as_view(), name="user_list"),
    path("usuarios/<int:pk>/acceso/", views.UserAccessUpdateView.as_view(), name="user_access"),
    path("salud/", views.health, name="health"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("service-worker.js", views.service_worker, name="service_worker"),
    path("producciones/nueva/", views.ProductionCreateView.as_view(), name="create"),
    path("producciones/<int:pk>/editar/", views.ProductionUpdateView.as_view(), name="update"),
    path("catalogos/", views.CatalogDashboardView.as_view(), name="catalogs"),
    path("catalogos/clientes/nuevo/", views.CustomerCreateView.as_view(), name="customer_create"),
    path("catalogos/vehiculos/nuevo/", views.VehicleCreateView.as_view(), name="vehicle_create"),
    path("catalogos/trabajadores/nuevo/", views.WorkerCreateView.as_view(), name="worker_create"),
    path("catalogos/tarifas/nueva/", views.RateCreateView.as_view(), name="rate_create"),
    path("producciones/<int:pk>/", views.ProductionDetailView.as_view(), name="detail"),
    path("producciones/<int:pk>/reporte/", views.ProductionReportView.as_view(), name="report"),
    path("producciones/<int:pk>/estado/", views.ProductionTransitionView.as_view(), name="transition"),
    path("producciones/<int:pk>/tuneles/llenada/nueva/", views.TunnelFillCreateView.as_view(), name="tunnel_fill_create"),
    path("producciones/<int:pk>/tuneles/registro/nuevo/", views.TunnelEntryCreateView.as_view(), name="tunnel_entry_create"),
    path("producciones/<int:pk>/tuneles/llenada/<int:fill_pk>/captura/", views.TunnelBatchEntryView.as_view(), name="tunnel_batch"),
    path(
        "producciones/<int:pk>/tuneles/llenada/<int:fill_pk>/registro/<int:entry_pk>/eliminar/",
        views.TunnelEntryDeleteView.as_view(),
        name="tunnel_entry_delete",
    ),
    path("producciones/<int:pk>/tuneles/llenada/<int:fill_pk>/estado/", views.TunnelFillTransitionView.as_view(), name="tunnel_fill_transition"),
    path("producciones/<int:pk>/recepcion/nuevo/", views.ReceptionCreateView.as_view(), name="reception_create"),
    path("producciones/<int:pk>/nuqueras/nuevo/", views.NuqueraCreateView.as_view(), name="nuquera_create"),
    path("producciones/<int:pk>/cuadrillas-tunel/nuevo/", views.TunnelCrewCreateView.as_view(), name="tunnel_crew_create"),
    path("producciones/<int:pk>/placas/nuevo/", views.PlateCreateView.as_view(), name="plate_create"),
    path("producciones/<int:pk>/cuadrillas-placas/nuevo/", views.PlateCrewCreateView.as_view(), name="plate_crew_create"),
    path("producciones/<int:pk>/empaque-tunel/nuevo/", views.TunnelPackagingCreateView.as_view(), name="tunnel_pack_create"),
    path("producciones/<int:pk>/empaque-placas/nuevo/", views.PlatePackagingCreateView.as_view(), name="plate_pack_create"),
    path("producciones/<int:pk>/materiales/nuevo/", views.MaterialUsageCreateView.as_view(), name="material_create"),
    path("producciones/<int:pk>/costos/nuevo/", views.CostEntryCreateView.as_view(), name="cost_create"),
    path(
        "producciones/<int:pk>/registros/<str:module>/<int:entry_pk>/corregir/",
        views.OperationalEntryUpdateView.as_view(),
        name="operational_entry_update",
    ),
    path(
        "producciones/<int:pk>/registros/<str:module>/<int:entry_pk>/eliminar/",
        views.OperationalEntryDeleteView.as_view(),
        name="operational_entry_delete",
    ),
    path("producciones/<int:pk>/excel/<str:kind>/generar/", views.GenerateExcelView.as_view(), name="generate_excel"),
    path("producciones/<int:pk>/pdf/", views.ProductionPdfView.as_view(), name="production_pdf"),
    path("archivos/<int:pk>/descargar/", views.DownloadGeneratedFileView.as_view(), name="download_file"),
]
