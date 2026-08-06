from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from productions.services.packaging_report import (
    PackagingReportError,
    build_plate_packaging_report_xlsx,
    build_tunnel_packaging_report_xlsx,
)


class PackagingReportPdfError(PackagingReportError):
    pass


def _libreoffice_binary():
    binary = shutil.which("soffice") or shutil.which("libreoffice")
    if not binary:
        raise PackagingReportPdfError(
            "No se encontrÃ³ LibreOffice para convertir el Excel oficial a PDF."
        )
    return binary


def _xlsx_to_pdf_payload(xlsx_payload, *, stem):
    binary = _libreoffice_binary()
    with tempfile.TemporaryDirectory(prefix="packaging-report-") as tmp_name:
        tmp_dir = Path(tmp_name)
        profile_dir = tmp_dir / "lo-profile"
        profile_dir.mkdir()
        input_path = tmp_dir / f"{stem}.xlsx"
        input_path.write_bytes(xlsx_payload)
        result = subprocess.run(
            [
                binary,
                "--headless",
                f"-env:UserInstallation=file:///{profile_dir.as_posix()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(tmp_dir),
                str(input_path),
            ],
            cwd=tmp_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        output_path = tmp_dir / f"{stem}.pdf"
        if result.returncode != 0 or not output_path.is_file():
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            detail = stderr or stdout or "LibreOffice no generÃ³ el PDF."
            raise PackagingReportPdfError(f"No se pudo convertir el Excel oficial a PDF: {detail}")
        return output_path.read_bytes()


def build_tunnel_packaging_report_pdf(production):
    xlsx_payload = build_tunnel_packaging_report_xlsx(production)
    return _xlsx_to_pdf_payload(xlsx_payload, stem=f"EMPAQUE_TUNEL_PP_{production.number}")


def build_plate_packaging_report_pdf(production):
    xlsx_payload = build_plate_packaging_report_xlsx(production)
    return _xlsx_to_pdf_payload(xlsx_payload, stem=f"EMPAQUE_PLAQUEROS_PP_{production.number}")
