from django.core.exceptions import PermissionDenied

from productions.models import AreaAssignment, Role


MANAGER_ROLES = {Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER}
READ_ALL_ROLES = MANAGER_ROLES | {Role.Codes.MANAGEMENT, Role.Codes.AUDITOR}
CREW_CONTROL_AREAS = {
    AreaAssignment.Area.TUNNEL_CREW,
    AreaAssignment.Area.PLATE_CREW,
}

ROLE_AREA_MAP = {
    "tunnel": AreaAssignment.Area.TUNNEL_CREW,
    "plate": AreaAssignment.Area.PLATE_CREW,
    "reception": AreaAssignment.Area.RECEPTION,
    "nuqueras": AreaAssignment.Area.NUQUERAS,
    "tunnel_pack": AreaAssignment.Area.TUNNEL_PACK,
    "plate_pack": AreaAssignment.Area.PLATE_PACK,
}

has_operational_role = lambda user: user.is_authenticated and user.roles.filter(code__in={Role.Codes.TUNNEL_SUPERVISOR, Role.Codes.PRODUCTION_MANAGER}).exists()


def require_roles(user, *role_codes):
    if not user.is_authenticated or not user.has_role(*role_codes):
        raise PermissionDenied("No tiene permiso para realizar esta acción.")


def can_view_production(user, production):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.roles.filter(code__in=READ_ALL_ROLES).exists():
        return True
    return production.assignments.filter(user=user, shift=production.shift, active=True).exists()


def can_view_crew_control(user, production):
    if not can_view_production(user, production):
        return False
    if user.is_superuser or user.roles.filter(code__in=READ_ALL_ROLES).exists():
        return True
    return production.assignments.filter(
        user=user,
        shift=production.shift,
        area__in=CREW_CONTROL_AREAS,
        active=True,
    ).exists()


def require_area_assignment(user, production, area, *, tunnel=None):
    if user.is_superuser or user.roles.filter(code__in=MANAGER_ROLES).exists():
        return
    filters = {"user": user, "production": production, "area": area, "shift": production.shift, "active": True}
    if tunnel is not None:
        filters["tunnel"] = tunnel
    if not AreaAssignment.objects.filter(**filters).exists():
        raise PermissionDenied("No está asignado a esta área, turno o túnel para la producción.")
