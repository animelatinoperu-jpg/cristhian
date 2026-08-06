from pathlib import Path
import json
import yaml


class MappingError(ValueError):
    pass


def load_mapping(path):
    path = Path(path)
    if not path.is_file():
        raise MappingError(f"No existe el mapa Excel: {path}")
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw) if raw.lstrip().startswith("{") else (yaml.safe_load(raw) or {})
    if data.get("status") != "validated":
        raise MappingError("El mapa Excel todavía no fue validado contra la plantilla real.")
    entries = data.get("mappings") or []
    if not entries:
        raise MappingError("El mapa validado no contiene celdas autorizadas.")
    for entry in entries:
        required = {"sheet", "field", "cell", "editable", "contains_formula"}
        if missing := required - entry.keys():
            raise MappingError(f"Entrada incompleta; faltan: {', '.join(sorted(missing))}")
        if entry["editable"] and entry["contains_formula"]:
            raise MappingError(f"La celda {entry['sheet']}!{entry['cell']} no puede ser editable y fórmula.")
    return data


def authorized_updates(mapping, values):
    updates = {}
    for entry in mapping["mappings"]:
        if not entry.get("editable") or entry.get("contains_formula"):
            continue
        field = entry["field"]
        if field in values:
            updates[(entry["sheet"], entry["cell"])] = values[field]
        elif entry.get("clear_if_missing"):
            updates[(entry["sheet"], entry["cell"])] = None
    return updates
