from django.core.exceptions import PermissionDenied, ValidationError
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from productions.models import Approval, AreaAssignment, AuditLog, Observation, ProductionOrder, Role, TunnelFill
from productions.request_context import suppress_automatic_audit
from .plate_balances import (
    cancel_production_balances_for_reopen,
    sync_production_carryover_balances,
)
from .permissions import require_area_assignment, require_roles
from .reconciliation import plate_reconciliation, tunnel_reconciliation


def _twelve_hours_after(value):
    reference_date = timezone.localdate()
    launched_at = datetime.combine(reference_date, value)
    if timezone.is_naive(launched_at):
        launched_at = timezone.make_aware(launched_at, timezone.get_current_timezone())
    return (launched_at + timedelta(hours=12)).time().replace(microsecond=0)


TRANSITIONS = {
    ProductionOrder.Status.DRAFT: {ProductionOrder.Status.OPEN, ProductionOrder.Status.VOID},
    ProductionOrder.Status.OPEN: {ProductionOrder.Status.IN_PROGRESS, ProductionOrder.Status.VOID},
    ProductionOrder.Status.IN_PROGRESS: {ProductionOrder.Status.REVIEW, ProductionOrder.Status.VOID},
    ProductionOrder.Status.REVIEW: {
        ProductionOrder.Status.APPROVED,
        ProductionOrder.Status.OBSERVED,
        ProductionOrder.Status.VOID,
    },
    ProductionOrder.Status.OBSERVED: {
        ProductionOrder.Status.IN_PROGRESS,
        ProductionOrder.Status.REVIEW,
        ProductionOrder.Status.VOID,
    },
    ProductionOrder.Status.APPROVED: {
        ProductionOrder.Status.CLOSED,
        ProductionOrder.Status.OBSERVED,
        ProductionOrder.Status.VOID,
    },
    ProductionOrder.Status.CLOSED: {ProductionOrder.Status.IN_PROGRESS, ProductionOrder.Status.VOID},
    ProductionOrder.Status.VOID: {ProductionOrder.Status.DRAFT},
}


def validate_closure(production):
    errors = []
    tunnel = tunnel_reconciliation(production)
    plates = plate_reconciliation(production)
    if tunnel.difference:
        errors.append(f"Túneles/cuadrillas: diferencia de {tunnel.difference} bandejas.")
    if plates.difference:
        errors.append(f"Plaqueros/cuadrillas: diferencia de {plates.difference} bandejas.")
    if Observation.objects.filter(production=production, resolved=False).exists():
        errors.append("Existen observaciones sin resolver.")
    if errors:
        raise ValidationError(errors)


@transaction.atomic
def transition_production(*, production_id, target_status, user, expected_version, reason=""):
    require_roles(user, Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER)
    production = ProductionOrder.objects.select_for_update().get(pk=production_id)
    if production.version != expected_version:
        raise ValidationError("La producción cambió en otra sesión. Recargue antes de continuar.")
    if target_status not in TRANSITIONS.get(production.status, set()):
        raise ValidationError(f"No se permite pasar de {production.get_status_display()} a {target_status}.")
    if production.status == ProductionOrder.Status.CLOSED and not reason.strip():
        raise ValidationError("La reapertura requiere un motivo.")
    if target_status in {ProductionOrder.Status.OBSERVED, ProductionOrder.Status.VOID} and not reason.strip():
        raise ValidationError("Observar o anular una producción requiere un motivo.")
    if production.status == ProductionOrder.Status.VOID and not reason.strip():
        raise ValidationError("Restaurar una producción eliminada requiere un motivo.")
    if production.status == ProductionOrder.Status.VOID and target_status == ProductionOrder.Status.DRAFT:
        number_in_use = (
            ProductionOrder.objects.exclude(pk=production.pk)
            .exclude(status=ProductionOrder.Status.VOID)
            .filter(number=production.number)
            .exists()
        )
        if number_in_use:
            raise ValidationError(
                f"No se puede restaurar el PP {production.number} porque ya existe otro parte activo con ese número."
            )
    if target_status == ProductionOrder.Status.CLOSED:
        validate_closure(production)
        production.closed_at = timezone.now()
    elif production.status == ProductionOrder.Status.CLOSED:
        cancel_production_balances_for_reopen(production=production)
        production.closed_at = None
    old_status = production.status
    production.status = target_status
    with suppress_automatic_audit():
        production.save(update_fields=["status", "closed_at", "version", "updated_at"])
    if target_status == ProductionOrder.Status.CLOSED:
        sync_production_carryover_balances(production=production, user=user)
    decision = None
    if target_status == ProductionOrder.Status.APPROVED:
        decision = Approval.Decision.APPROVE
    elif target_status == ProductionOrder.Status.OBSERVED:
        decision = Approval.Decision.OBSERVE
    elif old_status == ProductionOrder.Status.CLOSED:
        decision = Approval.Decision.REOPEN
    if decision:
        Approval.objects.create(production=production, module="PRODUCTION", decision=decision, reason=reason, user=user)
    AuditLog.objects.create(
        user=user,
        production=production,
        module="workflow",
        model_name=production._meta.label,
        record_pk=str(production.pk),
        action=AuditLog.Action.VOID if target_status == ProductionOrder.Status.VOID else AuditLog.Action.TRANSITION,
        old_value={"status": old_status},
        new_value={"status": target_status},
        reason=reason,
    )
    return production


@transaction.atomic
def transition_tunnel_fill(*, fill_id, target_status, user, expected_version, reason=""):
    fill = TunnelFill.objects.select_for_update().select_related("production", "tunnel").get(pk=fill_id)
    is_manager = user.is_superuser or user.roles.filter(code__in=[Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER]).exists()
    if fill.version != expected_version:
        raise ValidationError("La llenada cambió en otra sesión. Recargue antes de continuar.")
    if target_status == TunnelFill.Status.CLOSED:
        require_area_assignment(user, fill.production, AreaAssignment.Area.TUNNEL, tunnel=fill.tunnel)
        result = tunnel_reconciliation(fill.production, fill=fill)
        if result.difference and (not is_manager or not reason.strip()):
            raise ValidationError(f"Existe una diferencia de {result.difference} bandejas. Solo el jefe puede aprobarla con justificación.")
        if result.difference:
            Approval.objects.create(production=fill.production, module=f"{fill.tunnel.code}-L{fill.fill_number}", decision=Approval.Decision.APPROVE, reason=reason, user=user)
        fill.status = TunnelFill.Status.CLOSED
        if fill.launch_time is None:
            fill.launch_time = timezone.localtime().time().replace(microsecond=0)
        if fill.end_time is None:
            fill.end_time = _twelve_hours_after(fill.launch_time)
        fill.closed_at = timezone.now()
    elif target_status == TunnelFill.Status.REOPENED:
        require_roles(user, Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER)
        if not reason.strip():
            raise ValidationError("La reapertura requiere un motivo.")
        fill.status = TunnelFill.Status.REOPENED
        fill.closed_at = None
        Approval.objects.create(production=fill.production, module=f"{fill.tunnel.code}-L{fill.fill_number}", decision=Approval.Decision.REOPEN, reason=reason, user=user)
    else:
        raise ValidationError("Estado de llenada no permitido.")
    old_status = TunnelFill.objects.filter(pk=fill.pk).values_list("status", flat=True).first()
    fill.save(update_fields=["status", "launch_time", "end_time", "closed_at", "version", "updated_at"])
    AuditLog.objects.create(user=user, production=fill.production, module="tunnel_fill", model_name=fill._meta.label, record_pk=str(fill.pk), action=AuditLog.Action.TRANSITION, old_value={"status": old_status}, new_value={"status": fill.status}, reason=reason)
    return fill
