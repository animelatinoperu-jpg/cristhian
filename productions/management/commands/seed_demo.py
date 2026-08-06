from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from productions.models import (
    CostEntry,
    Crew,
    Customer,
    Material,
    MaterialUsage,
    NuqueraEntry,
    PlateCrewEntry,
    PlateEntry,
    PlatePackagingEntry,
    PlatePosition,
    Product,
    ProductionOrder,
    Rate,
    ReceptionEntry,
    Role,
    TemplateVersion,
    TroqueladoEntry,
    Tunnel,
    TunnelCrewEntry,
    TunnelEntry,
    TunnelFill,
    TunnelPackagingEntry,
    Vehicle,
    Worker,
)
from productions.services.layout import ensure_tunnel_racks
from productions.services.template_catalog import sync_template_catalog


class Command(BaseCommand):
    help = "Crea catálogos, administrador y una producción demostrativa funcional."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="admin")
        parser.add_argument("--password", default=None)

    def handle(self, *args, **options):
        for code, label in Role.Codes.choices:
            Role.objects.update_or_create(code=code, defaults={"name": label})
        for number in range(1, 7):
            Tunnel.objects.update_or_create(code=f"T{number}", defaults={"name": f"Túnel {number}"})

        crews = []
        for code in range(1, 7):
            crew, _ = Crew.objects.get_or_create(code=f"C{code:02d}", defaults={"name": f"Cuadrilla {code}"})
            crews.append(crew)

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
            Material.objects.update_or_create(name=name, defaults={"unit": unit, "active": True})

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=options["username"],
            defaults={"is_staff": True, "is_superuser": True, "email": "admin@example.invalid"},
        )
        if not user.is_staff or not user.is_superuser:
            user.is_staff = True
            user.is_superuser = True
            user.save(update_fields=["is_staff", "is_superuser"])
        if options["password"]:
            user.set_password(options["password"])
            user.save(update_fields=["password"])

        template = TemplateVersion.objects.filter(active=True).order_by("-created_at").first()
        report_path = settings.BASE_DIR / "docs" / "inventario_excel.json"
        catalog_result = None
        if template and report_path.is_file():
            catalog_result = sync_template_catalog(template, report_path)

        customer, _ = Customer.objects.update_or_create(
            name="Cliente de demostración",
            defaults={"tax_id": "DEMO", "active": True},
        )
        vehicle, _ = Vehicle.objects.update_or_create(
            plate="DEMO-01",
            defaults={"description": "Vehículo de demostración", "active": True},
        )
        worker, _ = Worker.objects.update_or_create(
            internal_code="TRAB-DEMO-01",
            defaults={
                "full_name": "Trabajador de demostración",
                "crew": crews[0],
                "position": "Operario",
                "active": True,
            },
        )
        troq_crew, _ = Crew.objects.get_or_create(
            code="TROQ-01",
            defaults={"name": "CHARLES TROQ", "active": True},
        )
        troq_worker, _ = Worker.objects.update_or_create(
            internal_code="TROQ-W1",
            defaults={
                "full_name": "JUAN PEREZ",
                "crew": troq_crew,
                "position": "Troquelador",
                "active": True,
            },
        )
        Worker.objects.update_or_create(
            internal_code="TROQ-W2",
            defaults={
                "full_name": "LUIS ROJAS",
                "crew": troq_crew,
                "position": "Troquelador",
                "active": True,
            },
        )
        today = timezone.localdate()
        rate, _ = Rate.objects.get_or_create(
            process="Proceso de demostración",
            effective_from=today,
            defaults={"amount": Decimal("1.0000"), "unit": "kg", "active": True},
        )

        product = Product.objects.filter(active=True).order_by("description", "code").first()
        production = None
        if template and product:
            production, _ = ProductionOrder.objects.get_or_create(
                number=999001,
                defaults={
                    "plant_lot": "LOTE-DEMO-2026",
                    "customer_lot": "CLIENTE-DEMO",
                    "customer": customer,
                    "process": "Congelado",
                    "main_product": product,
                    "reception_date": today,
                    "production_date": today,
                    "packaging_date": today,
                    "shift": ProductionOrder.Shift.DAY,
                    "series": "DEMO",
                    "observations": "Registro de demostración. Puede usarse para conocer el flujo.",
                    "template_version": template,
                    "status": ProductionOrder.Status.IN_PROGRESS,
                    "created_by": user,
                },
            )
            tunnel = Tunnel.objects.get(code="T1")
            fill, _ = TunnelFill.objects.get_or_create(
                production=production,
                tunnel=tunnel,
                fill_number=1,
                defaults={"date": today, "supervisor": user},
            )
            ensure_tunnel_racks(fill)
            rack = fill.racks.order_by("code").first()
            if rack:
                TunnelEntry.objects.get_or_create(
                    production=production,
                    rack=rack,
                    product=product,
                    defaults={
                        "responsible": user,
                        "tray_count": 20,
                        "date": today,
                        "observation": "Dato de demostración",
                    },
                )
                TunnelCrewEntry.objects.get_or_create(
                    production=production,
                    fill=fill,
                    crew=crews[0],
                    page_or_block="BLOQUE-DEMO",
                    defaults={
                        "responsible": user,
                        "tray_count": 20,
                        "date": today,
                        "observation": "Dato de demostración",
                    },
                )

            position = PlatePosition.objects.filter(
                template_version=template,
                plate_rack=PlatePosition.PlateRack.P1,
                active=True,
            ).first()
            if position:
                PlateEntry.objects.get_or_create(
                    production=production,
                    position=position,
                    product=product,
                    defaults={
                        "responsible": user,
                        "date": today,
                        "shift": production.shift,
                        "tray_count": 15,
                        "crew": crews[0],
                        "observation": "Dato de demostración",
                    },
                )
                PlateCrewEntry.objects.get_or_create(
                    production=production,
                    position=position,
                    page="PÁGINA-DEMO",
                    crew=crews[0],
                    defaults={
                        "responsible": user,
                        "tray_count": 15,
                        "date": today,
                        "observation": "Dato de demostración",
                    },
                )

            ReceptionEntry.objects.get_or_create(
                production=production,
                vehicle=vehicle,
                product=product,
                container="DINO-DEMO",
                defaults={
                    "responsible": user,
                    "date": today,
                    "crew": crews[0],
                    "weight_kg": Decimal("500.00"),
                    "observation": "Dato de demostración",
                },
            )
            NuqueraEntry.objects.get_or_create(
                production=production,
                worker=worker,
                process="Perfilado de demostración",
                defaults={
                    "responsible": user,
                    "date": today,
                    "shift": production.shift,
                    "crew": crews[0],
                    "weight_kg": Decimal("100.00"),
                    "start_time": "08:00",
                    "end_time": "09:00",
                    "observation": "Dato de demostración",
                },
            )
            TunnelPackagingEntry.objects.get_or_create(
                production=production,
                pallet_number=1,
                product=product,
                defaults={
                    "responsible": user,
                    "date": today,
                    "package_count": 5,
                    "observation": "Dato de demostración",
                },
            )
            PlatePackagingEntry.objects.get_or_create(
                production=production,
                pallet_number=1,
                product=product,
                defaults={
                    "responsible": user,
                    "date": today,
                    "package_count": 5,
                    "observation": "Dato de demostración",
                },
            )
            TroqueladoEntry.objects.get_or_create(
                production=production,
                worker=troq_worker,
                product_type="BOTÓN",
                cajas=5,
                kg_por_caja=Decimal("20.000"),
                start_time="08:00",
                end_time="09:00",
                defaults={
                    "responsible": user,
                    "date": today,
                    "shift": production.shift,
                    "crew": troq_crew,
                    "weight_kg": Decimal("100.00"),
                    "observation": "Dato de demostración",
                },
            )
            MaterialUsage.objects.get_or_create(
                production=production,
                material=Material.objects.get(name="Sacos"),
                defaults={
                    "responsible": user,
                    "quantity": Decimal("10.000"),
                    "observation": "Dato de demostración",
                },
            )
            CostEntry.objects.get_or_create(
                production=production,
                concept="Costo de demostración",
                defaults={
                    "responsible": user,
                    "quantity": Decimal("1.000"),
                    "unit_cost": Decimal("100.0000"),
                    "rate": rate,
                    "observation": "Dato de demostración",
                },
            )

        details = f" Usuario: {user.username} ({'creado' if created else 'existente'})."
        if catalog_result:
            details += f" Productos importados: {catalog_result['products']}. Posiciones P1-P3: {catalog_result['positions']}."
        if production:
            details += f" PP demostrativo: {production.number}."
        self.stdout.write(self.style.SUCCESS("Datos base listos." + details))
