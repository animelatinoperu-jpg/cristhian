from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

# from productions.forms import PlantAuthenticationForm


urlpatterns = [
    path("admin/", admin.site.urls),
    path("cuentas/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("cuentas/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("productions.urls")),
]
