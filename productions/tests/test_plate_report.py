import datetime as dt
import io
import zipfile
from xml.etree import ElementTree as ET

from pypdf import PdfReader

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from productions.models import (
    AuditLog,
    Crew,
    Customer,
    PlateCrewEntry,
    PlateEntry,
    PlatePosition,
    PlatePositionTiming,
    Product,
    ProductionOrder,
    ReceptionEntry,
    Role,
    TemplateVersion,
    User,
    Vehicle,
)


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _cell_value(sheet_xml, ref, shared_strings_xml=None):
    root = ET.fromstring(sheet_xml)
    cell = root.find(f".//m:c[@r='{ref}']", NS)
    if cell is None:
        return None
    if cell.attrib.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//m:t", NS))
    value = cell.find("m:v", NS)
    if cell.attrib.get("t") == "s" and value is not None and shared_strings_xml:
        shared_root = ET.fromstring(shared_strings_xml)
        shared = shared_root.findall("m:si", NS)[int(value.text)]
        return "".join(node.text or "" for node in shared.findall(".//m:t", NS))
    return value.text if value is not None else ""


def _cell_style(sheet_xml, styles_xml, ref):
    sheet_root = ET.fromstring(sheet_xml)
    styles_root = ET.fromstring(styles_xml)
    cell = sheet_root.find(f".//m:c[@r='{ref}']", NS)
    style_index = int(cell.attrib.get("s", "0"))
    cell_formats = styles_root.find("m:cellXfs", NS)
    cell_format = cell_formats.findall("m:xf", NS)[style_index]
    font_index = int(cell_format.attrib.get("fontId", "0"))
    fonts = styles_root.find("m:fonts", NS)
    font = fonts.findall("m:font", NS)[font_index]
    font_name = font.find("m:name", NS).attrib.get("val")
    alignment = cell_format.find("m:alignment", NS)
    return font_name, alignment.attrib.get("wrapText") if alignment is not None else None


def _cell_style_details(sheet_xml, styles_xml, ref):
    sheet_root = ET.fromstring(sheet_xml)
    styles_root = ET.fromstring(styles_xml)
    cell = sheet_root.find(f".//m:c[@r='{ref}']", NS)
    style_index = int(cell.attrib.get("s", "0"))
    cell_format = styles_root.find("m:cellXfs", NS).findall("m:xf", NS)[style_index]
    font_index = int(cell_format.attrib.get("fontId", "0"))
    font = styles_root.find("m:fonts", NS).findall("m:font", NS)[font_index]
    alignment = cell_format.find("m:alignment", NS)
    return {
        "name": font.find("m:name", NS).attrib.get("val"),
        "size": font.find("m:sz", NS).attrib.get("val"),
        "wrap": alignment.attrib.get("wrapText") if alignment is not None else None,
        "horizontal": alignment.attrib.get("horizontal") if alignment is not None else None,
        "vertical": alignment.attrib.get("vertical") if alignment is not None else None,
    }


def _row_style(sheet_xml, row_number):
    root = ET.fromstring(sheet_xml)
    return root.find(f".//m:row[@r='{row_number}']", NS).attrib


def _workbook_sheet_names(workbook_xml):
    root = ET.fromstring(workbook_xml)
    return [sheet.attrib["name"] for sheet in root.findall(".//m:sheet", NS)]


class PlateReportTests(TestCase):
    def setUp(self):
        role = Role.objects.create(code=Role.Codes.PRODUCTION_MANAGER, name="Jefe")
        self.user = User.objects.create_user("plate-report-manager", password="Secure-test-123")
        self.user.roles.add(role)
        customer = Customer.objects.create(name="GLOBAL TOP FOOD", tax_id="20555555555")
        main_product = Product.objects.create(code="POTA-GRANEL", description="POTA A GRANEL")
        self.product = Product.objects.create(
            code="PP-016",
            description="FILETE C/M C/T TRES PIELES 2.0-4.0 kg/pza",
        )
        template = TemplateVersion.objects.create(
            code="PP-PLATE-REPORT",
            file=SimpleUploadedFile("template.xlsm", b"fixture"),
            original_filename="template.xlsm",
            sha256="8" * 64,
            uploaded_by=self.user,
        )
        self.production = ProductionOrder.objects.create(
            number=1200,
            plant_lot="PLANTA-1200",
            customer_lot="LOTE-CLIENTE-1200",
            customer=customer,
            process="Pota",
            main_product=main_product,
            reception_date=dt.date(2026, 7, 18),
            production_date=dt.date(2026, 7, 18),
            shift=ProductionOrder.Shift.DAY,
            template_version=template,
            created_by=self.user,
        )
        vehicle = Vehicle.objects.create(plate="ABC-123")
        raw_product = Product.objects.create(code="RM-001", description="POTA ENTERA")
        ReceptionEntry.objects.create(
            production=self.production,
            responsible=self.user,
            date=dt.date(2026, 7, 18),
            vehicle=vehicle,
            car_number="1",
            product=raw_product,
            container="1",
            weight_kg="500.00",
        )
        self.position = PlatePosition.objects.create(
            template_version=template,
            plate_rack=PlatePosition.PlateRack.P1,
            position_key="ENV. PLACAS!E5",
            display_name="Bachada 1 · Plaquero 1",
        )
        PlateEntry.objects.create(
            production=self.production,
            responsible=self.user,
            date=dt.date(2026, 7, 18),
            shift=ProductionOrder.Shift.DAY,
            position=self.position,
            product=self.product,
            tray_count=33,
        )
        andres = Crew.objects.create(code="ANDRES", name="ANDRES")
        fermin = Crew.objects.create(code="FERMIN", name="FERMIN")
        for crew, trays in ((andres, 20), (fermin, 13)):
            PlateCrewEntry.objects.create(
                production=self.production,
                responsible=self.user,
                position=self.position,
                page="PAGINA 1",
                product=self.product,
                crew=crew,
                tray_count=trays,
                date=dt.date(2026, 7, 18),
            )
        PlatePositionTiming.objects.create(
            production=self.production,
            position=self.position,
            load_started_at=timezone.make_aware(dt.datetime(2026, 7, 18, 10, 6)),
            load_completed_at=timezone.make_aware(dt.datetime(2026, 7, 18, 12, 21)),
            launched_at=timezone.make_aware(dt.datetime(2026, 7, 18, 12, 25)),
        )
        self.client.force_login(self.user)

    def test_download_fills_official_plate_report_and_preserves_images(self):
        response = self.client.get(
            reverse("productions:plate_report_xlsx", args=[self.production.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("ENVASADO_PLAQUEROS_PP_1200", response["Content-Disposition"])

        with zipfile.ZipFile(io.BytesIO(response.content)) as package:
            self.assertIsNone(package.testzip())
            self.assertIn("xl/media/image1.jpeg", package.namelist())
            sheet = package.read("xl/worksheets/sheet1.xml")
            styles = package.read("xl/styles.xml")
            shared_strings = package.read("xl/sharedStrings.xml")

        self.assertEqual(_cell_value(sheet, "E11"), "GLOBAL TOP FOOD")
        self.assertEqual(_cell_value(sheet, "T11"), "20555555555")
        self.assertEqual(_cell_value(sheet, "D14"), "LOTE-CLIENTE-1200")
        self.assertEqual(_cell_value(sheet, "Q14"), "ABC-123")
        self.assertEqual(_cell_value(sheet, "Q15"), "INICIO 12:25 / FINAL 12:25")
        self.assertEqual(
            _cell_value(sheet, "B17", shared_strings),
            "INICIO / FIN DE CARGA",
        )
        self.assertEqual(
            _cell_value(sheet, "N17", shared_strings), "N°  DE PLAQUERO"
        )
        self.assertEqual(_cell_value(sheet, "B18"), "10:06 - 12:21")
        self.assertEqual(_cell_value(sheet, "D18"), "FILETE C/M C/T TRES PIELES")
        self.assertEqual(_cell_value(sheet, "J18"), "2.0-4.0 KG")
        self.assertEqual(_cell_value(sheet, "N18"), "B1-P1")
        self.assertEqual(_cell_value(sheet, "Q18"), "33")
        self.assertEqual(_cell_value(sheet, "T18"), "ANDRES")
        self.assertEqual(_cell_value(sheet, "V18"), "20")
        self.assertEqual(_cell_value(sheet, "D19"), "FILETE C/M C/T TRES PIELES")
        self.assertEqual(_cell_value(sheet, "Q19"), "")
        self.assertEqual(_cell_value(sheet, "T19"), "FERMIN")
        self.assertEqual(_cell_value(sheet, "V19"), "13")
        self.assertEqual(
            _cell_value(sheet, "E50", shared_strings), "RESUMEN DEL PRODUCTO"
        )
        self.assertEqual(
            _cell_value(sheet, "M50", shared_strings), "RESUMEN DEL PRODUCTO"
        )
        self.assertEqual(_cell_value(sheet, "G62"), "33")
        self.assertEqual(_cell_value(sheet, "T62"), "")
        self.assertEqual(
            _cell_value(sheet, "F68"),
            "ANDRES - 20",
        )
        self.assertEqual(
            _cell_value(sheet, "M68"),
            "FERMIN - 13",
        )
        self.assertEqual(_cell_style(sheet, styles, "D18"), ("Arial Black", "1"))
        self.assertEqual(_cell_style_details(sheet, styles, "D18")["size"], "12.5")
        self.assertEqual(
            _cell_style_details(sheet, styles, "V18")["horizontal"],
            "center",
        )
        self.assertEqual(_cell_style(sheet, styles, "E11"), ("Arial Black", "1"))
        self.assertEqual(_cell_style(sheet, styles, "E50"), ("Arial Black", "1"))
        self.assertEqual(
            _cell_style_details(sheet, styles, "E51"),
            {
                "name": "Arial Black",
                "size": "8",
                "wrap": "1",
                "horizontal": "center",
                "vertical": "center",
            },
        )
        self.assertEqual(_row_style(sheet, 51).get("ht"), "30")
        self.assertEqual(_row_style(sheet, 51).get("customHeight"), "1")
        self.assertEqual(_row_style(sheet, 68).get("ht"), "90")
        self.assertEqual(_cell_style_details(sheet, styles, "F68")["size"], "8")
        sheet_root = ET.fromstring(sheet)
        merge_refs = {
            node.attrib["ref"]
            for node in sheet_root.findall(".//m:mergeCell", NS)
        }
        self.assertIn("F68:K68", merge_refs)
        self.assertIn("M68:T68", merge_refs)
        self.assertIn(b'xmlns:x16r2="http://schemas.microsoft.com/', styles)
        self.assertIn(b'xmlns:xr="http://schemas.microsoft.com/', styles)
        self.assertIn(b'mc:Ignorable="x14ac x16r2 xr"', styles)
        self.assertTrue(
            AuditLog.objects.filter(
                production=self.production,
                module="plate-report",
                action=AuditLog.Action.DOWNLOAD,
            ).exists()
        )

    def test_plate_page_exposes_report_download(self):
        response = self.client.get(
            reverse("productions:plate_create", args=[self.production.pk])
        )
        self.assertContains(
            response,
            reverse("productions:plate_report_xlsx", args=[self.production.pk]),
        )
        self.assertContains(
            response,
            reverse("productions:plate_report_pdf", args=[self.production.pk]),
        )

    def test_pdf_download_contains_the_complete_a4_report(self):
        response = self.client.get(
            reverse("productions:plate_report_pdf", args=[self.production.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("ENVASADO_PLAQUEROS_PP_1200", response["Content-Disposition"])

        reader = PdfReader(io.BytesIO(response.content))
        self.assertEqual(len(reader.pages), 1)
        self.assertEqual(tuple(round(value, 1) for value in reader.pages[0].mediabox[2:]), (595.3, 841.9))
        text = reader.pages[0].extract_text()
        self.assertIn("REGISTRO DE ENVASADO (PLAQUERO)", text)
        self.assertIn("FILETE C/M C/T TRES PIELES", text)
        self.assertIn("2.0-4.0 KG", text)
        self.assertIn("ANDRES", text)
        self.assertTrue(
            AuditLog.objects.filter(
                production=self.production,
                module="plate-report-pdf",
                action=AuditLog.Action.DOWNLOAD,
            ).exists()
        )

    def test_download_creates_an_additional_sheet_after_31_detail_lines(self):
        second_page_product = Product.objects.create(
            code="PP-011",
            description="BOTON BLANCO SM ST",
        )
        for batch_number in range(2, 35):
            position = PlatePosition.objects.create(
                template_version=self.production.template_version,
                plate_rack=PlatePosition.PlateRack.P1,
                position_key=f"TEST-{batch_number}",
                display_name=f"Bachada {batch_number} · Plaquero 1",
            )
            PlateEntry.objects.create(
                production=self.production,
                responsible=self.user,
                date=dt.date(2026, 7, 18),
                shift=ProductionOrder.Shift.DAY,
                position=position,
                product=(
                    second_page_product
                    if batch_number >= 31
                    else self.product
                ),
                tray_count=1,
            )

        response = self.client.get(
            reverse("productions:plate_report_xlsx", args=[self.production.pk])
        )
        self.assertEqual(response.status_code, 200)

        with zipfile.ZipFile(io.BytesIO(response.content)) as package:
            self.assertIsNone(package.testzip())
            names = package.namelist()
            self.assertIn("xl/worksheets/sheet2.xml", names)
            self.assertIn("xl/worksheets/_rels/sheet2.xml.rels", names)
            self.assertIn("xl/drawings/drawing2.xml", names)
            self.assertIn("xl/drawings/_rels/drawing2.xml.rels", names)
            self.assertIn("xl/printerSettings/printerSettings2.bin", names)
            workbook = package.read("xl/workbook.xml")
            first_sheet = package.read("xl/worksheets/sheet1.xml")
            second_sheet = package.read("xl/worksheets/sheet2.xml")
            styles = package.read("xl/styles.xml")

        self.assertEqual(
            _workbook_sheet_names(workbook),
            ["ENVASADO", "ENVASADO 2"],
        )
        self.assertEqual(_cell_value(first_sheet, "U2"), "1 de 2")
        self.assertEqual(_cell_value(second_sheet, "U2"), "2 de 2")
        self.assertEqual(_cell_value(second_sheet, "D18"), "BOTON BLANCO")
        self.assertEqual(_cell_value(second_sheet, "J18"), "SM ST")
        self.assertEqual(_cell_value(second_sheet, "N18"), "B31-P1")
        self.assertEqual(_cell_value(second_sheet, "Q18"), "1")
        self.assertEqual(
            _cell_value(first_sheet, "E51"),
            "FILETE C/M C/T TRES PIELES 2.0-4.0 kg/pza",
        )
        self.assertEqual(_cell_value(first_sheet, "G51"), "62")
        self.assertEqual(_cell_value(second_sheet, "E51"), "BOTON BLANCO SM ST")
        self.assertEqual(_cell_value(second_sheet, "G51"), "4")
        self.assertEqual(_cell_value(second_sheet, "E52"), "")
        self.assertEqual(_cell_style(second_sheet, styles, "D18"), ("Arial Black", "1"))
        self.assertEqual(_row_style(second_sheet, 51).get("ht"), "30")

        pdf_response = self.client.get(
            reverse("productions:plate_report_pdf", args=[self.production.pk])
        )
        pdf_reader = PdfReader(io.BytesIO(pdf_response.content))
        self.assertEqual(len(pdf_reader.pages), 2)
        self.assertIn("FILETE C/M C/T TRES PIELES", pdf_reader.pages[0].extract_text())
        self.assertNotIn("BOTON BLANCO SM ST", pdf_reader.pages[0].extract_text())
        self.assertIn("BOTON BLANCO SM ST", pdf_reader.pages[1].extract_text())

    def test_report_without_physical_entries_returns_to_plate_page(self):
        PlateCrewEntry.objects.filter(production=self.production).delete()
        PlateEntry.objects.filter(production=self.production).delete()
        response = self.client.get(
            reverse("productions:plate_report_xlsx", args=[self.production.pk]),
            follow=True,
        )
        self.assertRedirects(
            response,
            reverse("productions:plate_create", args=[self.production.pk]),
        )
        self.assertContains(response, "Todavía no hay productos envasados")
