from django.db import migrations
from django.contrib.auth.hashers import make_password


def recreate_owner(apps, schema_editor):
    User = apps.get_model("productions", "User")
    Role = apps.get_model("productions", "Role")

    email = "cristhiancruzado2002@gmail.com"

    User.objects.filter(email__iexact=email).delete()

    user = User.objects.create(
        email=email,
        username=email.split("@")[0],
        password=make_password(None),
        is_active=True,
        is_staff=True,
        is_superuser=False,
        registration_status="ACTIVE",
    )

    role, _ = Role.objects.get_or_create(
        code="ADMIN",
        defaults={"name": "Administrador", "description": ""},
    )
    user.roles.add(role)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0031_fix_owner_sql"),
    ]

    operations = [
        migrations.RunPython(recreate_owner, noop),
    ]
