from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET


NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
FORMULA_ERRORS = {"#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A"}
CELL_REF_RE = re.compile(r"^\$?[A-Z]{1,3}\$?[1-9][0-9]*$")
SHEET_REF_RE = re.compile(r"(?:'((?:[^']|'')+)'|([A-Za-z0-9_.]+))!")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _xml(data: bytes):
    return ET.fromstring(data)


def _resolve_target(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = []
    for part in (PurePosixPath(base).parent / target).parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


class XlsmAnalyzer:
    def __init__(self, path):
        self.path = Path(path)

    def analyze(self):
        if not self.path.is_file():
            raise FileNotFoundError(f"No se encontró la plantilla: {self.path}")
        if self.path.suffix.lower() != ".xlsm":
            raise ValueError("La plantilla debe tener extensión .xlsm")
        if not zipfile.is_zipfile(self.path):
            raise ValueError("El archivo no es un paquete Open XML/ZIP válido")
        with zipfile.ZipFile(self.path) as package:
            names = set(package.namelist())
            required = {"[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
            missing = sorted(required - names)
            if missing:
                raise ValueError(f"Paquete incompleto; faltan: {', '.join(missing)}")
            workbook = _xml(package.read("xl/workbook.xml"))
            relationships = self._relationships(package, "xl/_rels/workbook.xml.rels", "xl/workbook.xml")
            shared_strings = self._shared_strings(package)
            defined_names = self._defined_names(workbook)
            sheets = []
            for index, sheet in enumerate(workbook.findall("m:sheets/m:sheet", NS), start=1):
                rel_id = sheet.attrib.get(f"{{{NS['r']}}}id")
                path = relationships.get(rel_id)
                item = self._analyze_sheet(package, path, shared_strings) if path in names else {"error": "XML de hoja ausente"}
                item.update(
                    {
                        "index": index,
                        "name": sheet.attrib["name"],
                        "state": sheet.attrib.get("state", "visible"),
                        "sheet_id": sheet.attrib.get("sheetId"),
                        "xml_path": path,
                        "print_areas": [d["value"] for d in defined_names if d["name"] == "_xlnm.Print_Area" and d.get("local_sheet_id") == index - 1],
                    }
                )
                sheets.append(item)
            calc = workbook.find("m:calcPr", NS)
            macro_parts = sorted(name for name in names if name.lower().endswith("vbaproject.bin"))
            media = sorted(name for name in names if name.startswith("xl/media/"))
            drawings = sorted(name for name in names if name.startswith("xl/drawings/") and name.endswith(".xml"))
            comments = sorted(name for name in names if "comments" in name.lower() and name.endswith(".xml"))
            return {
                "path": str(self.path.resolve()),
                "filename": self.path.name,
                "sha256": sha256_file(self.path),
                "size_bytes": self.path.stat().st_size,
                "zip_entries": len(names),
                "has_vba": bool(macro_parts),
                "vba_parts": macro_parts,
                "media_parts": media,
                "drawing_parts": drawings,
                "comment_parts": comments,
                "calculation": calc.attrib if calc is not None else {},
                "defined_names": defined_names,
                "sheets": sheets,
                "totals": {
                    "sheets": len(sheets),
                    "visible": sum(sheet["state"] == "visible" for sheet in sheets),
                    "hidden": sum(sheet["state"] != "visible" for sheet in sheets),
                    "formula_cells": sum(sheet.get("formula_count", 0) for sheet in sheets),
                    "non_formula_cells": sum(sheet.get("non_formula_count", 0) for sheet in sheets),
                    "merged_ranges": sum(len(sheet.get("merged_ranges", [])) for sheet in sheets),
                },
            }

    @staticmethod
    def _relationships(package, rels_path, source_path):
        root = _xml(package.read(rels_path))
        return {
            rel.attrib["Id"]: _resolve_target(source_path, rel.attrib["Target"])
            for rel in root.findall("pr:Relationship", NS)
            if rel.attrib.get("TargetMode") != "External"
        }

    @staticmethod
    def _shared_strings(package):
        if "xl/sharedStrings.xml" not in package.namelist():
            return []
        root = _xml(package.read("xl/sharedStrings.xml"))
        return ["".join(node.text or "" for node in item.iter(f"{{{NS['m']}}}t")) for item in root.findall("m:si", NS)]

    @staticmethod
    def _defined_names(workbook):
        result = []
        for node in workbook.findall("m:definedNames/m:definedName", NS):
            result.append(
                {
                    "name": node.attrib.get("name", ""),
                    "value": node.text or "",
                    "local_sheet_id": int(node.attrib["localSheetId"]) if "localSheetId" in node.attrib else None,
                    "hidden": node.attrib.get("hidden") == "1",
                    "damaged": any(error in (node.text or "") for error in FORMULA_ERRORS),
                }
            )
        return result

    def _analyze_sheet(self, package, path, shared_strings):
        root = _xml(package.read(path))
        formulas = []
        values = []
        errors = []
        style_usage = Counter()
        dependencies = set()
        for cell in root.findall(".//m:sheetData/m:row/m:c", NS):
            ref = cell.attrib.get("r")
            if not ref or not CELL_REF_RE.match(ref):
                continue
            formula = cell.find("m:f", NS)
            value_node = cell.find("m:v", NS)
            value = self._cell_value(cell, value_node, shared_strings)
            style_usage[cell.attrib.get("s", "0")] += 1
            if formula is not None:
                formula_text = formula.text or ""
                formulas.append({"cell": ref, "formula": formula_text, "cached_value": value})
                for match in SHEET_REF_RE.finditer(formula_text):
                    dependencies.add((match.group(1) or match.group(2)).replace("''", "'"))
            else:
                values.append({"cell": ref, "value": value, "type": cell.attrib.get("t", "n"), "style": cell.attrib.get("s")})
            if str(value) in FORMULA_ERRORS:
                errors.append({"cell": ref, "error": value})
        merged = [node.attrib["ref"] for node in root.findall("m:mergeCells/m:mergeCell", NS)]
        protection = root.find("m:sheetProtection", NS)
        data_validations = root.find("m:dataValidations", NS)
        relation_types = []
        rels_path = str(PurePosixPath(path).parent / "_rels" / f"{PurePosixPath(path).name}.rels")
        if rels_path in package.namelist():
            rel_root = _xml(package.read(rels_path))
            relation_types = [rel.attrib.get("Type", "").rsplit("/", 1)[-1] for rel in rel_root.findall("pr:Relationship", NS)]
        dimension = root.find("m:dimension", NS)
        return {
            "dimension": dimension.attrib.get("ref") if dimension is not None else None,
            "formula_count": len(formulas),
            "non_formula_count": len(values),
            "formulas": formulas,
            "non_formula_cells": values,
            "merged_ranges": merged,
            "protected": protection is not None,
            "protection_attributes": protection.attrib if protection is not None else {},
            "data_validation_count": int(data_validations.attrib.get("count", len(list(data_validations)))) if data_validations is not None else 0,
            "error_cells": errors,
            "dependencies": sorted(dependencies),
            "relationship_types": sorted(relation_types),
            "style_usage": dict(style_usage),
        }

    @staticmethod
    def _cell_value(cell, value_node, shared_strings):
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            return "".join(node.text or "" for node in cell.iter(f"{{{NS['m']}}}t"))
        if value_node is None:
            return None
        raw = value_node.text
        if cell_type == "s" and raw is not None:
            try:
                return shared_strings[int(raw)]
            except (ValueError, IndexError):
                return raw
        if cell_type == "b":
            return raw == "1"
        return raw

    @staticmethod
    def save_json(report, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
