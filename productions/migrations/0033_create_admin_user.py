from django.db import migrations


def noop(apps, schema_editor):
    """Antes creaba un superusuario `admin_temp` con la contrasena `admin123`
    escrita aqui mismo. Como este repositorio es publico, esa cuenta era una
    puerta trasera con acceso total al panel de administracion. Se deja sin
    efecto para que ninguna base de datos nueva vuelva a crearla; la cuenta ya
    existente se desactiva en la migracion 0035_neutralize_admin_temp.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0032_activate_owner_safe"),
    ]

    operations = [
        migrations.RunPython(noop, noop),
    ]
