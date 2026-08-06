import datetime as dt
import hashlib

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase

from productions.models import (
    Crew,
    Customer,
    PlateCrewEntry,
    PlateEntry,
    PlatePackagingEntry,
    PlatePosition,
    Product,
    ProductionOrder,
    TemplateVersion,
    Tunnel,
    TunnelCrewEntry,
    TunnelEntry,
    TunnelFill,
    TunnelRack,
    User,
)
from productions.services.reconciliation import tunnel_reconciliation


class BaseDataTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tester", password="Secure-test-123")
        self.customer = Customer.objects.create(name="Cliente Demo")
        self.product = Product.objects.create(code="P001", description="Manto")
        self.template = TemplateVersion.objects.create(code="PP-V1", file=SimpleUploadedFile("template.xlsm", b"fixture"), original_filename="template.xlsm", sha256="a" * 64, uploaded_by=self.user)
        self.production = ProductionOrder.objects.create(number=105, plant_lot="LOTE-01", customer=self.customer, process="Congelado", main_product=self.product, reception_date=dt.date(2026, 7, 13), production_date=dt.date(2026, 7, 13), shift=ProductionOrder.Shift.DAY, template_version=self.template, created_by=self.user, status=ProductionOrder.Status.IN_PROGRESS)
        self.tunnel = Tunnel.objects.create(code="T1", name="Túnel 1")
        self.fill = TunnelFill.objects.create(production=self.production, tunnel=self.tunnel, fill_number=1, date=dt.date(2026, 7, 13), supervisor=self.user)
        self.rack = TunnelRack.objects.create(fill=self.fill, code="R01", position_key="rack_01")


class CapacityAndReconciliationTests(BaseDataTestCase):
    def test_template_hash_is_computed_from_uploaded_bytes(self):
        self.assertEqual(self.template.sha256, hashlib.sha256(b"fixture").hexdigest())

    def test_fill_one_and_two_are_separate_and_third_is_invalid(self):
        TunnelFill.objects.create(production=self.production, tunnel=self.tunnel, fill_number=2, date=dt.date(2026, 7, 13), supervisor=self.user)
        invalid = TunnelFill(production=self.production, tunnel=self.tunnel, fill_number=3, date=dt.date(2026, 7, 13), supervisor=self.user)
        with self.assertRaises(ValidationError):
            invalid.full_clean()

    def test_rack_total_cannot_exceed_fifty(self):
        TunnelEntry.objects.create(production=self.production, responsible=self.user, rack=self.rack, product=self.product, tray_count=40, date=dt.date(2026, 7, 13))
        other_product = Product.objects.create(code="P002", description="Nuca")
        extra = TunnelEntry(production=self.production, responsible=self.user, rack=self.rack, product=other_product, tray_count=11, date=dt.date(2026, 7, 13))
        with self.assertRaises(ValidationError):
            extra.full_clean()

    def test_exact_active_duplicate_is_prevented(self):
        TunnelEntry.objects.create(production=self.production, responsible=self.user, rack=self.rack, product=self.product, tray_count=10, date=dt.date(2026, 7, 13))
        with self.assertRaises(IntegrityError), transaction.atomic():
            TunnelEntry.objects.create(production=self.production, responsible=self.user, rack=self.rack, product=self.product, tray_count=10, date=dt.date(2026, 7, 13))

    def test_operational_delete_is_logical(self):
        entry = TunnelEntry.objects.create(production=self.production, responsible=self.user, rack=self.rack, product=self.product, tray_count=10, date=dt.date(2026, 7, 13))
        entry.delete(user=self.user, reason="Corrección autorizada")
        entry.refresh_from_db()
        self.assertFalse(entry.is_active)
        self.assertEqual(entry.voided_by, self.user)

    def test_tunnel_reconciliation_reports_difference(self):
        TunnelEntry.objects.create(production=self.production, responsible=self.user, rack=self.rack, product=self.product, tray_count=40, date=dt.date(2026, 7, 13))
        crew = Crew.objects.create(code="C01", name="Cuadrilla 1")
        TunnelCrewEntry.objects.create(production=self.production, responsible=self.user, fill=self.fill, crew=crew, page_or_block="P1", tray_count=38, date=dt.date(2026, 7, 13))
        result = tunnel_reconciliation(self.production)
        self.assertEqual(result.physical_total, 40)
        self.assertEqual(result.declared_total, 38)
        self.assertEqual(result.difference, 2)

    def test_multiple_crews_can_share_a_rack_without_exceeding_its_physical_total(self):
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=self.rack,
            product=self.product,
            tray_count=50,
            date=dt.date(2026, 7, 13),
        )
        andres = Crew.objects.create(code="CUAD-01", name="ANDRES")
        fermin = Crew.objects.create(code="CUAD-02", name="FERMIN")
        first = TunnelCrewEntry(
            production=self.production,
            responsible=self.user,
            fill=self.fill,
            rack=self.rack,
            crew=andres,
            page_or_block="PAGINA 1",
            tray_count=30,
            date=dt.date(2026, 7, 13),
        )
        first.full_clean()
        first.save()
        second = TunnelCrewEntry(
            production=self.production,
            responsible=self.user,
            fill=self.fill,
            rack=self.rack,
            crew=fermin,
            page_or_block="PAGINA 1",
            tray_count=20,
            date=dt.date(2026, 7, 13),
        )
        second.full_clean()
        second.save()
        second.tray_count = 21
        with self.assertRaises(ValidationError):
            second.full_clean()

    def test_plate_position_keeps_p1_p2_p3_separate_and_enforces_capacity(self):
        p1 = PlatePosition.objects.create(template_version=self.template, plate_rack=PlatePosition.PlateRack.P1, position_key="grupo_01", display_name="P1 grupo 1", max_trays=189)
        p2 = PlatePosition.objects.create(template_version=self.template, plate_rack=PlatePosition.PlateRack.P2, position_key="grupo_01", display_name="P2 grupo 1", max_trays=189)
        self.assertNotEqual(p1.plate_rack, p2.plate_rack)
        entry = PlateEntry(production=self.production, responsible=self.user, date=dt.date(2026, 7, 13), shift=ProductionOrder.Shift.DAY, position=p1, product=self.product, tray_count=190)
        with self.assertRaises(ValidationError):
            entry.full_clean()

    def test_plate_positions_are_identified_by_batch_and_plaquero(self):
        first = PlatePosition.objects.create(
            template_version=self.template,
            plate_rack=PlatePosition.PlateRack.P1,
            position_key="ENV. PLACAS!E5",
            display_name="P1 · posición 1",
        )
        second = PlatePosition.objects.create(
            template_version=self.template,
            plate_rack=PlatePosition.PlateRack.P2,
            position_key="ENV. PLACAS!I5",
            display_name="P2 · posición 2",
        )

        self.assertEqual(first.batch_number, 1)
        self.assertEqual(first.plaquero_number, 1)
        self.assertEqual(first.color_marker, "🔵")
        self.assertEqual(first.operational_label, "Bachada 1 · Plaquero 1")
        self.assertEqual(second.batch_number, 2)
        self.assertEqual(second.plaquero_number, 2)
        self.assertEqual(second.color_marker, "🟠")
        self.assertEqual(str(second), "Bachada 2 · Plaquero 2")

    def test_plate_position_capacity_is_accumulated_across_products(self):
        position = PlatePosition.objects.create(
            template_version=self.template,
            plate_rack=PlatePosition.PlateRack.P1,
            position_key="grupo_01",
            display_name="P1 posición 1",
            max_trays=189,
        )
        PlateEntry.objects.create(
            production=self.production,
            responsible=self.user,
            date=dt.date(2026, 7, 13),
            shift=ProductionOrder.Shift.DAY,
            position=position,
            product=self.product,
            tray_count=100,
        )
        other_product = Product.objects.create(code="P002", description="Nuca")
        extra = PlateEntry(
            production=self.production,
            responsible=self.user,
            date=dt.date(2026, 7, 13),
            shift=ProductionOrder.Shift.DAY,
            position=position,
            product=other_product,
            tray_count=90,
        )
        with self.assertRaises(ValidationError):
            extra.full_clean()

    def test_multiple_crews_can_split_the_189_trays_of_one_plate_position(self):
        position = PlatePosition.objects.create(
            template_version=self.template,
            plate_rack=PlatePosition.PlateRack.P1,
            position_key="grupo_01",
            display_name="P1 posición 1",
            max_trays=189,
        )
        PlateEntry.objects.create(
            production=self.production,
            responsible=self.user,
            date=dt.date(2026, 7, 13),
            shift=ProductionOrder.Shift.DAY,
            position=position,
            product=self.product,
            tray_count=189,
        )
        for index, (name, trays) in enumerate(
            (("CUADRILLA A", 100), ("CUADRILLA B", 50), ("CUADRILLA C", 39)),
            start=1,
        ):
            crew = Crew.objects.create(code=f"CUAD-{index}", name=name)
            entry = PlateCrewEntry(
                production=self.production,
                responsible=self.user,
                position=position,
                page="PAGINA 1",
                crew=crew,
                tray_count=trays,
                date=dt.date(2026, 7, 13),
            )
            entry.full_clean()
            entry.save()
        extra_crew = Crew.objects.create(code="CUAD-4", name="CUADRILLA D")
        extra = PlateCrewEntry(
            production=self.production,
            responsible=self.user,
            position=position,
            page="PAGINA 1",
            crew=extra_crew,
            tray_count=1,
            date=dt.date(2026, 7, 13),
        )
        with self.assertRaises(ValidationError):
            extra.full_clean()

    def test_packaging_limits_and_conversions_come_from_template_version(self):
        self.template.rules = {**self.template.rules, "plate_pallet_max": 50, "package_trays": 3, "package_kg": 18}
        self.template.save()
        entry = PlatePackagingEntry(production=self.production, responsible=self.user, date=dt.date(2026, 7, 13), pallet_number=51, product=self.product, package_count=4)
        with self.assertRaises(ValidationError):
            entry.full_clean()
        entry.pallet_number = 50
        self.assertEqual(entry.tray_count, 12)
        self.assertEqual(entry.kilos, 72)
