from django.db import migrations, models
import django.db.models.deletion


def _backfill_troquelado_crews(apps, schema_editor):
    TroqueladoEntry = apps.get_model("productions", "TroqueladoEntry")
    Crew = apps.get_model("productions", "Crew")
    for entry in TroqueladoEntry.objects.filter(crew__isnull=True):
        crew = None
        if entry.worker_id is not None:
            crew = entry.worker.crew
        if crew is None:
            crew, _ = Crew.objects.get_or_create(
                code="TROQ-00",
                defaults={"name": "SIN CUADRILLA", "active": True},
            )
        entry.crew_id = crew.pk
        entry.save(update_fields=["crew_id"])


def _unset_troquelado_crews(apps, schema_editor):
    TroqueladoEntry = apps.get_model("productions", "TroqueladoEntry")
    TroqueladoEntry.objects.all().update(crew_id=None)


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0026_alter_areaassignment_area_alter_role_code_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="troqueladoentry",
            name="crew",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="troquelado_entries",
                to="productions.crew",
            ),
        ),
        migrations.AddField(
            model_name="troqueladoentry",
            name="product_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("ANILLAS BLANCAS", "Anillas blancas"),
                    ("MORDIDAS BLANCAS", "Mordidas blancas"),
                    ("ANILLAS AMARILLAS", "Anillas amarillas"),
                    ("MORDIDAS AMARILLAS", "Mordidas amarillas"),
                    ("BOTÓN", "Botón"),
                    ("RECORTE", "Recorte"),
                ],
                max_length=40,
                null=True,
            ),
        ),
        migrations.RunPython(_backfill_troquelado_crews, _unset_troquelado_crews),
        migrations.AlterField(
            model_name="troqueladoentry",
            name="crew",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="troquelado_entries",
                to="productions.crew",
            ),
        ),
    ]
