from django.contrib.auth.hashers import make_password
from django.db import migrations


TARGET_EMAIL = "cristhiancruzado2002@gmail.com"


def promote_to_admin(apps, schema_editor):
    User = apps.get_model("productions", "User")
    Role = apps.get_model("productions", "Role")

    role, _ = Role.objects.get_or_create(
        code="ADMIN",
        defaults={"name": "Administrador", "description": "Acceso total al sistema."},
    )

    user = User.objects.filter(email__iexact=TARGET_EMAIL).first()
    if user is None:
        username = TARGET_EMAIL.split("@")[0]
        base_username = username
        suffix = 1
        while User.objects.filter(username__iexact=username).exists():
            suffix += 1
            username = f"{base_username}{suffix}"
        user = User(
            username=username,
            email=TARGET_EMAIL,
            password=make_password(None),
            is_active=True,
            is_staff=True,
            registration_status="ACTIVE",
        )
        user.save()
    else:
        user.is_active = True
        user.registration_status = "ACTIVE"
        user.is_staff = True
        user.save(update_fields=["is_active", "registration_status", "is_staff"])

    user.roles.add(role)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0029_platepackworker_tunnelmanualbalance_tunnelpackworker"),
    ]

    operations = [
        migrations.RunPython(promote_to_admin, noop_reverse),
    ]
