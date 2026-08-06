from django.db import migrations


def repair_tunnel_crew_production(apps, schema_editor):
    TunnelCrewEntry = apps.get_model("productions", "TunnelCrewEntry")
    for entry in TunnelCrewEntry.objects.select_related("fill").all().iterator():
        canonical_production_id = entry.fill.production_id
        if entry.production_id != canonical_production_id:
            TunnelCrewEntry.objects.filter(pk=entry.pk).update(
                production_id=canonical_production_id
            )


class Migration(migrations.Migration):
    dependencies = [
        ("productions", "0008_tunnel_crew_entries_by_rack"),
    ]

    operations = [
        migrations.RunPython(repair_tunnel_crew_production, migrations.RunPython.noop),
    ]
