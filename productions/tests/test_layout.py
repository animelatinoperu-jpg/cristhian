from django.utils import timezone

from productions.models import Product, TunnelEntry, TunnelRack
from productions.services.layout import ensure_tunnel_racks
from productions.tests.test_tunnel_batch import TunnelBatchCaptureTests


class EnsureTunnelRacksTests(TunnelBatchCaptureTests):
    """Cubre ensure_tunnel_racks: creacion/eliminacion idempotente de racks
    y el numero de consultas que ejecuta (reutiliza el setUp de los tests
    de captura por tunel)."""

    def test_idempotent_when_racks_already_match_layout(self):
        ensure_tunnel_racks(self.fill)  # primera llamada: crea el layout inicial
        codes_before = sorted(r.code for r in self.fill.racks.all())
        ensure_tunnel_racks(self.fill)  # segunda llamada: no debe cambiar nada
        codes_after = sorted(r.code for r in self.fill.racks.all())
        self.assertEqual(codes_before, codes_after)
        self.assertTrue(codes_before)

    def test_orphan_rack_without_entries_is_deleted(self):
        ensure_tunnel_racks(self.fill)
        orphan = TunnelRack.objects.create(
            fill=self.fill, code="ZZZ_ORPHAN", position_key="orphan", max_trays=50
        )
        ensure_tunnel_racks(self.fill)
        self.assertFalse(TunnelRack.objects.filter(pk=orphan.pk).exists())

    def test_orphan_rack_with_active_entry_is_kept(self):
        ensure_tunnel_racks(self.fill)
        orphan = TunnelRack.objects.create(
            fill=self.fill, code="ZZZ_ORPHAN2", position_key="orphan2", max_trays=50
        )
        product = Product.objects.first()
        TunnelEntry.objects.create(
            production=self.fill.production,
            responsible=self.user,
            rack=orphan,
            product=product,
            tray_count=5,
            date=timezone.now().date(),
            observation="",
        )
        ensure_tunnel_racks(self.fill)
        self.assertTrue(TunnelRack.objects.filter(pk=orphan.pk).exists())

    def test_missing_configured_rack_is_recreated(self):
        ensure_tunnel_racks(self.fill)
        rack = self.fill.racks.first()
        code = rack.code
        rack.delete()
        self.assertFalse(TunnelRack.objects.filter(fill=self.fill, code=code).exists())
        ensure_tunnel_racks(self.fill)
        self.assertTrue(TunnelRack.objects.filter(fill=self.fill, code=code).exists())

    def test_query_count_is_small_once_layout_already_exists(self):
        ensure_tunnel_racks(self.fill)  # crea el layout inicial (no medido)
        with self.assertNumQueries(3):
            ensure_tunnel_racks(self.fill)
