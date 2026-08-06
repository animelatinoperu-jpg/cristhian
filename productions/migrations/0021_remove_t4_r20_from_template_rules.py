from django.db import migrations


def _clean_template_rules(apps, schema_editor):
    TemplateVersion = apps.get_model("productions", "TemplateVersion")
    TunnelRack = apps.get_model("productions", "TunnelRack")

    for template in TemplateVersion.objects.all():
        rules = dict(template.rules or {})
        tunnel_racks = dict(rules.get("tunnel_racks") or {})
        tunnel_t4 = dict(tunnel_racks.get("T4") or {})
        changed = False

        for fill_number in ("1", "2"):
            current = list(tunnel_t4.get(fill_number) or [])
            filtered = [rack for rack in current if rack.get("code") != "R20"]
            if filtered != current:
                tunnel_t4[fill_number] = filtered
                changed = True

        if changed:
            tunnel_racks["T4"] = tunnel_t4
            rules["tunnel_racks"] = tunnel_racks
            template.rules = rules
            template.save(update_fields=["rules"])

    empty_r20_racks = TunnelRack.objects.filter(
        fill__tunnel__code="T4",
        code="R20",
        entries__isnull=True,
        crew_entries__isnull=True,
    ).distinct()
    for rack in empty_r20_racks:
        rack.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0020_soft_delete_tunnel_fill"),
    ]

    operations = [
        migrations.RunPython(_clean_template_rules, migrations.RunPython.noop),
    ]
