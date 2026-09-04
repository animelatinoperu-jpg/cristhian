import json
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from productions.models import Product, TemplateVersion, User
from productions.services.template_catalog import sync_template_catalog


def _write_report(tmp_path, color_value=""):
    """Reporte minimo con una sola hoja COLORES; las demas hojas que
    sync_template_catalog busca (T1..T6, ENV. PLACAS, NUQUERAS, RE-COS,
    etc.) simplemente no existen y cada extractor las trata como vacias."""
    report = {
        "sheets": [
            {
                "name": "COLORES",
                "non_formula_cells": [
                    {"cell": "H4", "value": "ANILLAS BLANCAS SM ST"},
                    {"cell": "L4", "value": "PP-001"},
                    {"cell": "I4", "value": 10},
                    {"cell": "J4", "value": 0.17},
                    {"cell": "K4", "value": 11.7},
                    {"cell": "M4", "value": color_value},
                ],
            }
        ]
    }
    path = tmp_path / "reporte.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


class SyncTemplateCatalogColorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("uploader", password="Secure-test-123")
        self.template = TemplateVersion.objects.create(
            code="PP-TEST",
            file=SimpleUploadedFile("t.xlsm", b"x"),
            original_filename="t.xlsm",
            sha256="d" * 64,
            uploaded_by=self.user,
            rules={},
        )

    def test_syncing_with_an_empty_template_color_does_not_erase_the_saved_one(self):
        # El usuario asigna el color a mano en Catalogos > Laminas por
        # codigo. Antes, sync_template_catalog (usado por el comando de
        # mantenimiento ensure_reference_data) sobrescribia siempre el color
        # con lo que trajera la plantilla, y como esa columna suele venir
        # vacia, el color guardado se borraba cada vez que se corria ese
        # comando.
        product = Product.objects.create(
            code="PP-001",
            description="ANILLAS BLANCAS SM ST",
            color="AZUL",
        )
        report_path = _write_report(Path(self.template.file.storage.location), color_value="")

        sync_template_catalog(self.template, report_path)

        product.refresh_from_db()
        self.assertEqual(product.color, "AZUL")

    def test_syncing_with_an_explicit_template_color_still_updates_it(self):
        # Si la plantilla SI trae un color explicito, se sigue aplicando
        # (comportamiento util si algun dia se decide fijar el color desde
        # la plantilla en vez de la pantalla de Laminas).
        product = Product.objects.create(
            code="PP-001",
            description="ANILLAS BLANCAS SM ST",
            color="AZUL",
        )
        report_path = _write_report(Path(self.template.file.storage.location), color_value="ROJO")

        sync_template_catalog(self.template, report_path)

        product.refresh_from_db()
        self.assertEqual(product.color, "ROJO")
