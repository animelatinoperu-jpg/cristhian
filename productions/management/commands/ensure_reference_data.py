from hashlib import sha256
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from productions.models import (
    Crew,
    Customer,
    Material,
    Product,
    ProductionOrder,
    Role,
    TemplateVersion,
    Tunnel,
    User,
    Vehicle,
    Worker,
)
from productions.services.template_catalog import sync_template_catalog


TEMPLATE_CODE = "PP-V2"
TEMPLATE_FILENAME = "PLANTILLA_PP_V2.xlsm"


class Command(BaseCommand):
    help = "Restaura de forma idempotente la plantilla validada y los catálogos operativos."

    def handle(self, *args, **options):
        source = Path(settings.BASE_DIR) / "reference_assets" / TEMPLATE_FILENAME
        report = Path(settings.BASE_DIR) / "docs" / "inventario_excel.json"
        if not source.is_file():
            raise CommandError(f"No se encontró la plantilla incorporada: {source}")
        if not report.is_file():
            raise CommandError(f"No se encontró el inventario de la plantilla: {report}")

        uploader = User.objects.filter(is_superuser=True, is_active=True).order_by("pk").first()
        if uploader is None:
            raise CommandError("No existe un administrador activo para registrar la plantilla.")

        digest = sha256(source.read_bytes()).hexdigest()
        with transaction.atomic():
            template = self._ensure_template(source, digest, uploader)
            upgraded_productions = ProductionOrder.objects.filter(
                template_version__mapping_version="v1"
            ).exclude(template_version=template).update(template_version=template)
            self._ensure_base_catalogs()
            result = sync_template_catalog(template, report)
            Product.objects.update_or_create(
                code="POTA-GRANEL",
                description="POTA A GRANEL",
                defaults={
                    "presentation": "Producto principal del parte de producción",
                    "active": True,
                },
            )
            customer, _ = Customer.objects.update_or_create(
                name="Cliente de demostración",
                defaults={"tax_id": "DEMO", "active": True},
            )
            Vehicle.objects.update_or_create(
                plate="DEMO-01",
                defaults={"description": "Vehículo de demostración", "active": True},
            )
            demo_crew = Crew.objects.get(code="C01")
            Worker.objects.update_or_create(
                internal_code="TRAB-DEMO-01",
                defaults={
                    "full_name": "Trabajador de demostración",
                    "crew": demo_crew,
                    "position": "Operario",
                    "active": True,
                },
            )
        self.stdout.write(
            self.style.SUCCESS(
                "Datos de referencia listos: "
                f"plantilla {template.code}, cliente {customer.name}, "
                f"{result['products']} productos, {result['positions']} posiciones y "
                f"{upgraded_productions} partes antiguos actualizados."
            )
        )

    @staticmethod
    def _ensure_template(source, digest, uploader):
        template = TemplateVersion.objects.filter(code=TEMPLATE_CODE).first()
        digest_owner = TemplateVersion.objects.filter(sha256=digest).exclude(code=TEMPLATE_CODE).first()
        if digest_owner is not None:
            raise CommandError(
                f"La plantilla incorporada ya está registrada con el código {digest_owner.code}."
            )
        if template is None:
            template = TemplateVersion(
                code=TEMPLATE_CODE,
                original_filename=TEMPLATE_FILENAME,
                sha256=digest,
                uploaded_by=uploader,
                active=True,
                observations="Plantilla validada incorporada para restauración automática.",
                mapping_version="v2",
            )

        source_changed = bool(template.sha256 and template.sha256 != digest)
        file_missing = not template.file or not template.file.storage.exists(template.file.name)
        if source_changed or file_missing:
            if template.file and template.file.name and template.file.storage.exists(template.file.name):
                template.file.storage.delete(template.file.name)
            with source.open("rb") as handle:
                template.file.save(TEMPLATE_FILENAME, File(handle), save=False)

        template.original_filename = TEMPLATE_FILENAME
        template.sha256 = digest
        template.uploaded_by = uploader
        template.active = True
        template.mapping_version = "v2"
        if source_changed:
            template.observations = "Plantilla actualizada de forma controlada desde el activo validado incorporado."
        template.save()
        return template

    @staticmethod
    def _ensure_base_catalogs():
        for code, label in Role.Codes.choices:
            Role.objects.update_or_create(code=code, defaults={"name": label})
        for number in range(1, 7):
            Tunnel.objects.update_or_create(
                code=f"T{number}",
                defaults={"name": f"Túnel {number}", "active": True},
            )
        for number in range(1, 7):
            Crew.objects.update_or_create(
                code=f"C{number:02d}",
                defaults={"name": f"Cuadrilla {number}", "active": True},
            )
        for name, unit in [
            ("Sacos", "unidad"),
            ("Strech film", "rollo"),
            ("Cinta", "rollo"),
            ("Bolsas", "unidad"),
            ("Etiquetas", "unidad"),
            ("Rafia", "kg"),
            ("Láminas", "unidad"),
            ("Plumones", "unidad"),
        ]:
            Material.objects.update_or_create(
                name=name,
                defaults={"unit": unit, "active": True},
            )
