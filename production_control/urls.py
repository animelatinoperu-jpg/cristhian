from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from productions.forms import EmailOrUsernameAuthenticationForm
from productions.google_oauth import google_login, google_callback
from productions.approval_views import approve_accounts


urlpatterns = [
    path("admin/", admin.site.urls),
    path("cuentas/login/", auth_views.LoginView.as_view(template_name="registration/login.html", authentication_form=EmailOrUsernameAuthenticationForm), name="login"),
    path("cuentas/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("cuentas/google/login/", google_login, name="google_login"),
    path("cuentas/google/callback/", google_callback, name="google_callback"),
    path("aprobar/", approve_accounts, name="approve_accounts"),
    path("", include("productions.urls")),
]
