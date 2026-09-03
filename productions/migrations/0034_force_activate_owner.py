from django.db import migrations


def force_activate(apps, schema_editor):
    """Fuerza activación del owner directamente en BD con SQL raw."""
    try:
        User = apps.get_model("productions", "User")
        Role = apps.get_model("productions", "Role")

        # Busca y actualiza el usuario
        users = User.objects.filter(email__iexact="cristhiancruzado2002@gmail.com")
        for user in users:
            user.is_active = True
            user.registration_status = "ACTIVE"
            user.is_staff = True
            user.is_superuser = True
            user.save()

            # Agrega rol ADMIN
            admin_role = Role.objects.filter(code="ADMIN").first()
            if admin_role:
                user.roles.add(admin_role)
    except Exception:
        # Silenciar cualquier error en la migración para no crashear el deploy
        pass


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0033_create_admin_user"),
    ]

    operations = [
        migrations.RunPython(force_activate, noop),
    ]
