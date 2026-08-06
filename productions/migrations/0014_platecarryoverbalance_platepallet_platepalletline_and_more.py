import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0013_platepackagingallocation"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlateCarryoverBalance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("is_active", models.BooleanField(default=True)),
                ("voided_at", models.DateTimeField(blank=True, null=True)),
                ("void_reason", models.TextField(blank=True)),
                ("initial_trays", models.PositiveSmallIntegerField()),
                ("available_trays", models.PositiveSmallIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("AVAILABLE", "Disponible"),
                            ("CONSUMED", "Utilizado"),
                            ("HELD", "Retenido"),
                            ("WASTE", "Merma"),
                            ("CANCELLED", "Cancelado por reapertura"),
                        ],
                        default="AVAILABLE",
                        max_length=12,
                    ),
                ),
                ("generated_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("observation", models.TextField(blank=True)),
                (
                    "generated_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="generated_plate_balances",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "last_used_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="used_plate_balances",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "last_used_in_production",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="used_plate_balances",
                        to="productions.productionorder",
                    ),
                ),
                (
                    "origin_production",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="generated_plate_balances",
                        to="productions.productionorder",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="productions.product",
                    ),
                ),
                (
                    "source_entry",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="carryover_balance",
                        to="productions.plateentry",
                    ),
                ),
                (
                    "voided_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="voided_%(class)s_records",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="PlatePallet",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("is_active", models.BooleanField(default=True)),
                ("voided_at", models.DateTimeField(blank=True, null=True)),
                ("void_reason", models.TextField(blank=True)),
                ("pallet_number", models.PositiveSmallIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[("OPEN", "Abierto"), ("CLOSED", "Cerrado")],
                        default="OPEN",
                        max_length=10,
                    ),
                ),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "closed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="closed_plate_pallets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "production",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="plate_pallets",
                        to="productions.productionorder",
                    ),
                ),
                (
                    "voided_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="voided_%(class)s_records",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="PlatePalletLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("is_active", models.BooleanField(default=True)),
                ("voided_at", models.DateTimeField(blank=True, null=True)),
                ("void_reason", models.TextField(blank=True)),
                ("observation", models.TextField(blank=True)),
                ("date", models.DateField()),
                ("package_count", models.PositiveIntegerField()),
                (
                    "pallet",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="lines",
                        to="productions.platepallet",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="productions.product",
                    ),
                ),
                (
                    "production",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="productions.productionorder",
                    ),
                ),
                (
                    "responsible",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "voided_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="voided_%(class)s_records",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="PlatePalletConsumption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("tray_count", models.PositiveSmallIntegerField()),
                (
                    "carryover_balance",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pallet_consumptions",
                        to="productions.platecarryoverbalance",
                    ),
                ),
                (
                    "source_entry",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pallet_consumptions",
                        to="productions.plateentry",
                    ),
                ),
                (
                    "line",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="consumptions",
                        to="productions.platepalletline",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="platepallet",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True)),
                fields=("production", "pallet_number"),
                name="uniq_active_plate_pallet",
            ),
        ),
        migrations.AddConstraint(
            model_name="platepalletconsumption",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("carryover_balance__isnull", True),
                        ("source_entry__isnull", False),
                    ),
                    models.Q(
                        ("carryover_balance__isnull", False),
                        ("source_entry__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="plate_consumption_has_one_origin",
            ),
        ),
    ]
