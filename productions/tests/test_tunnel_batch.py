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
    Crew,
    TunnelCrewEntry,
    TunnelEntry,
    TunnelFill,
    TunnelRack,
    User,
)


class TunnelBatchCaptureTests(TestCase):
    def setUp(self):
        role = Role.objects.create(code=Role.Codes.PRODUCTION_MANAGER, name="Jefe")
        self.user = User.objects.create_user("manager", password="Secure-test-123")
        self.user.roles.add(role)
        customer = Customer.objects.create(name="Cliente")
        self.product = Product.objects.create(code="P001", description="CONOS DE POTA")
        self.other_product = Product.objects.create(code="P002", description="ALETA ENTERA")
        rules = {
            "rack_max_trays": 50,
            "tunnel_racks": {
                "T1": {
                    "1": [
                        {"code": "R01", "position_key": "T1!E5"},
                        {"code": "R02", "position_key": "T1!F5"},
                    ]
                }
            },
        }
        template = TemplateVersion.objects.create(
            code="PP-V1",
            file=SimpleUploadedFile("template.xlsm", b"fixture"),
            original_filename="template.xlsm",
            sha256="a" * 64,
            uploaded_by=self.user,
            rules=rules,
        )
        self.production = ProductionOrder.objects.create(
            number=101,
            plant_lot="L1",
            customer=customer,
            process="Pota",
            main_product=self.product,
            reception_date=dt.date(2026, 7, 13),
            production_date=dt.date(2026, 7, 13),
            shift=ProductionOrder.Shift.DAY,
            template_version=template,
            created_by=self.user,
        )
        tunnel = Tunnel.objects.create(code="T1", name="Túnel 1")
        self.fill = TunnelFill.objects.create(
            production=self.production,
            tunnel=tunnel,
            fill_number=1,
            date=dt.date(2026, 7, 13),
            supervisor=self.user,
        )
        self.client.force_login(self.user)
        self.url = reverse("productions:tunnel_batch", args=[self.production.pk, self.fill.pk])

    def _post_data(
        self,
        trays,
        *,
        first_capacity=50,
        second_capacity=50,
        first_product=None,
        first_entry_id="",
    ):
        racks = list(self.fill.racks.order_by("code"))
        first_product = first_product or self.product
        return {
            "racks-TOTAL_FORMS": "2",
            "racks-INITIAL_FORMS": "0",
            "racks-MIN_NUM_FORMS": "0",
            "racks-MAX_NUM_FORMS": "1000",
            "racks-0-rack_id": str(racks[0].pk),
            "racks-0-entry_id": str(first_entry_id),
            "racks-0-max_trays": str(first_capacity),
            "racks-0-product": str(first_product.pk),
            "racks-0-tray_count": str(trays),
            "racks-1-rack_id": str(racks[1].pk),
            "racks-1-entry_id": "",
            "racks-1-max_trays": str(second_capacity),
            "racks-1-product": "",
            "racks-1-tray_count": "",
        }

    def test_page_shows_all_racks_and_batch_save_creates_audited_entry(self):
        response = self.client.get(self.url)
        self.assertContains(response, "R01")
        self.assertContains(response, "R02")
        self.assertNotContains(response, "Fecha de la llenada")
        self.assertNotContains(response, "Observación general")
        self.assertNotContains(response, 'name="date"')
        self.assertNotContains(response, 'name="observation"')
        self.assertContains(response, "Capacidad del rack", count=2)
        self.assertContains(response, "data-rack-card", count=2)
        self.assertContains(response, "data-rack-capacity-select", count=2)
        self.assertContains(response, "data-rack-total", count=2)
        self.assertContains(response, "Cerrar túnel")
        self.assertContains(
            response,
            reverse(
                "productions:tunnel_fill_transition",
                args=[self.production.pk, self.fill.pk],
            ),
        )
        response = self.client.post(self.url, self._post_data(20))
        self.assertRedirects(response, self.url)
        entry = TunnelEntry.objects.get()
        self.assertEqual(entry.tray_count, 20)
        self.assertEqual(entry.date, self.fill.date)
        self.assertEqual(entry.observation, "")
        self.assertTrue(AuditLog.objects.filter(record_pk=str(entry.pk), action=AuditLog.Action.CREATE).exists())

        response = self.client.get(self.url)
        self.assertContains(response, "Corregir")
        self.assertContains(response, "Eliminar")
        self.assertContains(response, 'data-edit-entry')
        self.assertContains(response, reverse(
            "productions:tunnel_entry_delete",
            args=[self.production.pk, self.fill.pk, entry.pk],
        ))

    def test_racks_are_displayed_in_numeric_order(self):
        template = self.production.template_version
        rules = template.rules
        rules["tunnel_racks"]["T1"]["1"] = [
            {"code": "R1", "position_key": "T1!E5"},
            {"code": "R10", "position_key": "T1!N5"},
            {"code": "R2", "position_key": "T1!F5"},
            {"code": "R11", "position_key": "T1!O5"},
            {"code": "R3", "position_key": "T1!G5"},
        ]
        template.rules = rules
        template.save(update_fields=["rules"])
        self.fill.racks.all().delete()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        displayed_codes = [form.rack.code for form in response.context["formset"].forms]
        self.assertEqual(displayed_codes, ["R1", "R2", "R3", "R10", "R11"])

    def test_product_options_are_displayed_in_pp_code_order(self):
        Product.objects.create(code="PP-010", description="ALETA PRIMERA")
        Product.objects.create(code="PP-002", description="ZONA SEGUNDA")
        Product.objects.create(code="PP-001", description="CONOS TERCERO")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        displayed_codes = [
            product.code
            for product in response.context["formset"].forms[0].fields["product"].queryset
        ]
        self.assertEqual(displayed_codes, ["PP-001", "PP-002", "PP-010"])

    def test_saved_entry_can_change_product_and_tray_count(self):
        self.client.get(self.url)
        self.client.post(self.url, self._post_data(20))
        entry = TunnelEntry.objects.get(is_active=True)

        response = self.client.post(
            self.url,
            self._post_data(
                15,
                first_product=self.other_product,
                first_entry_id=entry.pk,
            ),
        )

        self.assertRedirects(response, self.url)
        entry.refresh_from_db()
        self.assertEqual(entry.product, self.other_product)
        self.assertEqual(entry.tray_count, 15)
        update_log = AuditLog.objects.filter(
            record_pk=str(entry.pk),
            action=AuditLog.Action.UPDATE,
        ).latest("timestamp")
        self.assertEqual(update_log.old_value["tray_count"], 20)
        self.assertEqual(update_log.new_value["tray_count"], 15)

    def test_saved_entry_can_be_removed_and_total_is_recalculated(self):
        self.client.get(self.url)
        self.client.post(self.url, self._post_data(20))
        entry = TunnelEntry.objects.get(is_active=True)
        delete_url = reverse(
            "productions:tunnel_entry_delete",
            args=[self.production.pk, self.fill.pk, entry.pk],
        )

        response = self.client.post(delete_url)

        self.assertRedirects(response, self.url)
        entry.refresh_from_db()
        self.assertFalse(entry.is_active)
        self.assertTrue(
            AuditLog.objects.filter(
                record_pk=str(entry.pk),
                action=AuditLog.Action.VOID,
            ).exists()
        )
        response = self.client.get(self.url)
        self.assertContains(response, 'data-current-total="0"')

    def test_second_product_can_be_added_to_same_rack_when_capacity_allows_it(self):
        self.client.get(self.url)
        self.client.post(self.url, self._post_data(14))
        racks = list(self.fill.racks.order_by("code"))

        response = self.client.post(
            self.url,
            {
                "racks-TOTAL_FORMS": "2",
                "racks-INITIAL_FORMS": "0",
                "racks-MIN_NUM_FORMS": "0",
                "racks-MAX_NUM_FORMS": "1000",
                "racks-0-rack_id": str(racks[0].pk),
                "racks-0-entry_id": "",
                "racks-0-max_trays": "50",
                "racks-0-product": str(self.other_product.pk),
                "racks-0-tray_count": "10",
                "racks-1-rack_id": str(racks[1].pk),
                "racks-1-entry_id": "",
                "racks-1-max_trays": "50",
                "racks-1-product": "",
                "racks-1-tray_count": "",
            },
        )

        self.assertRedirects(response, self.url)
        self.assertTrue(
            TunnelEntry.objects.filter(
                rack=racks[0], product=self.product, tray_count=14, is_active=True
            ).exists()
        )
        self.assertTrue(
            TunnelEntry.objects.filter(
                rack=racks[0], product=self.other_product, tray_count=10, is_active=True
            ).exists()
        )

    def test_save_single_rack_ignores_incomplete_values_from_other_racks(self):
        self.client.get(self.url)
        racks = list(self.fill.racks.order_by("code"))

        response = self.client.post(
            self.url,
            {
                "open_rack_id": str(racks[0].pk),
                "save_rack_id": str(racks[0].pk),
                "racks-TOTAL_FORMS": "2",
                "racks-INITIAL_FORMS": "0",
                "racks-MIN_NUM_FORMS": "0",
                "racks-MAX_NUM_FORMS": "1000",
                "racks-0-rack_id": str(racks[0].pk),
                "racks-0-entry_id": "",
                "racks-0-max_trays": "50",
                "racks-0-product": str(self.other_product.pk),
                "racks-0-tray_count": "12",
                "racks-1-rack_id": str(racks[1].pk),
                "racks-1-entry_id": "",
                "racks-1-max_trays": "50",
                "racks-1-product": str(self.product.pk),
                "racks-1-tray_count": "",
            },
        )

        self.assertRedirects(
            response,
            f"{self.url}?open_rack={racks[0].pk}#rack-{racks[0].pk}",
            fetch_redirect_response=False,
        )
        self.assertTrue(
            TunnelEntry.objects.filter(
                rack=racks[0], product=self.other_product, tray_count=12, is_active=True
            ).exists()
        )
        self.assertFalse(
            TunnelEntry.objects.filter(
                rack=racks[1], product=self.product, is_active=True
            ).exists()
        )

    def test_new_fill_form_renders_with_version_control_field(self):
        url = reverse("productions:tunnel_fill_create", args=[self.production.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="expected_version"')
        self.assertNotContains(response, 'name="date"')
        self.assertNotContains(response, 'name="observation"')

        response = self.client.post(
            url,
            {
                "tunnel": str(self.fill.tunnel_id),
                "fill_number": "2",
                "start_time": "",
                "launch_time": "",
                "end_time": "",
                "expected_version": str(self.production.version),
            },
        )
        self.assertEqual(response.status_code, 302)
        second_fill = TunnelFill.objects.get(production=self.production, tunnel=self.fill.tunnel, fill_number=2)
        self.assertEqual(second_fill.date, self.production.production_date)
        self.assertEqual(second_fill.observation, "")

    def test_capacity_error_is_shown_without_partial_write(self):
        self.client.get(self.url)
        response = self.client.post(self.url, self._post_data(51))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "capacidad de 50", status_code=400)
        self.assertFalse(TunnelEntry.objects.exists())

    def test_operator_can_mark_a_random_rack_as_70_trays(self):
        self.client.get(self.url)
        response = self.client.post(self.url, self._post_data(70, first_capacity=70))
        rack = self.fill.racks.order_by("code").first()
        self.assertRedirects(response, self.url)
        rack.refresh_from_db()
        self.assertEqual(rack.max_trays, 70)
        self.assertEqual(TunnelEntry.objects.get(rack=rack).tray_count, 70)
        self.assertTrue(
            AuditLog.objects.filter(
                model_name=rack._meta.label,
                record_pk=str(rack.pk),
                action=AuditLog.Action.UPDATE,
            ).exists()
        )

    def test_exceptional_49_tray_rack_is_saved_as_full_and_collapsible(self):
        self.client.get(self.url)
        response = self.client.post(
            self.url,
            self._post_data(49, first_capacity=49),
        )

        rack = self.fill.racks.order_by("code").first()
        self.assertRedirects(response, self.url)
        rack.refresh_from_db()
        self.assertEqual(rack.max_trays, 49)
        self.assertEqual(TunnelEntry.objects.get(rack=rack).tray_count, 49)

        response = self.client.get(self.url)
        self.assertContains(response, "49 bandejas (excepcional)")
        self.assertContains(response, "rack-card-complete")
        self.assertContains(response, "data-tunnel-rack-fold")

    def test_partial_rack_can_be_closed_with_reason_and_blocks_new_entries(self):
        self.client.get(self.url)
        self.client.post(self.url, self._post_data(2))
        rack = self.fill.racks.order_by("code").first()
        close_url = reverse(
            "productions:tunnel_rack_transition",
            args=[self.production.pk, self.fill.pk, rack.pk],
        )

        response = self.client.post(
            close_url,
            {"target_status": "CLOSED"},
        )

        self.assertRedirects(
            response,
            f"{self.url}?open_rack={rack.pk}#rack-{rack.pk}",
            fetch_redirect_response=False,
        )
        rack.refresh_from_db()
        self.assertEqual(rack.status, TunnelRack.Status.CLOSED)
        self.assertEqual(rack.close_reason, "Cierre manual incompleto: 2/50 bandejas.")
        entry = TunnelEntry(
            production=self.production,
            responsible=self.user,
            rack=rack,
            product=self.other_product,
            tray_count=1,
            date=self.fill.date,
        )
        with self.assertRaisesMessage(Exception, "rack está cerrado"):
            entry.full_clean()

        response = self.client.get(self.url)
        self.assertContains(response, "Rack cerrado")
        self.assertContains(response, "rack-card-closed")
        self.assertContains(response, "CERRADO")
        self.assertContains(response, "2 / 50 bandejas")

    def test_closed_rack_does_not_block_saving_trays_in_an_open_rack(self):
        self.client.get(self.url)
        self.client.post(self.url, self._post_data(50))
        racks = list(self.fill.racks.order_by("code"))
        close_url = reverse(
            "productions:tunnel_rack_transition",
            args=[self.production.pk, self.fill.pk, racks[0].pk],
        )
        self.client.post(close_url, {"target_status": "CLOSED"})

        response = self.client.post(
            self.url,
            {
                "racks-TOTAL_FORMS": "2",
                "racks-INITIAL_FORMS": "0",
                "racks-MIN_NUM_FORMS": "0",
                "racks-MAX_NUM_FORMS": "1000",
                "racks-0-rack_id": str(racks[0].pk),
                "racks-0-entry_id": "",
                "racks-0-max_trays": "50",
                "racks-0-product": "",
                "racks-0-tray_count": "",
                "racks-1-rack_id": str(racks[1].pk),
                "racks-1-entry_id": "",
                "racks-1-max_trays": "50",
                "racks-1-product": str(self.other_product.pk),
                "racks-1-tray_count": "2",
            },
        )

        self.assertRedirects(response, self.url)
        self.assertTrue(
            TunnelEntry.objects.filter(
                rack=racks[1], product=self.other_product, tray_count=2, is_active=True
            ).exists()
        )

    def test_crew_can_be_assigned_from_the_same_rack_card(self):
        crew = Crew.objects.create(name="Cuadrilla Azul")
        self.client.get(self.url)
        self.client.post(self.url, self._post_data(20))
        rack = self.fill.racks.order_by("code").first()

        response = self.client.post(
            self.url,
            {
                "crew_rack_id": str(rack.pk),
                f"crew_product_{rack.pk}": str(self.product.pk),
                f"crew_{rack.pk}": str(crew.pk),
                f"crew_trays_{rack.pk}": "20",
            },
        )

        self.assertRedirects(
            response,
            f"{self.url}?open_rack={rack.pk}#rack-{rack.pk}",
            fetch_redirect_response=False,
        )
        assignment = TunnelCrewEntry.objects.get(rack=rack, crew=crew, is_active=True)
        self.assertEqual(assignment.product, self.product)
        self.assertEqual(assignment.tray_count, 20)
        self.assertEqual(assignment.page_or_block, rack.code)
        self.assertTrue(
            AuditLog.objects.filter(
                record_pk=str(assignment.pk),
                action=AuditLog.Action.CREATE,
                module="tunnel-crews",
            ).exists()
        )

        response = self.client.get(self.url)
        self.assertContains(response, "Cuadrillas del rack")
        self.assertContains(response, "Cuadrilla Azul")

    def test_assign_all_uses_only_the_remaining_trays_for_each_product(self):
        target_crew = Crew.objects.create(code="CUAD-FERMIN", name="FERMIN")
        other_crew = Crew.objects.create(code="CUAD-CHERLITA", name="CHERLITA")
        self.client.get(self.url)
        rack = self.fill.racks.order_by("code").first()
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=rack,
            product=self.product,
            tray_count=20,
            date=self.fill.date,
        )
        TunnelEntry.objects.create(
            production=self.production,
            responsible=self.user,
            rack=rack,
            product=self.other_product,
            tray_count=30,
            date=self.fill.date,
        )
        TunnelCrewEntry.objects.create(
            production=self.production,
            responsible=self.user,
            fill=self.fill,
            rack=rack,
            product=self.product,
            crew=other_crew,
            page_or_block=rack.code,
            tray_count=5,
            date=self.fill.date,
        )
        TunnelCrewEntry.objects.create(
            production=self.production,
            responsible=self.user,
            fill=self.fill,
            rack=rack,
            product=self.product,
            crew=target_crew,
            page_or_block=rack.code,
            tray_count=3,
            date=self.fill.date,
        )

        response = self.client.post(
            f"{self.url}?crew_assign_mode=all",
            {
                "crew_rack_id": str(rack.pk),
                f"crew_{rack.pk}": str(target_crew.pk),
                f"crew_name_{rack.pk}": target_crew.name,
            },
        )

        self.assertRedirects(
            response,
            f"{self.url}?open_rack={rack.pk}#rack-{rack.pk}",
            fetch_redirect_response=False,
        )
        self.assertEqual(
            TunnelCrewEntry.objects.get(
                rack=rack,
                product=self.product,
                crew=target_crew,
                is_active=True,
            ).tray_count,
            15,
        )
        self.assertEqual(
            TunnelCrewEntry.objects.get(
                rack=rack,
                product=self.other_product,
                crew=target_crew,
                is_active=True,
            ).tray_count,
            30,
        )

    def test_summary_identifies_each_rack_with_pending_crew_trays(self):
        crew = Crew.objects.create(code="CUAD-ANDRES", name="ANDRES")
        self.client.get(self.url)
        self.client.post(self.url, self._post_data(20))
        rack = self.fill.racks.order_by("code").first()
        TunnelCrewEntry.objects.create(
            production=self.production,
            responsible=self.user,
            fill=self.fill,
            rack=rack,
            product=self.product,
            crew=crew,
            page_or_block=rack.code,
            tray_count=5,
            date=self.fill.date,
        )

        response = self.client.get(self.url)

        self.assertContains(response, "Racks pendientes de asignar:")
        self.assertContains(response, "R01 · 15 pendientes")
        self.assertContains(response, f"?open_rack={rack.pk}#rack-{rack.pk}")

    def test_batch_save_with_multiple_products_via_extra_rows(self):
        self.client.get(self.url)
        third = Product.objects.create(code="P003", description="PRODUCTO TERCERO")
        data = self._post_data(10)
        data["racks-0-extra_product_0"] = str(self.other_product.pk)
        data["racks-0-extra_trays_0"] = "5"
        data["racks-0-extra_product_1"] = str(third.pk)
        data["racks-0-extra_trays_1"] = "3"

        response = self.client.post(self.url, data)

        self.assertRedirects(response, self.url)
        rack = self.fill.racks.order_by("code").first()
        entries = {
            (entry.product_id, entry.tray_count)
            for entry in TunnelEntry.objects.filter(rack=rack, is_active=True)
        }
        self.assertEqual(
            entries,
            {(self.product.pk, 10), (self.other_product.pk, 5), (third.pk, 3)},
        )
        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.CREATE, module="tunnel_racks").count(),
            3,
        )

    def test_extra_rows_update_already_saved_entries(self):
        self.client.get(self.url)
        self.client.post(self.url, self._post_data(20))
        entry = TunnelEntry.objects.get(is_active=True)
        racks = list(self.fill.racks.order_by("code"))
        data = {
            "racks-TOTAL_FORMS": "2",
            "racks-INITIAL_FORMS": "0",
            "racks-MIN_NUM_FORMS": "0",
            "racks-MAX_NUM_FORMS": "1000",
            "racks-0-rack_id": str(racks[0].pk),
            "racks-0-entry_id": "",
            "racks-0-max_trays": "50",
            "racks-0-product": "",
            "racks-0-tray_count": "",
            "racks-0-extra_product_0": str(self.product.pk),
            "racks-0-extra_trays_0": "7",
            "racks-1-rack_id": str(racks[1].pk),
            "racks-1-entry_id": "",
            "racks-1-max_trays": "50",
            "racks-1-product": "",
            "racks-1-tray_count": "",
        }

        response = self.client.post(self.url, data)

        self.assertRedirects(response, self.url)
        entry.refresh_from_db()
        self.assertEqual(entry.tray_count, 7)
        update_log = AuditLog.objects.filter(
            record_pk=str(entry.pk),
            action=AuditLog.Action.UPDATE,
        ).latest("timestamp")
        self.assertEqual(update_log.old_value["tray_count"], 20)
        self.assertEqual(update_log.new_value["tray_count"], 7)

    def test_duplicate_product_in_extra_rows_is_rejected(self):
        self.client.get(self.url)
        data = self._post_data(10)
        data["racks-0-extra_product_0"] = str(self.other_product.pk)
        data["racks-0-extra_trays_0"] = "5"
        data["racks-0-extra_product_1"] = str(self.other_product.pk)
        data["racks-0-extra_trays_1"] = "5"

        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "ya fue seleccionado en este rack", status_code=400)
        self.assertFalse(TunnelEntry.objects.exists())

    def test_extra_rows_cannot_exceed_rack_capacity(self):
        self.client.get(self.url)
        data = self._post_data(10)
        data["racks-0-extra_product_0"] = str(self.other_product.pk)
        data["racks-0-extra_trays_0"] = "41"

        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "La cantidad total de bandejas supera la capacidad del rack",
            status_code=400,
        )
        self.assertFalse(TunnelEntry.objects.exists())

    def test_quick_create_warns_and_reuses_name_with_different_accent(self):
        Crew.objects.create(code="CUAD-FERMIN", name="FERMIN")
        quick_url = reverse(
            "productions:tunnel_crew_quick_create",
            args=[self.production.pk],
        )

        response = self.client.post(
            quick_url,
            {
                "fill": self.fill.pk,
                "name": "FERMÍN",
                "next": self.url,
            },
            follow=True,
        )

        self.assertEqual(Crew.objects.filter(code__startswith="CUAD-FERMIN").count(), 1)
        self.assertContains(
            response,
            "Esta cuadrilla ya existe como FERMIN. No se creó un duplicado",
        )

    def test_product_select_keeps_lamina_color_attribute_and_all_options(self):
        # Verifica que compartir el queryset/choices de producto entre las
        # filas del formset (optimizacion de performance) no rompe el color
        # de lamina por opcion ni la lista completa de productos por rack.
        self.product.color = "Rojo"
        self.product.save(update_fields=["color"])

        response = self.client.get(self.url)

        # Dos racks (R01 y R02) deben mostrar la opcion con su color de lamina.
        self.assertContains(
            response,
            f'<option value="{self.product.pk}" data-lamina-color="ROJO">P001 — CONOS DE POTA</option>',
            count=2,
        )
        # Y ambos deben mostrar tambien el producto sin color.
        self.assertContains(
            response,
            f'<option value="{self.other_product.pk}">P002 — ALETA ENTERA</option>',
            count=2,
        )
