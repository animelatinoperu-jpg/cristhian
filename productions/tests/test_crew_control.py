import datetime as dt
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from productions.models import (
    AreaAssignment,
    Crew,
    Customer,
    PlateCrewEntry,
    PlateEntry,
    PlatePosition,
    Product,
    ProductionOrder,
    ReceptionEntry,
    Role,
    TemplateVersion,
    Tunnel,
    TunnelCrewEntry,
    TunnelEntry,
    TunnelFill,
    TunnelRack,
    Vehicle,
    User,
)
from productions.services.crew_control import crew_control_summary, reception_cone_pota_summary


class CrewControlTests(TestCase):
    def setUp(self):
        manager_role = Role.objects.create(code=Role.Codes.PRODUCTION_MANAGER, name="Jefe")
        self.manager = User.objects.create_user("crew-manager", password="Secure-test-123")
        self.manager.roles.add(manager_role)
        self.area_user = User.objects.create_user("crew-area", password="Secure-test-123")
        self.other_user = User.objects.create_user("crew-other", password="Secure-test-123")

        customer = Customer.objects.create(name="Cliente de cuadrillas")
        main_product = Product.objects.create(code="MAIN-CREW", description="POTA A GRANEL")
        self.product = Product.objects.create(code="PP-CREW", description="CONOS DE POTA")
        self.template = TemplateVersion.objects.create(
            code="PP-CREW-CONTROL",
            file=SimpleUploadedFile("crew-template.xlsm", b"crew-control"),
            original_filename="crew-template.xlsm",
            sha256="c" * 64,
            uploaded_by=self.manager,
            rules={"tray_kg": 10},
        )
        self.production = ProductionOrder.objects.create(
            number=901,
            plant_lot="LOTE-CREW",
            customer=customer,
            process="Pota",
            main_product=main_product,
            reception_date=dt.date(2026, 7, 16),
            production_date=dt.date(2026, 7, 16),
            shift=ProductionOrder.Shift.DAY,
            template_version=self.template,
            created_by=self.manager,
        )
        AreaAssignment.objects.create(
            production=self.production,
            user=self.area_user,
            area=AreaAssignment.Area.TUNNEL_CREW,
            shift=self.production.shift,
        )

        self.andres = Crew.objects.create(code="CUAD-ANDRES", name="ANDRES")
        self.charly = Crew.objects.create(code="CUAD-CHARLY", name="CHARLY")
        t1 = Tunnel.objects.create(code="T1", name="Túnel 1")
        t2 = Tunnel.objects.create(code="T2", name="Túnel 2")
        fill1 = TunnelFill.objects.create(
            production=self.production,
            tunnel=t1,
            fill_number=1,
            date=self.production.production_date,
            supervisor=self.manager,
        )
        fill2 = TunnelFill.objects.create(
            production=self.production,
            tunnel=t2,
            fill_number=1,
            date=self.production.production_date,
            supervisor=self.manager,
        )
        rack1 = TunnelRack.objects.create(fill=fill1, code="R01", position_key="T1!R01", max_trays=50)
        rack2 = TunnelRack.objects.create(fill=fill2, code="R01", position_key="T2!R01", max_trays=50)
        common = {
            "production": self.production,
            "responsible": self.manager,
            "observation": "",
        }
        TunnelEntry.objects.create(
            **common,
            rack=rack1,
            product=self.product,
            tray_count=20,
            date=self.production.production_date,
        )
        TunnelEntry.objects.create(
            **common,
            rack=rack2,
            product=self.product,
            tray_count=30,
            date=self.production.production_date,
        )
        TunnelCrewEntry.objects.create(
            **common,
            fill=fill1,
            rack=rack1,
            crew=self.andres,
            page_or_block="PAGINA 1",
            tray_count=20,
            date=self.production.production_date,
        )
        TunnelCrewEntry.objects.create(
            **common,
            fill=fill2,
            rack=rack2,
            crew=self.andres,
            page_or_block="PAGINA 1",
            tray_count=30,
            date=self.production.production_date,
        )

        position = PlatePosition.objects.create(
            template_version=self.template,
            plate_rack=PlatePosition.PlateRack.P1,
            position_key="ENV. PLACAS!E5",
            display_name="P1 · posición 1",
        )
        PlateEntry.objects.create(
            **common,
            date=self.production.production_date,
            shift=self.production.shift,
            position=position,
            product=self.product,
            tray_count=15,
            crew=self.andres,
        )
        PlateCrewEntry.objects.create(
            **common,
            position=position,
            page="PAGINA 1",
            crew=self.andres,
            tray_count=15,
            date=self.production.production_date,
        )
        PlateCrewEntry.objects.create(
            **common,
            position=position,
            page="PAGINA 2",
            crew=self.charly,
            tray_count=10,
            date=self.production.production_date,
            is_active=False,
        )

    def test_summary_combines_processes_without_mixing_their_subtotals(self):
        summary = crew_control_summary(self.production)

        self.assertEqual(summary["crew_count"], 1)
        self.assertEqual(summary["tunnel_trays"], 50)
        self.assertEqual(summary["plate_trays"], 15)
        self.assertEqual(summary["total_trays"], 65)
        self.assertEqual(str(summary["total_kg"]), "650.00")

        row = summary["rows"][0]
        self.assertEqual(row["crew_name"], "ANDRES")
        self.assertEqual(row["tunnel_trays"], 50)
        self.assertEqual(row["plate_trays"], 15)
        self.assertEqual(row["total_trays"], 65)
        self.assertEqual([item["trays"] for item in row["tunnels"][:2]], [20, 30])

    def test_summary_unifies_duplicate_crew_names_with_accents(self):
        accented = Crew.objects.create(code="CUAD-ANDRES-ACCENT", name="ANDRÉS")
        fill = TunnelFill.objects.filter(production=self.production).order_by("pk").first()
        rack = fill.racks.first()
        TunnelCrewEntry.objects.create(
            production=self.production,
            responsible=self.manager,
            fill=fill,
            rack=rack,
            product=self.product,
            crew=accented,
            page_or_block=rack.code,
            tray_count=5,
            date=self.production.production_date,
        )

        summary = crew_control_summary(self.production)

        self.assertEqual(summary["crew_count"], 1)
        self.assertEqual(summary["rows"][0]["crew_name"], "ANDRES")
        self.assertEqual(summary["rows"][0]["tunnel_trays"], 55)
        self.assertEqual(summary["rows"][0]["total_trays"], 70)

    def test_reception_cone_pota_summary_groups_by_normalized_crew_name(self):
        vehicle = Vehicle.objects.create(plate="RM-123")
        plain = Crew.objects.create(code="CUAD-FERMIN-A", name="FERMIN")
        accented = Crew.objects.create(code="CUAD-FERMIN-B", name="FERMÍN")
        ReceptionEntry.objects.create(
            production=self.production,
            responsible=self.manager,
            observation="",
            date=self.production.reception_date,
            vehicle=vehicle,
            car_number="1",
            product=self.product,
            crew=accented,
            container="1",
            weight_kg="100.00",
        )
        ReceptionEntry.objects.create(
            production=self.production,
            responsible=self.manager,
            observation="",
            date=self.production.reception_date,
            vehicle=vehicle,
            car_number="1",
            product=self.product,
            crew=plain,
            container="2",
            weight_kg="130.00",
        )

        summary = reception_cone_pota_summary(self.production)

        self.assertEqual(summary["entry_count"], 2)
        self.assertEqual(summary["total_weight"], Decimal("230.00"))
        self.assertEqual(len(summary["rows"]), 1)
        self.assertEqual(
            [(row["crew_name"], row["dino_count"], row["total_weight"]) for row in summary["rows"]],
            [("FERMIN", 2, Decimal("230.00"))],
        )

    def test_manager_sees_preview_and_full_consolidated_page(self):
        self.client.force_login(self.manager)

        detail = self.client.get(reverse("productions:detail", args=[self.production.pk]))
        consolidated = self.client.get(reverse("productions:crew_control", args=[self.production.pk]))

        self.assertContains(detail, "CONTROL GENERAL DE CUADRILLAS")
        self.assertContains(detail, "650,00 kg")
        self.assertEqual(consolidated.status_code, 200)
        self.assertContains(consolidated, "Túneles y envasado en plaqueros")
        self.assertContains(consolidated, "ANDRES")
        self.assertContains(consolidated, "650,00 kg")
        self.assertContains(consolidated, "CONSOLIDADO OFICIAL")
        self.assertNotContains(consolidated, "VISTA DE PRUEBA")
        self.assertContains(consolidated, "Llenada 1 · R01")
        self.assertContains(consolidated, "Bachada 1 · Plaquero 1")
        self.assertContains(consolidated, "PAGINA 1")

    def test_assigned_crew_user_can_view_but_unassigned_user_cannot(self):
        url = reverse("productions:crew_control", args=[self.production.pk])

        self.client.force_login(self.area_user)
        self.assertEqual(self.client.get(url).status_code, 200)

        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(url).status_code, 403)
