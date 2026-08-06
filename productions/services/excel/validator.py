from io import BytesIO
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


FILENAME_RE = re.compile(r"^PP_[0-9]+_[A-Za-z0-9_-]+_(PRELIMINAR|FINAL)_v[0-9]+\.xlsm$")
MC_IGNORABLE = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable"


def _missing_ignorable_prefixes(data):
    root = ET.fromstring(data)
    ignorable = root.attrib.get(MC_IGNORABLE, "").split()
    if not ignorable:
        return []
    declared = {
        prefix
        for _, (prefix, _) in ET.iterparse(BytesIO(data), events=("start-ns",))
        if prefix
    }
    return sorted(set(ignorable) - declared)


def validate_output_file(path):
    path = Path(path)
    issues = []
    if path.suffix.lower() != ".xlsm":
        issues.append("Extensión distinta de .xlsm")
    if not FILENAME_RE.match(path.name):
        issues.append("Nombre de archivo no conforme")
    if not zipfile.is_zipfile(path):
        issues.append("Paquete ZIP/Open XML inválido")
        return issues
    with zipfile.ZipFile(path) as package:
        if "xl/vbaProject.bin" not in package.namelist():
            issues.append("Falta xl/vbaProject.bin")
        for name in package.namelist():
            if not name.endswith(".xml"):
                continue
            try:
                missing = _missing_ignorable_prefixes(package.read(name))
            except ET.ParseError as exc:
                issues.append(f"XML inválido en {name}: {exc}")
                continue
            if missing:
                issues.append(f"Prefijos de compatibilidad no declarados en {name}: {', '.join(missing)}")
    return issues
