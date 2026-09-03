from .request_context import current_request


class AuditRequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Activa automáticamente todas las cuentas PENDING (Google OAuth)
        # en cada request para permitir ingreso inmediato
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("""
                    UPDATE productions_user
                    SET is_active = true,
                        registration_status = 'ACTIVE'
                    WHERE registration_status = 'PENDING'
                """)
        except Exception:
            pass

        token = current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            current_request.reset(token)
