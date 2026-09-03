from django.db import migrations


def make_owner_admin(apps, schema_editor):
    from django.contrib.auth.hashers import make_password

    User = apps.get_model("productions", "User")
    Role = apps.get_model("productions", "Role")

    email = "cristhiancruzado2002@gmail.com"
    user = User.objects.filter(email__iexact=email).first()

    if user is None:
        user = User.objects.create(
            email=email,
            username=email.split("@")[0],
            password=make_password(None),
            is_active=True,
            is_staff=True,
            registration_status="ACTIVE",
        )

    user.is_active = True
    user.is_staff = True
    user.registration_status = "ACTIVE"
    user.save(update_fields=["is_active", "is_staff", "registration_status"])

    role, _ = Role.objects.get_or_create(
        code="ADMIN",
        defaults={"name": "Administrador", "description": "Acceso total al sistema."},
    )
    user.roles.add(role)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0029_platepackworker_tunnelmanualbalance_tunnelpackworker"),
    ]

    operations = [
        migrations.RunPython(make_owner_admin, noop),
    ]
