import datetime as dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from productions.models import (
    Customer,
    PlateCarryoverBalance,
    PlateEntry,
    PlatePackagingEntry,
    PlatePackagingAllocation,
    PlatePallet,
    PlatePalletConsumption,
    PlatePalletLine,
    PlatePosition,
    PlatePositionTiming,
    Product,
    ProductionOrder,
    Role,
    TemplateVersion,
    User,
)
from productions.services.plate_balances import (
    auto_pack_product,
    plate_pallet_dashboard,
    plate_product_availability,
    set_plate_pallet_status,
    sync_production_carryover_balances,
    void_auto_pack_line,
)


class PlateBalanceTests(TestCase):
    def setUp(self):
        role = Role.objects.create(
            code=Role.Codes.PRODUCTION_MANAGER,
            name="Jefe de producción",
        )
        self.user = User.objects.create_user(
            "plate-balance-manager",
            password="Secure-test-123",
        )
        self.user.roles.add(role)
        self.customer = Customer.objects.create(name="Cliente de saldos")
        self.other_customer = Customer.objects.create(name="Otro cliente")
        self.main_product = Product.objects.create(
            code="POTA-GRANEL-SALDOS",
            description="POTA A GRANEL",
        )
        self.product_a = Product.objects.create(
            code="PP-SALDO-01",
            description="ANILLAS BLANCAS",
        )
        self.product_b = Product.objects.create(
            code="PP-SALDO-02",
            description="FILETE DARUMA",
        )
        self.template = TemplateVersion.objects.create(
            code="PP-SALDOS",
            file=SimpleUploadedFile("template.xlsm", b"fixture-balance"),
            original_filename="template.xlsm",
            sha256="b" * 64,
            uploaded_by=self.user,
            rules={
                "package_trays": 2,
                "package_kg": 20,
                "plate_pallet_package_capacity": 56,
                "plate_pallet_max": 50,
            },
        )
        self.origin = self._production(
            number=910,
            production_date=dt.date(2026, 7, 15),
            status=ProductionOrder.Status.CLOSED,
        )
        self.current = self._production(
            number=911,
            production_date=dt.date(2026, 7, 16),
        )
        self.origin_position = self._position("EM-PLA!E5", "Bachada 1 · Plaquero 1")
        self.current_position = self._position("EM-PLA!H5", "Bachada 2 · Plaquero 2")

    def _production(self, *, number, production_date, status=ProductionOrder.Status.DRAFT, customer=None):
        return ProductionOrder.objects.create(
            number=number,
            plant_lot=f"LOTE-{number}",
            customer=customer or self.customer,
            process="Pota",
            main_product=self.main_product,
            reception_date=production_date,
            production_date=production_date,
            shift=ProductionOrder.Shift.DAY,
            template_version=self.template,
            created_by=self.user,
            status=status,
        )

    def _position(self, key, label):
        return PlatePosition.objects.create(
            template_version=self.template,
            plate_rack=PlatePosition.PlateRack.P1,
            position_key=key,
            display_name=label,
        )

    def _source(self, production, position, product, trays):
        source = PlateEntry.objects.create(
            production=production,
            responsible=self.user,
            observation="",
            date=production.production_date,
            shift=production.shift,
            position=position,
            product=product,
            tray_count=trays,
        )
        now = timezone.now()
        PlatePositionTiming.objects.create(
            production=production,
            position=position,
            load_started_at=now,
            load_completed_at=now,
            launched_at=now,
            unloaded_at=now,
        )
        return source

    def _prior_balance(self, *, product=None, trays=1, customer=None):
        production = self.origin
        if customer is not None:
            production = self._production(
                number=909,
                production_date=dt.date(2026, 7, 14),
                status=ProductionOrder.Status.CLOSED,
                customer=customer,
            )
        position = self.origin_position
        if production != self.origin:
            position = self._position("EM-PLA!K5", "Bachada 3 · Plaquero 3")
        source = self._source(
            production,
            position,
            product or self.product_a,
            trays,
        )
        return PlateCarryoverBalance.objects.create(
            origin_production=production,
            source_entry=source,
            product=product or self.product_a,
            initial_trays=trays,
            available_trays=trays,
            generated_by=self.user,
        )

    def test_automatic_packaging_joins_prior_balance_with_current_trays(self):
        balance = self._prior_balance(trays=1)
        source = self._source(
            self.current,
            self.current_position,
            self.product_a,
            13,
        )

        availability = plate_product_availability(self.current)[0]
        self.assertEqual(availability["current_trays"], 13)
        self.assertEqual(availability["carryover_trays"], 1)
        self.assertEqual(availability["possible_packages"], 7)
        self.assertEqual(availability["residual_trays"], 0)

        result = auto_pack_product(
            production=self.current,
            product_id=self.product_a.pk,
            pallet_number=1,
            user=self.user,
        )

        self.assertEqual(result["package_count"], 7)
        self.assertEqual(result["tray_count"], 14)
        self.assertEqual(result["kg"], Decimal("140"))
        self.assertEqual(result["carryover_used"], 1)
        self.assertEqual(result["current_used"], 13)
        balance.refresh_from_db()
        self.assertEqual(balance.status, PlateCarryoverBalance.Status.CONSUMED)
        self.assertEqual(balance.available_trays, 0)
        self.assertEqual(
            PlatePalletConsumption.objects.filter(
                line=result["line"],
                source_entry=source,
            ).get().tray_count,
            13,
        )

    def test_same_pallet_accepts_multiple_products_and_can_close_early(self):
        self._source(self.current, self.current_position, self.product_a, 20)
        second_position = self._position("EM-PLA!N5", "Bachada 4 · Plaquero 1")
        self._source(self.current, second_position, self.product_b, 30)

        first = auto_pack_product(
            production=self.current,
            product_id=self.product_a.pk,
            pallet_number=4,
            user=self.user,
        )
        second = auto_pack_product(
            production=self.current,
            product_id=self.product_b.pk,
            pallet_number=4,
            user=self.user,
        )
        pallet = first["pallet"]
        self.assertEqual(second["pallet_total"], 25)

        dashboard = plate_pallet_dashboard(self.current)[0]
        self.assertEqual(dashboard["package_count"], 25)
        self.assertEqual(len(dashboard["products"]), 2)

        pallet = set_plate_pallet_status(
            pallet_id=pallet.pk,
            production=self.current,
            target_status=PlatePallet.Status.CLOSED,
            user=self.user,
        )
        self.assertEqual(pallet.status, PlatePallet.Status.CLOSED)
        with self.assertRaises(ValidationError):
            auto_pack_product(
                production=self.current,
                product_id=self.product_a.pk,
                pallet_number=4,
                user=self.user,
            )

    def test_pallet_capacity_limits_the_automatic_result(self):
        self._source(self.current, self.current_position, self.product_a, 120)
        result = auto_pack_product(
            production=self.current,
            product_id=self.product_a.pk,
            pallet_number=2,
            user=self.user,
        )
        self.assertEqual(result["package_count"], 56)
        self.assertEqual(result["residual_trays"], 8)
        with self.assertRaises(ValidationError):
            auto_pack_product(
                production=self.current,
                product_id=self.product_a.pk,
                pallet_number=2,
                user=self.user,
            )

    def test_automatic_packaging_uses_only_the_space_left_by_legacy_packages(self):
        PlatePackagingEntry.objects.create(
            production=self.current,
            responsible=self.user,
            observation="",
            date=self.current.production_date,
            pallet_number=1,
            product=self.product_b,
            package_count=20,
        )
        self._source(self.current, self.current_position, self.product_a, 89)

        result = auto_pack_product(
            production=self.current,
            product_id=self.product_a.pk,
            pallet_number=1,
            user=self.user,
        )

        self.assertEqual(result["package_count"], 36)
        self.assertEqual(result["pallet_total"], 56)
        self.assertEqual(result["residual_trays"], 17)

    def test_deleting_automatic_movement_restores_prior_balance(self):
        balance = self._prior_balance(trays=1)
        self._source(self.current, self.current_position, self.product_a, 1)
        result = auto_pack_product(
            production=self.current,
            product_id=self.product_a.pk,
            pallet_number=1,
            user=self.user,
        )

        void_auto_pack_line(
            line_id=result["line"].pk,
            production=self.current,
            user=self.user,
        )

        balance.refresh_from_db()
        result["line"].refresh_from_db()
        self.assertEqual(balance.available_trays, 1)
        self.assertEqual(balance.status, PlateCarryoverBalance.Status.AVAILABLE)
        self.assertFalse(result["line"].is_active)

    def test_closing_sync_creates_only_the_unpacked_balance(self):
        source = self._source(
            self.origin,
            self.origin_position,
            self.product_a,
            81,
        )
        PlatePackagingAllocation.objects.create(
            production=self.origin,
            responsible=self.user,
            observation="",
            date=self.origin.production_date,
            source_entry=source,
            pallet_number=1,
            package_count=40,
        )

        balances = sync_production_carryover_balances(
            production=self.origin,
            user=self.user,
        )

        self.assertEqual(len(balances), 1)
        self.assertEqual(balances[0].initial_trays, 1)
        self.assertEqual(balances[0].available_kg, Decimal("10.00"))

    def test_balance_from_another_customer_is_not_used(self):
        self._prior_balance(trays=1, customer=self.other_customer)
        self._source(self.current, self.current_position, self.product_a, 1)

        availability = plate_product_availability(self.current)[0]
        self.assertEqual(availability["carryover_trays"], 0)
        self.assertEqual(availability["possible_packages"], 0)
        with self.assertRaises(ValidationError):
            auto_pack_product(
                production=self.current,
                product_id=self.product_a.pk,
                pallet_number=1,
                user=self.user,
            )

    def test_mobile_page_uses_product_and_pallet_without_package_quantity(self):
        self._source(self.current, self.current_position, self.product_a, 20)
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("productions:plate_pack_create", args=[self.current.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CÁLCULO AUTOMÁTICO")
        self.assertContains(response, 'name="product"', html=False)
        self.assertContains(response, 'name="pallet_number"', html=False)
        self.assertContains(
            response,
            f'<option value="{self.product_a.pk}" selected',
            html=False,
        )
        self.assertContains(response, 'value="1"', html=False)
        self.assertNotContains(response, 'name="package_count"', html=False)
        self.assertContains(response, "ENV. PLACAS")
        self.assertContains(response, "EM-PLA")
        self.assertContains(response, "20 bandejas → 10 sacos")
        self.assertContains(response, "Asignar sacos a un pallet")
        self.assertContains(
            response,
            f"?position={self.current_position.pk}&amp;product={self.product_a.pk}",
            html=False,
        )

        response = self.client.post(
            reverse("productions:plate_pack_auto", args=[self.current.pk]),
            {"product": self.product_a.pk, "pallet_number": 3},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            PlatePalletLine.objects.filter(
                production=self.current,
                pallet__pallet_number=3,
                product=self.product_a,
                package_count=10,
                is_active=True,
            ).exists()
        )
        self.assertContains(response, "se formaron 10 bultos")
        self.assertContains(response, "Producto completamente empacado")
        self.assertNotContains(response, "Calcular bultos de este producto")
        auto_select = response.content.decode("utf-8").split(
            'id="auto-pack-product"', 1
        )[1].split("</select>", 1)[0]
        self.assertNotIn(
            f'value="{self.product_a.pk}"',
            auto_select,
        )
