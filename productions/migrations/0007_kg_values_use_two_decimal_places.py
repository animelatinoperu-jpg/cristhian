from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("productions", "0006_user_approved_at_user_approved_by_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="standard_weight_kg",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AlterField(
            model_name="product",
            name="plus_weight_kg",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AlterField(
            model_name="product",
            name="packaging_weight_kg",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AlterField(
            model_name="receptionentry",
            name="weight_kg",
            field=models.DecimalField(decimal_places=2, max_digits=12, validators=[MinValueValidator(0)]),
        ),
        migrations.AlterField(
            model_name="nuqueraentry",
            name="weight_kg",
            field=models.DecimalField(decimal_places=2, max_digits=12, validators=[MinValueValidator(0)]),
        ),
    ]
