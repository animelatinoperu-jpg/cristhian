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
    User,
)


class ProductionEditingTests(TestCase):
    def setUp(self):
        manager_role = Role.objects.create(
            code=Role.Codes.PRODUCTION_MANAGER,
            name="Jefe de producción",
        )
        self.manager = User.objects.create_user("production-editor", password="Secure-test-123")
        self.manager.roles.add(manager_role)
        self.ordinary_user = User.objects.create_user("ordinary-user", password="Secure-test-123")
        self.customer = Customer.objects.create(name="Cliente original")
        self.new_customer = Customer.objects.create(name="Cliente corregido")
        self.main_product = Product.objects.create(
            code="POTA-GRANEL",
            description="POTA A GRANEL",
        )
        self.template = TemplateVersion.objects.create(
            code="PP-EDIT",
            file=SimpleUploadedFile("template.xlsm", b"template-edit"),
            original_filename="template.xlsm",
            sha256="8" * 64,
            uploaded_by=self.manager,
            active=True,
            mapping_version="v2",
        )
        self.production = ProductionOrder.objects.create(
            number=1000,
            plant_lot="LOTE-ORIGINAL",
            customer_lot="CLIENTE-01",
            customer=self.customer,
            process="Proceso original",
            main_product=self.main_product,
            reception_date=dt.date(2026, 7, 14),
            production_date=dt.date(2026, 7, 14),
            shift=ProductionOrder.Shift.DAY,
            template_version=self.template,
            created_by=self.manager,
            status=ProductionOrder.Status.IN_PROGRESS,
        )

    def test_manager_sees_edit_action_on_list_and_detail(self):
        self.client.force_login(self.manager)
        edit_url = reverse("productions:update", args=[self.production.pk])

        list_response = self.client.get(reverse("productions:list"))
        detail_response = self.client.get(
            reverse("productions:detail", args=[self.production.pk])
        )

        self.assertContains(list_response, "Editar datos")
        self.assertContains(list_response, edit_url)
        self.assertContains(detail_response, "Editar datos del PP")
        self.assertContains(detail_response, edit_url)

    def test_manager_can_edit_main_production_data_with_audit_log(self):
        self.client.force_login(self.manager)
        edit_url = reverse("productions:update", args=[self.production.pk])

        response = self.client.get(edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Editar PP 1000")
        self.assertEqual(response.context["form"]["plant_lot"].value(), "LOTE-ORIGINAL")
        self.assertNotContains(response, "Observaciones del vehículo")
        self.assertNotContains(response, "Observaciones de placas")
        self.assertNotContains(response, ">Observaciones<")

        response = self.client.post(
            edit_url,
            {
                "number": "1001",
                "plant_lot": "LOTE-CORREGIDO",
                "customer_lot": "CLIENTE-02",
                "customer": str(self.new_customer.pk),
                "process": "Proceso corregido",
                "main_product": str(self.main_product.pk),
                "reception_date": "2026-07-15",
                "packaging_date": "2026-07-16",
                "series": "SERIE-2",
                "vehicle_notes": "Vehículo corregido",
                "plate_notes": "Placas corregidas",
                "observations": "Observación corregida",
                "template_version": str(self.template.pk),
            },
        )

        self.assertRedirects(
            response,
            reverse("productions:detail", args=[self.production.pk]),
        )
        self.production.refresh_from_db()
        self.assertEqual(self.production.number, 1001)
        self.assertEqual(self.production.plant_lot, "LOTE-CORREGIDO")
        self.assertEqual(self.production.customer, self.new_customer)
        self.assertEqual(self.production.reception_date, dt.date(2026, 7, 15))
        self.assertEqual(self.production.production_date, dt.date(2026, 7, 15))
        self.assertEqual(self.production.packaging_date, dt.date(2026, 7, 16))
        self.assertEqual(self.production.customer_lot, "PPF15072026")
        self.assertEqual(self.production.series, "001")
        audit = AuditLog.objects.get(
            module="production",
            record_pk=str(self.production.pk),
            action=AuditLog.Action.UPDATE,
        )
        self.assertEqual(audit.old_value["plant_lot"], "LOTE-ORIGINAL")
        self.assertEqual(audit.new_value["plant_lot"], "LOTE-CORREGIDO")
        self.assertEqual(audit.new_value["customer"], "Cliente corregido")

    def test_non_manager_cannot_edit_production(self):
        self.client.force_login(self.ordinary_user)

        response = self.client.get(
            reverse("productions:update", args=[self.production.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_approved_production_must_be_reopened_before_editing(self):
        self.production.status = ProductionOrder.Status.APPROVED
        self.production.save(update_fields=["status"])
        self.client.force_login(self.manager)

        edit_response = self.client.get(
            reverse("productions:update", args=[self.production.pk])
        )
        list_response = self.client.get(reverse("productions:list"))
        detail_response = self.client.get(
            reverse("productions:detail", args=[self.production.pk])
        )

        self.assertEqual(edit_response.status_code, 403)
        self.assertNotContains(list_response, "Editar datos")
        self.assertNotContains(detail_response, "Editar datos del PP")
