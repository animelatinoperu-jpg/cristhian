import datetime as dt
import hashlib
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from productions.models import AuditLog, Customer, GeneratedFile, Product, ProductionOrder, TemplateVersion, User


class ExcelDownloadTests(TestCase):
    def _records(self):
        user = User.objects.create_superuser("excel-admin", password="Secure-test-123")
        customer = Customer.objects.create(name="Cliente")
        product = Product.objects.create(code="PP-001", description="Producto")
        template = TemplateVersion.objects.create(
            code="PP-TEST",
            file=SimpleUploadedFile("plantilla.xlsm", b"plantilla"),
            original_filename="plantilla.xlsm",
            sha256=hashlib.sha256(b"plantilla").hexdigest(),
            uploaded_by=user,
        )
        production = ProductionOrder.objects.create(
            number=1,
            plant_lot="LOTE-1",
            customer=customer,
            process="Congelado",
            main_product=product,
            reception_date=dt.date(2026, 7, 13),
            production_date=dt.date(2026, 7, 13),
            shift=ProductionOrder.Shift.DAY,
            template_version=template,
            created_by=user,
        )
        payload = b"archivo-xlsm-con-macros"
        generated = GeneratedFile.objects.create(
            production=production,
            template_version=template,
            kind=GeneratedFile.Kind.PRELIMINARY,
            sequence=1,
            file=SimpleUploadedFile("PP_1_LOTE-1_PRELIMINAR_v1.xlsm", payload),
            filename="PP_1_LOTE-1_PRELIMINAR_v1.xlsm",
            sha256=hashlib.sha256(payload).hexdigest(),
            generated_by=user,
            integrity_report={"valid": True, "sheet_count": 28, "has_vba": True},
            valid=True,
        )
        return user, production, generated, payload

    def test_direct_download_returns_complete_xlsm_attachment(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            user, _, generated, payload = self._records()
            self.client.force_login(user)

            response = self.client.get(reverse("productions:download_file", args=[generated.pk]))

            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), payload)
            self.assertEqual(response["Content-Type"], "application/vnd.ms-excel.sheet.macroEnabled.12")
            self.assertIn(generated.filename, response["Content-Disposition"])
            self.assertEqual(response["Cache-Control"], "private, no-store")
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
            self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.DOWNLOAD, record_pk=str(generated.pk)).exists())

    def test_generate_button_generates_and_downloads_in_one_action(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            user, production, generated, payload = self._records()
            self.client.force_login(user)

            with patch("productions.views.generate_production_workbook", return_value=generated):
                response = self.client.post(
                    reverse("productions:generate_excel", args=[production.pk, "preliminary"])
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), payload)
            self.assertIn("attachment", response["Content-Disposition"])

    def test_primary_download_action_generates_current_workbook_instead_of_reusing_old_file(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            user, production, generated, _ = self._records()
            self.client.force_login(user)

            response = self.client.get(reverse("productions:detail", args=[production.pk]))

            self.assertEqual(response.status_code, 200)
            self.assertContains(
                response,
                reverse("productions:generate_excel", args=[production.pk, "preliminary"]),
            )
            self.assertContains(response, "GENERAR Y DESCARGAR EXCEL ACTUALIZADO")
            self.assertContains(response, "Incluye todos los registros guardados hasta este momento")
            self.assertNotContains(response, reverse("productions:download_file", args=[generated.pk]))
