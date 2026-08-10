"""sync_railway_to_local -- Copia producciones desde Railway Postgres a SQLite local."""

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.db.models import Max

from productions.models import (
    AreaAssignment,
    AuditLog,
    CostEntry,
    Crew,
    Customer,
    GeneratedFile,
    MaterialUsage,
    NuqueraEntry,
    PlateCarryoverBalance,
    PlateCrewEntry,
    PlateEntry,
    PlatePackagingAllocation,
    PlatePackagingEntry,
    PlatePallet,
    PlatePalletConsumption,
    PlatePalletLine,
    PlatePosition,
    PlatePositionTiming,
    Product,
    ProductionOrder,
    Rate,
    ReceptionCarTiming,
    ReceptionEntry,
    Role,
    TemplateVersion,
    TroqueladoEntry,
    Tunnel,
    TunnelCrewEntry,
    TunnelEntry,
    TunnelFill,
    TunnelPackagingEntry,
    TunnelRack,
    User,
    Vehicle,
    Worker,
)

PRODUCTION_DEPENDENCIES = [
    (Customer, "customer"),
    (TemplateVersion, "template_version"),
    (User, "responsible"),
    (User, "created_by"),
]

PRODUCTION_CHILDREN = [
    (TunnelFill, "production"),
    (ReceptionEntry, "production"),
    (ReceptionCarTiming, "production"),
    (NuqueraEntry, "production"),
    (TunnelCrewEntry, "production"),
    (PlateEntry, "production"),
    (PlateCrewEntry, "production"),
    (PlatePosition, "production"),
    (PlatePositionTiming, "production"),
    (TunnelPackagingEntry, "production"),
    (PlatePackagingEntry, "production"),
    (PlateCarryoverBalance, "production"),
    (PlatePallet, "production"),
    (MaterialUsage, "production"),
    (CostEntry, "production"),
    (TroqueladoEntry, "production"),
]

CHILD_CHILDREN = [
    (TunnelEntry, "rack__fill__production"),
    (TunnelRack, "fill__production"),
    (PlatePackagingAllocation, "position__production"),
    (PlatePalletConsumption, "pallet__production"),
    (PlatePalletLine, "pallet__production"),
    (GeneratedFile, "production"),
    (AuditLog, "production"),
    (AreaAssignment, "production"),
]

RELATED_MODELS = [
    Product, Crew, Tunnel, Rate, Role, Vehicle, Worker,
]


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Sobrescribir producciones locales aunque ya existan")

    def handle(self, *args, **options):
        remote_alias = "railway"
        force_flag = options["force"]

        try:
            remote_db = connections[remote_alias]
        except Exception:
            raise CommandError(
                "No se encontro la conexion 'railway' en DATABASES. "
                "Asegurese de que el archivo .env.railway existe con las credenciales correctas."
            )

        local_pks = set(
            ProductionOrder.objects.using("default").values_list("pk", flat=True)
        )

        remote_productions = (
            ProductionOrder.objects.using(remote_alias)
            .select_related(*[f"{dep[0]._meta.db_table}__{dep[1]}" for dep in PRODUCTION_DEPENDENCIES if dep[0] is not User])
            .order_by("pk")
        )

        total = remote_productions.count()
        self.stdout.write(f"Producciones en Railway: {total}")

        copied = 0
        skipped = 0

        for prod in remote_productions.iterator(chunk_size=10):
            if prod.pk in local_pks and not force_flag:
                skipped += 1
                continue

            with transaction.atomic(using="default"):
                self._copy_production(prod, remote_alias)
            copied += 1
            self.stdout.write(f"  [OK] PP-{prod.pp_number}/{prod.created_at.year % 100:02d}")

        self.stdout.write(self.style.SUCCESS(f"Sync completo: {copied} copiadas, {skipped} omitidas (ya existian)."))

    def _copy_production(self, prod, remote):
        prod_pk = prod.pk

        for record in self._iter_related(prod, remote):
            record.pk = None
            record._state.db = "default"
            record.save(using="default")

        self._copy_children(prod_pk, remote)

    def _iter_related(self, prod, remote):
        for user_model, attr in PRODUCTION_DEPENDENCIES:
            if isinstance(getattr(prod, attr, None), user_model):
                obj = getattr(prod, attr)
                if not obj._state.db:
                    obj._state.db = "default"
                yield obj

    def _copy_children(self, prod_pk, remote):
        for model, lookup in PRODUCTION_CHILDREN + CHILD_CHILDREN:
            filter_kwargs = {lookup + "_id": prod_pk}
            for obj in model.objects.using(remote).filter(**filter_kwargs).iterator(chunk_size=50):
                obj.pk = None
                obj._state.db = "default"
                obj.save(using="default")
