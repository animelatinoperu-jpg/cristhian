import datetime as dt

from django.contrib.auth import authenticate
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from productions.forms import UserRegistrationForm
from productions.models import (
    AreaAssignment,
    AuditLog,
    Customer,
    Product,
    ProductionOrder,
    Role,
    TemplateVersion,
    Tunnel,
    User,
)


class UserRegistrationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-users",
            email="admin@example.com",
            password="Secure-admin-12345",
        )
        self.tunnel_role = Role.objects.create(code=Role.Codes.TUNNEL, name="Supervisor de túnel")
        self.reception_role = Role.objects.create(code=Role.Codes.RECEPTION, name="Recepción")
        self.manager_role = Role.objects.create(code=Role.Codes.PRODUCTION_MANAGER, name="Jefe de producción")
        customer = Customer.objects.create(name="Cliente de usuarios")
        product = Product.objects.create(code="USR-01", description="Producto de usuarios")
        template = TemplateVersion.objects.create(
            code="PP-USERS",
            file=SimpleUploadedFile("users.xlsm", b"users"),
            original_filename="users.xlsm",
            sha256="c" * 64,
            uploaded_by=self.admin,
        )
        self.production = ProductionOrder.objects.create(
            number=7001,
            plant_lot="LOTE-USUARIOS",
            customer=customer,
            process="Proceso",
            main_product=product,
            reception_date=dt.date.today(),
            production_date=dt.date.today(),
            shift=ProductionOrder.Shift.DAY,
            template_version=template,
            created_by=self.admin,
        )
        self.tunnel = Tunnel.objects.create(code="T-USR", name="Túnel de usuarios")

    @staticmethod
    def registration_payload(**overrides):
        data = {
            "username": "nuevo.supervisor",
            "first_name": "Nuevo",
            "last_name": "Supervisor",
            "email": "nuevo@example.com",
            "requested_role": Role.Codes.TUNNEL,
            "password1": "Secure-user-12345",
            "password2": "Secure-user-12345",
            "website": "",
        }
        data.update(overrides)
        return data

    def create_pending_user(self, **overrides):
        payload = self.registration_payload(**overrides)
        form = UserRegistrationForm(payload)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_login_page_offers_account_creation(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "SOLUCIONES &amp; PROCESOS MARINOS")
        self.assertContains(response, "CONTROL DE PRODUCCIÓN PESQUERA")
        self.assertContains(response, "Control y trazabilidad")
        self.assertContains(response, "de la producción pesquera")
        self.assertContains(response, "logo-delfines.png")
        self.assertContains(response, "Crear mi cuenta")
        self.assertContains(response, reverse("productions:register"))

    def test_public_registration_creates_inactive_pending_account(self):
        response = self.client.post(reverse("productions:register"), self.registration_payload())
        self.assertRedirects(response, reverse("productions:register_done"))
        user = User.objects.get(username="nuevo.supervisor")
        self.assertFalse(user.is_active)
        self.assertEqual(user.registration_status, User.RegistrationStatus.PENDING)
        self.assertEqual(user.requested_role, Role.Codes.TUNNEL)
        self.assertTrue(user.check_password("Secure-user-12345"))
        self.assertTrue(AuditLog.objects.filter(user=user, action=AuditLog.Action.CREATE, module="auth").exists())

    def test_registration_cannot_request_administrator_role(self):
        form = UserRegistrationForm(self.registration_payload(requested_role=Role.Codes.ADMIN))
        self.assertFalse(form.is_valid())
        self.assertIn("requested_role", form.errors)

    def test_pending_account_cannot_login_or_accumulate_failed_attempts(self):
        user = self.create_pending_user()
        self.assertIsNone(authenticate(username=user.username, password="Secure-user-12345"))
        user.refresh_from_db()
        self.assertEqual(user.failed_login_attempts, 0)

        response = self.client.post(
            reverse("login"),
            {"username": user.username, "password": "Secure-user-12345"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "pendiente de aprobación")
        user.refresh_from_db()
        self.assertEqual(user.failed_login_attempts, 0)

    def test_only_administrator_can_open_user_management(self):
        ordinary = User.objects.create_user("ordinary", password="Secure-user-12345")
        ordinary.roles.add(self.reception_role)
        self.client.force_login(ordinary)
        response = self.client.get(reverse("productions:user_list"))
        self.assertEqual(response.status_code, 403)

    def test_user_access_page_lists_every_operational_record_separately(self):
        pending = self.create_pending_user()
        for code, label in Role.Codes.choices:
            Role.objects.get_or_create(code=code, defaults={"name": label})

        self.client.force_login(self.admin)
        response = self.client.get(reverse("productions:user_access", args=[pending.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registros operativos permitidos")
        self.assertContains(response, "Recepción de materia prima (R.M)")
        self.assertContains(response, "Nuqueras o perfilado (NUQ)")
        self.assertContains(response, "Llenado y supervisión de túneles")
        self.assertContains(response, "Bandejas por cuadrilla de túnel")
        self.assertContains(response, "Envasado en placas (P1–P3)")
        self.assertContains(response, "Cuadrillas de placas")
        self.assertContains(response, "Empaque de túneles")
        self.assertContains(response, "Empaque de placas")
        self.assertContains(response, "Materiales e insumos")
        self.assertContains(response, "Costos de producción")
        self.assertContains(response, "Troquelado")
        self.assertContains(response, "Permitir este registro operativo", count=11)

    def test_administrator_approves_role_production_and_tunnel_together(self):
        pending = self.create_pending_user()
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("productions:user_access", args=[pending.pk]),
            {
                "first_name": pending.first_name,
                "last_name": pending.last_name,
                "email": pending.email,
                "registration_status": User.RegistrationStatus.ACTIVE,
                "roles": [self.tunnel_role.pk],
                "productions": [self.production.pk],
                "tunnels": [self.tunnel.pk],
            },
        )
        self.assertRedirects(response, reverse("productions:user_list"))
        pending.refresh_from_db()
        self.assertTrue(pending.is_active)
        self.assertEqual(pending.registration_status, User.RegistrationStatus.ACTIVE)
        self.assertEqual(pending.approved_by, self.admin)
        self.assertIsNotNone(pending.approved_at)
        self.assertTrue(pending.roles.filter(code=Role.Codes.TUNNEL).exists())
        self.assertTrue(
            AreaAssignment.objects.filter(
                user=pending,
                production=self.production,
                area=AreaAssignment.Area.TUNNEL,
                tunnel=self.tunnel,
                shift=self.production.shift,
                active=True,
            ).exists()
        )
        self.assertTrue(AuditLog.objects.filter(user=self.admin, module="users", record_pk=str(pending.pk)).exists())

    def test_operational_account_cannot_be_activated_without_production(self):
        pending = self.create_pending_user()
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("productions:user_access", args=[pending.pk]),
            {
                "first_name": pending.first_name,
                "last_name": pending.last_name,
                "email": pending.email,
                "registration_status": User.RegistrationStatus.ACTIVE,
                "roles": [self.reception_role.pk],
                "productions": [],
                "tunnels": [],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Seleccione al menos un parte de producción")
        pending.refresh_from_db()
        self.assertFalse(pending.is_active)

    def test_rejecting_account_disables_existing_assignments(self):
        user = User.objects.create_user(
            "to-reject",
            first_name="Usuario",
            last_name="Rechazado",
            email="reject@example.com",
            password="Secure-user-12345",
            registration_status=User.RegistrationStatus.ACTIVE,
        )
        user.roles.add(self.reception_role)
        assignment = AreaAssignment.objects.create(
            user=user,
            production=self.production,
            area=AreaAssignment.Area.RECEPTION,
            shift=self.production.shift,
            active=True,
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("productions:user_access", args=[user.pk]),
            {
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "registration_status": User.RegistrationStatus.REJECTED,
                "roles": [self.reception_role.pk],
                "productions": [],
                "tunnels": [],
            },
        )
        self.assertRedirects(response, reverse("productions:user_list"))
        user.refresh_from_db()
        assignment.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(assignment.active)
