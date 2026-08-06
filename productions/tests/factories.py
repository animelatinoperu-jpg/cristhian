import hashlib
import zipfile
from pathlib import Path

import yaml


def build_minimal_xlsm(path: Path):
    parts = {
        "[Content_Types].xml": b'''<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.ms-excel.sheet.macroEnabled.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/vbaProject.bin" ContentType="application/vnd.ms-office.vbaProject"/></Types>''',
        "_rels/.rels": b'''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''',
        "xl/workbook.xml": b'''<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="x15 xr" xmlns:x15="http://schemas.microsoft.com/office/spreadsheetml/2010/11/main" xmlns:xr="http://schemas.microsoft.com/office/spreadsheetml/2014/revision"><sheets><sheet name="PP" sheetId="1" r:id="rId1"/></sheets><definedNames><definedName name="_xlnm.Print_Area" localSheetId="0">PP!$A$1:$C$1</definedName></definedNames><calcPr calcMode="manual"/></workbook>''',
        "xl/_rels/workbook.xml.rels": b'''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.microsoft.com/office/2006/relationships/vbaProject" Target="vbaProject.bin"/></Relationships>''',
        "xl/worksheets/sheet1.xml": b'''<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="x14ac xr" xmlns:x14ac="http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac" xmlns:xr="http://schemas.microsoft.com/office/spreadsheetml/2014/revision" xr:uid="{37D560FA-1D6B-408B-9F66-BA797DFF6286}"><dimension ref="A1:C1"/><sheetData><row r="1"><c r="A1" s="1" t="inlineStr"><is><t>VACIO</t></is></c><c r="B1" s="1"><f>A1*2</f><v>0</v></c><c r="C1" s="1" t="inlineStr"><is><t>ESTRUCTURA</t></is></c></row></sheetData><mergeCells count="1"><mergeCell ref="B1:C1"/></mergeCells></worksheet>''',
        "xl/vbaProject.bin": b"FAKE-VBA-FIXTURE-BYTES",
        "xl/styles.xml": b'''<?xml version="1.0"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>''',
        "xl/media/image1.png": b"FAKE-PNG",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        for name, data in parts.items():
            package.writestr(name, data)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_mapping(path: Path, template_hash: str, *, cell="A1", field="production.number"):
    data = {
        "version": "v1",
        "status": "validated",
        "template": {"filename": "template.xlsm", "sha256": template_hash},
        "mappings": [
            {
                "sheet": "PP",
                "module": "production",
                "field": field,
                "cell": cell,
                "data_type": "integer",
                "editable": True,
                "contains_formula": False,
                "validation": {},
                "conversions": {},
                "dependencies": [],
            }
        ],
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
