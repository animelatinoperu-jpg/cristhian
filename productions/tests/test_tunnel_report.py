import datetime as dt
import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from productions.models import (
    Crew, Customer, Product, ProductionOrder, Role, TemplateVersion, Tunnel,
    TunnelCrewEntry, TunnelEntry, TunnelFill, TunnelRack, User,
)
from productions.tests.test_plate_report import _cell_style_details

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _cell_value(sheet_xml, ref):
    cell = ET.fromstring(sheet_xml).find(f".//m:c[@r='{ref}']", NS)
    if cell is None:
        return None
    if cell.attrib.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//m:t", NS))
    value = cell.find("m:v", NS)
    return value.text if value is not None else ""


class TunnelReportTests(TestCase):
    def setUp(self):
        role = Role.objects.create(code=Role.Codes.PRODUCTION_MANAGER, name="Jefe")
        self.user = User.objects.create_user("tunnel-report-manager", password="Secure-test-123")
        self.user.roles.add(role)
        customer = Customer.objects.create(name="GLOBAL TOP FOOD", tax_id="20555555555")
        main = Product.objects.create(code="MAIN", description="POTA")
        product = Product.objects.create(code="PP-003", description="ANILLAS BLANCAS SM ST MEDIANA")
        template = TemplateVersion.objects.create(
            code="PP-TUNNEL-REPORT", file=SimpleUploadedFile("template.xlsm", b"fixture"),
            original_filename="template.xlsm", sha256="7" * 64, uploaded_by=self.user,
        )
        self.production = ProductionOrder.objects.create(
            number=1300, plant_lot="PLANTA-1300", customer_lot="LOTE-1300",
            customer=customer, process="Pota", main_product=main,
            reception_date=dt.date(2026, 7, 18), production_date=dt.date(2026, 7, 18),
            shift=ProductionOrder.Shift.DAY, template_version=template, created_by=self.user,
        )
        tunnel = Tunnel.objects.create(code="T2", name="Túnel 2")
        self.tunnel = tunnel
        fill = TunnelFill.objects.create(
            production=self.production, tunnel=tunnel, fill_number=1,
            date=dt.date(2026, 7, 18), start_time=dt.time(8, 0),
            launch_time=dt.time(10, 30), end_time=dt.time(11, 0), supervisor=self.user,
        )
        rack = TunnelRack.objects.create(fill=fill, code="R02", position_key="T2!R02", max_trays=50)
        TunnelEntry.objects.create(
            production=self.production, responsible=self.user, rack=rack,
            product=product, tray_count=49, date=dt.date(2026, 7, 18),
        )
        crew = Crew.objects.create(code="ANDRES", name="ANDRES")
        TunnelCrewEntry.objects.create(
            production=self.production, responsible=self.user, fill=fill, rack=rack,
            crew=crew, page_or_block="R02", tray_count=49, date=dt.date(2026, 7, 18),
        )
        self.client.force_login(self.user)

    def test_download_populates_official_tunnel_template(self):
        response = self.client.get(reverse("productions:tunnel_report_xlsx_by_tunnel", args=[self.production.pk, self.tunnel.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("ENVASADO_T2_PP_1300", response["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as package:
            self.assertIsNone(package.testzip())
            self.assertIn("xl/media/image1.jpeg", package.namelist())
            sheet = package.read("xl/worksheets/sheet1.xml")
            workbook = package.read("xl/workbook.xml")
            generated_styles = package.read("xl/styles.xml")
        self.assertEqual(_cell_value(sheet, "S7"), "PP 1300")
        self.assertEqual(_cell_value(sheet, "E11"), "GLOBAL TOP FOOD")
        self.assertEqual(_cell_value(sheet, "K15"), "HORA DE LANZAMIENTO DE TÚNELES (INICIO Y FINAL)")
        self.assertEqual(_cell_value(sheet, "B18"), "R02")
        self.assertEqual(_cell_value(sheet, "D18"), "ANILLAS BLANCAS")
        self.assertEqual(_cell_value(sheet, "J18"), "SM ST MEDIANA")
        self.assertEqual(_cell_value(sheet, "N18"), "T2")
        self.assertEqual(_cell_value(sheet, "Q18"), "49")
        self.assertEqual(_cell_value(sheet, "T18"), "ANDRES")
        self.assertEqual(_cell_value(sheet, "V18"), "49")
        self.assertEqual(
            _cell_value(sheet, "F70"),
            "ANDRES - 49 bandejas - 490.00 kg",
        )
        self.assertIn(b"$B$1:$V$76", workbook)
        self.assertEqual(_cell_style_details(sheet, generated_styles, "V18")["horizontal"], "center")

    def test_pdf_download_is_separated_by_tunnel(self):
        response = self.client.get(reverse("productions:tunnel_report_pdf_by_tunnel", args=[self.production.pk, self.tunnel.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("ENVASADO_T2_PP_1300", response["Content-Disposition"])
        reader = PdfReader(io.BytesIO(response.content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("ANILLAS BLANCAS", text)
        self.assertIn("T2", text)
        self.assertIn("R02", text)
        self.assertIn("ANDRES", text)
        self.assertIn("490.00 kg", text)

    def test_tunnel_crew_can_be_traced_by_product_in_same_rack(self):
        rack = TunnelRack.objects.get(fill__production=self.production, code="R02")
        product = Product.objects.create(code="PP-004", description="FILETE POTA 2.0-4.0 kg/pza")
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=rack,
            product=product,
            tray_count=1,
            date=dt.date(2026, 7, 18),
        )
        TunnelCrewEntry.objects.create(
            production=self.production,
            responsible=self.user,
            fill=rack.fill,
            rack=rack,
            product=product,
            crew=Crew.objects.get(code="ANDRES"),
            page_or_block="R02",
            tray_count=1,
            date=dt.date(2026, 7, 18),
        )

        response = self.client.get(reverse("productions:tunnel_report_xlsx_by_tunnel", args=[self.production.pk, self.tunnel.pk]))
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as package:
            sheet = package.read("xl/worksheets/sheet1.xml")
        self.assertEqual(_cell_value(sheet, "D18"), "ANILLAS BLANCAS")
        self.assertEqual(_cell_value(sheet, "V18"), "49")
        self.assertEqual(_cell_value(sheet, "D19"), "FILETE POTA")
        self.assertEqual(_cell_value(sheet, "V19"), "1")
