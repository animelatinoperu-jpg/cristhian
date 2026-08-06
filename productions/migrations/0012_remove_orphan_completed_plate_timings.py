from django.db import migrations
from django.db.models import Q


def remove_orphan_completed_plate_timings(apps, schema_editor):
    PlateEntry = apps.get_model("productions", "PlateEntry")
    PlateCrewEntry = apps.get_model("productions", "PlateCrewEntry")
    PlatePositionTiming = apps.get_model("productions", "PlatePositionTiming")

    completed_timings = PlatePositionTiming.objects.filter(
        Q(load_completed_at__isnull=False)
        | Q(launched_at__isnull=False)
        | Q(unloaded_at__isnull=False)
    )
    orphan_ids = []
    for timing in completed_timings.iterator():
        has_physical = PlateEntry.objects.filter(
            production_id=timing.production_id,
            position_id=timing.position_id,
            is_active=True,
        ).exists()
        has_crews = PlateCrewEntry.objects.filter(
            production_id=timing.production_id,
            position_id=timing.position_id,
            is_active=True,
        ).exists()
        if not has_physical and not has_crews:
            orphan_ids.append(timing.pk)

    if orphan_ids:
        PlatePositionTiming.objects.filter(pk__in=orphan_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("productions", "0011_platepositiontiming_load_started_and_unloaded"),
    ]

    operations = [
        migrations.RunPython(
            remove_orphan_completed_plate_timings,
            migrations.RunPython.noop,
        ),
    ]
