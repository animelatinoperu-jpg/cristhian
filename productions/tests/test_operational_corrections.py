import datetime as dt
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from productions.models import (
    AreaAssignment,
    AuditLog,
    CostEntry,
    Crew,
    Customer,
    Material,
    MaterialUsage,
    NuqueraEntry,
    PlateCrewEntry,
    PlateCarryoverBalance,
    PlateEntry,
    PlatePositionTiming,
    PlatePackagingAllocation,
    PlatePackagingEntry,
    PlatePosition,
    Product,
    ProductionOrder,
    Rate,
    ReceptionEntry,
    Role,
    TemplateVersion,
    TroqueladoEntry,
    Tunnel,
    TunnelCrewEntry,
    TunnelEntry,
    TunnelFill,
    TunnelRack,
    TunnelPackagingEntry,
    User,
    Vehicle,
    Worker,
)
from productions.services.excel.generator import production_values
from productions.services.excel.mapper import load_mapping


class OperationalCorrectionTests(TestCase):
    def setUp(self):
        role = Role.objects.create(code=Role.Codes.PRODUCTION_MANAGER, name="Jefe")
        self.user = User.objects.create_user("manager-operations", password="Secure-test-123")
        self.user.roles.add(role)
        customer = Customer.objects.create(name="Cliente operativo")
        main_product = Product.objects.create(code="POTA-GRANEL", description="POTA A GRANEL")
        self.raw_product = Product.objects.create(code="RM-001", description="POTA ENTERA")
        self.product = Product.objects.create(code="PP-001", description="ALETA ENTERA")
        self.template = TemplateVersion.objects.create(
            code="PP-OPERATIONS",
            file=SimpleUploadedFile("template.xlsm", b"fixture-operations"),
            original_filename="template.xlsm",
            sha256="9" * 64,
            uploaded_by=self.user,
            rules={"tunnel_pallet_max": 20, "plate_pallet_max": 20},
        )
        self.production = ProductionOrder.objects.create(
            number=800,
            plant_lot="LOTE-800",
            customer=customer,
            process="Pota",
            main_product=main_product,
            reception_date=dt.date(2026, 7, 14),
            production_date=dt.date(2026, 7, 14),
            shift=ProductionOrder.Shift.DAY,
            template_version=self.template,
            created_by=self.user,
        )
        self.vehicle = Vehicle.objects.create(plate="ABC-123")
        self.reception_crew = Crew.objects.create(code="RM-CUAD-01", name="LUIS")
        self.crew = Crew.objects.create(code="NUQ-01", name="Cuadrilla Uno")
        self.worker = Worker.objects.create(
            internal_code="NUQ-W01",
            full_name="Trabajador Uno",
            crew=self.crew,
        )
        tunnel = Tunnel.objects.create(code="T1", name="Túnel 1")
        self.fill = TunnelFill.objects.create(
            production=self.production,
            tunnel=tunnel,
            fill_number=1,
            date=dt.date(2026, 7, 14),
            supervisor=self.user,
        )
        self.position = PlatePosition.objects.create(
            template_version=self.template,
            plate_rack=PlatePosition.PlateRack.P1,
            position_key="P1!E5",
            display_name="Posición 1",
        )
        self.material = Material.objects.create(name="Bolsa", unit="unidades")
        self.rate = Rate.objects.create(
            process="Fileteado",
            amount=Decimal("1.2500"),
            unit="kg",
            effective_from=dt.date(2026, 1, 1),
        )

        common = {"production": self.production, "responsible": self.user, "observation": ""}
        self.reception = ReceptionEntry.objects.create(
            **common,
            date=dt.date(2026, 7, 14),
            vehicle=self.vehicle,
            car_number="1",
            product=self.raw_product,
            crew=self.reception_crew,
            container="4",
            weight_kg=Decimal("100.00"),
        )
        self.nuquera = NuqueraEntry.objects.create(
            **common,
            date=dt.date(2026, 7, 14),
            shift=ProductionOrder.Shift.DAY,
            crew=self.crew,
            worker=self.worker,
            process="Perfilado",
            weight_kg=Decimal("25.00"),
            start_time=dt.time(8, 0),
            end_time=dt.time(9, 0),
        )
        self.tunnel_crew = TunnelCrewEntry.objects.create(
            **common,
            fill=self.fill,
            crew=self.crew,
            page_or_block="PAGINA 1",
            tray_count=10,
            date=dt.date(2026, 7, 14),
        )
        self.plate = PlateEntry.objects.create(
            **common,
            date=dt.date(2026, 7, 14),
            shift=ProductionOrder.Shift.DAY,
            position=self.position,
            product=self.product,
            tray_count=12,
            crew=self.crew,
        )
        self.plate_crew = PlateCrewEntry.objects.create(
            **common,
            position=self.position,
            page="PAGINA 1",
            product=self.product,
            crew=self.crew,
            tray_count=12,
            date=dt.date(2026, 7, 14),
        )
        self.tunnel_pack = TunnelPackagingEntry.objects.create(
            **common,
            date=dt.date(2026, 7, 14),
            pallet_number=1,
            product=self.product,
            package_count=5,
        )
        self.plate_pack = PlatePackagingEntry.objects.create(
            **common,
            date=dt.date(2026, 7, 14),
            pallet_number=1,
            product=self.product,
            package_count=5,
        )
        self.pack_position = PlatePosition.objects.create(
            template_version=self.template,
            plate_rack=PlatePosition.PlateRack.P2,
            position_key="EM-PLA!N5",
            display_name="Bachada 4 · Plaquero 2",
        )
        self.pack_source = PlateEntry.objects.create(
            **common,
            date=dt.date(2026, 7, 14),
            shift=ProductionOrder.Shift.DAY,
            position=self.pack_position,
            product=self.product,
            tray_count=12,
        )
        PlatePositionTiming.objects.create(
            production=self.production,
            position=self.pack_position,
            load_started_at=dt.datetime(
                2026, 7, 16, 15, 0, tzinfo=dt.timezone.utc
            ),
            load_completed_at=dt.datetime(
                2026, 7, 16, 15, 20, tzinfo=dt.timezone.utc
            ),
            launched_at=dt.datetime(
                2026, 7, 16, 15, 30, tzinfo=dt.timezone.utc
            ),
            unloaded_at=dt.datetime(
                2026, 7, 16, 16, 0, tzinfo=dt.timezone.utc
            ),
        )
        self.plate_pack_trace = PlatePackagingAllocation.objects.create(
            **common,
            date=dt.date(2026, 7, 14),
            source_entry=self.pack_source,
            pallet_number=2,
            package_count=4,
        )
        self.material_usage = MaterialUsage.objects.create(
            **common,
            material=self.material,
            quantity=Decimal("30.000"),
        )
        self.cost = CostEntry.objects.create(
            **common,
            concept="Mano de obra",
            quantity=Decimal("10.000"),
            unit_cost=Decimal("2.5000"),
            rate=self.rate,
        )
        self.troquelado = TroqueladoEntry.objects.create(
            **common,
            date=dt.date(2026, 7, 14),
            shift=ProductionOrder.Shift.DAY,
            crew=self.crew,
            worker=self.worker,
            product_type="BOTÓN",
            cajas=10,
            kg_por_caja=Decimal("20.000"),
            weight_kg=Decimal("200.000"),
            start_time=dt.time(8, 0),
            end_time=dt.time(9, 0),
        )
        self.modules = {
            "reception": (self.reception, "productions:reception_create"),
            "nuqueras": (self.nuquera, "productions:nuquera_create"),
            "tunnel-crews": (self.tunnel_crew, "productions:tunnel_crew_create"),
            "plates": (self.plate, "productions:plate_create"),
            "plate-crews": (self.plate_crew, "productions:plate_crew_create"),
            "tunnel-pack": (self.tunnel_pack, "productions:tunnel_pack_create"),
            "plate-pack": (self.plate_pack_trace, "productions:plate_pack_create"),
            "materials": (self.material_usage, "productions:material_create"),
            "costs": (self.cost, "productions:cost_create"),
            "troquelado": (self.troquelado, "productions:troquelado_create"),
        }
        self.client.force_login(self.user)

    def test_all_operational_pages_show_saved_records_and_correction_actions(self):
        for module, (entry, create_url_name) in self.modules.items():
            with self.subTest(module=module):
                response = self.client.get(reverse(create_url_name, args=[self.production.pk]))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Registros guardados")
                self.assertContains(response, "Corregir")
                self.assertContains(response, "Eliminar")
                self.assertContains(
                    response,
                    reverse(
                        "productions:operational_entry_update",
                        args=[self.production.pk, module, entry.pk],
                    ),
                )

    def test_troquelado_create_computes_weight_and_redirects_on_volver(self):
        response = self.client.post(
            reverse("productions:troquelado_create", args=[self.production.pk]),
            {
                "shift": ProductionOrder.Shift.DAY,
                "crew": str(self.crew.pk),
                "worker": str(self.worker.pk),
                "product_type": "BOTÓN",
                "cajas": "7",
                "kg_por_caja": "20.00",
                "start_time": "08:00",
                "end_time": "09:00",
                "observation": "",
                "volver": "1",
            },
        )
        self.assertRedirects(
            response,
            reverse("productions:detail", args=[self.production.pk]),
        )
        entry = TroqueladoEntry.objects.get(worker=self.worker, cajas=7)
        self.assertEqual(entry.weight_kg, Decimal("140.00"))
        self.assertEqual(entry.date, self.production.production_date)

    def test_troquelado_dashboard_shows_worker_breakdown(self):
        response = self.client.get(
            reverse("productions:troquelado_create", args=[self.production.pk])
        )
        self.assertEqual(response.status_code, 200)
        dashboard = response.context["troquelado_dashboard"]
        self.assertEqual(dashboard["record_count"], 1)
        self.assertEqual(dashboard["cajas_total"], 10)
        self.assertEqual(dashboard["kg_total"], Decimal("200.00"))
        self.assertEqual(len(dashboard["per_crew"]), 1)
        crew = dashboard["per_crew"][0]
        self.assertEqual(crew["crew_name"], self.crew.name)
        self.assertEqual(crew["kg"], Decimal("200.00"))
        self.assertEqual(len(crew["workers"]), 1)
        self.assertEqual(crew["workers"][0]["name"], self.worker.full_name)
        self.assertContains(response, "TOTAL PESADO")
        self.assertContains(response, "Resumen de la jornada")

    def test_troquelado_rejects_end_before_start(self):
        response = self.client.post(
            reverse("productions:troquelado_create", args=[self.production.pk]),
            {
                "shift": ProductionOrder.Shift.DAY,
                "crew": str(self.crew.pk),
                "worker": str(self.worker.pk),
                "product_type": "BOTÓN",
                "cajas": "5",
                "kg_por_caja": "10.00",
                "start_time": "10:00",
                "end_time": "09:00",
                "observation": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La hora de término debe ser posterior al inicio")

    def test_production_detail_shows_troquelado_card(self):
        response = self.client.get(
            reverse("productions:detail", args=[self.production.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ">Troquelado<")
        self.assertContains(
            response,
            reverse("productions:troquelado_create", args=[self.production.pk]),
        )

    def test_production_detail_shows_one_combined_plate_access(self):
        response = self.client.get(
            reverse("productions:detail", args=[self.production.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Env. placas", count=1)
        self.assertContains(response, "Envasado y cuadrillas", count=1)
        self.assertNotContains(response, "Cuadrillas placas")
        self.assertContains(
            response,
            reverse("productions:plate_create", args=[self.production.pk]),
        )

    def test_plate_crew_only_user_uses_the_same_combined_access(self):
        operator = User.objects.create_user("plate-crew-only", password="Secure-test-123")
        AreaAssignment.objects.create(
            production=self.production,
            user=operator,
            area=AreaAssignment.Area.PLATE_CREW,
            shift=self.production.shift,
        )
        self.client.force_login(operator)

        response = self.client.get(
            reverse("productions:detail", args=[self.production.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Env. placas", count=1)
        self.assertNotContains(response, "Cuadrillas placas")
        self.assertContains(
            response,
            reverse("productions:plate_crew_create", args=[self.production.pk]),
        )
        self.assertNotContains(
            response,
            reverse("productions:plate_create", args=[self.production.pk]),
        )

    def test_plate_physical_capture_is_separate_from_crew_distribution(self):
        response = self.client.get(
            reverse("productions:plate_create", args=[self.production.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "máximo total de <strong>189 bandejas</strong>")
        self.assertNotContains(response, 'name="crew"')
        self.assertContains(
            response,
            (
                reverse("productions:plate_crew_create", args=[self.production.pk])
                + f"?position={self.position.pk}"
            ),
        )

    def test_plate_products_list_is_limited_to_the_template_products(self):
        Product.objects.create(code="PP-002", description="OTRO DE LA PLANTILLA")
        fuera = Product.objects.create(code="PP-999", description="FUERA DE LA PLANTILLA")
        self.template.rules = {
            **self.template.rules,
            "plate_product_codes": [self.product.code, "PP-002"],
        }
        self.template.save(update_fields=["rules"])

        response = self.client.get(
            reverse("productions:plate_create", args=[self.production.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="%d"' % self.product.pk)
        self.assertNotContains(response, 'value="%d"' % fuera.pk)

    def test_plate_products_list_falls_back_when_template_has_no_plate_codes(self):
        extra = Product.objects.create(code="PP-002", description="OTRO PRODUCTO")
        self.template.rules.pop("plate_product_codes", None)
        self.template.save(update_fields=["rules"])

        response = self.client.get(
            reverse("productions:plate_create", args=[self.production.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="%s"' % extra.pk)

    def test_plate_crew_page_returns_to_plate_physical_capture(self):
        response = self.client.get(
            reverse("productions:plate_crew_create", args=[self.production.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "← Volver a Envasado en plaqueros")
        self.assertContains(
            response,
            reverse("productions:plate_create", args=[self.production.pk]),
        )
        self.assertNotContains(response, f"← Volver al PP {self.production.number}")

    def test_unloaded_plaquero_links_its_codes_to_packaging(self):
        plate_page = self.client.get(
            reverse("productions:plate_create", args=[self.production.pk])
        )
        packaging_url = (
            reverse("productions:plate_pack_create", args=[self.production.pk])
            + f"?position={self.pack_position.pk}#operational-entry-form"
        )

        self.assertEqual(plate_page.status_code, 200)
        self.assertContains(plate_page, packaging_url)
        self.assertContains(plate_page, "Ver códigos y registrar empaque")

        response = self.client.get(packaging_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Códigos y empaque")
        self.assertContains(response, self.product.code)
        self.assertContains(response, self.product.description)
        self.assertContains(response, "4 pendientes")
        self.assertContains(response, "P2 · 4 bultos")
        self.assertContains(response, "80.00 kg")

    def test_plate_packaging_cannot_exceed_the_downloaded_code(self):
        url = (
            reverse("productions:plate_pack_create", args=[self.production.pk])
            + f"?position={self.pack_position.pk}&source={self.pack_source.pk}"
        )
        response = self.client.post(
            url,
            {
                "source_entry": str(self.pack_source.pk),
                "pallet_number": "3",
                "package_count": "2",
                "observation": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            PlatePackagingAllocation.objects.filter(
                production=self.production,
                source_entry=self.pack_source,
                is_active=True,
            ).count(),
            2,
        )

        response = self.client.post(
            url,
            {
                "source_entry": str(self.pack_source.pk),
                "pallet_number": "4",
                "package_count": "1",
                "observation": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solo quedan 0 bandejas")
        self.assertFalse(
            PlatePackagingAllocation.objects.filter(
                production=self.production,
                source_entry=self.pack_source,
                pallet_number=4,
                is_active=True,
            ).exists()
        )

    def test_plate_packaging_requires_the_plaquero_download(self):
        response = self.client.post(
            reverse("productions:plate_pack_create", args=[self.production.pk]),
            {
                "source_entry": str(self.plate.pk),
                "pallet_number": "3",
                "package_count": "1",
                "observation": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Escoja una opción válida. Esa opción no está entre las disponibles.",
        )
        self.assertFalse(
            PlatePackagingAllocation.objects.filter(
                production=self.production,
                source_entry=self.plate,
                is_active=True,
            ).exists()
        )

    def test_plate_code_with_packaging_cannot_be_deleted(self):
        response = self.client.post(
            reverse(
                "productions:operational_entry_delete",
                args=[self.production.pk, "plates", self.pack_source.pk],
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.pack_source.refresh_from_db()
        self.assertTrue(self.pack_source.is_active)
        self.assertContains(response, "ya tiene 4 bultos registrados en empaque")

    def test_plate_packaging_is_aggregated_in_em_pla_by_pallet_and_code(self):
        PlatePackagingAllocation.objects.create(
            production=self.production,
            responsible=self.user,
            observation="",
            date=dt.date(2026, 7, 14),
            source_entry=self.pack_source,
            pallet_number=1,
            package_count=2,
        )

        values = production_values(self.production)

        self.assertEqual(values["plate_packaging.P1.PP-001.packages"], 7)
        self.assertEqual(values["plate_packaging.P1.PP-001.trays"], 14)
        self.assertEqual(
            values["plate_packaging.P1.PP-001.kg"],
            Decimal("140.00"),
        )
        self.assertEqual(values["plate_packaging.P2.PP-001.packages"], 4)

    def test_plate_timing_buttons_take_the_server_time_automatically(self):
        page = self.client.get(
            reverse("productions:plate_create", args=[self.production.pk])
        )
        self.assertContains(page, "Iniciar llenado · tomar hora")
        self.assertContains(page, "Finalizar carga · pendiente de inicio")
        self.assertContains(page, "Registrar lanzamiento · pendiente de carga")
        self.assertContains(page, "Registrar descarga · pendiente de lanzamiento")
        self.assertContains(page, "PASO 1")
        self.assertContains(page, "PASO 2")
        self.assertContains(page, "PASO 3")
        self.assertContains(page, "data-plate-capture-form", html=False)
        self.assertContains(page, "data-plate-start-button", html=False)
        self.assertContains(page, "data-plate-save-button", html=False)
        self.assertContains(
            page,
            reverse(
                "productions:plate_load_start",
                args=[self.production.pk, self.position.pk],
            ),
        )

        start_time = dt.datetime(2026, 7, 16, 15, 0, 0, tzinfo=dt.timezone.utc)
        with patch("productions.views.timezone.now", return_value=start_time):
            response = self.client.post(
                reverse(
                    "productions:plate_load_start",
                    args=[self.production.pk, self.position.pk],
                )
            )
        self.assertEqual(response.status_code, 302)
        timing = PlatePositionTiming.objects.get(
            production=self.production,
            position=self.position,
        )
        self.assertEqual(timing.load_started_at, start_time)
        self.assertEqual(timing.load_started_by, self.user)

        started_page = self.client.get(
            reverse("productions:plate_create", args=[self.production.pk]),
            {"position": self.position.pk},
        )
        self.assertContains(
            started_page,
            f'data-open-positions="{self.position.pk}"',
            html=False,
        )

        load_time = dt.datetime(2026, 7, 16, 15, 30, 45, tzinfo=dt.timezone.utc)
        with patch("productions.views.timezone.now", return_value=load_time):
            response = self.client.post(
                reverse(
                    "productions:plate_load_complete",
                    args=[self.production.pk, self.position.pk],
                )
            )
        self.assertEqual(response.status_code, 302)
        timing.refresh_from_db()
        self.assertEqual(timing.load_completed_at, load_time)
        self.assertEqual(timing.load_completed_by, self.user)
        self.assertIsNone(timing.launched_at)

        loaded_page = self.client.get(response.url)
        self.assertContains(loaded_page, "data-plate-fold open", html=False)
        self.assertContains(loaded_page, "10:30:45")
        self.assertContains(loaded_page, "30 min 45 s")
        self.assertContains(loaded_page, "Registrar lanzamiento · tomar hora")
        self.assertContains(
            loaded_page,
            reverse(
                "productions:plate_launch_register",
                args=[self.production.pk, self.position.pk],
            ),
        )

        launch_time = dt.datetime(2026, 7, 16, 16, 5, 7, tzinfo=dt.timezone.utc)
        with patch("productions.views.timezone.now", return_value=launch_time):
            response = self.client.post(
                reverse(
                    "productions:plate_launch_register",
                    args=[self.production.pk, self.position.pk],
                )
            )
        self.assertEqual(response.status_code, 302)
        timing.refresh_from_db()
        self.assertEqual(timing.launched_at, launch_time)
        self.assertEqual(timing.launched_by, self.user)

        launched_page = self.client.get(response.url)
        self.assertContains(launched_page, "data-plate-fold open", html=False)
        self.assertContains(launched_page, "11:05:07")
        self.assertContains(launched_page, "✓ Lanzamiento registrado")
        self.assertContains(launched_page, "Registrar descarga · tomar hora")
        self.assertContains(launched_page, "plate-position-launched")

        unload_time = dt.datetime(2026, 7, 16, 18, 35, 7, tzinfo=dt.timezone.utc)
        with patch("productions.views.timezone.now", return_value=unload_time):
            response = self.client.post(
                reverse(
                    "productions:plate_unload_register",
                    args=[self.production.pk, self.position.pk],
                )
            )
        self.assertEqual(response.status_code, 302)
        timing.refresh_from_db()
        self.assertEqual(timing.unloaded_at, unload_time)
        self.assertEqual(timing.unloaded_by, self.user)

        unloaded_page = self.client.get(response.url)
        self.assertNotContains(unloaded_page, "data-plate-fold open", html=False)
        self.assertContains(unloaded_page, "13:35:07")
        self.assertContains(unloaded_page, "2 h 30 min 00 s")
        self.assertContains(unloaded_page, "✓ Descarga registrada")
        self.assertContains(unloaded_page, "plate-position-unloaded")

    def test_plate_load_cannot_finish_before_filling_starts(self):
        response = self.client.post(
            reverse(
                "productions:plate_load_complete",
                args=[self.production.pk, self.position.pk],
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Primero pulse «Iniciar llenado»")
        self.assertFalse(
            PlatePositionTiming.objects.filter(
                production=self.production,
                position=self.position,
            ).exists()
        )

    def test_plate_products_cannot_be_registered_before_starting_fill(self):
        second_product = Product.objects.create(
            code="PP-START-02",
            description="PRODUCTO DESPUES DEL INICIO",
        )
        url = reverse("productions:plate_create", args=[self.production.pk])
        payload = {
            "shift": ProductionOrder.Shift.DAY,
            "position": self.position.pk,
            "product": second_product.pk,
            "tray_count": 20,
            "observation": "",
        }

        blocked = self.client.post(url, payload)

        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "Primero inicie el llenado del plaquero")
        self.assertFalse(
            PlateEntry.objects.filter(
                production=self.production,
                position=self.position,
                product=second_product,
            ).exists()
        )

        self.client.post(
            reverse(
                "productions:plate_load_start",
                args=[self.production.pk, self.position.pk],
            )
        )
        saved = self.client.post(url, payload)

        self.assertEqual(saved.status_code, 302)
        self.assertTrue(
            PlateEntry.objects.filter(
                production=self.production,
                position=self.position,
                product=second_product,
                tray_count=20,
                is_active=True,
            ).exists()
        )

    def test_plate_shift_is_derived_from_exact_start_time(self):
        afternoon_product = Product.objects.create(
            code="PP-TARDE-01",
            description="PRODUCTO DEL TURNO TARDE",
        )
        afternoon_start = dt.datetime(
            2026,
            7,
            16,
            19,
            0,
            0,
            tzinfo=dt.timezone.utc,
        )
        with patch("productions.views.timezone.now", return_value=afternoon_start):
            response = self.client.post(
                reverse(
                    "productions:plate_load_start",
                    args=[self.production.pk, self.position.pk],
                ),
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Turno Tarde")
        self.assertContains(response, "14:00:00")

        saved = self.client.post(
            reverse("productions:plate_create", args=[self.production.pk]),
            {
                "position": self.position.pk,
                "product": afternoon_product.pk,
                "tray_count": 20,
                "observation": "",
            },
        )

        self.assertEqual(saved.status_code, 302)
        entry = PlateEntry.objects.get(
            production=self.production,
            position=self.position,
            product=afternoon_product,
        )
        self.assertEqual(entry.shift, ProductionOrder.Shift.AFTERNOON)

    def test_shift_boundaries_follow_confirmed_plant_schedule(self):
        lima = dt.timezone(dt.timedelta(hours=-5))
        cases = (
            (dt.datetime(2026, 7, 16, 6, 0, tzinfo=lima), ProductionOrder.Shift.DAY),
            (dt.datetime(2026, 7, 16, 13, 59, tzinfo=lima), ProductionOrder.Shift.DAY),
            (dt.datetime(2026, 7, 16, 14, 0, tzinfo=lima), ProductionOrder.Shift.AFTERNOON),
            (dt.datetime(2026, 7, 16, 21, 59, tzinfo=lima), ProductionOrder.Shift.AFTERNOON),
            (dt.datetime(2026, 7, 16, 22, 0, tzinfo=lima), ProductionOrder.Shift.NIGHT),
            (dt.datetime(2026, 7, 16, 5, 59, tzinfo=lima), ProductionOrder.Shift.NIGHT),
        )
        for started_at, expected in cases:
            with self.subTest(started_at=started_at):
                self.assertEqual(
                    ProductionOrder.Shift.from_datetime(started_at),
                    expected,
                )

    def test_plate_cards_are_collapsible_and_follow_operational_colors(self):
        url = reverse("productions:plate_create", args=[self.production.pk])

        idle = self.client.get(url)
        self.assertContains(idle, 'data-plate-fold')
        self.assertContains(idle, 'plate-card-state-idle')
        self.assertContains(idle, "Cuadrillas: reparto completo 12/12")
        self.assertContains(idle, "Empaque: espera descarga")
        self.assertContains(idle, "Empaque: en proceso")
        self.assertContains(idle, "8/12 bandejas")

        timing = PlatePositionTiming.objects.create(
            production=self.production,
            position=self.position,
            load_started_at=dt.datetime(2026, 7, 16, 19, 0, tzinfo=dt.timezone.utc),
            load_started_by=self.user,
        )
        filling = self.client.get(url)
        self.assertContains(filling, 'plate-card-state-filling')
        self.assertContains(filling, 'Turno Tarde · En llenado')

        self.plate.tray_count = self.position.max_trays
        self.plate.save(update_fields=["tray_count"])
        complete = self.client.get(url)
        self.assertContains(complete, 'plate-card-state-complete')
        self.assertContains(complete, 'Carga completa')

        timing.load_completed_at = dt.datetime(2026, 7, 16, 19, 20, tzinfo=dt.timezone.utc)
        timing.launched_at = dt.datetime(2026, 7, 16, 19, 30, tzinfo=dt.timezone.utc)
        timing.save(update_fields=["load_completed_at", "launched_at"])
        launched = self.client.get(url)
        self.assertContains(launched, 'plate-card-state-launched')

        timing.unloaded_at = dt.datetime(2026, 7, 16, 20, 0, tzinfo=dt.timezone.utc)
        timing.save(update_fields=["unloaded_at"])
        unloaded = self.client.get(url)
        self.assertContains(unloaded, 'plate-card-state-unloaded')
        self.assertContains(unloaded, 'Descargado')

    def test_plate_card_marks_registered_packaging_and_reconciled_balance(self):
        url = reverse("productions:plate_create", args=[self.production.pk])
        PlatePackagingAllocation.objects.create(
            production=self.production,
            responsible=self.user,
            observation="",
            date=dt.date(2026, 7, 14),
            source_entry=self.pack_source,
            pallet_number=3,
            package_count=2,
        )

        packaged = self.client.get(url)
        self.assertContains(packaged, "Empaque registrado")
        self.assertContains(packaged, "12/12 bandejas")

        balance_position = PlatePosition.objects.create(
            template_version=self.template,
            plate_rack=PlatePosition.PlateRack.P3,
            position_key="EM-PLA!BALANCE-TEST",
            display_name="Bachada 5 · Plaquero 3",
        )
        balance_source = PlateEntry.objects.create(
            production=self.production,
            responsible=self.user,
            observation="",
            date=dt.date(2026, 7, 14),
            shift=ProductionOrder.Shift.DAY,
            position=balance_position,
            product=self.product,
            tray_count=1,
        )
        PlatePositionTiming.objects.create(
            production=self.production,
            position=balance_position,
            unloaded_at=dt.datetime(2026, 7, 16, 20, 0, tzinfo=dt.timezone.utc),
            unloaded_by=self.user,
        )

        pending_balance = self.client.get(url)
        self.assertContains(pending_balance, "Sin bultos completos")
        self.assertContains(pending_balance, "saldo por conciliar: 1 bandeja")

        PlateCarryoverBalance.objects.create(
            origin_production=self.production,
            source_entry=balance_source,
            product=self.product,
            initial_trays=1,
            available_trays=1,
            generated_by=self.user,
        )
        reconciled = self.client.get(url)
        self.assertContains(reconciled, "Empaque conciliado")
        self.assertContains(reconciled, "saldo 1 bandeja")

    def test_plate_launch_cannot_be_registered_before_load_completion(self):
        response = self.client.post(
            reverse(
                "productions:plate_launch_register",
                args=[self.production.pk, self.position.pk],
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Primero pulse «Finalizar carga»")
        self.assertFalse(
            PlatePositionTiming.objects.filter(
                production=self.production,
                position=self.position,
            ).exists()
        )

    def test_plate_unload_cannot_be_registered_before_launch(self):
        timing = PlatePositionTiming.objects.create(
            production=self.production,
            position=self.position,
            load_started_at=dt.datetime(2026, 7, 16, 15, 0, tzinfo=dt.timezone.utc),
            load_completed_at=dt.datetime(2026, 7, 16, 15, 30, tzinfo=dt.timezone.utc),
        )

        response = self.client.post(
            reverse(
                "productions:plate_unload_register",
                args=[self.production.pk, self.position.pk],
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Primero registre el lanzamiento")
        timing.refresh_from_db()
        self.assertIsNone(timing.unloaded_at)

    def test_deleting_last_plate_product_clears_its_timing_control(self):
        emptyable_position = PlatePosition.objects.create(
            template_version=self.template,
            plate_rack=PlatePosition.PlateRack.P2,
            position_key="P2!F5",
            display_name="Posición para limpiar",
        )
        plate = PlateEntry.objects.create(
            production=self.production,
            responsible=self.user,
            date=dt.date(2026, 7, 14),
            shift=ProductionOrder.Shift.DAY,
            position=emptyable_position,
            product=self.product,
            tray_count=20,
            observation="",
        )
        PlatePositionTiming.objects.create(
            production=self.production,
            position=emptyable_position,
            load_started_at=dt.datetime(2026, 7, 16, 15, 0, tzinfo=dt.timezone.utc),
            load_completed_at=dt.datetime(2026, 7, 16, 15, 20, tzinfo=dt.timezone.utc),
            launched_at=dt.datetime(2026, 7, 16, 15, 30, tzinfo=dt.timezone.utc),
            unloaded_at=dt.datetime(2026, 7, 16, 16, 0, tzinfo=dt.timezone.utc),
        )

        response = self.client.post(
            reverse(
                "productions:operational_entry_delete",
                args=[self.production.pk, "plates", plate.pk],
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            PlatePositionTiming.objects.filter(
                production=self.production,
                position=emptyable_position,
            ).exists()
        )
        self.assertContains(response, "se limpió el control horario")

    def test_user_can_remove_a_timing_control_that_has_no_plate_products(self):
        empty_position = PlatePosition.objects.create(
            template_version=self.template,
            plate_rack=PlatePosition.PlateRack.P3,
            position_key="P3!G5",
            display_name="Control vacío",
        )
        PlatePositionTiming.objects.create(
            production=self.production,
            position=empty_position,
            load_started_at=dt.datetime(2026, 7, 16, 15, 0, tzinfo=dt.timezone.utc),
        )

        page = self.client.get(
            reverse("productions:plate_create", args=[self.production.pk])
        )
        self.assertContains(
            page,
            reverse(
                "productions:plate_timing_reset",
                args=[self.production.pk, empty_position.pk],
            ),
        )
        self.assertContains(page, "Eliminar control vacío")

        response = self.client.post(
            reverse(
                "productions:plate_timing_reset",
                args=[self.production.pk, empty_position.pk],
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            PlatePositionTiming.objects.filter(
                production=self.production,
                position=empty_position,
            ).exists()
        )
        self.assertContains(response, "Se eliminó el control vacío")

    def test_timing_control_with_physical_products_cannot_be_removed(self):
        PlatePositionTiming.objects.create(
            production=self.production,
            position=self.position,
            load_started_at=dt.datetime(2026, 7, 16, 15, 0, tzinfo=dt.timezone.utc),
        )

        response = self.client.post(
            reverse(
                "productions:plate_timing_reset",
                args=[self.production.pk, self.position.pk],
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            PlatePositionTiming.objects.filter(
                production=self.production,
                position=self.position,
            ).exists()
        )
        self.assertContains(response, "todavía tiene bandejas")

    def test_plate_positions_are_ordered_by_batch_then_plaquero(self):
        for plate_rack, cell, name in (
            (PlatePosition.PlateRack.P2, "F5", "P2 · posición 1"),
            (PlatePosition.PlateRack.P3, "G5", "P3 · posición 1"),
            (PlatePosition.PlateRack.P1, "H5", "P1 · posición 2"),
        ):
            PlatePosition.objects.create(
                template_version=self.template,
                plate_rack=plate_rack,
                position_key=f"ENV. PLACAS!{cell}",
                display_name=name,
            )

        response = self.client.get(
            reverse("productions:plate_create", args=[self.production.pk])
        )
        content = response.content.decode()

        first = content.index("Bachada 1 · Plaquero 1")
        second = content.index("Bachada 1 · Plaquero 2")
        third = content.index("Bachada 1 · Plaquero 3")
        fourth = content.index("Bachada 2 · Plaquero 1")
        self.assertLess(first, second)
        self.assertLess(second, third)
        self.assertLess(third, fourth)
        self.assertContains(response, "🔵 Bachada 1 · Plaquero 1")
        self.assertContains(response, "🟠 Bachada 1 · Plaquero 2")
        self.assertContains(response, "🟣 Bachada 1 · Plaquero 3")

    def test_full_plate_position_is_hidden_from_capture_selector(self):
        complete_position = PlatePosition.objects.create(
            template_version=self.template,
            plate_rack=PlatePosition.PlateRack.P3,
            position_key="EM-PLA!FULL-TEST",
            display_name="Bachada 9 - Plaquero 3",
            max_trays=12,
        )
        PlateEntry.objects.create(
            production=self.production,
            responsible=self.user,
            observation="",
            date=dt.date(2026, 7, 14),
            shift=ProductionOrder.Shift.DAY,
            position=complete_position,
            product=self.product,
            tray_count=12,
        )

        response = self.client.get(
            reverse("productions:plate_create", args=[self.production.pk])
        )

        available_position_ids = set(
            response.context["form"].fields["position"].queryset.values_list(
                "pk", flat=True
            )
        )
        self.assertNotIn(complete_position.pk, available_position_ids)
        self.assertIn(self.position.pk, available_position_ids)

    def test_closed_partial_plate_position_is_hidden_from_capture_selector(self):
        response = self.client.get(
            reverse("productions:plate_create", args=[self.production.pk])
        )

        available_position_ids = set(
            response.context["form"].fields["position"].queryset.values_list(
                "pk", flat=True
            )
        )
        self.assertNotIn(self.pack_position.pk, available_position_ids)
        self.assertIn(self.position.pk, available_position_ids)

    def test_plate_crew_control_shows_payment_and_marks_completed_position(self):
        response = self.client.get(
            reverse("productions:plate_crew_create", args=[self.production.pk]),
            {"position": self.pack_position.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bandejas por cuadrilla en plaqueros")
        self.assertContains(response, "TOTAL PARA PAGO POR CUADRILLA")
        self.assertContains(response, "12 / 12")
        self.assertContains(response, "120.00 kg")
        self.assertContains(response, "tunnel-crew-rack-complete")
        self.assertContains(response, "plate-position-plaquero-1")
        self.assertContains(response, "plate-position-dot")
        self.assertContains(response, "plate-crew-card-collapsible")
        self.assertContains(response, "data-plate-fold open")
        self.assertContains(response, "plate-card-chevron")
        self.assertContains(response, 'name="crew_name"')
        self.assertContains(response, 'list="plate-crew-options"')
        available_position_ids = set(
            response.context["form"].fields["position"].queryset.values_list(
                "pk", flat=True
            )
        )
        self.assertNotIn(self.position.pk, available_position_ids)
        self.assertIn(self.pack_position.pk, available_position_ids)

    def test_completed_plate_position_returns_to_selector_after_a_crew_is_removed(self):
        url = reverse("productions:plate_crew_create", args=[self.production.pk])
        self.plate_crew.is_active = False
        self.plate_crew.save(update_fields=["is_active"])

        response = self.client.get(url)

        available_position_ids = set(
            response.context["form"].fields["position"].queryset.values_list(
                "pk", flat=True
            )
        )
        self.assertIn(self.position.pk, available_position_ids)

    def test_plate_crew_form_accepts_100_50_39_and_rejects_more_than_189(self):
        self.plate_crew.is_active = False
        self.plate_crew.save(update_fields=["is_active"])
        self.plate.tray_count = 189
        self.plate.save(update_fields=["tray_count"])
        crews = [
            Crew.objects.create(code="CUAD-A", name="CUADRILLA A"),
            Crew.objects.create(code="CUAD-B", name="CUADRILLA B"),
            Crew.objects.create(code="CUAD-C", name="CUADRILLA C"),
            Crew.objects.create(code="CUAD-D", name="CUADRILLA D"),
        ]
        url = reverse("productions:plate_crew_create", args=[self.production.pk])
        for crew, trays in zip(crews[:3], (100, 50, 39)):
            response = self.client.post(
                url,
                {
                    "position": str(self.position.pk),
                    "page": "PAGINA 1",
                    "product": str(self.product.pk),
                    "crew_name": crew.name,
                    "tray_count": str(trays),
                    "observation": "",
                },
            )
            self.assertEqual(response.status_code, 302)

        response = self.client.post(
            url,
            {
                "position": str(self.position.pk),
                "page": "PAGINA 1",
                "product": str(self.product.pk),
                "crew_name": crews[3].name,
                "tray_count": "1",
                "observation": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "entre las disponibles")
        self.assertEqual(
            PlateCrewEntry.objects.filter(
                production=self.production,
                position=self.position,
                is_active=True,
            ).count(),
            3,
        )

    def test_plate_crew_records_product_and_controls_its_available_trays(self):
        second_product = Product.objects.create(
            code="PP-CUAD-02",
            description="PRODUCTO TRAZABLE POR CUADRILLA",
        )
        PlateEntry.objects.create(
            production=self.production,
            responsible=self.user,
            observation="",
            date=dt.date(2026, 7, 14),
            shift=ProductionOrder.Shift.DAY,
            position=self.position,
            product=second_product,
            tray_count=30,
            crew=self.crew,
        )
        product_crew = Crew.objects.create(code="CUAD-PROD-01", name="CUADRILLA PRODUCTO 1")
        other_crew = Crew.objects.create(code="CUAD-PROD-02", name="CUADRILLA PRODUCTO 2")
        url = reverse("productions:plate_crew_create", args=[self.production.pk])

        page = self.client.get(url, {"position": self.position.pk})
        self.assertContains(page, 'name="product"')
        self.assertContains(page, "PP-CUAD-02 — PRODUCTO TRAZABLE POR CUADRILLA")

        saved = self.client.post(
            url,
            {
                "position": str(self.position.pk),
                "page": "PAGINA 1",
                "product": str(second_product.pk),
                "crew_name": product_crew.name,
                "tray_count": "20",
                "observation": "",
            },
        )
        self.assertEqual(
            saved.status_code,
            302,
            saved.context["form"].errors.as_text() if saved.status_code == 200 else "",
        )
        self.assertTrue(
            PlateCrewEntry.objects.filter(
                production=self.production,
                position=self.position,
                product=second_product,
                crew=product_crew,
                tray_count=20,
                is_active=True,
            ).exists()
        )

        rejected = self.client.post(
            url,
            {
                "position": str(self.position.pk),
                "page": "PAGINA 1",
                "product": str(second_product.pk),
                "crew_name": other_crew.name,
                "tray_count": "11",
                "observation": "",
            },
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertContains(rejected, "solo quedan 10 para repartir entre cuadrillas")

        control = self.client.get(url, {"position": self.position.pk})
        self.assertContains(control, "PP-CUAD-02 · PRODUCTO TRAZABLE POR CUADRILLA")
        self.assertContains(control, "20 bandejas · 200.00 kg")

    def test_reception_record_can_be_corrected(self):
        url = reverse(
            "productions:operational_entry_update",
            args=[self.production.pk, "reception", self.reception.pk],
        )
        response = self.client.get(url)
        self.assertContains(response, "Modo corrección")

        response = self.client.post(
            url,
            {
                "date": "2026-07-15",
                "vehicle_text": "xyz-987",
                "car_number": "2",
                "product": str(self.raw_product.pk),
                "crew": str(self.reception_crew.pk),
                "container": "5",
                "weight_kg": "125.50",
                "time": "08:30",
                "observation": "",
            },
        )

        self.assertRedirects(
            response,
            reverse("productions:reception_create", args=[self.production.pk]),
        )
        self.reception.refresh_from_db()
        self.assertEqual(self.reception.vehicle.plate, "XYZ-987")
        self.assertEqual(self.reception.container, "5")
        self.assertEqual(self.reception.weight_kg, Decimal("125.50"))
        update_logs = AuditLog.objects.filter(
            record_pk=str(self.reception.pk),
            action=AuditLog.Action.UPDATE,
        )
        self.assertEqual(update_logs.count(), 1)
        self.assertEqual(update_logs.get().module, "reception")

    def test_reception_vehicle_is_a_manual_text_field(self):
        response = self.client.get(
            reverse("productions:reception_create", args=[self.production.pk])
        )

        self.assertContains(response, 'name="vehicle_text"')
        self.assertContains(response, 'placeholder="Escriba la placa o identificación"')
        self.assertNotContains(response, 'name="vehicle"')
        self.assertContains(response, "fila PLACA de la hoja R.M")

    def test_reception_kg_accepts_and_displays_only_two_decimals(self):
        url = reverse("productions:reception_create", args=[self.production.pk])

        response = self.client.get(url)

        self.assertContains(response, 'name="weight_kg"')
        self.assertContains(response, 'step="0.01"')
        self.assertContains(response, "100.00 kg")
        self.assertNotContains(response, "100.000 kg")

        response = self.client.post(
            url,
            {
                "vehicle_text": "XYZ-987",
                "car_number": "2",
                "product": str(self.raw_product.pk),
                "crew": str(self.reception_crew.pk),
                "container": "1",
                "weight_kg": "50.123",
                "time": "08:30",
                "observation": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"]["weight_kg"].errors)
        self.assertFalse(ReceptionEntry.objects.filter(vehicle__plate="XYZ-987").exists())

    def test_reception_can_continue_filling_dinos_in_the_selected_car(self):
        url = (
            reverse("productions:reception_create", args=[self.production.pk])
            + f"?car={self.vehicle.pk}"
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form["vehicle_text"].value(), self.vehicle.plate)
        self.assertEqual(form["car_number"].value(), "1")
        self.assertEqual(form["product"].value(), self.raw_product.pk)
        self.assertTrue(form.fields["vehicle_text"].disabled)
        self.assertTrue(form.fields["car_number"].disabled)
        self.assertTrue(form.fields["product"].disabled)
        self.assertContains(response, "Agregando otro dino al CARRO 1")
        self.assertContains(response, "Registrar otro carro")

        response = self.client.post(
            url,
            {
                "vehicle_text": "VEHICULO-EQUIVOCADO",
                "car_number": "2",
                "product": str(self.product.pk),
                "crew": str(self.reception_crew.pk),
                "container": "5",
                "weight_kg": "50.00",
                "time": "08:30",
                "observation": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"?car={self.vehicle.pk}", response["Location"])
        created = ReceptionEntry.objects.get(production=self.production, container="5")
        self.assertEqual(created.vehicle, self.vehicle)
        self.assertEqual(created.car_number, "1")
        self.assertEqual(created.product, self.raw_product)
        self.assertEqual(created.weight_kg, Decimal("50.00"))

    def test_reception_can_select_existing_crew_and_only_capture_dino_and_weight(self):
        url = (
            reverse("productions:reception_create", args=[self.production.pk])
            + f"?car={self.vehicle.pk}&crew={self.reception_crew.pk}"
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_reception_crew"], self.reception_crew)
        form = response.context["form"]
        self.assertEqual(form["crew"].value(), self.reception_crew.pk)
        self.assertTrue(form.fields["crew"].disabled)
        self.assertContains(response, "Agregando dino para LUIS")
        self.assertContains(response, f"?car={self.vehicle.pk}&crew={self.reception_crew.pk}")

        response = self.client.post(
            url,
            {
                "container": "6",
                "weight_kg": "80.50",
            },
        )

        self.assertEqual(response.status_code, 302)
        created = ReceptionEntry.objects.get(production=self.production, container="6")
        self.assertEqual(created.crew, self.reception_crew)
        self.assertEqual(created.vehicle, self.vehicle)
        self.assertEqual(created.weight_kg, Decimal("80.50"))

    def test_reception_car_has_automatic_start_and_can_be_closed(self):
        from productions.models import ReceptionCarTiming

        create_url = reverse("productions:reception_create", args=[self.production.pk])
        response = self.client.post(
            create_url,
            {
                "vehicle_text": "ABC-123",
                "car_number": "1",
                "product": str(self.raw_product.pk),
                "crew": str(self.reception_crew.pk),
                "container": "6",
                "weight_kg": "80.50",
                "observation": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        timing = ReceptionCarTiming.objects.get(
            production=self.production,
            vehicle=self.vehicle,
        )
        self.assertIsNotNone(timing.started_at)
        created = ReceptionEntry.objects.get(production=self.production, container="6")
        self.assertEqual(created.time, timing.started_at.astimezone().time().replace(microsecond=0))

        response = self.client.post(
            reverse(
                "productions:reception_car_close",
                args=[self.production.pk, self.vehicle.pk],
            )
        )
        self.assertEqual(response.status_code, 302)
        timing.refresh_from_db()
        self.assertIsNotNone(timing.closed_at)

        response = self.client.post(
            create_url,
            {
                "vehicle_text": "ABC-123",
                "car_number": "1",
                "product": str(self.raw_product.pk),
                "crew": str(self.reception_crew.pk),
                "container": "7",
                "weight_kg": "80.50",
                "observation": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Este carro ya fue cerrado")

    def test_reception_renders_each_car_as_a_separate_panel(self):
        second_vehicle = Vehicle.objects.create(plate="XYZ-987")
        ReceptionEntry.objects.create(
            production=self.production,
            responsible=self.user,
            observation="",
            date=dt.date(2026, 7, 14),
            vehicle=second_vehicle,
            car_number="2",
            product=self.raw_product,
            crew=self.reception_crew,
            container="1",
            weight_kg=Decimal("75.00"),
        )

        response = self.client.get(
            reverse("productions:reception_create", args=[self.production.pk])
        )

        self.assertContains(response, "reception-records-shell")
        self.assertContains(response, 'class="reception-car-group', count=2)
        self.assertContains(response, "Agregar dino a este carro", count=2)

    def test_reception_saved_records_are_grouped_by_car(self):
        second_crew = Crew.objects.create(code="RM-CUAD-02", name="FELIX")
        second_dino = ReceptionEntry.objects.create(
            production=self.production,
            responsible=self.user,
            observation="",
            date=dt.date(2026, 7, 14),
            vehicle=self.vehicle,
            car_number="1",
            product=self.raw_product,
            crew=second_crew,
            container="5",
            weight_kg=Decimal("50.00"),
        )

        response = self.client.get(
            reverse("productions:reception_create", args=[self.production.pk])
        )

        self.assertEqual(response.status_code, 200)
        groups = response.context["reception_record_groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["car_number"], "1")
        self.assertEqual(groups[0]["dino_count"], 2)
        self.assertEqual(groups[0]["total_weight"], Decimal("150.00"))
        self.assertEqual(
            [
                (total["name"], total["dino_count"], total["total_weight"])
                for total in groups[0]["crew_totals"]
            ],
            [
                ("FELIX", 1, Decimal("50.00")),
                ("LUIS", 1, Decimal("100.00")),
            ],
        )
        self.assertEqual(
            [entry.pk for entry in groups[0]["entries"]],
            [self.reception.pk, second_dino.pk],
        )
        self.assertContains(response, "Registros guardados por carro")
        self.assertContains(response, "CARRO 1")
        self.assertContains(response, "2 dinos")
        self.assertContains(response, "Mapa de dinos y kilos")
        self.assertContains(response, "Total del carro")
        self.assertContains(response, "TOTAL ACUMULADO POR CUADRILLA")
        self.assertContains(response, "Ver carro:")
        self.assertContains(response, 'href="#carro-1"')
        self.assertContains(response, "Dino 4")
        self.assertContains(response, "Dino 5")
        self.assertContains(
            response,
            '<small class="reception-entry-user">Registrado por: <strong>manager-operations</strong></small>',
            count=2,
            html=True,
        )

    def test_reception_rejects_car_number_used_by_another_vehicle(self):
        response = self.client.post(
            reverse("productions:reception_create", args=[self.production.pk]),
            {
                "vehicle_text": "XYZ-987",
                "car_number": "1",
                "product": str(self.raw_product.pk),
                "crew": str(self.reception_crew.pk),
                "container": "1",
                "weight_kg": "50.00",
                "time": "08:30",
                "observation": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El carro 1 ya pertenece al vehículo ABC-123")
        self.assertEqual(
            ReceptionEntry.objects.filter(production=self.production, is_active=True).count(),
            1,
        )

    def test_reception_rejects_same_dino_for_any_crew_in_the_same_car(self):
        response = self.client.post(
            reverse("productions:reception_create", args=[self.production.pk]),
            {
                "vehicle_text": "ABC-123",
                "car_number": "1",
                "product": str(self.raw_product.pk),
                "crew": str(self.reception_crew.pk),
                "container": "4",
                "weight_kg": "75.00",
                "time": "08:45",
                "observation": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "El Dino 4 ya fue registrado en este carro por la cuadrilla LUIS",
        )
        self.assertEqual(
            ReceptionEntry.objects.filter(production=self.production, is_active=True).count(),
            1,
        )

        second_crew = Crew.objects.create(code="RM-CUAD-02", name="CHARLES")
        response = self.client.post(
            reverse("productions:reception_create", args=[self.production.pk]),
            {
                "vehicle_text": "ABC-123",
                "car_number": "1",
                "product": str(self.raw_product.pk),
                "crew": str(second_crew.pk),
                "container": "4",
                "weight_kg": "75.00",
                "time": "08:45",
                "observation": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "El Dino 4 ya fue registrado en este carro por la cuadrilla LUIS",
        )
        self.assertEqual(
            ReceptionEntry.objects.filter(production=self.production, is_active=True).count(),
            1,
        )

    def test_reception_only_accepts_car_numbers_from_one_to_nine(self):
        response = self.client.post(
            reverse("productions:reception_create", args=[self.production.pk]),
            {
                "vehicle_text": "XYZ-987",
                "car_number": "10",
                "product": str(self.raw_product.pk),
                "crew": str(self.reception_crew.pk),
                "container": "1",
                "weight_kg": "50.00",
                "time": "09:00",
                "observation": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ingrese un número de carro del 1 al 9")
        self.assertEqual(
            ReceptionEntry.objects.filter(production=self.production, is_active=True).count(),
            1,
        )

    def test_reception_marks_existing_duplicate_car_numbers_as_conflicts(self):
        other_vehicle = Vehicle.objects.create(plate="XYZ-987")
        ReceptionEntry.objects.create(
            production=self.production,
            responsible=self.user,
            observation="",
            date=dt.date(2026, 7, 14),
            vehicle=other_vehicle,
            car_number="1",
            product=self.raw_product,
            crew=self.reception_crew,
            container="1",
            weight_kg=Decimal("50.00"),
        )

        response = self.client.get(
            reverse("productions:reception_create", args=[self.production.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["reception_record_groups"]), 2)
        self.assertTrue(
            all(
                group["has_car_conflict"]
                for group in response.context["reception_record_groups"]
            )
        )
        self.assertContains(response, "el número de carro está repetido en otro vehículo", count=2)

    def test_operational_pages_do_not_request_a_manual_date(self):
        for module, (_, create_url_name) in self.modules.items():
            with self.subTest(module=module):
                response = self.client.get(
                    reverse(create_url_name, args=[self.production.pk])
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'name="date"')

    def test_approved_production_operational_module_opens_in_read_only_mode(self):
        self.production.status = ProductionOrder.Status.APPROVED
        self.production.save(update_fields=["status"])

        response = self.client.get(
            reverse("productions:reception_create", args=[self.production.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Modo consulta")
        self.assertContains(response, "Registros guardados por carro")
        self.assertContains(response, "Dino 4")
        self.assertNotContains(response, "Guardar registro")
        self.assertNotContains(response, ">Corregir<")
        self.assertNotContains(response, ">Eliminar<")

    def test_approved_production_still_rejects_new_operational_records(self):
        self.production.status = ProductionOrder.Status.APPROVED
        self.production.save(update_fields=["status"])

        response = self.client.post(
            reverse("productions:reception_create", args=[self.production.pk]),
            {
                "vehicle_text": "NEW-001",
                "car_number": "2",
                "product": str(self.raw_product.pk),
                "crew": str(self.reception_crew.pk),
                "container": "1",
                "weight_kg": "50.00",
                "time": "09:00",
                "observation": "",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ReceptionEntry.objects.filter(vehicle__plate="NEW-001").exists())

    def test_reception_and_nuqueras_keep_separate_crew_catalogs(self):
        reception_response = self.client.get(
            reverse("productions:reception_create", args=[self.production.pk])
        )
        nuquera_response = self.client.get(
            reverse("productions:nuquera_create", args=[self.production.pk])
        )

        self.assertContains(reception_response, "LUIS")
        self.assertNotContains(reception_response, "Cuadrilla Uno")
        self.assertContains(nuquera_response, "Cuadrilla Uno")

    def test_crew_tareo_page_shows_the_crew_work_and_its_workers(self):
        # La pagina del tareo por cuadrilla dependia de crew_tareo_summary,
        # que no existia y tumbaba tanto esta vista como el reporte
        # consolidado (el import agrupado fallaba en silencio).
        response = self.client.get(
            reverse("productions:crew_tareo", args=[self.production.pk, self.crew.pk])
        )

        self.assertEqual(response.status_code, 200)
        # El servicio normaliza el nombre de la cuadrilla a mayusculas.
        self.assertContains(response, "CUADRILLA UNO")
        self.assertContains(response, "Trabajador Uno")
        # Debe sumar su trabajo de tunel (10 bdj) y de plaqueros (12 bdj).
        self.assertContains(response, "22 bandejas")

    def test_consolidated_report_page_opens(self):
        response = self.client.get(
            reverse("productions:report", args=[self.production.pk])
        )

        self.assertEqual(response.status_code, 200)

    def test_selecting_a_crew_goes_directly_to_the_capture_panel(self):
        # "Elegir otra cuadrilla" debe seguir funcionando de forma directa,
        # sin forzar el paso de asistencia (que es opcional y vive aparte).
        second_worker = Worker.objects.create(
            internal_code="NUQ-W02",
            full_name="Trabajador Dos",
            crew=self.crew,
        )

        response = self.client.get(
            reverse("productions:nuquera_create", args=[self.production.pk]),
            {"crew": self.crew.pk},
        )

        self.assertContains(response, "Toque una persona para registrar sus pesos")
        self.assertContains(response, "Trabajador Uno")
        self.assertContains(response, "Trabajador Dos")
        second_worker.delete()

    def test_marking_attendance_is_a_separate_optional_step(self):
        second_worker = Worker.objects.create(
            internal_code="NUQ-W02",
            full_name="Trabajador Dos",
            crew=self.crew,
        )

        response = self.client.get(
            reverse("productions:nuquera_create", args=[self.production.pk]),
            {"attendance": self.crew.pk},
        )

        self.assertContains(response, "Desmarque a quien no asistió")
        self.assertContains(response, "Trabajador Uno")
        self.assertContains(response, "Trabajador Dos")
        # El paso de asistencia no debe activar por si solo el panel de
        # captura de pesos (esa cuadrilla es independiente de 'crew').
        self.assertNotContains(response, "Toque una persona para registrar sus pesos")
        second_worker.delete()

    def test_deactivating_a_worker_removes_it_from_future_suggestions(self):
        second_worker = Worker.objects.create(
            internal_code="NUQ-W02",
            full_name="Trabajador Dos",
            crew=self.crew,
        )

        response = self.client.post(
            reverse(
                "productions:nuquera_worker_deactivate",
                args=[self.production.pk, second_worker.pk],
            ),
            {"next": reverse("productions:nuquera_create", args=[self.production.pk])},
            follow=True,
        )

        second_worker.refresh_from_db()
        self.assertFalse(second_worker.active)
        self.assertContains(response, "Trabajador Dos fue dado de baja")
        # Ya no debe aparecer en el datalist de sugerencias del catalogo.
        self.assertNotContains(response, '<option value="Trabajador Dos">')
        # El resto del catalogo (Trabajador Uno) sigue disponible.
        self.assertContains(response, "Trabajador Uno")

    def test_cannot_deactivate_a_worker_from_another_area(self):
        other_area_worker = Worker.objects.create(
            internal_code="TROQ-W01",
            full_name="Trabajador de Troquelado",
        )

        response = self.client.post(
            reverse(
                "productions:nuquera_worker_deactivate",
                args=[self.production.pk, other_area_worker.pk],
            )
        )

        self.assertEqual(response.status_code, 404)
        other_area_worker.refresh_from_db()
        self.assertTrue(other_area_worker.active)

    def test_marking_attendance_filters_the_capture_panel_to_present_workers(self):
        second_worker = Worker.objects.create(
            internal_code="NUQ-W02",
            full_name="Trabajador Dos",
            crew=self.crew,
        )

        response = self.client.get(
            reverse("productions:nuquera_create", args=[self.production.pk]),
            {"crew": self.crew.pk, "present": [self.worker.pk]},
        )

        self.assertContains(response, "Toque una persona para registrar sus pesos")
        self.assertContains(
            response, f'data-quick-worker-id="{self.worker.pk}"'
        )
        # "Trabajador Dos" no marco asistencia: no debe tener boton de
        # seleccion rapida ni aparecer en el <select> de respaldo del
        # formulario (aunque si puede seguir en el datalist global para
        # crear/reutilizar trabajadores, que no depende de la cuadrilla).
        self.assertNotContains(response, f'data-quick-worker-id="{second_worker.pk}"')
        self.assertNotContains(
            response, f'<option value="{second_worker.pk}">Trabajador Dos</option>'
        )
        second_worker.delete()

    def test_reception_uses_the_pp_date_automatically(self):
        response = self.client.post(
            reverse("productions:reception_create", args=[self.production.pk]),
            {
                "vehicle_text": "auto-456",
                "car_number": "2",
                "product": str(self.raw_product.pk),
                "crew": str(self.reception_crew.pk),
                "container": "6",
                "weight_kg": "90.50",
                "time": "08:45",
                "observation": "",
            },
        )

        created = ReceptionEntry.objects.get(vehicle__plate="AUTO-456")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("productions:reception_create", args=[self.production.pk])
            + f"?car={created.vehicle_id}#reception-entry-form",
        )
        self.assertEqual(created.date, self.production.reception_date)

    def test_manual_vehicle_is_mapped_only_to_the_rm_reception_sheet(self):
        values = production_values(self.production)
        self.assertEqual(values["reception.vehicle1.plate"], "ABC-123")
        self.assertEqual(values["reception.row1.weight_kg"], Decimal("100.00"))
        self.assertEqual(values["reception.vehicle1.weight.row4.crew1"], Decimal("100.00"))
        self.assertEqual(values["nuqueras.row1.weight_kg"], Decimal("25.00"))
        self.assertEqual(values["tunnel_packaging.P1.PP-001.kg"], Decimal("100.00"))

        mapping = load_mapping(Path(settings.BASE_DIR) / "config" / "excel_mapping_v2.yaml")
        plate_targets = [
            item
            for item in mapping["mappings"]
            if item["field"].startswith("reception.vehicle")
            and item["field"].endswith(".plate")
        ]

        self.assertEqual(len(plate_targets), 10)
        self.assertTrue(
            all(
                item["sheet"] == "R.M" and item["module"] == "reception"
                for item in plate_targets
            )
        )
        self.assertEqual(
            (plate_targets[0]["sheet"], plate_targets[0]["cell"]),
            ("R.M", "C14"),
        )

    def test_tunnel_crews_use_dynamic_excel_slots(self):
        values = production_values(self.production)
        self.assertEqual(values["crew_roster.CUAD-01.name"], self.crew.name)
        self.assertEqual(
            values["tunnel_crews.T1.fill1.PAGINA 1.CUAD-01.trays"],
            self.tunnel_crew.tray_count,
        )

    def test_tunnel_crew_excel_uses_fill_as_authoritative_production(self):
        other_production = ProductionOrder.objects.create(
            number=801,
            plant_lot="LOTE-801",
            customer=self.production.customer,
            process="Pota",
            main_product=self.production.main_product,
            reception_date=dt.date(2026, 7, 14),
            production_date=dt.date(2026, 7, 14),
            shift=ProductionOrder.Shift.DAY,
            template_version=self.template,
            created_by=self.user,
        )
        flor = Crew.objects.create(code="CUAD-FLOR", name="FLOR")
        mismatched = TunnelCrewEntry.objects.create(
            production=other_production,
            responsible=self.user,
            fill=self.fill,
            crew=flor,
            page_or_block="PAGINA 1",
            tray_count=20,
            date=dt.date(2026, 7, 14),
        )

        mismatched.refresh_from_db()
        self.assertEqual(mismatched.production_id, self.production.pk)
        TunnelCrewEntry.objects.filter(pk=mismatched.pk).update(
            production=other_production
        )
        mismatched.refresh_from_db()
        self.assertEqual(mismatched.production_id, other_production.pk)
        values = production_values(self.production)
        flor_slot = next(
            key.split(".")[1]
            for key, value in values.items()
            if key.startswith("crew_roster.") and value == "FLOR"
        )
        self.assertEqual(
            values[f"tunnel_crews.T1.fill1.PAGINA 1.{flor_slot}.trays"],
            20,
        )

    def test_tunnel_crew_update_accepts_fill_owned_historical_record(self):
        other_production = ProductionOrder.objects.create(
            number=802,
            plant_lot="LOTE-802",
            customer=self.production.customer,
            process="Pota",
            main_product=self.production.main_product,
            reception_date=dt.date(2026, 7, 14),
            production_date=dt.date(2026, 7, 14),
            shift=ProductionOrder.Shift.DAY,
            template_version=self.template,
            created_by=self.user,
        )
        TunnelCrewEntry.objects.filter(pk=self.tunnel_crew.pk).update(
            production=other_production
        )

        response = self.client.get(
            reverse(
                "productions:operational_entry_update",
                args=[self.production.pk, "tunnel-crews", self.tunnel_crew.pk],
            )
        )

        self.assertEqual(response.status_code, 200)

    def test_tunnel_crew_payment_summary_converts_trays_to_ten_kg_each(self):
        response = self.client.get(
            reverse("productions:tunnel_crew_create", args=[self.production.pk])
            + f"?fill={self.fill.pk}"
        )

        self.assertEqual(response.status_code, 200)
        total = response.context["tunnel_crew_data"]["crew_totals"][0]
        self.assertEqual(total["tray_count"], 10)
        self.assertEqual(total["weight_kg"], Decimal("100.00"))
        self.assertContains(response, "100.00 kg")
        self.assertContains(response, "10 bandejas × 10 kg")

    def test_fully_assigned_rack_is_marked_as_complete(self):
        rack = TunnelRack.objects.create(fill=self.fill, code="R02", position_key="T1!F5")
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=rack,
            product=self.product,
            tray_count=10,
            date=dt.date(2026, 7, 14),
        )
        TunnelCrewEntry.objects.create(
            production=self.production,
            responsible=self.user,
            fill=self.fill,
            rack=rack,
            crew=self.crew,
            page_or_block="PAGINA 1",
            tray_count=10,
            date=dt.date(2026, 7, 14),
        )

        response = self.client.get(
            reverse("productions:tunnel_crew_create", args=[self.production.pk])
            + f"?fill={self.fill.pk}"
        )

        self.assertContains(
            response,
            'class="tunnel-crew-rack tunnel-crew-rack-complete"',
            html=False,
        )
        self.assertContains(response, "10 / 10")

    def test_empty_tunnel_crew_tray_count_shows_form_error_instead_of_server_error(self):
        rack = TunnelRack.objects.create(fill=self.fill, code="R02", position_key="T1!F5")
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=rack,
            product=self.product,
            tray_count=10,
            date=dt.date(2026, 7, 14),
        )
        url = (
            reverse("productions:tunnel_crew_create", args=[self.production.pk])
            + f"?fill={self.fill.pk}"
        )

        response = self.client.post(
            url,
            {
                "fill": self.fill.pk,
                "rack": rack.pk,
                "crew_name": self.crew.name,
                "crew": self.crew.pk,
                "tray_count": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Este campo es obligatorio")
        self.assertFalse(
            TunnelCrewEntry.objects.filter(rack=rack, crew=self.crew, is_active=True).exists()
        )

    def test_new_tunnel_crew_can_be_created_and_assigned_to_a_rack(self):
        rack = TunnelRack.objects.create(fill=self.fill, code="R01", position_key="T1!E5")
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=rack,
            product=self.product,
            tray_count=40,
            date=dt.date(2026, 7, 14),
        )
        quick_url = reverse("productions:tunnel_crew_quick_create", args=[self.production.pk])
        response = self.client.post(quick_url, {"fill": self.fill.pk, "name": "Cuadrilla Ramírez"})
        self.assertEqual(response.status_code, 302)
        new_crew = Crew.objects.get(name="CUADRILLA RAMÍREZ")
        self.assertTrue(new_crew.code.startswith("CUAD-"))

        create_url = reverse("productions:tunnel_crew_create", args=[self.production.pk])
        response = self.client.post(
            f"{create_url}?fill={self.fill.pk}",
            {
                "fill": self.fill.pk,
                "rack": rack.pk,
                "crew_name": new_crew.name,
                "crew": new_crew.pk,
                "tray_count": 25,
            },
        )
        self.assertEqual(response.status_code, 302)
        assignment = TunnelCrewEntry.objects.get(rack=rack, crew=new_crew, is_active=True)
        self.assertEqual(assignment.tray_count, 25)
        create_logs = AuditLog.objects.filter(
            record_pk=str(assignment.pk),
            model_name=assignment._meta.label,
            action=AuditLog.Action.CREATE,
        )
        self.assertEqual(create_logs.count(), 1)
        self.assertEqual(create_logs.get().module, "tunnel-crews")

    def test_tunnel_crew_assignment_cannot_exceed_the_filled_rack_balance(self):
        rack = TunnelRack.objects.create(fill=self.fill, code="R01", position_key="T1!E5")
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=rack,
            product=self.product,
            tray_count=40,
            date=dt.date(2026, 7, 14),
        )
        andres = Crew.objects.create(code="CUAD-ANDRES", name="ANDRES")
        flor = Crew.objects.create(code="CUAD-FLOR", name="FLOR")
        create_url = reverse("productions:tunnel_crew_create", args=[self.production.pk])
        first_response = self.client.post(
            f"{create_url}?fill={self.fill.pk}",
            {
                "fill": self.fill.pk,
                "rack": rack.pk,
                "crew_name": andres.name,
                "crew": andres.pk,
                "tray_count": 30,
            },
        )
        self.assertEqual(first_response.status_code, 302)

        second_response = self.client.post(
            f"{create_url}?fill={self.fill.pk}",
            {
                "fill": self.fill.pk,
                "rack": rack.pk,
                "crew_name": flor.name,
                "crew": flor.pk,
                "tray_count": 15,
            },
        )

        self.assertEqual(second_response.status_code, 200)
        self.assertContains(second_response, "Solo quedan 10 por asignar")
        self.assertFalse(
            TunnelCrewEntry.objects.filter(rack=rack, crew=flor, is_active=True).exists()
        )

    def test_twelfth_participating_crew_is_rejected_before_excel_generation(self):
        rack = TunnelRack.objects.create(
            fill=self.fill,
            code="R01",
            position_key="T1!E5",
            max_trays=70,
        )
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=rack,
            product=self.product,
            tray_count=20,
            date=dt.date(2026, 7, 14),
        )
        for number in range(1, 12):
            crew = Crew.objects.create(
                code=f"CUAD-{number:02d}",
                name=f"CUADRILLA {number:02d}",
            )
            TunnelCrewEntry.objects.create(
                production=self.production,
                responsible=self.user,
                fill=self.fill,
                rack=rack,
                crew=crew,
                page_or_block="PAGINA 1",
                tray_count=1,
                date=dt.date(2026, 7, 14),
            )
        extra = Crew.objects.create(code="CUAD-12", name="CUADRILLA 12")
        create_url = reverse("productions:tunnel_crew_create", args=[self.production.pk])

        response = self.client.post(
            f"{create_url}?fill={self.fill.pk}",
            {
                "fill": self.fill.pk,
                "rack": rack.pk,
                "crew_name": extra.name,
                "crew": extra.pk,
                "tray_count": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "admite como máximo 11 cuadrillas")
        self.assertFalse(
            TunnelCrewEntry.objects.filter(rack=rack, crew=extra, is_active=True).exists()
        )

    def test_operational_record_can_be_removed(self):
        url = reverse(
            "productions:operational_entry_delete",
            args=[self.production.pk, "materials", self.material_usage.pk],
        )
        response = self.client.post(url)

        self.assertRedirects(
            response,
            reverse("productions:material_create", args=[self.production.pk]),
        )
        self.material_usage.refresh_from_db()
        self.assertFalse(self.material_usage.is_active)
        self.assertTrue(
            AuditLog.objects.filter(
                record_pk=str(self.material_usage.pk),
                action=AuditLog.Action.VOID,
                module="materials",
            ).exists()
        )

    def test_material_form_only_allows_the_four_manual_excel_inputs(self):
        allowed_materials = [
            Material.objects.create(name="Strech film", unit="rollo"),
            Material.objects.create(name="Rafia", unit="kg"),
            Material.objects.create(name="Plumones", unit="unidad"),
            Material.objects.create(name="Hielo", unit="kg"),
        ]
        blocked_material = Material.objects.create(name="Cinta de prueba", unit="rollo")
        url = reverse("productions:material_create", args=[self.production.pk])

        response = self.client.get(url)
        field = response.context["form"].fields["material"]

        self.assertEqual(list(field.queryset), allowed_materials)
        self.assertEqual(
            [field.label_from_instance(material) for material in allowed_materials],
            ["Strech film", "Rafia", "Plumón", "Hielo"],
        )
        self.assertContains(response, "los demás insumos los calcula la plantilla de Excel")

        response = self.client.post(
            url,
            {"material": blocked_material.pk, "quantity": "2", "observation": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"]["material"].errors)
        self.assertFalse(
            MaterialUsage.objects.filter(
                production=self.production,
                material=blocked_material,
            ).exists()
        )

    def test_excel_values_ignore_materials_calculated_by_the_template(self):
        rafia = Material.objects.create(name="Rafia", unit="kg")
        MaterialUsage.objects.create(
            production=self.production,
            responsible=self.user,
            observation="",
            material=rafia,
            quantity=Decimal("6.25"),
        )

        values = production_values(self.production)

        self.assertEqual(values["materials.rafia.quantity"], Decimal("6.25"))
        self.assertNotIn("materials.bolsa.quantity", values)
