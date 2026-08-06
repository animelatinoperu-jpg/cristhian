import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0012_remove_orphan_completed_plate_timings"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatePackagingAllocation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("is_active", models.BooleanField(default=True)),
                ("voided_at", models.DateTimeField(blank=True, null=True)),
                ("void_reason", models.TextField(blank=True)),
                ("observation", models.TextField(blank=True)),
                ("date", models.DateField()),
                ("pallet_number", models.PositiveSmallIntegerField()),
                ("package_count", models.PositiveIntegerField()),
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
                    "source_entry",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="packaging_allocations",
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
            options={
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("is_active", True)),
                        fields=("production", "source_entry", "pallet_number"),
                        name="uniq_plate_pack_source_pallet",
                    )
                ],
            },
        ),
    ]
