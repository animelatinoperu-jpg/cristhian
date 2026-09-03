from datetime import timedelta

from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.db.models import F
from django.utils import timezone


class LockoutBackend(ModelBackend):
    """Bloqueo temporal después de intentos fallidos, sin revelar si la cuenta existe."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        field = User.USERNAME_FIELD
        lookup = {field: username or kwargs.get(field)}
        try:
            candidate = User.objects.get(**lookup)
        except User.DoesNotExist:
            # Intenta buscar por email también
            if username:
                try:
                    candidate = User.objects.get(email=username)
                except User.DoesNotExist:
                    User().set_password(password)
                    return None
            else:
                User().set_password(password)
                return None
        if not candidate.is_active:
            User().set_password(password)
            return None
        if candidate.locked_until and candidate.locked_until > timezone.now():
            return None
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is not None:
            if user.failed_login_attempts or user.locked_until:
                User.objects.filter(pk=user.pk).update(failed_login_attempts=0, locked_until=None)
            return user
        User.objects.filter(pk=candidate.pk).update(failed_login_attempts=F("failed_login_attempts") + 1)
        candidate.refresh_from_db(fields=["failed_login_attempts"])
        if candidate.failed_login_attempts >= settings.LOGIN_FAILURE_LIMIT:
            User.objects.filter(pk=candidate.pk).update(locked_until=timezone.now() + timedelta(minutes=settings.LOGIN_LOCK_MINUTES))
        return None
