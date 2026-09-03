import logging
import requests
from django.conf import settings
from django.contrib.auth import login
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USER_INFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


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

        from productions.models import User
        user, created = User.objects.get_or_create(
            email=email,
            defaults={"username": email.split("@")[0], "first_name": name}
        )

        login(request, user, backend="productions.auth_backends.LockoutBackend")
        return redirect("productions:list")

    except Exception:
        logger.exception("Unexpected error in Google OAuth callback")
        return redirect("login")
