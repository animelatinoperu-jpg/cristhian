import datetime as dt
import json
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from productions.models import (
    Crew,
    Customer,
    NuqueraEntry,
    PlateCrewEntry,
    PlateEntry,
    PlatePosition,
    Product,
    ProductionOrder,
    ReceptionEntry,
    Role,
    TemplateVersion,
    TroqueladoEntry,
    Tunnel,
    TunnelCrewEntry,
    TunnelEntry,
    TunnelFill,
    TunnelRack,
    User,
    Vehicle,
    Worker,
)


class DashboardViewTests(TestCase):
    def setUp(self):
        role = Role.objects.create(code=Role.Codes.PRODUCTION_MANAGER, name="Jefe")
        self.user = User.objects.create_user("dashboard-user", password="Secure-test-123")
        self.user.roles.add(role)
        customer = Customer.objects.create(name="Cliente dashboard")
        main_product = Product.objects.create(code="POTA-GRANEL", description="POTA A GRANEL")
        self.raw_product = Product.objects.create(code="RM-001", description="POTA ENTERA")
        self.product = Product.objects.create(code="PP-001", description="ALETA ENTERA")
        self.template = TemplateVersion.objects.create(
            code="PP-DASHBOARD",
            file=SimpleUploadedFile("template.xlsm", b"fixture-dashboard"),
            original_filename="template.xlsm",
            sha256="8" * 64,
            uploaded_by=self.user,
            rules={"tray_kg": 10},
        )
        self.today = timezone.localdate()
        self.production = ProductionOrder.objects.create(
            number=901,
            plant_lot="LOTE-901",
            customer=customer,
            process="Pota",
            main_product=main_product,
            reception_date=self.today,
            production_date=self.today,
            shift=ProductionOrder.Shift.DAY,
            template_version=self.template,
            status=ProductionOrder.Status.OPEN,
            created_by=self.user,
        )
        self.vehicle = Vehicle.objects.create(plate="DASH-001")
        self.crew = Crew.objects.create(code="DASH-01", name="Cuadrilla Dashboard")
        self.worker = Worker.objects.create(
            internal_code="DASH-W01",
            full_name="Trabajador Dashboard",
            crew=self.crew,
        )
        tunnel = Tunnel.objects.create(code="T1", name="Túnel 1")
        self.fill = TunnelFill.objects.create(
            production=self.production,
            tunnel=tunnel,
            fill_number=1,
            date=self.today,
            supervisor=self.user,
        )
        self.rack = TunnelRack.objects.create(
            fill=self.fill,
            code="R01",
            position_key="T1!A1",
        )
        self.position = PlatePosition.objects.create(
            template_version=self.template,
            plate_rack=PlatePosition.PlateRack.P1,
            position_key="P1!E5",
            display_name="Posición 1",
        )

        common = {"production": self.production, "responsible": self.user, "observation": ""}
        ReceptionEntry.objects.create(
            **common,
            date=self.today,
            vehicle=self.vehicle,
            car_number="1",
            product=self.raw_product,
            crew=self.crew,
            container="7",
            weight_kg=Decimal("100.00"),
        )
        NuqueraEntry.objects.create(
            **common,
            date=self.today,
            shift=ProductionOrder.Shift.DAY,
            crew=self.crew,
            worker=self.worker,
            process="Perfilado",
            weight_kg=Decimal("25.00"),
            start_time=dt.time(8, 0),
            end_time=dt.time(9, 0),
        )
        TunnelEntry.objects.create(
            **common,
            rack=self.rack,
            product=self.product,
            tray_count=10,
            date=self.today,
        )
        TunnelCrewEntry.objects.create(
            **common,
            fill=self.fill,
            rack=self.rack,
            product=self.product,
            crew=self.crew,
            page_or_block="PAGINA 1",
            tray_count=10,
            date=self.today,
        )
        PlateEntry.objects.create(
            **common,
            date=self.today,
            shift=ProductionOrder.Shift.DAY,
            position=self.position,
            product=self.product,
            tray_count=12,
            crew=self.crew,
        )
        PlateCrewEntry.objects.create(
            **common,
            position=self.position,
            page="PAGINA 1",
            product=self.product,
            crew=self.crew,
            tray_count=12,
            date=self.today,
        )
        TroqueladoEntry.objects.create(
            **common,
            date=self.today,
            shift=ProductionOrder.Shift.DAY,
            crew=self.crew,
            worker=self.worker,
            product_type="BOTÓN",
            cajas=10,
            kg_por_caja=Decimal("20.00"),
            weight_kg=Decimal("200.00"),
            start_time=dt.time(8, 0),
            end_time=dt.time(9, 0),
        )

    def test_requires_login(self):
        response = self.client.get(reverse("productions:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/cuentas/login/", response["Location"])

    def test_shows_real_aggregated_data(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("productions:dashboard"))
        self.assertEqual(response.status_code, 200)

        kpis = response.context["kpis"]
        self.assertEqual(kpis["total_kg"], 100.0)
        self.assertEqual(kpis["tunnel_kg"], 100.0)  # 10 bandejas x 10 kg
        self.assertEqual(kpis["plate_kg"], 120.0)  # 12 bandejas x 10 kg
        self.assertEqual(kpis["nuquera_kg"], 25.0)
        self.assertEqual(kpis["troquelado_kg"], 200.0)
        self.assertEqual(kpis["active_pp"], 1)
        self.assertEqual(kpis["active_crews"], 1)

        crew_data = json.loads(response.context["crew_data"])
        self.assertIn("Cuadrilla Dashboard", crew_data["labels"])
        # 25 (nuquera) + 200 (troquelado) + 100 (túnel) + 120 (placas)
        self.assertIn(445.0, crew_data["datasets"][0]["data"])

        area_data = json.loads(response.context["area_data"])
        self.assertEqual(area_data["datasets"][0]["data"], [100.0, 100.0, 120.0, 25.0, 200.0])

        trend_data = json.loads(response.context["trend_data"])
        self.assertEqual(len(trend_data["labels"]), 14)
        # El último día es hoy: 100 kg de recepción.
        self.assertEqual(trend_data["datasets"][0]["data"][-1], 100.0)

        comparison_data = json.loads(response.context["comparison_data"])
        self.assertEqual(comparison_data["datasets"][0]["data"][-1], 100.0)
        self.assertEqual(comparison_data["datasets"][1]["data"][-1], 120.0)

        recent = list(response.context["recent_pp"])
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].number, 901)
        self.assertEqual(recent[0].reception_total, Decimal("100.00"))
        self.assertTrue(response.context["has_data"])

    def test_void_production_is_excluded(self):
        self.production.status = ProductionOrder.Status.VOID
        self.production.save(update_fields=["status"])
        self.client.force_login(self.user)
        response = self.client.get(reverse("productions:dashboard"))
        self.assertEqual(response.status_code, 200)
        kpis = response.context["kpis"]
        self.assertEqual(kpis["total_kg"], 0.0)
        self.assertEqual(kpis["tunnel_kg"], 0.0)
        self.assertEqual(kpis["plate_kg"], 0.0)
        self.assertEqual(kpis["nuquera_kg"], 0.0)
        self.assertEqual(kpis["troquelado_kg"], 0.0)
        self.assertEqual(kpis["active_pp"], 0)
        self.assertFalse(response.context["has_data"])
