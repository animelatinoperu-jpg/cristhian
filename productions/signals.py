from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from contextvars import ContextVar

from .models import AuditLog, OperationalRecord, ProductionOrder
from .request_context import automatic_audit_suppressed, current_request


_old_instances = ContextVar("audit_old_instances", default={})


def _json_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _snapshot(instance):
    excluded = {"created_at", "updated_at", "version"}
    return {
        field.name: _json_value(getattr(instance, field.attname))
        for field in instance._meta.concrete_fields
        if field.name not in excluded
    }


@receiver(pre_save)
def capture_old_instance(sender, instance, **kwargs):
    if not isinstance(instance, (OperationalRecord, ProductionOrder)) or not instance.pk:
        return
    try:
        snapshots = dict(_old_instances.get())
        snapshots[(sender, instance.pk)] = _snapshot(sender.objects.get(pk=instance.pk))
        _old_instances.set(snapshots)
    except sender.DoesNotExist:
        pass


@receiver(post_save)
def write_audit_log(sender, instance, created, **kwargs):
    if not isinstance(instance, (OperationalRecord, ProductionOrder)):
        return
    request = current_request.get()
    snapshots = dict(_old_instances.get())
    old = snapshots.pop((sender, instance.pk), None)
    _old_instances.set(snapshots)
    new = _snapshot(instance)
    if old == new or automatic_audit_suppressed.get():
        return
    production = instance if isinstance(instance, ProductionOrder) else instance.production
    AuditLog.objects.create(
        user=request.user if request and request.user.is_authenticated else None,
        production=production,
        module=sender._meta.app_label,
        model_name=sender._meta.label,
        record_pk=str(instance.pk),
        action=AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
        old_value=old,
        new_value=new,
        ip_address=request.META.get("REMOTE_ADDR") if request else None,
        user_agent=request.META.get("HTTP_USER_AGENT", "") if request else "",
    )


@receiver(user_logged_in)
def audit_login(sender, request, user, **kwargs):
    AuditLog.objects.create(
        user=user,
        module="auth",
        action=AuditLog.Action.LOGIN,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
