from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("productions", "0016_plate_crew_product_traceability"),
    ]

    operations = [
        migrations.AddField(
            model_name="tunnelrack",
            name="status",
            field=models.CharField(
                choices=[("OPEN", "Abierto"), ("CLOSED", "Cerrado")],
                default="OPEN",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="tunnelrack",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tunnelrack",
            name="closed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="closed_tunnel_racks",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="tunnelrack",
            name="close_reason",
            field=models.TextField(blank=True),
        ),
    ]
