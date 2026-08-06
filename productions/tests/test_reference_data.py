import datetime as dt
import io
import tempfile
from hashlib import sha256
from pathlib import Path

from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.conf import settings

from productions.models import (
    Crew,
    Customer,
    GeneratedFile,
    PlatePosition,
    Product,
    ProductionOrder,
    Role,
    TemplateVersion,
    Tunnel,
    User,
)
from productions.forms import ProductionOrderForm, ReceptionEntryForm
from productions.services.excel.generator import generate_production_workbook, mapping_capabilities
from productions.services.excel.validator import validate_output_file


class EnsureReferenceDataTests(TestCase):
    def test_restores_reference_catalogs_and_is_idempotent(self):
        administrator = User.objects.create_superuser(
            username="reference-admin",
            email="reference@example.com",
            password="Secure-admin-12345",
        )
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            legacy_customer = Customer.objects.create(name="Cliente antiguo")
            legacy_product = Product.objects.create(code="LEGACY-PRODUCT", description="Producto antiguo")
            legacy_template = TemplateVersion.objects.create(
                code="PP-V1-LEGACY",
                file=SimpleUploadedFile("legacy.xlsm", b"legacy-template"),
                original_filename="legacy.xlsm",
                sha256="legacy-template-sha256",
                uploaded_by=administrator,
                mapping_version="v1",
            )
            legacy_production = ProductionOrder.objects.create(
                number=779001,
                plant_lot="LOTE-ANTIGUO",
                customer=legacy_customer,
                process="Proceso antiguo",
                main_product=legacy_product,
                reception_date=dt.date(2026, 7, 14),
                production_date=dt.date(2026, 7, 14),
                shift=ProductionOrder.Shift.DAY,
                template_version=legacy_template,
                created_by=administrator,
            )
            call_command("ensure_reference_data", stdout=output)
            first_counts = (
                Customer.objects.count(),
                Product.objects.count(),
                TemplateVersion.objects.count(),
                PlatePosition.objects.count(),
                Role.objects.count(),
                Tunnel.objects.count(),
                Crew.objects.count(),
            )
            template = TemplateVersion.objects.get(code="PP-V2")
            reference_digest = sha256(
                (Path(settings.BASE_DIR) / "reference_assets" / "PLANTILLA_PP_V2.xlsm").read_bytes()
            ).hexdigest()
            legacy_production.refresh_from_db()
            self.assertEqual(legacy_production.template_version, template)
            self.assertEqual(template.mapping_version, "v2")
            self.assertEqual(template.sha256, reference_digest)
            self.assertEqual(sha256(Path(template.file.path).read_bytes()).hexdigest(), reference_digest)
            self.assertTrue(template.file.storage.exists(template.file.name))
            self.assertGreaterEqual(Product.objects.count(), 80)
            self.assertEqual(PlatePosition.objects.filter(template_version=template).count(), 24)
            self.assertEqual(Role.objects.count(), len(Role.Codes.choices))
            self.assertEqual(Tunnel.objects.count(), 6)
            reception_crews = ReceptionEntryForm().fields["crew"].queryset
            self.assertEqual(
                list(reception_crews.values_list("name", flat=True)),
                ["LUIS", "FELIX", "CHARLES"],
            )
            main_product = Product.objects.get(code="POTA-GRANEL")
            self.assertEqual(main_product.description, "POTA A GRANEL")
            production_form = ProductionOrderForm()
            self.assertEqual(list(production_form.fields["main_product"].queryset), [main_product])
            self.assertEqual(production_form.fields["main_product"].initial, main_product.pk)
            self.assertNotIn("production_date", production_form.fields)
            self.assertNotIn("shift", production_form.fields)
            self.assertNotIn("vehicle_notes", production_form.fields)
            self.assertNotIn("plate_notes", production_form.fields)
            self.assertNotIn("observations", production_form.fields)

            bound_form = ProductionOrderForm(
                {
                    "number": 880002,
                    "plant_lot": "PRUEBA-CAMPOS-AUTOMATICOS",
                    "customer_lot": "",
                    "customer": Customer.objects.get(name="Cliente de demostración").pk,
                    "process": "Validación",
                    "main_product": main_product.pk,
                    "reception_date": "2026-07-14",
                    "packaging_date": "",
                    "series": "",
                    "vehicle_notes": "",
                    "plate_notes": "",
                    "observations": "",
                    "template_version": template.pk,
                }
            )
            self.assertTrue(bound_form.is_valid(), bound_form.errors)
            unsaved_production = bound_form.save(commit=False)
            self.assertEqual(unsaved_production.production_date, dt.date(2026, 7, 14))
            self.assertEqual(unsaved_production.packaging_date, dt.date(2026, 7, 15))
            self.assertEqual(unsaved_production.customer_lot, "PPF14072026")
            self.assertEqual(unsaved_production.series, "001")
            self.assertEqual(unsaved_production.shift, ProductionOrder.Shift.DAY)

            production = ProductionOrder.objects.create(
                number=880001,
                plant_lot="PRUEBA-PLANTILLA-NUBE",
                customer=Customer.objects.get(name="Cliente de demostración"),
                process="Validación de plantilla",
                main_product=Product.objects.filter(active=True).first(),
                reception_date=dt.date(2026, 7, 14),
                production_date=dt.date(2026, 7, 14),
                shift=ProductionOrder.Shift.DAY,
                template_version=template,
                created_by=administrator,
                status=ProductionOrder.Status.APPROVED,
            )
            self.assertEqual(mapping_capabilities(template)["scope"], "full")
            generated = generate_production_workbook(
                production_id=production.pk,
                user=administrator,
                kind=GeneratedFile.Kind.FINAL,
            )
            self.assertTrue(generated.valid, generated.integrity_report)
            self.assertTrue(generated.file.storage.exists(generated.file.name))
            self.assertTrue(generated.integrity_report["has_vba"])
            self.assertEqual(generated.integrity_report["sheet_count"], 28)
            self.assertIn("_FINAL_", generated.filename)
            self.assertEqual(validate_output_file(generated.file.path), [])

            call_command("ensure_reference_data", stdout=output)
            second_counts = (
                Customer.objects.count(),
                Product.objects.count(),
                TemplateVersion.objects.count(),
                PlatePosition.objects.count(),
                Role.objects.count(),
                Tunnel.objects.count(),
                Crew.objects.count(),
            )

        self.assertEqual(first_counts, second_counts)
