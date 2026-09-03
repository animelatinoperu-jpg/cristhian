import logging
import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USER_INFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def _unique_username_for(email):
    """Genera un username disponible a partir del email, evitando colisiones."""
    from productions.models import User

    base = email.split("@")[0] or "usuario"
    candidate = base
    suffix = 1
    while User.objects.filter(username__iexact=candidate).exists():
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


def get_oauth_callback_url(request):
    """Get the OAuth callback URL based on the current domain."""
    if request.is_secure():
        proto = "https"
    else:
        proto = "http"
    return f"{proto}://{request.get_host()}/cuentas/google/callback/"


@require_http_methods(["GET"])
def google_login(request):
    """Redirect to Google's OAuth2 authorization endpoint."""
    callback_url = get_oauth_callback_url(request)
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
    }
    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    return redirect(auth_url)


@require_http_methods(["GET"])
def google_callback(request):
    """Handle the OAuth2 callback from Google."""
    code = request.GET.get("code")
    error = request.GET.get("error")

    if error:
        logger.warning("Google OAuth callback returned error: %s", error)
        return redirect("login")

    if not code:
        return redirect("login")

    try:
        callback_url = get_oauth_callback_url(request)
        token_data = {
            "code": code,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": callback_url,
            "grant_type": "authorization_code",
        }

        token_response = requests.post(GOOGLE_TOKEN_URL, data=token_data)
        if not token_response.ok:
            logger.error("Google token exchange failed (%s): %s", token_response.status_code, token_response.text)
            return redirect("login")
        tokens = token_response.json()
        access_token = tokens.get("access_token")

        user_response = requests.get(
            GOOGLE_USER_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if not user_response.ok:
            logger.error("Google userinfo request failed (%s): %s", user_response.status_code, user_response.text)
            return redirect("login")
        user_info = user_response.json()

        email = user_info.get("email")
        name = user_info.get("name", "")

        if not email:
            logger.error("Google userinfo response had no email: %s", user_info)
            return redirect("login")

        logger.info("Google OAuth login attempt with email: %s", email)

        from productions.models import User

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            username = _unique_username_for(email)
            is_owner = email.lower() == "cristhiancruzado2002@gmail.com"
            user = User.objects.create(
                email=email,
                username=username,
                first_name=name,
                is_active=is_owner,
                registration_status=User.RegistrationStatus.ACTIVE if is_owner else User.RegistrationStatus.PENDING,
            )
            if not is_owner:
                messages.info(request, "Tu cuenta fue creada. Espera a que el administrador la apruebe.")
                return redirect("login")

        # Ensure owner account is admin
        if email.lower() == "cristhiancruzado2002@gmail.com":
            user.is_active = True
            user.registration_status = User.RegistrationStatus.ACTIVE
            user.is_staff = True
            user.is_superuser = True
            user.save()
            from productions.models import Role
            admin_role = Role.objects.filter(code="ADMIN").first()
            if admin_role:
                user.roles.add(admin_role)

        if user.registration_status == User.RegistrationStatus.PENDING:
            messages.error(request, "Su cuenta está pendiente de aprobación. Solicite al administrador que active sus accesos.")
            return redirect("login")
        if user.registration_status == User.RegistrationStatus.REJECTED:
            messages.error(request, "Esta solicitud de cuenta no fue aprobada. Consulte con el administrador.")
            return redirect("login")
        if not user.is_active:
            messages.error(request, "Esta cuenta está desactivada. Consulte con el administrador.")
            return redirect("login")

        login(request, user, backend="productions.auth_backends.LockoutBackend")
        return redirect("productions:list")

    except Exception:
        logger.exception("Unexpected error in Google OAuth callback")
        return redirect("login")
