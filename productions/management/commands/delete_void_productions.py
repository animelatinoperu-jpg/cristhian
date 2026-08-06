from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from productions.models import ProductionOrder
from productions.services.permanent_delete import permanently_delete_production


class Command(BaseCommand):
    help = "Elimina definitivamente uno o más partes de producción anulados (VOID)."

    def add_arguments(self, parser):
        parser.add_argument("numbers", nargs="+", type=int, help="Números de parte a eliminar (ej: 103 105).")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Solo muestra los relacionados que se borrarían sin borrar nada.",
        )

    def handle(self, *args, **options):
        numbers = options["numbers"]
        dry_run = options["dry_run"]
        productions = list(
            ProductionOrder.objects.filter(number__in=numbers).order_by("number")
        )
        missing = [n for n in numbers if n not in {p.number for p in productions}]
        if missing:
            raise CommandError(f"No se encontraron los partes: {missing}.")
        for production in productions:
            if production.status != ProductionOrder.Status.VOID:
                raise CommandError(
                    f"El parte PP {production.number} está en estado "
                    f"{production.get_status_display()}; solo se eliminan anulados."
                )
        total = 0
        for production in productions:
            if dry_run:
                self.stdout.write(
                    f"[DRY-RUN] PP {production.number} · {production.get_status_display()} · "
                    f"{str(production)}"
                )
                continue
            with transaction.atomic():
                report = permanently_delete_production(
                    production_id=production.pk,
                    expected_version=production.version,
                )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Eliminado PP {report['number']}: {report['label']} "
                    f"(audit_logs={report['audit_logs']}, tunnel_entries={report['tunnel_entries']}, "
                    f"generated_files={report['generated_files']})."
                )
            )
            total += 1
        if dry_run:
            self.stdout.write(f"[DRY-RUN] {len(productions)} parte(s) listos para eliminar.")
        else:
            self.stdout.write(self.style.SUCCESS(f"Eliminados {total} parte(s) en firme."))
