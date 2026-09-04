from .request_context import current_request


class AuditRequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # No se activan cuentas aquí. Antes este middleware ejecutaba en cada
        # request un UPDATE que ponía en ACTIVE a todas las cuentas PENDING,
        # lo que anulaba por completo la aprobación manual: cualquiera que se
        # registrara quedaba habilitado solo, y una cuenta que un administrador
        # dejaba pendiente se reactivaba sola en el siguiente clic. La
        # aprobación se hace desde Usuarios (UserAccessUpdateView).
        token = current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            current_request.reset(token)
