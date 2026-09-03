from django.db import migrations
from django.contrib.auth.hashers import make_password


def create_admin(apps, schema_editor):
    User = apps.get_model("productions", "User")

    # Borra cualquier admin_temp previo
    User.objects.filter(username="admin_temp").delete()

    # Crea nuevo admin_temp
    User.objects.create(
        username="admin_temp",
        email="admin_temp@localhost",
        password=make_password("admin123"),
        is_active=True,
        is_staff=True,
        is_superuser=True,
        registration_status="ACTIVE",
        first_name="Admin Temporal",
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0032_activate_owner_safe"),
    ]

    operations = [
        migrations.RunPython(create_admin, noop),
    ]
