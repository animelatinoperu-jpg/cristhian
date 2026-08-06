from __future__ import annotations

import datetime as dt
from decimal import Decimal
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from .analyzer import NS, XlsmAnalyzer
from .mapper import authorized_updates, load_mapping


ET.register_namespace("", NS["m"])
ET.register_namespace("r", NS["r"])


class UnsafeWriteError(ValueError):
    pass


class SafeXlsmWriter:
    def __init__(self, template_path, mapping_path):
        self.template_path = Path(template_path)
        self.mapping_path = Path(mapping_path)

    def write(self, output_path, values, formula_overrides=None, cell_overrides=None):
        report = XlsmAnalyzer(self.template_path).analyze()
        if not report["has_vba"]:
            raise UnsafeWriteError("La plantilla no contiene xl/vbaProject.bin; se rechaza la generación XLSM.")
        mapping = load_mapping(self.mapping_path)
        if mapping.get("template", {}).get("sha256") != report["sha256"]:
            raise UnsafeWriteError("El hash de la plantilla no coincide con el mapa validado.")
        updates = authorized_updates(mapping, values)
        if not updates:
            raise UnsafeWriteError("No hay valores asociados a celdas editables autorizadas.")
        normalized_formula_overrides = {}
        for (sheet, cell), override in (formula_overrides or {}).items():
            if isinstance(override, dict):
                normalized = {
                    "formula": str(override["formula"]),
                    "cached_value": override.get("cached_value"),
                }
            else:
                normalized = {"formula": str(override), "cached_value": None}
            normalized_formula_overrides[(sheet, cell.replace("$", ""))] = normalized
        normalized_cell_overrides = {
            (sheet, cell.replace("$", "")): value
            for (sheet, cell), value in (cell_overrides or {}).items()
        }
        for key, value in normalized_cell_overrides.items():
            if key in updates and updates[key] != value:
                raise UnsafeWriteError(f"Hay dos valores distintos autorizados para {key[0]}!{key[1]}.")
            updates[key] = value
        sheet_paths = {sheet["name"]: sheet["xml_path"] for sheet in report["sheets"]}
        grouped = {}
        for (sheet, cell), value in updates.items():
            if sheet not in sheet_paths:
                raise UnsafeWriteError(f"El mapa referencia una hoja inexistente: {sheet}")
            grouped.setdefault(sheet_paths[sheet], {})[cell.replace("$", "")] = value
        grouped_formulas = {}
        for (sheet, cell), override in normalized_formula_overrides.items():
            if sheet not in sheet_paths:
                raise UnsafeWriteError(f"La fórmula autorizada referencia una hoja inexistente: {sheet}")
            grouped_formulas.setdefault(sheet_paths[sheet], {})[cell] = override
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".xlsm", delete=False, dir=output_path.parent) as temp:
            temp_path = Path(temp.name)
        try:
            with zipfile.ZipFile(self.template_path, "r") as source, zipfile.ZipFile(temp_path, "w") as target:
                for info in source.infolist():
                    data = source.read(info.filename)
                    if info.filename in grouped or info.filename in grouped_formulas:
                        data = self._update_sheet(
                            data,
                            grouped.get(info.filename, {}),
                            grouped_formulas.get(info.filename, {}),
                        )
                    elif info.filename == "xl/workbook.xml":
                        data = self._set_recalculation(data)
                    target.writestr(info, data)
            shutil.move(str(temp_path), str(output_path))
        finally:
            temp_path.unlink(missing_ok=True)
        return output_path, set(updates) | set(normalized_formula_overrides)

    @staticmethod
    def _update_sheet(data, updates, formula_overrides=None):
        root = ET.fromstring(data)
        cells = {cell.attrib.get("r"): cell for cell in root.findall(".//m:sheetData/m:row/m:c", NS)}
        for ref, value in updates.items():
            cell = cells.get(ref)
            if cell is None:
                raise UnsafeWriteError(f"La celda autorizada {ref} no existe físicamente en la plantilla.")
            if cell.find("m:f", NS) is not None:
                raise UnsafeWriteError(f"Se intentó escribir sobre la fórmula {ref}.")
            data = SafeXlsmWriter._replace_cell_payload(data, ref, value)
        for ref, override in (formula_overrides or {}).items():
            cell = cells.get(ref)
            if cell is None:
                raise UnsafeWriteError(f"La celda de fórmula autorizada {ref} no existe en la plantilla.")
            if cell.find("m:f", NS) is None:
                raise UnsafeWriteError(f"La celda autorizada {ref} no contiene una fórmula.")
            data = SafeXlsmWriter._replace_formula_payload(
                data,
                ref,
                override["formula"],
                cached_value=override.get("cached_value"),
            )
        return data

    @staticmethod
    def _replace_formula_payload(data, ref, formula, cached_value=None):
        if not formula:
            raise UnsafeWriteError(f"La fórmula autorizada para {ref} está vacía.")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", formula):
            raise UnsafeWriteError(f"La fórmula autorizada para {ref} contiene caracteres no permitidos.")
        encoded_ref = re.escape(ref.encode("ascii"))
        cell_pattern = re.compile(
            rb'(?P<open><c\b(?=[^>]*\br=["\']' + encoded_ref + rb'["\'])[^>]*?)>(?P<body>.*?)</c>',
            re.DOTALL,
        )
        match = cell_pattern.search(data)
        if match is None:
            raise UnsafeWriteError(f"No se pudo localizar de forma segura la fórmula {ref}.")
        escaped_formula = escape(formula).encode("utf-8")
        body, count = re.subn(
            rb'(?P<open><f\b[^>]*>).*?(?P<close></f>)',
            lambda item: item.group("open") + escaped_formula + item.group("close"),
            match.group("body"),
            count=1,
            flags=re.DOTALL,
        )
        if count != 1:
            raise UnsafeWriteError(f"No se pudo actualizar de forma segura la fórmula {ref}.")
        body = re.sub(rb"<v\b[^>]*>.*?</v>", b"", body, count=1, flags=re.DOTALL)
        opening = match.group("open")
        if cached_value is not None:
            if isinstance(cached_value, str):
                opening = re.sub(rb'\s+t\s*=\s*(?:"[^"]*"|\'[^\']*\')', b"", opening).rstrip()
                opening += b' t="str"'
                cached = escape(cached_value).encode("utf-8")
            elif isinstance(cached_value, bool):
                cached = b"1" if cached_value else b"0"
            else:
                cached = str(cached_value).encode("ascii")
            body += b"<v>" + cached + b"</v>"
        # Si no hay valor calculado se elimina el cache anterior para obligar a
        # Excel a recalcularlo. Cuando sí lo hay, queda visible de inmediato.
        replacement = opening + b">" + body + b"</c>"
        return data[: match.start()] + replacement + data[match.end() :]

    @staticmethod
    def _replace_cell_payload(data, ref, value):
        # ElementTree is intentionally used only for validation above. Serializing a
        # complete Excel worksheet renames Microsoft extension prefixes (mc, x14ac,
        # xr, etc.) while mc:Ignorable still references their original names. Excel
        # then reports the otherwise valid ZIP package as damaged. Replace only the
        # authorized <c> element payload so every other byte remains untouched.
        encoded_ref = re.escape(ref.encode("ascii"))
        pattern = re.compile(
            rb'(?P<open><c\b(?=[^>]*\br=["\']' + encoded_ref + rb'["\'])[^>]*?)(?:\s*/>|>(?P<body>.*?)</c>)',
            re.DOTALL,
        )
        cell_type, payload = SafeXlsmWriter._cell_payload(value)

        def replacement(match):
            opening = re.sub(rb'\s+t\s*=\s*(?:"[^"]*"|\'[^\']*\')', b"", match.group("open")).rstrip()
            if cell_type:
                opening += b' t="' + cell_type + b'"'
            return opening + b">" + payload + b"</c>"

        updated, count = pattern.subn(replacement, data, count=1)
        if count != 1:
            raise UnsafeWriteError(f"No se pudo actualizar de forma segura la celda {ref}.")
        return updated

    @staticmethod
    def _cell_payload(value):
        if value is None:
            return None, b""
        if isinstance(value, bool):
            return b"b", b"<v>1</v>" if value else b"<v>0</v>"
        elif isinstance(value, (int, float, Decimal)):
            return None, f"<v>{value}</v>".encode("ascii")
        elif isinstance(value, dt.datetime):
            value = value.replace(tzinfo=None)
            serial = (value - dt.datetime(1899, 12, 30)).total_seconds() / 86400
            return None, f"<v>{serial}</v>".encode("ascii")
        elif isinstance(value, dt.date):
            serial = (value - dt.date(1899, 12, 30)).days
            return None, f"<v>{serial}</v>".encode("ascii")
        elif isinstance(value, dt.time):
            serial = (value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1_000_000) / 86400
            return None, f"<v>{serial}</v>".encode("ascii")
        else:
            text = str(value)
            if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
                raise UnsafeWriteError("El valor contiene caracteres de control no permitidos por Excel.")
            attributes = ' xml:space="preserve"' if text.startswith(" ") or text.endswith(" ") else ""
            escaped = escape(text).replace("\r", "&#13;")
            return b"inlineStr", f"<is><t{attributes}>{escaped}</t></is>".encode("utf-8")

    @staticmethod
    def _set_recalculation(data):
        replacement = b'<calcPr calcMode="auto" fullCalcOnLoad="1" forceFullCalc="1" calcId="0"/>'
        updated, count = re.subn(rb"<calcPr\b[^>]*(?:/>|>.*?</calcPr>)", replacement, data, count=1, flags=re.DOTALL)
        if count:
            return updated
        closing = data.rfind(b"</workbook>")
        if closing < 0:
            raise UnsafeWriteError("workbook.xml no contiene un cierre válido.")
        return data[:closing] + replacement + data[closing:]
