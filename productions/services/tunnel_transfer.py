import re

from django.core.exceptions import ValidationError
from django.db import transaction

from productions.models import AuditLog, Tunnel, TunnelCrewEntry, TunnelEntry, TunnelFill
from productions.services.layout import ensure_tunnel_racks


def _natural_code_key(value):
    match = re.search(r"\d+", value or "")
    if not match:
        return (value.casefold(), -1, "")
    return (
        value[: match.start()].casefold(),
        int(match.group()),
        value[match.end() :].casefold(),
    )


def _canonical_rack_code(value):
    """Iguala variantes de escritura como R1, R01 y R001."""
    value = (value or "").strip()
    match = re.fullmatch(r"([^\d]*)(\d+)([^\d]*)", value)
    if not match:
        return value.casefold()
    return (
        match.group(1).casefold(),
        int(match.group(2)),
        match.group(3).casefold(),
    )


def _configured_racks(fill, tunnel):
    layouts = fill.production.template_version.rules.get("tunnel_racks", {})
    configured = layouts.get(tunnel.code, {}).get(str(fill.fill_number), [])
    if tunnel.code == "T4":
        configured = [rack for rack in configured if rack.get("code") != "R20"]
    return configured


@transaction.atomic
def transfer_tunnel_fill(
    *,
    fill_id,
    target_tunnel_id,
    user,
    reason,
    ip_address=None,
    user_agent="",
):
    """Traslada una llenada completa entre túneles sin duplicar sus registros."""

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Ingrese el motivo de la transferencia.")

    fill = (
        TunnelFill.objects.select_for_update()
        .select_related("production__template_version", "tunnel")
        .get(pk=fill_id, is_active=True)
    )
    target = Tunnel.objects.get(pk=target_tunnel_id, active=True)
    source = fill.tunnel

    if target.pk == source.pk:
        raise ValidationError("Seleccione un túnel diferente al actual.")
    if (
        TunnelFill.objects.select_for_update()
        .filter(
            production=fill.production,
            tunnel=target,
            fill_number=fill.fill_number,
            is_active=True,
        )
        .exclude(pk=fill.pk)
        .exists()
    ):
        raise ValidationError(
            f"{target.code} ya tiene la llenada {fill.fill_number}. "
            "No se pueden mezclar dos llenadas."
        )

    configured = _configured_racks(fill, target)
    if not configured:
        raise ValidationError(
            f"La plantilla no tiene racks configurados para "
            f"{target.code} · Llenada {fill.fill_number}."
        )
    configured_by_code = {
        _canonical_rack_code(rack["code"]): rack for rack in configured
    }

    # También se incluyen registros anulados: la transferencia no debe borrar
    # historial operativo aunque ya no aparezca en el conteo activo.
    physical_codes = set(
        TunnelEntry.objects.filter(rack__fill=fill).values_list(
            "rack__code", flat=True
        )
    )
    crew_codes = set(
        TunnelCrewEntry.objects.filter(fill=fill, rack__isnull=False).values_list(
            "rack__code", flat=True
        )
    )
    incompatible = sorted(
        [
            code
            for code in physical_codes | crew_codes
            if _canonical_rack_code(code) not in configured_by_code
        ],
        key=_natural_code_key,
    )
    if incompatible:
        raise ValidationError(
            f"No se puede transferir a {target.code}: su configuración no contiene "
            f"los racks con información {', '.join(incompatible)}."
        )

    old_value = {
        "tunnel_id": source.pk,
        "tunnel": source.code,
        "fill_number": fill.fill_number,
        "status": fill.status,
    }

    for rack in fill.racks.select_for_update():
        rack_config = configured_by_code.get(_canonical_rack_code(rack.code))
        if rack_config:
            new_position = rack_config["position_key"]
            new_code = rack_config["code"]
            if rack.position_key != new_position or rack.code != new_code:
                rack.position_key = new_position
                rack.code = new_code
                rack.save(update_fields=["code", "position_key", "updated_at"])
        else:
            # Solo llega aquí si el rack está vacío; los racks usados se validaron arriba.
            rack.delete()

    fill.tunnel = target
    fill.save(update_fields=["tunnel", "updated_at"])
    ensure_tunnel_racks(fill)

    AuditLog.objects.create(
        user=user,
        production=fill.production,
        module="tunnel-fill-transfer",
        model_name=TunnelFill._meta.label,
        record_pk=str(fill.pk),
        action=AuditLog.Action.UPDATE,
        old_value=old_value,
        new_value={
            "tunnel_id": target.pk,
            "tunnel": target.code,
            "fill_number": fill.fill_number,
            "status": fill.status,
        },
        reason=reason,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return fill
