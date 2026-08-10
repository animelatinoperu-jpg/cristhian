import datetime as dt
import io
import zipfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from productions.models import (
    Customer,
    PlateEntry,
    PlatePackagingAllocation,
    PlatePosition,
    PlatePositionTiming,
    PlatePallet,
    PlatePalletLine,
    Product,
    ProductionOrder,
    Role,
    TemplateVersion,
    Tunnel,
    TunnelEntry,
    TunnelFill,
    TunnelRack,
    TunnelPackagingEntry,
    User,
)
from productions.tests.test_tunnel_report import _cell_value

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _style_index(sheet_xml, ref):
    import xml.etree.ElementTree as ET

    cell = ET.fromstring(sheet_xml).find(f".//m:c[@r='{ref}']", NS)
    return int(cell.attrib.get("s", "0"))


def _style_details(styles_xml, index):
    import xml.etree.ElementTree as ET

    root = ET.fromstring(styles_xml)
    cell_format = root.find("m:cellXfs", NS).findall("m:xf", NS)[index]
    font_id = int(cell_format.attrib.get("fontId", "0"))
    font = root.find("m:fonts", NS).findall("m:font", NS)[font_id]
    font_name = font.find("m:name", NS).attrib.get("val")
    alignment = cell_format.find("m:alignment", NS)
    return {
        "font": font_name,
        "horizontal": alignment.attrib.get("horizontal") if alignment is not None else None,
        "vertical": alignment.attrib.get("vertical") if alignment is not None else None,
    }


class PackagingReportTests(TestCase):
    def setUp(self):
        role = Role.objects.create(code=Role.Codes.PRODUCTION_MANAGER, name="Jefe")
        self.user = User.objects.create_user("pack-report-manager", password="Secure-test-123")
        self.user.roles.add(role)
        customer = Customer.objects.create(name="GLOBAL TOP FOOD", tax_id="20555555555")
        self.main = Product.objects.create(code="MAIN", description="POTA")
        self.product = Product.objects.create(code="PP-003", description="ANILLAS BLANCAS SM ST MEDIANA")
        self.template = TemplateVersion.objects.create(
            code="PP-PACK-REPORT",
            file=SimpleUploadedFile("template.xlsm", b"fixture"),
            original_filename="template.xlsm",
            sha256="8" * 64,
            uploaded_by=self.user,
            rules={"package_kg": 20, "package_trays": 2},
        )
        self.production = ProductionOrder.objects.create(
            number=1400,
            plant_lot="PLANTA-1400",
            customer_lot="LOTE-1400",
            customer=customer,
            process="Pota",
            main_product=self.main,
            reception_date=dt.date(2026, 7, 18),
            production_date=dt.date(2026, 7, 18),
            shift=ProductionOrder.Shift.DAY,
            template_version=self.template,
            created_by=self.user,
        )
        self.client.force_login(self.user)

    def test_tunnel_packaging_report_uses_official_template(self):
        TunnelPackagingEntry.objects.create(
            production=self.production,
            responsible=self.user,
            date=dt.date(2026, 7, 18),
            pallet_number=1,
            product=self.product,
            package_count=12,
        )

        response = self.client.get(reverse("productions:tunnel_pack_report_xlsx", args=[self.production.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("EMPAQUE_TUNEL_PP_1400", response["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as package:
            self.assertIsNone(package.testzip())
            sheet = package.read("xl/worksheets/sheet1.xml")
            styles = package.read("xl/styles.xml")
        self.assertEqual(_cell_value(sheet, "F2"), "                    REGISTRO DE EMPAQUE (TUNEL)")
        self.assertEqual(_cell_value(sheet, "S8"), "PP 1400")
        self.assertEqual(_cell_value(sheet, "D12"), "GLOBAL TOP FOOD")
        self.assertEqual(_cell_value(sheet, "B19"), "P1")
        self.assertEqual(_cell_value(sheet, "D19"), "ANILLAS BLANCAS")
        self.assertEqual(_cell_value(sheet, "G19"), "SM ST MEDIANA")
        self.assertEqual(_cell_value(sheet, "N19"), "12")
        self.assertEqual(_cell_value(sheet, "R19"), "240")
        self.assertEqual(_cell_value(sheet, "N44"), "12")
        self.assertEqual(_cell_value(sheet, "R44"), "240")
        details = _style_details(styles, _style_index(sheet, "D19"))
        self.assertEqual(details["font"], "Arial Black")
        self.assertEqual(details["horizontal"], "center")
        self.assertEqual(details["vertical"], "center")

    def test_packaging_report_does_not_use_internal_product_code_as_weight_code(self):
        conus = Product.objects.create(code="PP-013", description="CONOS DE POTA")
        TunnelPackagingEntry.objects.create(
            production=self.production,
            responsible=self.user,
            date=dt.date(2026, 7, 18),
            pallet_number=1,
            product=conus,
            package_count=4,
        )

        response = self.client.get(reverse("productions:tunnel_pack_report_xlsx", args=[self.production.pk]))

        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as package:
            sheet = package.read("xl/worksheets/sheet1.xml")
        self.assertEqual(_cell_value(sheet, "D19"), "CONOS DE POTA")
        self.assertEqual(_cell_value(sheet, "G19"), "")

    @patch("productions.views.build_tunnel_packaging_report_pdf", return_value=b"%PDF-1.4\n%test\n")
    def test_tunnel_packaging_pdf_download(self, build_pdf):
        conus = Product.objects.create(code="PP-013", description="CONOS DE POTA")
        TunnelPackagingEntry.objects.create(
            production=self.production,
            responsible=self.user,
            date=dt.date(2026, 7, 18),
            pallet_number=1,
            product=conus,
            package_count=4,
        )

        response = self.client.get(reverse("productions:tunnel_pack_report_pdf", args=[self.production.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("EMPAQUE_TUNEL_PP_1400", response["Content-Disposition"])
        self.assertEqual(response.content, b"%PDF-1.4\n%test\n")
        build_pdf.assert_called_once_with(self.production)

    def test_plate_packaging_report_groups_allocations_by_pallet_and_product(self):
        position = PlatePosition.objects.create(
            template_version=self.template,
            position_key="Hoja!E5",
            display_name="Bachada 1 · Plaquero 1",
            plate_rack="P1",
        )
        PlatePositionTiming.objects.create(
            production=self.production,
            position=position,
            unloaded_at=dt.datetime(2026, 7, 18, 12, 0, tzinfo=dt.timezone.utc),
        )
        source = PlateEntry.objects.create(
            production=self.production,
            responsible=self.user,
            position=position,
            product=self.product,
            tray_count=20,
            shift=ProductionOrder.Shift.DAY,
            date=dt.date(2026, 7, 18),
        )
        PlatePackagingAllocation.objects.create(
            production=self.production,
            responsible=self.user,
            date=dt.date(2026, 7, 18),
            source_entry=source,
            pallet_number=2,
            package_count=5,
        )

        response = self.client.get(reverse("productions:plate_pack_report_xlsx", args=[self.production.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("EMPAQUE_PLAQUEROS_PP_1400", response["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as package:
            self.assertIsNone(package.testzip())
            sheet = package.read("xl/worksheets/sheet1.xml")
        self.assertEqual(_cell_value(sheet, "F2"), "                    REGISTRO DE EMPAQUE (PLAQUEROS)")
        self.assertEqual(_cell_value(sheet, "B19"), "P2")
        self.assertEqual(_cell_value(sheet, "N19"), "5")
        self.assertEqual(_cell_value(sheet, "R19"), "100")

    def test_plate_packaging_report_includes_automatic_pallet_lines(self):
        pallet = PlatePallet.objects.create(
            production=self.production,
            pallet_number=1,
        )
        PlatePalletLine.objects.create(
            production=self.production,
            responsible=self.user,
            pallet=pallet,
            product=self.product,
            package_count=56,
            date=dt.date(2026, 7, 18),
        )

        response = self.client.get(reverse("productions:plate_pack_report_xlsx", args=[self.production.pk]))

        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as package:
            sheet = package.read("xl/worksheets/sheet1.xml")
        self.assertEqual(_cell_value(sheet, "B19"), "P1")
        self.assertEqual(_cell_value(sheet, "D19"), "ANILLAS BLANCAS")
        self.assertEqual(_cell_value(sheet, "N19"), "56")
        self.assertEqual(_cell_value(sheet, "R19"), "1120")

    def test_plate_packaging_report_uses_second_sheet_when_detail_rows_overflow(self):
        for pallet_number in range(1, 27):
            pallet = PlatePallet.objects.create(
                production=self.production,
                pallet_number=pallet_number,
            )
            PlatePalletLine.objects.create(
                production=self.production,
                responsible=self.user,
                pallet=pallet,
                product=self.product,
                package_count=1,
                date=dt.date(2026, 7, 18),
            )

        response = self.client.get(reverse("productions:plate_pack_report_xlsx", args=[self.production.pk]))

        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as package:
            self.assertIsNone(package.testzip())
            workbook_xml = package.read("xl/workbook.xml").decode("utf-8")
            sheet1 = package.read("xl/worksheets/sheet1.xml")
            sheet2 = package.read("xl/worksheets/sheet2.xml")
        self.assertIn("Página 2", workbook_xml)
        self.assertEqual(_cell_value(sheet1, "B43"), "P25")
        self.assertEqual(_cell_value(sheet1, "N44"), "25")
        self.assertEqual(_cell_value(sheet2, "B19"), "P26")
        self.assertEqual(_cell_value(sheet2, "N19"), "1")
        self.assertEqual(_cell_value(sheet2, "N44"), "1")

    def test_tunnel_auto_packaging_uses_trays_from_tunnel_entries(self):
        tunnel = Tunnel.objects.create(code="T1", name="Túnel 1")
        fill = TunnelFill.objects.create(
            production=self.production,
            tunnel=tunnel,
            fill_number=1,
            date=dt.date(2026, 7, 18),
            supervisor=self.user,
        )
        rack = TunnelRack.objects.create(fill=fill, code="R01", position_key="T1!R01", max_trays=50)
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=rack,
            product=self.product,
            tray_count=21,
            date=dt.date(2026, 7, 18),
        )

        response = self.client.get(reverse("productions:tunnel_pack_create", args=[self.production.pk]))
        self.assertContains(response, "Formar bultos desde envasado en túneles")
        self.assertContains(response, "10 bultos")
        self.assertContains(response, "Pallet automático")
        self.assertContains(response, "Origen pendiente")
        self.assertContains(response, "T1 · Llenada 1 · R01")
        self.assertNotContains(response, "Número de palé")
        self.assertNotContains(response, "Guardar registro")

        response = self.client.post(
            reverse("productions:tunnel_pack_auto", args=[self.production.pk]),
            {"product": str(self.product.pk), "pallet_number": "3"},
        )

        self.assertRedirects(
            response,
            f"{reverse('productions:tunnel_pack_create', args=[self.production.pk])}?product={self.product.pk}&pallet=1#tunnel-auto-pack-form",
        )
        entry = TunnelPackagingEntry.objects.get(
            production=self.production,
            product=self.product,
            pallet_number=1,
            is_active=True,
        )
        self.assertEqual(entry.package_count, 10)
        self.assertEqual(entry.tray_count, 20)
        self.assertEqual(entry.kilos, 200)

    def test_tunnel_pack_page_shows_tunnel_cards_and_filters_by_tunnel(self):
        self.template.rules = {
            "package_kg": 20,
            "package_trays": 2,
            "tunnel_pallet_package_capacity": 10,
            "tunnel_pallet_max": 50,
        }
        self.template.save(update_fields=["rules"])
        other_product = Product.objects.create(code="PP-004", description="ANILLAS VERDES SM ST")
        tunnel1 = Tunnel.objects.create(code="T1", name="Túnel uno")
        tunnel2 = Tunnel.objects.create(code="T2", name="Túnel dos")
        fill1 = TunnelFill.objects.create(
            production=self.production,
            tunnel=tunnel1,
            fill_number=1,
            date=dt.date(2026, 7, 18),
            supervisor=self.user,
        )
        rack1 = TunnelRack.objects.create(fill=fill1, code="R01", position_key="T1!R01", max_trays=50)
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=rack1,
            product=self.product,
            tray_count=6,
            date=dt.date(2026, 7, 18),
        )
        fill2 = TunnelFill.objects.create(
            production=self.production,
            tunnel=tunnel2,
            fill_number=1,
            date=dt.date(2026, 7, 18),
            supervisor=self.user,
        )
        rack2 = TunnelRack.objects.create(fill=fill2, code="R01", position_key="T2!R01", max_trays=50)
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=rack2,
            product=self.product,
            tray_count=6,
            date=dt.date(2026, 7, 18),
        )
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=rack2,
            product=other_product,
            tray_count=4,
            date=dt.date(2026, 7, 18),
        )

        url = reverse("productions:tunnel_pack_create", args=[self.production.pk])
        response = self.client.get(url)
        self.assertContains(response, "Tarjetas de túneles")
        self.assertContains(response, "Túnel uno")
        self.assertContains(response, "Túnel dos")
        self.assertContains(response, "6 bultos")
        self.assertContains(response, "tunnel-pack-card-t1")
        self.assertContains(response, "tunnel-pack-card-t2")
        self.assertContains(response, "img/tunnel-t1.webp")
        self.assertContains(response, "img/tunnel-t2.webp")

        filtered = self.client.get(url, {"tunnel": "T1"})
        self.assertContains(filtered, "tunnel-pack-card-active")
        self.assertContains(filtered, "3 bultos")
        self.assertContains(filtered, 'name="tunnel"')
        self.assertNotContains(filtered, "PP-004")

        filtered_t2 = self.client.get(url, {"tunnel": "T2"})
        self.assertContains(filtered_t2, "PP-003")
        self.assertContains(filtered_t2, "PP-004")
        self.assertContains(filtered_t2, "3 bultos")
        self.assertContains(filtered_t2, "2 bultos")

    def test_tunnel_auto_pack_can_be_limited_to_one_tunnel(self):
        self.template.rules = {
            "package_kg": 20,
            "package_trays": 2,
            "tunnel_pallet_package_capacity": 10,
            "tunnel_pallet_max": 50,
        }
        self.template.save(update_fields=["rules"])
        tunnel1 = Tunnel.objects.create(code="T1", name="Túnel uno")
        tunnel2 = Tunnel.objects.create(code="T2", name="Túnel dos")
        for tunnel in (tunnel1, tunnel2):
            fill = TunnelFill.objects.create(
                production=self.production,
                tunnel=tunnel,
                fill_number=1,
                date=dt.date(2026, 7, 18),
                supervisor=self.user,
            )
            rack = TunnelRack.objects.create(fill=fill, code="R01", position_key=f"{tunnel.code}!R01", max_trays=50)
            TunnelEntry.objects.create(
                production=self.production,
                responsible=self.user,
                rack=rack,
                product=self.product,
                tray_count=6,
                date=dt.date(2026, 7, 18),
            )

        url = reverse("productions:tunnel_pack_auto", args=[self.production.pk])
        response = self.client.post(url, {"product": str(self.product.pk), "tunnel": "T1"})
        self.assertRedirects(
            response,
            f"{reverse('productions:tunnel_pack_create', args=[self.production.pk])}?product={self.product.pk}&pallet=1&tunnel=T1#tunnel-auto-pack-form",
        )
        entry = TunnelPackagingEntry.objects.get(
            production=self.production,
            product=self.product,
            pallet_number=1,
            is_active=True,
        )
        self.assertEqual(entry.package_count, 3)

        exhausted = self.client.post(
            url,
            {"product": str(self.product.pk), "tunnel": "T1"},
            follow=True,
        )
        self.assertContains(exhausted, "no reúne bandejas pendientes")

        other = self.client.post(url, {"product": str(self.product.pk), "tunnel": "T2"})
        entry.refresh_from_db()
        self.assertEqual(entry.package_count, 6)

        global_url = reverse("productions:tunnel_pack_create", args=[self.production.pk])
        page = self.client.get(global_url)
        self.assertContains(page, "6 bultos")

    def test_tunnel_auto_packaging_moves_to_next_pallet_when_current_is_full(self):
        self.template.rules = {"package_kg": 20, "package_trays": 2, "tunnel_pallet_package_capacity": 10, "tunnel_pallet_max": 50}
        self.template.save(update_fields=["rules"])
        tunnel = Tunnel.objects.create(code="T1", name="Túnel 1")
        fill = TunnelFill.objects.create(
            production=self.production,
            tunnel=tunnel,
            fill_number=1,
            date=dt.date(2026, 7, 18),
            supervisor=self.user,
        )
        rack = TunnelRack.objects.create(fill=fill, code="R01", position_key="T1!R01", max_trays=50)
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=rack,
            product=self.product,
            tray_count=30,
            date=dt.date(2026, 7, 18),
        )
        TunnelPackagingEntry.objects.create(
            production=self.production,
            responsible=self.user,
            date=dt.date(2026, 7, 18),
            pallet_number=1,
            product=self.product,
            package_count=10,
        )

        response = self.client.post(
            reverse("productions:tunnel_pack_auto", args=[self.production.pk]),
            {"product": str(self.product.pk), "pallet_number": "1"},
        )

        self.assertRedirects(
            response,
            f"{reverse('productions:tunnel_pack_create', args=[self.production.pk])}?product={self.product.pk}&pallet=2#tunnel-auto-pack-form",
        )
        entry = TunnelPackagingEntry.objects.get(
            production=self.production,
            product=self.product,
            pallet_number=2,
            is_active=True,
        )
        self.assertEqual(entry.package_count, 5)
