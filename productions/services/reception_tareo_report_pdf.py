from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from .reception_tareo_report import (
    ReceptionTareoReportError,
    build_reception_tareo_xlsx,
)


class ReceptionTareoPdfError(ReceptionTareoReportError):
    pass


PDF_LAYOUTS = {
    "POTA ENTERA": {
        "print_area": "B2:V80",
        "orientation": "landscape",
    },
    "CUADRILLA 1": {
        "print_area": "B2:P47",
        "orientation": "portrait",
    },
    "CUADRILLA 2": {
        "print_area": "B2:P47",
        "orientation": "portrait",
    },
}


def _prepare_tareo_xlsx_for_pdf(xlsx_payload: bytes) -> bytes:
    """Keep the official workbook intact and only define its printed pages."""
    workbook = load_workbook(BytesIO(xlsx_payload), keep_links=False)
    for sheet_name, layout in PDF_LAYOUTS.items():
        worksheet = workbook[sheet_name]
        worksheet.print_area = layout["print_area"]
        worksheet.page_setup.orientation = layout["orientation"]
        worksheet.page_setup.paperSize = worksheet.PAPERSIZE_A4
        worksheet.sheet_properties.pageSetUpPr.fitToPage = True
        worksheet.page_setup.fitToWidth = 1
        worksheet.page_setup.fitToHeight = 1
        worksheet.page_setup.scale = None
        worksheet.sheet_view.showGridLines = False

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _libreoffice_binary() -> str:
    binary = shutil.which("soffice") or shutil.which("libreoffice")
    if not binary:
        raise ReceptionTareoPdfError(
            "No se encontró LibreOffice para convertir el tareo oficial a PDF."
        )
    return binary


def _xlsx_to_pdf_payload(xlsx_payload: bytes, *, stem: str) -> bytes:
    binary = _libreoffice_binary()
    with tempfile.TemporaryDirectory(prefix="reception-tareo-pdf-") as temp_name:
        temp_dir = Path(temp_name)
        profile_dir = temp_dir / "libreoffice-profile"
        profile_dir.mkdir()
        input_path = temp_dir / f"{stem}.xlsx"
        output_path = temp_dir / f"{stem}.pdf"
        input_path.write_bytes(_prepare_tareo_xlsx_for_pdf(xlsx_payload))

        result = subprocess.run(
            [
                binary,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
                "--convert-to",
                "pdf:calc_pdf_Export",
                "--outdir",
                str(temp_dir),
                str(input_path),
            ],
            cwd=temp_dir,
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        if result.returncode != 0 or not output_path.is_file():
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            detail = stderr or stdout or "LibreOffice no generó el PDF."
            raise ReceptionTareoPdfError(
                f"No se pudo convertir el tareo oficial a PDF: {detail[:300]}"
            )

        payload = output_path.read_bytes()
        if not payload.startswith(b"%PDF"):
            raise ReceptionTareoPdfError("El tareo generado no es un PDF válido.")
        return payload


def build_reception_tareo_pdf(production) -> bytes:
    xlsx_payload = build_reception_tareo_xlsx(production)
    return _xlsx_to_pdf_payload(
        xlsx_payload,
        stem=f"FILETEROS-POTA_TAREO_PP_{production.number}",
    )
