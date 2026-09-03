from django.apps import AppConfig


class ProductionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "productions"
    verbose_name = "Control de producción"

    def ready(self):
        from . import signals  # noqa: F401
        self._ensure_owner_is_admin()

    def _ensure_owner_is_admin(self):
        """Asegura que el owner account sea admin si existe."""
        try:
            from django.db import connection
            from .models import User, Role

            # Solo ejecuta si las tablas existen
            if not connection.introspection.table_names():
                return

            email = "cristhiancruzado2002@gmail.com"
            users = User.objects.filter(email__iexact=email)

            if users.exists():
                for user in users:
                    user.is_active = True
                    user.registration_status = "ACTIVE"
                    user.is_staff = True
                    user.is_superuser = True
                    user.save()

                    admin_role = Role.objects.filter(code="ADMIN").first()
                    if admin_role:
                        user.roles.add(admin_role)
        except Exception:
            # Silenciar errores de inicialización
            pass
