import tempfile
import zipfile
import yaml
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase
from django.conf import settings

from productions.services.excel.analyzer import NS, XlsmAnalyzer
from productions.services.excel.integrity_checker import IntegrityChecker
from productions.services.excel.generator import _tunnel_payment_overrides
from productions.services.excel.mapper import MappingError, load_mapping
from productions.services.excel.validator import _missing_ignorable_prefixes, validate_output_file
from productions.services.excel.writer import SafeXlsmWriter, UnsafeWriteError
from .factories import build_minimal_xlsm, write_mapping


class ExcelEngineTests(SimpleTestCase):
    def test_reference_template_has_four_manual_material_cells(self):
        template = Path(settings.BASE_DIR) / "reference_assets" / "PLANTILLA_PP_V2.xlsm"
        report = XlsmAnalyzer(template).analyze()
        pp_sheet = next(sheet for sheet in report["sheets"] if sheet["name"] == "PP")
        mapping = load_mapping(Path(settings.BASE_DIR) / "config" / "excel_mapping_v2.yaml")
        material_targets = {
            item["field"]: item["cell"]
            for item in mapping["mappings"]
            if item["module"] == "materials"
        }

        self.assertTrue(report["has_vba"])
        self.assertFalse(any(item["cell"] == "E77" for item in pp_sheet["formulas"]))
        self.assertTrue(any(item["cell"] == "E77" for item in pp_sheet["non_formula_cells"]))
        self.assertEqual(
            material_targets,
            {
                "materials.strech_film.quantity": "E77",
                "materials.rafia.quantity": "E81",
                "materials.hielo.quantity": "E94",
                "materials.plumones.quantity": "E95",
            },
        )

    def test_writer_preserves_two_decimal_kg_payload(self):
        cell_type, payload = SafeXlsmWriter._cell_payload(Decimal("5040.00"))

        self.assertIsNone(cell_type)
        self.assertEqual(payload, b"<v>5040.00</v>")

    def test_analyzer_inventories_vba_formulas_and_print_area(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "template.xlsm"
            build_minimal_xlsm(source)
            report = XlsmAnalyzer(source).analyze()
            self.assertTrue(report["has_vba"])
            self.assertEqual([sheet["name"] for sheet in report["sheets"]], ["PP"])
            self.assertEqual(report["sheets"][0]["formula_count"], 1)
            self.assertEqual(report["sheets"][0]["print_areas"], ["PP!$A$1:$C$1"])

    def test_safe_writer_changes_only_authorized_cell_and_preserves_vba_formula(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "template.xlsm"
            target = Path(temp) / "PP_1_LOTE_FINAL_v1.xlsm"
            mapping = Path(temp) / "mapping.yaml"
            template_hash = build_minimal_xlsm(source)
            write_mapping(mapping, template_hash)
            _, authorized = SafeXlsmWriter(source, mapping).write(target, {"production.number": 105})
            result = IntegrityChecker(source, target, authorized).check()
            self.assertTrue(result["valid"], result["issues"])
            with zipfile.ZipFile(source) as before, zipfile.ZipFile(target) as after:
                self.assertEqual(before.read("xl/vbaProject.bin"), after.read("xl/vbaProject.bin"))
                root = ET.fromstring(after.read("xl/worksheets/sheet1.xml"))
                cells = {cell.attrib["r"]: cell for cell in root.findall(".//m:c", NS)}
                self.assertEqual(cells["A1"].find("m:v", NS).text, "105")
                self.assertEqual(cells["B1"].find("m:f", NS).text, "A1*2")

    def test_safe_writer_applies_only_expected_formula_override(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "template.xlsm"
            target = Path(temp) / "output.xlsm"
            mapping = Path(temp) / "mapping.yaml"
            template_hash = build_minimal_xlsm(source)
            write_mapping(mapping, template_hash)
            overrides = {("PP", "B1"): "(A1*2)*10"}

            _, authorized = SafeXlsmWriter(source, mapping).write(
                target,
                {"production.number": 105},
                formula_overrides=overrides,
            )
            result = IntegrityChecker(
                source,
                target,
                authorized,
                formula_overrides=overrides,
            ).check()

            self.assertTrue(result["valid"], result["issues"])
            with zipfile.ZipFile(target) as package:
                root = ET.fromstring(package.read("xl/worksheets/sheet1.xml"))
                formula_cell = root.find(".//m:c[@r='B1']", NS)
                self.assertEqual(formula_cell.find("m:f", NS).text, "(A1*2)*10")
                self.assertIsNone(formula_cell.find("m:v", NS))

            rejected = IntegrityChecker(source, target, authorized).check()
            self.assertFalse(rejected["valid"])
            self.assertTrue(any("fórmulas no autorizadas" in issue for issue in rejected["issues"]))

    def test_safe_writer_persists_formula_cache_and_visible_detail(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "template.xlsm"
            target = Path(temp) / "output.xlsm"
            mapping = Path(temp) / "mapping.yaml"
            template_hash = build_minimal_xlsm(source)
            write_mapping(mapping, template_hash)
            formulas = {
                ("PP", "B1"): {
                    "formula": "(A1*2)*10",
                    "cached_value": Decimal("2100.00"),
                }
            }

            _, authorized = SafeXlsmWriter(source, mapping).write(
                target,
                {"production.number": 105},
                formula_overrides=formulas,
                cell_overrides={("PP", "C1"): "T1 · 20 BDJ"},
            )
            result = IntegrityChecker(
                source,
                target,
                authorized,
                formula_overrides=formulas,
            ).check()

            self.assertTrue(result["valid"], result["issues"])
            with zipfile.ZipFile(target) as package:
                root = ET.fromstring(package.read("xl/worksheets/sheet1.xml"))
                formula_cell = root.find(".//m:c[@r='B1']", NS)
                detail_cell = root.find(".//m:c[@r='C1']", NS)
                self.assertEqual(formula_cell.find("m:f", NS).text, "(A1*2)*10")
                self.assertEqual(formula_cell.find("m:v", NS).text, "2100.00")
                self.assertEqual(detail_cell.find("m:is/m:t", NS).text, "T1 · 20 BDJ")

    def test_reference_template_converts_tunnel_crew_payment_cells_to_kg(self):
        template = Path(settings.BASE_DIR) / "reference_assets" / "PLANTILLA_PP_V2.xlsm"
        mapping = load_mapping(Path(settings.BASE_DIR) / "config" / "excel_mapping_v2.yaml")
        values = {
            "crew_roster.CUAD-01.name": "ANDRES",
            "crew_roster.CUAD-02.name": "CHARLY",
            "crew_roster.CUAD-03.name": "FLOR",
            "tunnel_crews.T1.fill1.PAGINA 1.CUAD-01.trays": 20,
            "tunnel_crews.T1.fill1.PAGINA 1.CUAD-02.trays": 30,
            "tunnel_crews.T1.fill1.PAGINA 1.CUAD-03.trays": 20,
        }

        formulas, cells = _tunnel_payment_overrides(template, mapping, values)

        self.assertEqual(len(formulas), 110)
        self.assertEqual(
            formulas[("T.EMV", "C8")],
            {
                "formula": "SUM('T1'!F62,'T1'!G62)*10",
                "cached_value": Decimal("200.00"),
            },
        )
        self.assertEqual(
            formulas[("T.EMV", "H8")],
            {
                "formula": "SUM('T1'!F63,'T1'!G63)*10",
                "cached_value": Decimal("300.00"),
            },
        )
        self.assertEqual(
            formulas[("T.EMV", "G6")],
            {
                "formula": "IF('T1'!E63<>\"\",'T1'!E63,\"DISPONIBLE\")",
                "cached_value": "CHARLY",
            },
        )
        self.assertEqual(
            formulas[("T.EMV", "G30")]["cached_value"],
            "DISPONIBLE",
        )
        self.assertEqual(
            formulas[("T.EMV", "G130")]["cached_value"],
            "DISPONIBLE",
        )
        self.assertEqual(cells[("T.EMV", "B8")], "T1 · 20 BDJ")
        self.assertEqual(cells[("T.EMV", "G8")], "T1 · 30 BDJ")
        self.assertEqual(cells[("T.EMV", "L8")], "T1 · 20 BDJ")

    def test_reference_template_assigns_a_crew_from_another_tunnel_to_a_free_box(self):
        template = Path(settings.BASE_DIR) / "reference_assets" / "PLANTILLA_PP_V2.xlsm"
        mapping = load_mapping(Path(settings.BASE_DIR) / "config" / "excel_mapping_v2.yaml")
        values = {
            "crew_roster.CUAD-01.name": "NUEVA CUADRILLA",
            "tunnel_crews.T6.fill1.PAGINA 1.CUAD-01.trays": 49,
        }

        formulas, cells = _tunnel_payment_overrides(template, mapping, values)

        self.assertEqual(formulas[("T.EMV", "B6")]["cached_value"], "NUEVA CUADRILLA")
        self.assertEqual(formulas[("T.EMV", "C20")]["cached_value"], Decimal("490.00"))
        self.assertEqual(cells[("T.EMV", "B20")], "T6 · 49 BDJ")

    def test_safe_writer_preserves_microsoft_extension_prefixes_verbatim(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "template.xlsm"
            target = Path(temp) / "PP_1_LOTE_FINAL_v1.xlsm"
            mapping = Path(temp) / "mapping.yaml"
            template_hash = build_minimal_xlsm(source)
            write_mapping(mapping, template_hash)

            SafeXlsmWriter(source, mapping).write(target, {"production.number": 105})

            with zipfile.ZipFile(source) as before, zipfile.ZipFile(target) as after:
                original_workbook = before.read("xl/workbook.xml")
                generated_workbook = after.read("xl/workbook.xml")
                original_sheet = before.read("xl/worksheets/sheet1.xml")
                generated_sheet = after.read("xl/worksheets/sheet1.xml")

            self.assertEqual(original_workbook.split(b"><sheets", 1)[0], generated_workbook.split(b"><sheets", 1)[0])
            self.assertEqual(original_sheet.split(b"><dimension", 1)[0], generated_sheet.split(b"><dimension", 1)[0])
            self.assertIn(b'mc:Ignorable="x14ac xr"', generated_sheet)
            self.assertIn(b'xmlns:x14ac=', generated_sheet)
            self.assertIn(b'xmlns:xr=', generated_sheet)
            self.assertNotIn(b"xmlns:ns", generated_sheet)
            self.assertEqual(validate_output_file(target), [])

    def test_validator_detects_lost_markup_compatibility_prefixes(self):
        damaged = b'''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:ns1="http://schemas.openxmlformats.org/markup-compatibility/2006" ns1:Ignorable="x14ac xr"><sheetData/></worksheet>'''
        self.assertEqual(_missing_ignorable_prefixes(damaged), ["x14ac", "xr"])

    def test_writer_rejects_formula_cell_even_if_mapping_claims_it_is_editable(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "template.xlsm"
            target = Path(temp) / "output.xlsm"
            mapping = Path(temp) / "mapping.yaml"
            template_hash = build_minimal_xlsm(source)
            write_mapping(mapping, template_hash, cell="B1")
            with self.assertRaises(UnsafeWriteError):
                SafeXlsmWriter(source, mapping).write(target, {"production.number": 1})

    def test_writer_clears_authorized_stale_input_when_value_is_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "template.xlsm"
            target = Path(temp) / "output.xlsm"
            mapping = Path(temp) / "mapping.yaml"
            template_hash = build_minimal_xlsm(source)
            write_mapping(mapping, template_hash, field="optional.value")
            data = yaml.safe_load(mapping.read_text(encoding="utf-8"))
            data["mappings"][0]["clear_if_missing"] = True
            mapping.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            SafeXlsmWriter(source, mapping).write(target, {})
            with zipfile.ZipFile(target) as package:
                root = ET.fromstring(package.read("xl/worksheets/sheet1.xml"))
                cell = root.find(".//m:c[@r='A1']", NS)
                self.assertIsNone(cell.find("m:is", NS))
                self.assertIsNone(cell.find("m:v", NS))

    def test_unvalidated_mapping_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            mapping = Path(temp) / "mapping.yaml"
            mapping.write_text("status: blocked\nmappings: []\n", encoding="utf-8")
            with self.assertRaises(MappingError):
                load_mapping(mapping)
