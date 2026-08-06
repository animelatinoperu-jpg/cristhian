from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("productions", "0004_soft_delete_production_orders"),
    ]

    operations = [
        migrations.AddField(
            model_name="tunnelrack",
            name="max_trays",
            field=models.PositiveSmallIntegerField(default=50),
        ),
    ]
