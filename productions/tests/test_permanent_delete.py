import datetime as dt

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from productions.models import (
    AuditLog,
    Customer,
    Product,
    ProductionOrder,
    Role,
    TemplateVersion,
    Tunnel,
    TunnelEntry,
    TunnelFill,
    TunnelRack,
    User,
)
from productions.services.permanent_delete import permanently_delete_production
from productions.services.workflow import transition_production


class PermanentDeleteTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("manager", password="Secure-test-123")
        self.manager.roles.add(
            Role.objects.create(code=Role.Codes.PRODUCTION_MANAGER, name="Jefe")
        )
        customer = Customer.objects.create(name="Cliente")
        self.product = Product.objects.create(code="P001", description="Producto")
        template = TemplateVersion.objects.create(
            code="PP-V1",
            file=SimpleUploadedFile("t.xlsm", b"x"),
            original_filename="t.xlsm",
            sha256="b" * 64,
            uploaded_by=self.manager,
        )
        self.production = ProductionOrder.objects.create(
            number=1,
            plant_lot="L1",
            customer=customer,
            process="Proceso",
            main_product=self.product,
            reception_date=dt.date.today(),
            production_date=dt.date.today(),
            shift=ProductionOrder.Shift.DAY,
            template_version=template,
            created_by=self.manager,
        )
        self.tunnel = Tunnel.objects.create(code="T1", name="Túnel 1")

    def _void(self):
        return transition_production(
            production_id=self.production.pk,
            target_status=ProductionOrder.Status.VOID,
            user=self.manager,
            expected_version=self.production.version,
            reason="Borrado de prueba",
        )

    def _add_tunnel_data(self):
        fill = TunnelFill.objects.create(
            production=self.production,
            tunnel=self.tunnel,
            fill_number=1,
            date=dt.date.today(),
            supervisor=self.manager,
        )
        rack = TunnelRack.objects.create(fill=fill, code="A1", position_key="A1")
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.manager,
            rack=rack,
            product=self.product,
            tray_count=10,
            date=dt.date.today(),
        )
        return fill

    def test_only_void_productions_can_be_permanently_deleted(self):
        with self.assertRaises(ValidationError):
            permanently_delete_production(
                production_id=self.production.pk,
                expected_version=self.production.version,
            )

    def test_void_production_is_permanently_removed_with_all_related_records(self):
        self._void()
        fill = self._add_tunnel_data()
        self.production.refresh_from_db()
        self.assertGreater(self.production.audit_logs.count(), 0)

        report = permanently_delete_production(
            production_id=self.production.pk,
            expected_version=self.production.version,
        )

        self.assertTrue(report["deleted"])
        self.assertGreater(report["tunnel_entries"], 0)
        self.assertGreater(report["audit_logs"], 0)
        self.assertFalse(ProductionOrder.objects.filter(pk=self.production.pk).exists())
        self.assertFalse(TunnelFill.objects.filter(pk=fill.pk).exists())
        self.assertFalse(TunnelRack.objects.filter(fill_id=fill.pk).exists())
        self.assertFalse(TunnelEntry.objects.filter(production_id=self.production.pk).exists())
        self.assertFalse(AuditLog.objects.filter(production_id=self.production.pk).exists())

    def test_stale_version_rejects_permanent_delete(self):
        self._void()
        stale = self.production.version
        self.production.refresh_from_db()
        with self.assertRaises(ValidationError):
            permanently_delete_production(
                production_id=self.production.pk,
                expected_version=stale,
            )
        self.assertTrue(ProductionOrder.objects.filter(pk=self.production.pk).exists())

    def test_hard_delete_button_only_shown_for_void_productions(self):
        self.client.force_login(self.manager)
        detail = self.client.get(
            "/producciones/{}/".format(self.production.pk)
        )
        self.assertNotContains(detail, "Borrar definitivamente")
        self._void()
        self.production.refresh_from_db()
        detail = self.client.get(
            "/producciones/{}/".format(self.production.pk)
        )
        self.assertContains(detail, "Borrar definitivamente")

    def test_hard_delete_view_removes_void_production(self):
        self._void()
        self.production.refresh_from_db()
        self.client.force_login(self.manager)
        response = self.client.post(
            "/producciones/{}/eliminar-definitivo/".format(self.production.pk),
            {
                "expected_version": self.production.version,
                "reason": "Borrado definitivo de prueba",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProductionOrder.objects.filter(pk=self.production.pk).exists())

    def test_hard_delete_view_rejects_non_void_production(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            "/producciones/{}/eliminar-definitivo/".format(self.production.pk),
            {
                "expected_version": self.production.version,
                "reason": "Intento de borrado",
            },
        )
        self.assertRedirects(response, "/producciones/{}/".format(self.production.pk))
        self.assertTrue(ProductionOrder.objects.filter(pk=self.production.pk).exists())
        self.production.refresh_from_db()
        self.assertNotEqual(self.production.status, ProductionOrder.Status.VOID)
