from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .analyzer import NS, XlsmAnalyzer


class IntegrityChecker:
    def __init__(self, original, generated, authorized_cells, formula_overrides=None):
        self.original = Path(original)
        self.generated = Path(generated)
        self.authorized_cells = {(sheet, cell.replace("$", "")) for sheet, cell in authorized_cells}
        self.formula_overrides = {
            (sheet, cell.replace("$", "")): (
                str(override["formula"])
                if isinstance(override, dict)
                else str(override)
            )
            for (sheet, cell), override in (formula_overrides or {}).items()
        }
        self.authorized_cells.update(self.formula_overrides)

    def check(self):
        issues = []
        try:
            original_report = XlsmAnalyzer(self.original).analyze()
            generated_report = XlsmAnalyzer(self.generated).analyze()
        except Exception as exc:
            return {"valid": False, "issues": [str(exc)]}
        for key in ("has_vba", "vba_parts", "media_parts", "drawing_parts", "comment_parts"):
            if original_report[key] != generated_report[key]:
                issues.append(f"Cambió el componente estructural: {key}")
        original_sheets = [(s["name"], s["state"]) for s in original_report["sheets"]]
        generated_sheets = [(s["name"], s["state"]) for s in generated_report["sheets"]]
        if original_sheets != generated_sheets:
            issues.append("Cambió el nombre, orden o visibilidad de las hojas.")
        if original_report["defined_names"] != generated_report["defined_names"]:
            issues.append("Cambiaron los nombres definidos del libro.")
        with zipfile.ZipFile(self.original) as before, zipfile.ZipFile(self.generated) as after:
            if set(before.namelist()) != set(after.namelist()):
                issues.append("Cambió la lista de componentes del paquete XLSM.")
            for part in original_report["vba_parts"]:
                if self._hash(before.read(part)) != self._hash(after.read(part)):
                    issues.append(f"Se modificó VBA: {part}")
            editable_parts = {"xl/workbook.xml"} | {sheet["xml_path"] for sheet in original_report["sheets"]}
            for part in set(before.namelist()) - editable_parts:
                if part in after.namelist() and self._hash(before.read(part)) != self._hash(after.read(part)):
                    issues.append(f"Cambió un componente no editable: {part}")
            if self._normalized_workbook(before.read("xl/workbook.xml")) != self._normalized_workbook(after.read("xl/workbook.xml")):
                issues.append("Cambió workbook.xml fuera de la configuración de recálculo.")
            original_by_name = {s["name"]: s for s in original_report["sheets"]}
            generated_by_name = {s["name"]: s for s in generated_report["sheets"]}
            for sheet_name, source_sheet in original_by_name.items():
                target_sheet = generated_by_name.get(sheet_name)
                if not target_sheet:
                    continue
                if source_sheet.get("merged_ranges") != target_sheet.get("merged_ranges"):
                    issues.append(f"Cambiaron rangos combinados en {sheet_name}.")
                source_formulas = {item["cell"]: item["formula"] for item in source_sheet.get("formulas", [])}
                target_formulas = {item["cell"]: item["formula"] for item in target_sheet.get("formulas", [])}
                expected_formulas = dict(source_formulas)
                for (override_sheet, cell), formula in self.formula_overrides.items():
                    if override_sheet != sheet_name:
                        continue
                    if cell not in source_formulas:
                        issues.append(f"La fórmula autorizada no existe en la plantilla: {sheet_name}!{cell}")
                        continue
                    expected_formulas[cell] = formula
                if expected_formulas != target_formulas:
                    issues.append(f"Cambiaron fórmulas no autorizadas en {sheet_name}.")
                issues.extend(self._check_cell_changes(before.read(source_sheet["xml_path"]), after.read(target_sheet["xml_path"]), sheet_name))
        return {
            "valid": not issues,
            "issues": issues,
            "original_sha256": original_report["sha256"],
            "generated_sha256": generated_report["sha256"],
            "sheet_count": len(generated_report["sheets"]),
            "has_vba": generated_report["has_vba"],
        }

    def _check_cell_changes(self, before_data, after_data, sheet_name):
        before_root = ET.fromstring(before_data)
        after_root = ET.fromstring(after_data)
        before = self._cell_payloads_from_root(before_root)
        after = self._cell_payloads_from_root(after_root)
        issues = []
        for ref in set(before) | set(after):
            if before.get(ref) != after.get(ref) and (sheet_name, ref) not in self.authorized_cells:
                issues.append(f"Cambio no autorizado en {sheet_name}!{ref}")
        authorized_refs = {cell for sheet, cell in self.authorized_cells if sheet == sheet_name}
        for ref in authorized_refs:
            source = before_root.find(f".//m:c[@r='{ref}']", NS)
            target = after_root.find(f".//m:c[@r='{ref}']", NS)
            if source is not None and target is not None:
                parent = next((row for row in after_root.findall(".//m:sheetData/m:row", NS) if target in list(row)), None)
                if parent is not None:
                    index = list(parent).index(target)
                    parent.remove(target)
                    parent.insert(index, ET.fromstring(ET.tostring(source, encoding="utf-8")))
        if ET.tostring(before_root, encoding="utf-8") != ET.tostring(after_root, encoding="utf-8"):
            issues.append(f"Cambió XML estructural fuera de celdas autorizadas en {sheet_name}.")
        return issues

    @staticmethod
    def _cell_payloads(data):
        root = ET.fromstring(data)
        return IntegrityChecker._cell_payloads_from_root(root)

    @staticmethod
    def _cell_payloads_from_root(root):
        return {
            cell.attrib["r"]: ET.tostring(cell, encoding="utf-8")
            for cell in root.findall(".//m:sheetData/m:row/m:c", NS)
            if "r" in cell.attrib
        }

    @staticmethod
    def _normalized_workbook(data):
        root = ET.fromstring(data)
        calc = root.find("m:calcPr", NS)
        if calc is not None:
            root.remove(calc)
        return ET.tostring(root, encoding="utf-8")

    @staticmethod
    def _hash(data):
        return hashlib.sha256(data).hexdigest()
