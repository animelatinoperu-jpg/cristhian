from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("productions", "0007_kg_values_use_two_decimal_places"),
    ]

    operations = [
        migrations.AddField(
            model_name="tunnelcrewentry",
            name="rack",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="crew_entries",
                to="productions.tunnelrack",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="tunnelcrewentry",
            name="uniq_tunnel_crew_block",
        ),
        migrations.AddConstraint(
            model_name="tunnelcrewentry",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True), ("rack__isnull", False)),
                fields=("fill", "rack", "crew"),
                name="uniq_active_tunnel_rack_crew",
            ),
        ),
        migrations.AddConstraint(
            model_name="tunnelcrewentry",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True), ("rack__isnull", True)),
                fields=("fill", "crew", "page_or_block"),
                name="uniq_legacy_tunnel_crew_block",
            ),
        ),
    ]
