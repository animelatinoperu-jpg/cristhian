import datetime as dt

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import authenticate
from django.test import TestCase, override_settings
from django.urls import reverse

from productions.models import AreaAssignment, Customer, Product, ProductionOrder, Role, TemplateVersion, Tunnel, User
from productions.services.permissions import require_area_assignment
from productions.services.workflow import transition_production


class PermissionWorkflowTests(TestCase):
    def setUp(self):
        self.manager_role = Role.objects.create(code=Role.Codes.PRODUCTION_MANAGER, name="Jefe")
        self.tunnel_role = Role.objects.create(code=Role.Codes.TUNNEL, name="Túnel")
        self.manager = User.objects.create_user("manager", password="Secure-test-123")
        self.manager.roles.add(self.manager_role)
        self.supervisor = User.objects.create_user("supervisor", password="Secure-test-123")
        self.supervisor.roles.add(self.tunnel_role)
        customer = Customer.objects.create(name="Cliente")
        product = Product.objects.create(code="P001", description="Producto")
        template = TemplateVersion.objects.create(code="PP-V1", file=SimpleUploadedFile("t.xlsm", b"x"), original_filename="t.xlsm", sha256="b" * 64, uploaded_by=self.manager)
        self.production = ProductionOrder.objects.create(number=1, plant_lot="L1", customer=customer, process="Proceso", main_product=product, reception_date=dt.date.today(), production_date=dt.date.today(), shift=ProductionOrder.Shift.DAY, template_version=template, created_by=self.manager)
        self.t1 = Tunnel.objects.create(code="T1", name="Túnel 1")
        self.t2 = Tunnel.objects.create(code="T2", name="Túnel 2")
        AreaAssignment.objects.create(production=self.production, user=self.supervisor, area=AreaAssignment.Area.TUNNEL, shift=ProductionOrder.Shift.DAY, tunnel=self.t1)

    def test_login_required(self):
        response = self.client.get(reverse("productions:detail", args=[self.production.pk]))
        self.assertEqual(response.status_code, 302)

    def test_supervisor_cannot_access_unassigned_tunnel(self):
        require_area_assignment(self.supervisor, self.production, AreaAssignment.Area.TUNNEL, tunnel=self.t1)
        with self.assertRaises(PermissionDenied):
            require_area_assignment(self.supervisor, self.production, AreaAssignment.Area.TUNNEL, tunnel=self.t2)

    def test_production_list_is_scoped_to_assignment_shift(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("productions:list"))
        self.assertContains(response, "L1")
        AreaAssignment.objects.filter(user=self.supervisor).update(shift=ProductionOrder.Shift.NIGHT)
        response = self.client.get(reverse("productions:list"))
        self.assertEqual(list(response.context["productions"]), [])

    def test_optimistic_version_rejects_stale_transition(self):
        stale = self.production.version
        transition_production(production_id=self.production.pk, target_status=ProductionOrder.Status.OPEN, user=self.manager, expected_version=stale)
        with self.assertRaises(ValidationError):
            transition_production(production_id=self.production.pk, target_status=ProductionOrder.Status.IN_PROGRESS, user=self.manager, expected_version=stale)

    def test_manager_can_open_production(self):
        result = transition_production(production_id=self.production.pk, target_status=ProductionOrder.Status.OPEN, user=self.manager, expected_version=self.production.version)
        self.assertEqual(result.status, ProductionOrder.Status.OPEN)
        self.assertTrue(result.audit_logs.filter(action="TRANSITION").exists())

    def test_operational_areas_render_as_industrial_gallery(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("productions:detail", args=[self.production.pk]))
        self.assertContains(response, "production-stage-card")
        self.assertContains(response, "production-stage-card__image")
        self.assertContains(response, "production-stage-card__overlay")
        self.assertContains(response, "data-module-strip")
        self.assertContains(response, 'data-module-scroll="-1"')
        self.assertContains(response, 'data-module-scroll="1"')
        self.assertContains(response, "Seleccione una etapa para registrar su avance.")

    def test_manager_can_remove_review_and_restore_a_production(self):
        self.client.force_login(self.manager)
        detail = self.client.get(reverse("productions:detail", args=[self.production.pk]))
        self.assertContains(detail, "Eliminar parte de producción")

        response = self.client.post(
            reverse("productions:transition", args=[self.production.pk]),
            {
                "expected_version": self.production.version,
                "target_status": ProductionOrder.Status.VOID,
                "reason": "Parte creado con datos incorrectos",
            },
        )
        self.assertRedirects(response, reverse("productions:list"))
        self.production.refresh_from_db()
        self.assertEqual(self.production.status, ProductionOrder.Status.VOID)
        self.assertTrue(self.production.audit_logs.filter(action="VOID", reason__icontains="incorrectos").exists())

        active_list = self.client.get(reverse("productions:list"))
        self.assertNotContains(active_list, "L1")
        deleted_list = self.client.get(reverse("productions:list") + "?view=void")
        self.assertContains(deleted_list, "L1")

        restored = transition_production(
            production_id=self.production.pk,
            target_status=ProductionOrder.Status.DRAFT,
            user=self.manager,
            expected_version=self.production.version,
            reason="Eliminación accidental",
        )
        self.assertEqual(restored.status, ProductionOrder.Status.DRAFT)

    def test_void_number_can_be_reused_but_old_part_cannot_conflict_on_restore(self):
        deleted = transition_production(
            production_id=self.production.pk,
            target_status=ProductionOrder.Status.VOID,
            user=self.manager,
            expected_version=self.production.version,
            reason="Se reemplazará el parte",
        )
        ProductionOrder.objects.create(
            number=self.production.number,
            plant_lot="L1-CORREGIDO",
            customer=self.production.customer,
            process=self.production.process,
            main_product=self.production.main_product,
            reception_date=self.production.reception_date,
            production_date=self.production.production_date,
            shift=self.production.shift,
            template_version=self.production.template_version,
            created_by=self.manager,
        )

        with self.assertRaises(ValidationError):
            transition_production(
                production_id=deleted.pk,
                target_status=ProductionOrder.Status.DRAFT,
                user=self.manager,
                expected_version=deleted.version,
                reason="Intento de restauración",
            )

    @override_settings(LOGIN_FAILURE_LIMIT=3, LOGIN_LOCK_MINUTES=15)
    def test_account_is_temporarily_locked_after_failed_attempts(self):
        for _ in range(3):
            self.assertIsNone(authenticate(username="supervisor", password="incorrecta"))
        self.supervisor.refresh_from_db()
        self.assertIsNotNone(self.supervisor.locked_until)
        self.assertIsNone(authenticate(username="supervisor", password="Secure-test-123"))
