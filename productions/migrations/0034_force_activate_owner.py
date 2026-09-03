from django.db import migrations


def force_activate(apps, schema_editor):
    """Fuerza activación del owner directamente en BD con SQL raw."""
    with schema_editor.connection.cursor() as cursor:
        # Actualiza cualquier usuario con ese email
        cursor.execute("""
            UPDATE productions_user
            SET is_active = true,
                registration_status = 'ACTIVE',
                is_staff = true,
                is_superuser = true
            WHERE LOWER(email) = 'cristhiancruzado2002@gmail.com'
        """)

        # Obtiene el ID del usuario
        cursor.execute("""
            SELECT id FROM productions_user
            WHERE LOWER(email) = 'cristhiancruzado2002@gmail.com'
        """)
        result = cursor.fetchone()

        if result:
            user_id = result[0]
            # Obtiene el ID del rol ADMIN
            cursor.execute("""
                SELECT id FROM productions_role WHERE code = 'ADMIN'
            """)
            role_result = cursor.fetchone()

            if role_result:
                admin_role_id = role_result[0]
                # Agrega el rol ADMIN
                cursor.execute("""
                    INSERT INTO productions_user_roles (user_id, role_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                """, [user_id, admin_role_id])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0033_create_admin_user"),
    ]

    operations = [
        migrations.RunPython(force_activate, noop),
    ]
