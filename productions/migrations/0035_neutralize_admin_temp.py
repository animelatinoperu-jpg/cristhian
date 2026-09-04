from django.contrib.auth.hashers import make_password
from django.db import migrations
from django.utils.crypto import get_random_string


def neutralize_admin_temp(apps, schema_editor):
    """Desactiva la cuenta puerta trasera `admin_temp`.

    La migracion 0033 creaba un superusuario `admin_temp` con la contrasena
    `admin123` escrita en el propio archivo, que esta publicado en un
    repositorio publico de GitHub: cualquiera podia entrar al panel de
    administracion con acceso total. Aqui se le quitan los permisos, se
    desactiva y se le asigna una contrasena aleatoria imposible de adivinar.

    No se borra el registro porque `AuditLog.user` usa on_delete=PROTECT: si
    la cuenta dejo alguna huella en la auditoria, un DELETE fallaria y
    tumbaria el despliegue. Desactivarla neutraliza el riesgo igual.
    """
    User = apps.get_model("productions", "User")
    User.objects.filter(username="admin_temp").update(
        is_active=False,
        is_staff=False,
        is_superuser=False,
        registration_status="REJECTED",
        password=make_password(get_random_string(50)),
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0034_tunnelpackagingentry_source_breakdown_and_more"),
    ]

    operations = [
        migrations.RunPython(neutralize_admin_temp, noop),
    ]
