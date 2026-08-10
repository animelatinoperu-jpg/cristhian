"""sync_railway_to_local -- Copia producciones desde Railway Postgres a SQLite local."""

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction, IntegrityError
from django.apps import apps


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Sobrescribir producciones locales aunque ya existan")

    def handle(self, *args, **options):
        remote = "railway"
        force = options["force"]

        try:
            connections[remote].ensure_connection()
        except Exception as e:
            raise CommandError(f"No se pudo conectar a Railway: {e}")

        ProductionOrder = apps.get_model("productions", "ProductionOrder")
        User = apps.get_model("productions", "User")
        Customer = apps.get_model("productions", "Customer")
        TemplateVersion = apps.get_model("productions", "TemplateVersion")
        Product = apps.get_model("productions", "Product")

        remote_ids = set(ProductionOrder.objects.using(remote).values_list("pk", flat=True))
        local_ids = set(ProductionOrder.objects.using("default").values_list("pk", flat=True))

        self.stdout.write(f"Producciones en Railway: {len(remote_ids)}")
        self.stdout.write(f"Producciones locales: {len(local_ids)}")

        new_ids = remote_ids - local_ids if not force else remote_ids
        if not new_ids:
            self.stdout.write(self.style.SUCCESS("Todo sincronizado."))
            return

        related = []
        for model in apps.get_models():
            if model._meta.app_label != "productions":
                continue
            if model is ProductionOrder:
                continue
            for field in model._meta.get_fields():
                if field.is_relation and field.related_model == ProductionOrder:
                    related.append((model, field.name))
                    break

        copied = 0
        for pk in sorted(new_ids):
            prod = ProductionOrder.objects.using(remote).get(pk=pk)
            label = f"PP-{prod.number}/{str(prod.created_at.year)[-2:]}"

            sid = transaction.savepoint(using="default")
            try:
                self._map_fks(prod, remote, User, Customer, TemplateVersion, Product)
                old_pk = prod.pk
                prod.pk = None
                prod._state.db = "default"
                prod.save(using="default")
                new_pk = prod.pk

                for model, fk_name in related:
                    for obj in model.objects.using(remote).filter(**{fk_name: old_pk}).iterator(chunk_size=100):
                        try:
                            obj.pk = None
                            obj._state.db = "default"
                            setattr(obj, fk_name + "_id", new_pk)
                            obj.save(using="default")
                        except IntegrityError:
                            pass

                transaction.savepoint_commit(sid, using="default")
                copied += 1
                self.stdout.write(f"  [OK] {label}")

            except Exception as e:
                transaction.savepoint_rollback(sid, using="default")
                self.stdout.write(self.style.WARNING(f"  [SKIP] {label}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Sync completo: {copied} producciones."))

    def _map_fks(self, prod, remote, User, Customer, TemplateVersion, Product):
        """Mapea las FKs de la produccion remota a IDs locales."""
        if prod.created_by_id:
            remote_user = User.objects.using(remote).get(pk=prod.created_by_id)
            local_user = User.objects.using("default").filter(username=remote_user.username).first()
            if local_user:
                prod.created_by_id = local_user.pk
            else:
                try:
                    remote_user.pk = None
                    remote_user._state.db = "default"
                    remote_user.save(using="default")
                    prod.created_by_id = remote_user.pk
                except IntegrityError:
                    prod.created_by_id = User.objects.using("default").first().pk

        if prod.customer_id:
            remote_cust = Customer.objects.using(remote).get(pk=prod.customer_id)
            local_cust = Customer.objects.using("default").filter(name__iexact=remote_cust.name).first()
            if not local_cust:
                remote_cust.pk = None
                remote_cust._state.db = "default"
                remote_cust.save(using="default")
                local_cust = remote_cust
            prod.customer_id = local_cust.pk

        if prod.template_version_id:
            remote_tv = TemplateVersion.objects.using(remote).get(pk=prod.template_version_id)
            local_tv = TemplateVersion.objects.using("default").filter(code=remote_tv.code).first()
            if not local_tv:
                local_tv = TemplateVersion.objects.using("default").create(
                    code=remote_tv.code,
                    original_filename=remote_tv.original_filename,
                    sha256=remote_tv.sha256,
                    uploaded_by=User.objects.using("default").first(),
                    active=remote_tv.active,
                    observations=remote_tv.observations,
                    mapping_version=remote_tv.mapping_version,
                    rules=remote_tv.rules,
                )
            prod.template_version_id = local_tv.pk

        if prod.main_product_id:
            remote_mp = Product.objects.using(remote).get(pk=prod.main_product_id)
            local_mp = Product.objects.using("default").filter(code=remote_mp.code).first()
            if not local_mp:
                remote_mp.pk = None
                remote_mp._state.db = "default"
                remote_mp.save(using="default")
                local_mp = remote_mp
            prod.main_product_id = local_mp.pk
