import tempfile

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings


class TemporaryMediaTestRunner(DiscoverRunner):
    """Aísla los FileField de prueba para no contaminar el almacenamiento privado."""

    def setup_test_environment(self, **kwargs):
        self._media_directory = tempfile.TemporaryDirectory(prefix="production-tests-")
        self._media_override = override_settings(MEDIA_ROOT=self._media_directory.name)
        self._media_override.enable()
        return super().setup_test_environment(**kwargs)

    def teardown_test_environment(self, **kwargs):
        try:
            return super().teardown_test_environment(**kwargs)
        finally:
            self._media_override.disable()
            self._media_directory.cleanup()
