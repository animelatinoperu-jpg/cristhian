from django.core.management.base import BaseCommand

from productions.models import Role


class Command(BaseCommand):
    help = "Crea o actualiza los roles base de la aplicación de forma idempotente."

    def handle(self, *args, **options):
        created = 0
        updated = 0
        for code, label in Role.Codes.choices:
            role, was_created = Role.objects.update_or_create(
                code=code,
                defaults={"name": label},
            )
            created += int(was_created)
            updated += int(not was_created)
        self.stdout.write(self.style.SUCCESS(f"Roles listos: {created} creados, {updated} actualizados."))
