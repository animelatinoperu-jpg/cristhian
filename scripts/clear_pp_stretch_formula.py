"""Quita únicamente la fórmula histórica de PP!E77 sin reserializar el XLSM."""

from __future__ import annotations

import argparse
import hashlib
import os
import posixpath
import re
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def worksheet_path(package: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(package.read("xl/workbook.xml"))
    sheet = next(
        item
        for item in workbook.findall(f".//{{{MAIN_NS}}}sheet")
        if item.attrib.get("name") == sheet_name
    )
    relationship_id = sheet.attrib[f"{{{REL_NS}}}id"]
    relationships = ET.fromstring(package.read("xl/_rels/workbook.xml.rels"))
    relationship = next(
        item
        for item in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
        if item.attrib.get("Id") == relationship_id
    )
    return posixpath.normpath(posixpath.join("xl", relationship.attrib["Target"]))


def clear_formula(source: Path, sheet_name: str, cell_ref: str, expected_formula: str) -> str:
    temp_path = None
    with zipfile.ZipFile(source, "r") as package:
        macro_digest = hashlib.sha256(package.read("xl/vbaProject.bin")).hexdigest()
        target_sheet = worksheet_path(package, sheet_name)
        worksheet = package.read(target_sheet)
        cell_pattern = re.compile(
            rb'(?P<open><c\b(?=[^>]*\br=["\']'
            + re.escape(cell_ref.encode("ascii"))
            + rb'["\'])[^>]*>)(?P<body>.*?)(?P<close></c>)',
            re.DOTALL,
        )
        match = cell_pattern.search(worksheet)
        if match is None:
            raise RuntimeError(f"No existe físicamente {sheet_name}!{cell_ref}.")
        formula_match = re.search(rb"<f\b[^>]*>(.*?)</f>", match.group("body"), re.DOTALL)
        if formula_match is None:
            updated_worksheet = worksheet
        else:
            actual_formula = formula_match.group(1).decode("utf-8").strip()
            if actual_formula != expected_formula:
                raise RuntimeError(
                    f"Fórmula inesperada en {sheet_name}!{cell_ref}: {actual_formula!r}."
                )
            clean_body = re.sub(rb"<f\b[^>]*>.*?</f>", b"", match.group("body"), count=1, flags=re.DOTALL)
            clean_body = re.sub(rb"<v\b[^>]*>.*?</v>", b"", clean_body, count=1, flags=re.DOTALL)
            clean_cell = match.group("open") + clean_body + match.group("close")
            updated_worksheet = worksheet[: match.start()] + clean_cell + worksheet[match.end() :]

        content_types = re.sub(
            rb'<Override\b(?=[^>]*\bPartName=["\']/xl/calcChain\.xml["\'])[^>]*/>',
            b"",
            package.read("[Content_Types].xml"),
            count=1,
        )
        workbook_rels = re.sub(
            rb'<Relationship\b(?=[^>]*\bType=["\'][^"\']*/calcChain["\'])[^>]*/>',
            b"",
            package.read("xl/_rels/workbook.xml.rels"),
            count=1,
        )
        workbook = package.read("xl/workbook.xml")
        calc_pr = b'<calcPr calcMode="auto" fullCalcOnLoad="1" forceFullCalc="1" calcId="0"/>'
        workbook, count = re.subn(
            rb"<calcPr\b[^>]*(?:/>|>.*?</calcPr>)",
            calc_pr,
            workbook,
            count=1,
            flags=re.DOTALL,
        )
        if not count:
            closing = workbook.rfind(b"</workbook>")
            if closing < 0:
                raise RuntimeError("workbook.xml no contiene un cierre válido.")
            workbook = workbook[:closing] + calc_pr + workbook[closing:]

        replacements = {
            target_sheet: updated_worksheet,
            "[Content_Types].xml": content_types,
            "xl/_rels/workbook.xml.rels": workbook_rels,
            "xl/workbook.xml": workbook,
        }

        with tempfile.NamedTemporaryFile(suffix=".xlsm", delete=False, dir=source.parent) as temp:
            temp_path = Path(temp.name)
        with zipfile.ZipFile(temp_path, "w") as output:
            for info in package.infolist():
                if info.filename == "xl/calcChain.xml":
                    continue
                data = replacements.get(info.filename, package.read(info.filename))
                output.writestr(info, data)

    try:
        os.replace(temp_path, source)
    finally:
        temp_path.unlink(missing_ok=True)

    with zipfile.ZipFile(source, "r") as repaired:
        if "xl/calcChain.xml" in repaired.namelist():
            raise RuntimeError("No se pudo retirar la cadena de cálculo dañada.")
        repaired_macro_digest = hashlib.sha256(repaired.read("xl/vbaProject.bin")).hexdigest()
        if repaired_macro_digest != macro_digest:
            raise RuntimeError("El proyecto de macros cambió durante la reparación.")

    return hashlib.sha256(source.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    digest = clear_formula(args.template, "PP", "E77", "4+7+2+2")
    print(f"Fórmula eliminada de PP!E77. SHA-256: {digest}")


if __name__ == "__main__":
    main()
