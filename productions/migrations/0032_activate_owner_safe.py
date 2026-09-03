from django.db import migrations


def activate_owner(apps, schema_editor):
    User = apps.get_model("productions", "User")
    Role = apps.get_model("productions", "Role")

    email = "cristhiancruzado2002@gmail.com"
    users = User.objects.filter(email__iexact=email)

    if users.exists():
        for user in users:
            user.is_active = True
            user.registration_status = "ACTIVE"
            user.is_staff = True
            user.save()

            admin_role = Role.objects.filter(code="ADMIN").first()
            if admin_role:
                user.roles.add(admin_role)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0031_fix_owner_sql"),
    ]

    operations = [
        migrations.RunPython(activate_owner, noop),
    ]
