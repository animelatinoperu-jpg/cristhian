from .models import Role, User

# Bandera para reactivar la lista general de botones de descarga de reportes
# (los reportes ya están disponibles dentro de cada caja/módulo correspondiente).
MOSTRAR_REPORTES_GENERALES = False


def navigation_permissions(request):
    user = request.user
    if not user.is_authenticated:
        return {
            "can_manage_catalogs": False,
            "can_manage_users": False,
            "pending_user_count": 0,
            "mostrar_reportes_generales": MOSTRAR_REPORTES_GENERALES,
        }

    can_manage_users = user.is_superuser or user.roles.filter(code=Role.Codes.ADMIN).exists()
    can_manage_catalogs = can_manage_users or user.roles.filter(code=Role.Codes.PRODUCTION_MANAGER).exists()
    return {
        "can_manage_catalogs": can_manage_catalogs,
        "can_manage_users": can_manage_users,
        "pending_user_count": (
            User.objects.filter(registration_status=User.RegistrationStatus.PENDING).count()
            if can_manage_users
            else 0
        ),
        "mostrar_reportes_generales": MOSTRAR_REPORTES_GENERALES,
    }
