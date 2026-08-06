from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0019_plate_manual_initial_balance"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="tunnelfill",
            name="uniq_tunnel_fill",
        ),
        migrations.AddField(
            model_name="tunnelfill",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="tunnelfill",
            name="void_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="tunnelfill",
            name="voided_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tunnelfill",
            name="voided_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="voided_tunnel_fills",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="tunnelfill",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_active=True),
                fields=("production", "tunnel", "fill_number"),
                name="uniq_active_tunnel_fill",
            ),
        ),
    ]
