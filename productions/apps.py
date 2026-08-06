from django.apps import AppConfig


class ProductionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "productions"
    verbose_name = "Control de producción"

    def ready(self):
        from . import signals  # noqa: F401
