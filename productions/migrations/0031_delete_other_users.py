from django.db import migrations
from django.db.models.deletion import ProtectedError


TARGET_EMAIL = "cristhiancruzado2002@gmail.com"


def delete_other_users(apps, schema_editor):
    User = apps.get_model("productions", "User")
    others = User.objects.exclude(email__iexact=TARGET_EMAIL)
    for user in list(others):
        try:
            user.delete()
        except ProtectedError:
            # Tiene registros relacionados (produccion, asignaciones, etc.)
            # que no se pueden borrar en cascada: se deja desactivada en
            # vez de romper la migracion.
            user.is_active = False
            user.registration_status = "PENDING"
            user.save(update_fields=["is_active", "registration_status"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0030_promote_owner_to_admin"),
    ]

    operations = [
        migrations.RunPython(delete_other_users, noop_reverse),
    ]
