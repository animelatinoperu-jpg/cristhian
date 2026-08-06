from productions.models import TunnelRack


def ensure_tunnel_racks(fill):
    """Crea los racks exactos detectados en la hoja de túnel asignada."""

    layouts = fill.production.template_version.rules.get("tunnel_racks", {})
    configured = layouts.get(fill.tunnel.code, {}).get(str(fill.fill_number), [])
    # El T4 físico termina en R19. Algunas configuraciones antiguas guardadas
    # en la base de datos todavía incluyen R20.
    if fill.tunnel.code == "T4":
        configured = [rack for rack in configured if rack.get("code") != "R20"]
    default_capacity = fill.production.template_version.rules.get("rack_max_trays", 50)
    configured_codes = {rack["code"] for rack in configured}

    for rack in fill.racks.all():
        if (
            rack.code not in configured_codes
            and not rack.entries.filter(is_active=True).exists()
            and not rack.crew_entries.filter(is_active=True).exists()
        ):
            rack.delete()

    for rack in configured:
        TunnelRack.objects.get_or_create(
            fill=fill,
            code=rack["code"],
            defaults={
                "position_key": rack["position_key"],
                "max_trays": rack.get("max_trays", default_capacity),
            },
        )
    return len(configured)
