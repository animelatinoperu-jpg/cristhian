import datetime as dt

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from productions.models import Crew, Customer, Product, ProductionOrder, ReceptionEntry, TemplateVersion, User, Vehicle
from productions.services.pdf_report import build_production_pdf


class PdfReportTests(TestCase):
    def test_a4_summary_contains_production_identity_and_page(self):
        user = User.objects.create_user("manager", password="Secure-test-123")
        customer = Customer.objects.create(name="Cliente PDF")
        product = Product.objects.create(code="P001", description="Manto")
        template = TemplateVersion.objects.create(code="PP-V1", file=SimpleUploadedFile("t.xlsm", b"x"), original_filename="t.xlsm", sha256="c" * 64, uploaded_by=user)
        production = ProductionOrder.objects.create(number=205, plant_lot="LOTE-PDF", customer=customer, process="Congelado", main_product=product, reception_date=dt.date(2026, 7, 13), production_date=dt.date(2026, 7, 13), shift=ProductionOrder.Shift.DAY, template_version=template, created_by=user)
        payload = build_production_pdf(production)
        reader = PdfReader(__import__("io").BytesIO(payload))
        self.assertGreater(len(payload), 2000)
        self.assertEqual(len(reader.pages), 1)
        text = reader.pages[0].extract_text()
        self.assertIn("PP 205 - LOTE-PDF", text)
        self.assertIn("Pagina 1", text)
        user.is_superuser = True
        user.is_staff = True
        user.save(update_fields=["is_superuser", "is_staff"])
        self.client.force_login(user)
        response = self.client.get(reverse("productions:production_pdf", args=[production.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("PP_205_LOTE-PDF.pdf", response["Content-Disposition"])
        report = self.client.get(reverse("productions:report", args=[production.pk]))
        self.assertEqual(report.status_code, 200)
        self.assertContains(report, "REPORTE CONSOLIDADO", html=False)

    def test_pdf_includes_reception_cone_pota_by_crew(self):
        user = User.objects.create_user("manager", password="Secure-test-123")
        customer = Customer.objects.create(name="Cliente PDF")
        product = Product.objects.create(code="P013", description="CONOS DE POTA")
        template = TemplateVersion.objects.create(code="PP-V1", file=SimpleUploadedFile("t.xlsm", b"x"), original_filename="t.xlsm", sha256="c" * 64, uploaded_by=user)
        production = ProductionOrder.objects.create(number=206, plant_lot="LOTE-PDF-2", customer=customer, process="Pota", main_product=product, reception_date=dt.date(2026, 7, 13), production_date=dt.date(2026, 7, 13), shift=ProductionOrder.Shift.DAY, template_version=template, created_by=user)
        crew = Crew.objects.create(code="CUAD-01", name="FERMÍN")
        vehicle = Vehicle.objects.create(plate="ABC-123")
        ReceptionEntry.objects.create(
            production=production,
            responsible=user,
            observation="",
            date=production.reception_date,
            vehicle=vehicle,
            car_number="1",
            product=product,
            crew=crew,
            container="1",
            weight_kg="130.00",
        )

        payload = build_production_pdf(production)
        reader = PdfReader(__import__("io").BytesIO(payload))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)

        self.assertIn("Peso de limpieza de cono de pota por cuadrilla", text)
        self.assertIn("FERMIN", text)
        self.assertIn("130.00", text)
