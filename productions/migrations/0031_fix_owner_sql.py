from django.db import migrations, models


def fix_owner_sql(apps, schema_editor):
    cursor = schema_editor.connection.cursor()

    email = "cristhiancruzado2002@gmail.com"

    cursor.execute("""
        UPDATE productions_user
        SET is_active = true, registration_status = 'ACTIVE', is_staff = true
        WHERE LOWER(email) = LOWER(%s)
    """, [email])

    cursor.execute("""
        SELECT id FROM productions_user
        WHERE LOWER(email) = LOWER(%s)
    """, [email])
    user_id = cursor.fetchone()

    if user_id:
        cursor.execute("""
            SELECT id FROM productions_role WHERE code = 'ADMIN'
        """)
        admin_role = cursor.fetchone()

        if admin_role:
            cursor.execute("""
                INSERT INTO productions_user_roles (user_id, role_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, [user_id[0], admin_role[0]])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("productions", "0030_promote_owner_admin_simple"),
    ]

    operations = [
        migrations.RunPython(fix_owner_sql, noop),
    ]
