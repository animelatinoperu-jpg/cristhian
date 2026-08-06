import datetime as dt
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from productions.models import (
    Crew,
    Customer,
    GeneratedFile,
    PlateEntry,
    PlatePosition,
    PlatePositionTiming,
    Product,
    ProductionOrder,
    Role,
    TemplateVersion,
    Tunnel,
    TunnelCrewEntry,
    TunnelEntry,
    TunnelFill,
    TunnelRack,
    User,
)
from productions.services.excel.analyzer import XlsmAnalyzer
from productions.services.excel.generator import generate_production_workbook
from productions.services.plate_balances import auto_pack_product
from .factories import build_minimal_xlsm, write_mapping


class DatabaseRegenerationTests(TestCase):
    def test_final_workbook_can_be_regenerated_entirely_from_database(self):
        with tempfile.TemporaryDirectory() as temp, self.settings(MEDIA_ROOT=temp):
            temp_path = Path(temp)
            source = temp_path / "source.xlsm"
            template_hash = build_minimal_xlsm(source)
            mapping = temp_path / "mapping.yaml"
            write_mapping(mapping, template_hash)
            manager_role = Role.objects.create(code=Role.Codes.PRODUCTION_MANAGER, name="Jefe")
            manager = User.objects.create_user("manager", password="Secure-test-123")
            manager.roles.add(manager_role)
            customer = Customer.objects.create(name="Cliente")
            product = Product.objects.create(code="P001", description="Manto")
            template = TemplateVersion.objects.create(
                code="PP-V1",
                file=SimpleUploadedFile("template.xlsm", source.read_bytes()),
                original_filename="template.xlsm",
                sha256=template_hash,
                uploaded_by=manager,
            )
            production = ProductionOrder.objects.create(
                number=105,
                plant_lot="1080PPF15072026",
                customer=customer,
                process="Congelado",
                main_product=product,
                reception_date=dt.date(2026, 7, 13),
                production_date=dt.date(2026, 7, 15),
                shift=ProductionOrder.Shift.DAY,
                template_version=template,
                created_by=manager,
                status=ProductionOrder.Status.APPROVED,
            )
            first = generate_production_workbook(production_id=production.pk, user=manager, kind=GeneratedFile.Kind.FINAL, mapping_path=mapping)
            second = generate_production_workbook(production_id=production.pk, user=manager, kind=GeneratedFile.Kind.FINAL, mapping_path=mapping)
            self.assertTrue(first.valid)
            self.assertTrue(second.valid)
            self.assertEqual((first.sequence, second.sequence), (1, 2))
            self.assertTrue(first.file.storage.exists(first.file.name))
            self.assertEqual(first.integrity_report["has_vba"], True)
            self.assertIn("PP_105_1080PPF15072026_FINAL_v1.xlsm", first.filename)


class ReferenceWorkbookCrewGenerationTests(TestCase):
    def test_new_tunnel_crews_reach_tunnel_and_payment_sheets(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            source = Path(settings.BASE_DIR) / "reference_assets" / "PLANTILLA_PP_V2.xlsm"
            manager_role = Role.objects.create(
                code=Role.Codes.PRODUCTION_MANAGER,
                name="Jefe de producción",
            )
            manager = User.objects.create_user("crew-manager", password="Secure-test-123")
            manager.roles.add(manager_role)
            customer = Customer.objects.create(name="Cliente de cuadrillas")
            product = Product.objects.create(code="PP-001", description="Producto de prueba")
            template = TemplateVersion.objects.create(
                code="PP-V2-INTEGRATION",
                file=SimpleUploadedFile(
                    source.name,
                    source.read_bytes(),
                    content_type="application/vnd.ms-excel.sheet.macroEnabled.12",
                ),
                original_filename=source.name,
                uploaded_by=manager,
                mapping_version="v2",
            )
            production = ProductionOrder.objects.create(
                number=106,
                plant_lot="LOTE-CUADRILLAS",
                customer=customer,
                process="Pota",
                main_product=product,
                reception_date=dt.date(2026, 7, 16),
                production_date=dt.date(2026, 7, 16),
                shift=ProductionOrder.Shift.DAY,
                template_version=template,
                created_by=manager,
                status=ProductionOrder.Status.IN_PROGRESS,
            )
            tunnel = Tunnel.objects.create(code="T1", name="Túnel 1")
            fill = TunnelFill.objects.create(
                production=production,
                tunnel=tunnel,
                fill_number=1,
                date=dt.date(2026, 7, 16),
                supervisor=manager,
            )
            rack_1 = TunnelRack.objects.create(
                fill=fill,
                code="R01",
                position_key="T1!E5",
                max_trays=70,
            )
            rack_2 = TunnelRack.objects.create(
                fill=fill,
                code="R02",
                position_key="T1!F5",
                max_trays=50,
            )
            TunnelEntry.objects.create(
                production=production,
                responsible=manager,
                rack=rack_1,
                product=product,
                tray_count=70,
                date=dt.date(2026, 7, 16),
            )
            TunnelEntry.objects.create(
                production=production,
                responsible=manager,
                rack=rack_2,
                product=product,
                tray_count=10,
                date=dt.date(2026, 7, 16),
            )
            assignments = (
                ("CUAD-ANDRES", "ANDRES", rack_1, 20),
                ("CUAD-CHARLY", "CHARLY", rack_1, 30),
                ("CUAD-FLOR", "FLOR", rack_1, 20),
                ("CUAD-HANNOY", "HANNOY", rack_2, 10),
            )
            for code, name, rack, trays in assignments:
                crew = Crew.objects.create(code=code, name=name)
                TunnelCrewEntry.objects.create(
                    production=production,
                    responsible=manager,
                    fill=fill,
                    rack=rack,
                    crew=crew,
                    page_or_block="PAGINA 1",
                    tray_count=trays,
                    date=dt.date(2026, 7, 16),
                )

            plate_position = PlatePosition.objects.create(
                template_version=template,
                plate_rack=PlatePosition.PlateRack.P1,
                position_key="ENV. PLACAS!E5",
                display_name="Bachada 1 · Plaquero 1",
            )
            PlateEntry.objects.create(
                production=production,
                responsible=manager,
                observation="",
                date=production.production_date,
                shift=production.shift,
                position=plate_position,
                product=product,
                tray_count=40,
            )
            now = timezone.now()
            PlatePositionTiming.objects.create(
                production=production,
                position=plate_position,
                load_started_at=now,
                load_completed_at=now,
                launched_at=now,
                unloaded_at=now,
            )
            auto_pack_product(
                production=production,
                product_id=product.pk,
                pallet_number=1,
                user=manager,
            )

            generated = generate_production_workbook(
                production_id=production.pk,
                user=manager,
                kind=GeneratedFile.Kind.PRELIMINARY,
            )

            report = XlsmAnalyzer(generated.file.path).analyze()
            self.assertTrue(report["has_vba"])
            self.assertEqual(report["totals"]["sheets"], 28)
            tunnel_sheet = next(sheet for sheet in report["sheets"] if sheet["name"] == "T1")
            tunnel_cells = {
                item["cell"]: item["value"]
                for item in tunnel_sheet["non_formula_cells"]
            }
            self.assertEqual(tunnel_cells["E62"], "ANDRES")
            self.assertEqual(tunnel_cells["E63"], "CHARLY")
            self.assertEqual(tunnel_cells["E64"], "FLOR")
            self.assertEqual(tunnel_cells["E65"], "HANNOY")
            self.assertEqual(tunnel_cells["F64"], "20")

            payment_sheet = next(
                sheet for sheet in report["sheets"] if sheet["name"] == "T.EMV"
            )
            payment_formulas = {
                item["cell"]: item
                for item in payment_sheet["formulas"]
            }
            self.assertEqual(payment_formulas["L6"]["cached_value"], "FLOR")
            self.assertEqual(payment_formulas["M8"]["cached_value"], "200.00")
            self.assertEqual(payment_formulas["B30"]["cached_value"], "HANNOY")
            self.assertEqual(payment_formulas["C32"]["cached_value"], "100.00")

            plate_packaging_sheet = next(
                sheet for sheet in report["sheets"] if sheet["name"] == "EM-PLA"
            )
            plate_packaging_cells = {
                item["cell"]: item["value"]
                for item in plate_packaging_sheet["non_formula_cells"]
            }
            self.assertEqual(plate_packaging_cells["E6"], "20")
