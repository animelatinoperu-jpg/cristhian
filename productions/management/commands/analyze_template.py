from pathlib import Path
import shutil

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from productions.services.excel.analyzer import FORMULA_ERRORS, XlsmAnalyzer
from productions.models import TemplateVersion
from django.contrib.auth import get_user_model


EXPECTED_SHEETS = ["PP", "RE-COS", "NUQUERAS", "R.M", "T.EMV", "T1", "T2", "T3", "T4", "T5", "T6", "RES-EN", "ENV. PLACAS", "EM-PLA", "EM-TUN", "COLORES", "R-G", "PROYECTADO", "RENDIMIENTO"]


class Command(BaseCommand):
    help = "Inspecciona la plantilla XLSM real y genera los documentos de Fase 0."

    def add_arguments(self, parser):
        parser.add_argument("--template", default=str(settings.TEMPLATE_SOURCE_PATH))
        parser.add_argument("--docs-dir", default=str(settings.BASE_DIR / "docs"))
        parser.add_argument("--mapping", default=str(settings.EXCEL_MAPPING_PATH))
        parser.add_argument("--copy-private", action="store_true")
        parser.add_argument("--register-user", help="Usuario que registrará la versión privada")
        parser.add_argument("--template-version", dest="template_version", default="PP-V1")
        parser.add_argument("--mapping-version", dest="mapping_version", default="v1")

    def handle(self, *args, **options):
        template = Path(options["template"])
        docs_dir = Path(options["docs_dir"])
        mapping_path = Path(options["mapping"])
        docs_dir.mkdir(parents=True, exist_ok=True)
        if not template.is_file():
            self._write_missing_docs(docs_dir, mapping_path, template)
            raise CommandError(f"Plantilla ausente: {template}. Se documentó el bloqueo sin inventar celdas.")
        report = XlsmAnalyzer(template).analyze()
        XlsmAnalyzer.save_json(report, docs_dir / "inventario_excel.json")
        self._write_inventory(docs_dir, report)
        self._write_map(docs_dir, mapping_path, report)
        self._write_flow(docs_dir, report)
        self._write_inconsistencies(docs_dir, report)
        if options["copy_private"]:
            version_code = options["template_version"]
            destination = settings.PRIVATE_TEMPLATE_DIR / version_code / template.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(template, destination)
            self.stdout.write(self.style.SUCCESS(f"Copia privada: {destination}"))
            if options["register_user"]:
                user = get_user_model().objects.get(username=options["register_user"])
                relative_name = destination.relative_to(settings.MEDIA_ROOT).as_posix()
                TemplateVersion.objects.update_or_create(
                    code=version_code,
                    defaults={
                        "file": relative_name,
                        "original_filename": template.name,
                        "sha256": report["sha256"],
                        "uploaded_by": user,
                        "mapping_version": options["mapping_version"],
                        "rules": {
                            "tray_kg": 10,
                            "rack_max_trays": 50,
                            "plate_rack_max_trays": 189,
                            "package_trays": 2,
                            "package_kg": 20,
                            "tunnel_pallet_max": None,
                            "plate_pallet_max": None,
                        },
                    },
                )
                self.stdout.write(self.style.SUCCESS(f"Versión registrada: {version_code}"))
        self.stdout.write(self.style.SUCCESS(f"Analizadas {len(report['sheets'])} hojas. Hash: {report['sha256']}"))

    @staticmethod
    def _write_missing_docs(docs_dir, mapping_path, template):
        absolute = template.resolve()
        inventory = f"""# Inventario de hojas

Estado: **BLOQUEADO — PLANTILLA AUSENTE**

Se buscó la plantilla en `{absolute}` y no existe. No es técnicamente posible enumerar hojas, macros, fórmulas, rangos, dibujos ni protecciones sin el archivo binario real.

No se han inferido nombres ni estructuras a partir del texto del encargo.
"""
        cell_map = """# Mapa de celdas

Estado: **NO GENERADO**

No hay coordenadas autorizadas porque `input/PLANTILLA_PP_V1.xlsm` no está presente. Toda escritura queda deshabilitada hasta ejecutar `python manage.py analyze_template --copy-private` con la plantilla real y validar manualmente los candidatos de entrada.
"""
        flow = """# Flujo entre hojas

Estado: **NO DETERMINADO**

Las dependencias deben extraerse de las fórmulas y nombres definidos del libro real. El motor no supone que T1–T6 o sus segundas llenadas compartan coordenadas.
"""
        inconsistencies = """# Inconsistencias del Excel

## Bloqueo crítico

- `input/PLANTILLA_PP_V1.xlsm` no existe en el workspace, en los adjuntos accesibles ni en el historial Git.
- No se pueden comprobar VBA, errores de fórmula, nombres definidos, impresión, protecciones, imágenes ni dibujos.
- La generación XLSM permanece deshabilitada de forma segura: el mapa tiene estado `blocked`.
"""
        (docs_dir / "INVENTARIO_HOJAS.md").write_text(inventory, encoding="utf-8")
        (docs_dir / "MAPA_CELDAS.md").write_text(cell_map, encoding="utf-8")
        (docs_dir / "FLUJO_ENTRE_HOJAS.md").write_text(flow, encoding="utf-8")
        (docs_dir / "INCONSISTENCIAS_EXCEL.md").write_text(inconsistencies, encoding="utf-8")
        mapping_path.parent.mkdir(parents=True, exist_ok=True)
        mapping_path.write_text(
            yaml.safe_dump(
                {
                    "version": "v1",
                    "status": "blocked",
                    "reason": "input/PLANTILLA_PP_V1.xlsm ausente; no se inventan coordenadas",
                    "template": {"filename": "PLANTILLA_PP_V1.xlsm", "sha256": None},
                    "defaults": {"tray_kg": 10, "rack_max_trays": 50, "plate_rack_max_trays": 189, "package_trays": 2, "package_kg": 20},
                    "mappings": [],
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _write_inventory(docs_dir, report):
        lines = ["# Inventario de hojas", "", f"- Archivo: `{report['filename']}`", f"- SHA-256: `{report['sha256']}`", f"- VBA: {'sí' if report['has_vba'] else 'no'}", f"- Hojas: {len(report['sheets'])}", "", "| # | Hoja | Estado | Dimensión | Fórmulas | Valores | Combinadas | Protegida |", "|---:|---|---|---|---:|---:|---:|---|"]
        for sheet in report["sheets"]:
            lines.append(f"| {sheet['index']} | `{sheet['name']}` | {sheet['state']} | {sheet.get('dimension') or '—'} | {sheet.get('formula_count', 0)} | {sheet.get('non_formula_count', 0)} | {len(sheet.get('merged_ranges', []))} | {'sí' if sheet.get('protected') else 'no'} |")
        lines.extend(["", "## Componentes", "", f"- Macros: {', '.join(report['vba_parts']) or 'ninguna'}", f"- Imágenes: {len(report['media_parts'])}", f"- Dibujos: {len(report['drawing_parts'])}", f"- Comentarios: {len(report['comment_parts'])}", f"- Cálculo: `{report['calculation']}`"])
        (docs_dir / "INVENTARIO_HOJAS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _write_map(docs_dir, mapping_path, report):
        lines = ["# Mapa de celdas", "", "Las celdas sin fórmula se listan como **candidatas**, no como editables. La autorización requiere revisión funcional; esto evita escribir sobre rótulos o estructura.", "", "| Hoja | Celda | Valor actual | Tipo | Estado |", "|---|---|---|---|---|"]
        candidates = []
        for sheet in report["sheets"]:
            for cell in sheet.get("non_formula_cells", []):
                value = str(cell.get("value", "")).replace("|", "\\|").replace("\n", " ")[:80]
                lines.append(f"| `{sheet['name']}` | `{cell['cell']}` | {value} | {cell['type']} | candidato no autorizado |")
                candidates.append({"sheet": sheet["name"], "cell": cell["cell"], "current_value": cell.get("value"), "style": cell.get("style"), "editable": False, "contains_formula": False})
        (docs_dir / "MAPA_CELDAS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        mapping_path.parent.mkdir(parents=True, exist_ok=True)
        mapping_path.write_text(yaml.safe_dump({"version": "v1", "status": "requires_review", "template": {"filename": report["filename"], "sha256": report["sha256"]}, "defaults": {"tray_kg": 10, "rack_max_trays": 50, "plate_rack_max_trays": 189, "package_trays": 2, "package_kg": 20}, "mappings": [], "candidates": candidates}, allow_unicode=True, sort_keys=False), encoding="utf-8")

    @staticmethod
    def _write_flow(docs_dir, report):
        lines = ["# Flujo entre hojas", "", "Dependencias extraídas de referencias explícitas en fórmulas:", "", "| Hoja destino | Hojas referenciadas |", "|---|---|"]
        for sheet in report["sheets"]:
            lines.append(f"| `{sheet['name']}` | {', '.join(f'`{item}`' for item in sheet.get('dependencies', [])) or '—'} |")
        (docs_dir / "FLUJO_ENTRE_HOJAS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _write_inconsistencies(docs_dir, report):
        actual = {sheet["name"] for sheet in report["sheets"]}
        missing_expected = [name for name in EXPECTED_SHEETS if name not in actual]
        lines = ["# Inconsistencias del Excel", "", "## Hallazgos automáticos", ""]
        lines.append(f"- VBA: {'presente' if report['has_vba'] else 'AUSENTE'}.")
        lines.append(f"- Hojas mencionadas en el encargo pero no detectadas: {', '.join(missing_expected) or 'ninguna'}.")
        damaged_names = [item["name"] for item in report["defined_names"] if item["damaged"]]
        lines.append(f"- Nombres definidos dañados: {', '.join(damaged_names) or 'ninguno'}.")
        error_count = 0
        for sheet in report["sheets"]:
            for error in sheet.get("error_cells", []):
                lines.append(f"- `{sheet['name']}!{error['cell']}` contiene `{error['error']}`.")
                error_count += 1
        if not error_count:
            lines.append(f"- No se detectaron valores en caché {', '.join(sorted(FORMULA_ERRORS))}; esto no sustituye recalcular en Excel.")
        lines.extend(["", "## Revisión manual requerida", "", "- Confirmar qué celdas sin fórmula son realmente campos de entrada.", "- Confirmar objetos ActiveX/Form Controls y comportamiento de macros en Microsoft Excel.", "- Verificar visualmente impresión, saltos de página y fórmulas tras recalcular."])
        (docs_dir / "INCONSISTENCIAS_EXCEL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
