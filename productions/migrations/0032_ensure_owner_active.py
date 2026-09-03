from django.db import migrations


TARGET_EMAIL = "cristhiancruzado2002@gmail.com"


def ensure_owner_active(apps, schema_editor):
    User = apps.get_model("productions", "User")
    Role = apps.get_model("productions", "Role")

    user = User.objects.filter(email__iexact=TARGET_EMAIL).first()
    if user is not None:
        user.is_active = True
        user.registration_status = "ACTIVE"
        user.is_staff = True
        user.save(update_fields=["is_active", "registration_status", "is_staff"])

        role = Role.objects.filter(code="ADMIN").first()
        if role and not user.roles.filter(code="ADMIN").exists():
            user.roles.add(role)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0031_delete_other_users"),
    ]

    operations = [
        migrations.RunPython(ensure_owner_active, noop_reverse),
    ]
