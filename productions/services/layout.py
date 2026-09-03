from productions.models import TunnelCrewEntry, TunnelEntry, TunnelRack


def ensure_tunnel_racks(fill):
    """Crea los racks exactos detectados en la hoja de túnel asignada.

    Optimizado para hacer un numero fijo de consultas en vez de una por
    cada rack existente/configurado: con touneles de ~12 racks, la version
    anterior generaba mas de 30 consultas individuales cada vez que se
    llamaba esta funcion (se llama en cada GET y POST de la captura por
    tunel), lo que sumaba varios segundos de demora al guardar.
    """

    layouts = fill.production.template_version.rules.get("tunnel_racks", {})
    configured = layouts.get(fill.tunnel.code, {}).get(str(fill.fill_number), [])
    # El T4 físico termina en R19. Algunas configuraciones antiguas guardadas
    # en la base de datos todavía incluyen R20.
    if fill.tunnel.code == "T4":
        configured = [rack for rack in configured if rack.get("code") != "R20"]
    default_capacity = fill.production.template_version.rules.get("rack_max_trays", 50)
    configured_codes = {rack["code"] for rack in configured}

    existing_racks = list(fill.racks.all())
    racks_with_entries = set()
    racks_with_crew_entries = set()
    if existing_racks:
        existing_ids = [rack.pk for rack in existing_racks]
        racks_with_entries = set(
            TunnelEntry.objects.filter(rack_id__in=existing_ids, is_active=True)
            .values_list("rack_id", flat=True)
            .distinct()
        )
        racks_with_crew_entries = set(
            TunnelCrewEntry.objects.filter(rack_id__in=existing_ids, is_active=True)
            .values_list("rack_id", flat=True)
            .distinct()
        )

    remaining_codes = set()
    for rack in existing_racks:
        if (
            rack.code not in configured_codes
            and rack.pk not in racks_with_entries
            and rack.pk not in racks_with_crew_entries
        ):
            rack.delete()
        else:
            remaining_codes.add(rack.code)

    for rack in configured:
        if rack["code"] in remaining_codes:
            continue
        TunnelRack.objects.get_or_create(
            fill=fill,
            code=rack["code"],
            defaults={
                "position_key": rack["position_key"],
                "max_trays": rack.get("max_trays", default_capacity),
            },
        )
    return len(configured)
