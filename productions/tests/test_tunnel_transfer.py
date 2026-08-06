import datetime as dt

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

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
    User,
)
from productions.services.layout import ensure_tunnel_racks


class TunnelTransferTests(TestCase):
    def setUp(self):
        role = Role.objects.create(code=Role.Codes.PRODUCTION_MANAGER, name="Jefe")
        self.user = User.objects.create_user("manager-transfer", password="test-123")
        self.user.roles.add(role)
        customer = Customer.objects.create(name="Cliente transferencia")
        self.product = Product.objects.create(code="PTR", description="PRODUCTO PRUEBA")
        template = TemplateVersion.objects.create(
            code="PP-TRANSFER",
            file=SimpleUploadedFile("template.xlsm", b"fixture"),
            original_filename="template.xlsm",
            sha256="f" * 64,
            uploaded_by=self.user,
            rules={
                "rack_max_trays": 50,
                "tunnel_racks": {
                    "T1": {
                        "1": [
                            {"code": "R01", "position_key": "T1!A1"},
                            {"code": "R02", "position_key": "T1!A2"},
                        ]
                    },
                    "T2": {
                        "1": [
                            {"code": "R001", "position_key": "T2!B1"},
                            {"code": "R002", "position_key": "T2!B2"},
                        ]
                    },
                },
            },
        )
        self.production = ProductionOrder.objects.create(
            number=999,
            plant_lot="TRANSFER",
            customer=customer,
            process="Pota",
            main_product=self.product,
            reception_date=dt.date(2026, 7, 24),
            production_date=dt.date(2026, 7, 24),
            shift=ProductionOrder.Shift.DAY,
            template_version=template,
            created_by=self.user,
        )
        self.source = Tunnel.objects.create(code="T1", name="Túnel 1")
        self.target = Tunnel.objects.create(code="T2", name="Túnel 2")
        self.fill = TunnelFill.objects.create(
            production=self.production,
            tunnel=self.source,
            fill_number=1,
            date=dt.date(2026, 7, 24),
            start_time=dt.time(8, 0),
            launch_time=dt.time(9, 0),
            end_time=dt.time(21, 0),
            supervisor=self.user,
            observation="Cambio especial",
        )
        ensure_tunnel_racks(self.fill)
        self.rack = self.fill.racks.get(code="R01")
        self.entry = TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=self.rack,
            product=self.product,
            tray_count=14,
            date=self.fill.date,
        )
        self.client.force_login(self.user)
        self.url = reverse(
            "productions:tunnel_fill_transfer",
            args=[self.production.pk, self.fill.pk],
        )

    def test_transfers_fill_records_hours_and_audit_as_one_unit(self):
        response = self.client.post(
            self.url,
            {"target_tunnel": self.target.pk, "reason": "Cambio operativo"},
        )

        self.assertEqual(response.status_code, 302)
        self.fill.refresh_from_db()
        self.rack.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual(self.fill.tunnel, self.target)
        self.assertEqual(self.fill.launch_time, dt.time(9, 0))
        self.assertEqual(self.fill.end_time, dt.time(21, 0))
        self.assertEqual(self.fill.observation, "Cambio especial")
        self.assertEqual(self.rack.code, "R001")
        self.assertEqual(self.rack.position_key, "T2!B1")
        self.assertEqual(self.entry.rack, self.rack)
        self.assertEqual(self.entry.tray_count, 14)
        audit = AuditLog.objects.get(module="tunnel-fill-transfer")
        self.assertEqual(audit.old_value["tunnel"], "T1")
        self.assertEqual(audit.new_value["tunnel"], "T2")

    def test_does_not_merge_with_existing_target_fill(self):
        TunnelFill.objects.create(
            production=self.production,
            tunnel=self.target,
            fill_number=1,
            date=self.fill.date,
            supervisor=self.user,
        )
        response = self.client.post(
            self.url,
            {"target_tunnel": self.target.pk, "reason": "Cambio operativo"},
            follow=True,
        )

        self.fill.refresh_from_db()
        self.assertEqual(self.fill.tunnel, self.source)
        self.assertContains(response, "No se pueden mezclar dos llenadas")
