import re
from decimal import Decimal, ROUND_HALF_UP
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, F, IntegerField, OuterRef, Prefetch, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView

from .forms import (
    CustomerForm,
    MaterialUsageForm,
    CostEntryForm,
    NuqueraEntryForm,
    PlateCrewEntryForm,
    PlateEntryForm,
    PlatePackagingEntryForm,
    ProductionOrderForm,
    ReceptionEntryForm,
    TransitionForm,
    TunnelCrewEntryForm,
    TunnelBatchFormSet,
    TunnelEntryForm,
    TunnelFillForm,
    TunnelPackagingEntryForm,
    UserAccessForm,
    UserRegistrationForm,
    RateForm,
    VehicleForm,
    WorkerForm,
)
from .models import AreaAssignment, AuditLog, Customer, GeneratedFile, PlatePosition, PlateEntry, PlateCrewEntry, Product, ProductionOrder, Rate, Role, TemplateVersion, TunnelFill, TunnelRack, TunnelEntry, TunnelCrewEntry, ReceptionEntry, NuqueraEntry, TunnelPackagingEntry, PlatePackagingEntry, MaterialUsage, CostEntry, User, Vehicle, Worker
from .services.excel import GenerationError, generate_production_workbook, mapping_capabilities
from .services.permissions import can_view_production, require_area_assignment, require_roles
from .services.pdf_report import build_production_pdf
from .services.reconciliation import plate_reconciliation, tunnel_reconciliation
from .services.layout import ensure_tunnel_racks
from .services.workflow import TRANSITIONS, transition_production, transition_tunnel_fill


PRODUCTION_EDITABLE_STATUSES = {
    ProductionOrder.Status.DRAFT,
    ProductionOrder.Status.OPEN,
    ProductionOrder.Status.IN_PROGRESS,
    ProductionOrder.Status.REVIEW,
    ProductionOrder.Status.OBSERVED,
}

KG_QUANTUM = Decimal("0.01")


def _format_kg(value):
    return f"{Decimal(value or 0).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP):.2f}"


def _production_order_payload(production):
    return {
        "number": production.number,
        "plant_lot": production.plant_lot,
        "customer_lot": production.customer_lot,
        "customer": production.customer.name,
        "process": production.process,
        "main_product": production.main_product.description,
        "reception_date": production.reception_date.isoformat(),
        "production_date": production.production_date.isoformat(),
        "packaging_date": production.packaging_date.isoformat() if production.packaging_date else None,
        "shift": production.shift,
        "series": production.series,
        "vehicle_notes": production.vehicle_notes,
        "plate_notes": production.plate_notes,
        "observations": production.observations,
        "template_version": production.template_version.code,
        "status": production.status,
    }


class FormTitleMixin:
    form_title = "Nuevo registro"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = self.form_title
        return context


class UserRegistrationView(FormView):
    template_name = "registration/register.html"
    form_class = UserRegistrationForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("productions:list")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        with transaction.atomic():
            user = form.save()
            AuditLog.objects.create(
                user=user,
                module="auth",
                model_name=user._meta.label,
                record_pk=str(user.pk),
                action=AuditLog.Action.CREATE,
                new_value={
                    "username": user.username,
                    "registration_status": user.registration_status,
                    "requested_role": user.requested_role,
                },
                ip_address=self.request.META.get("REMOTE_ADDR"),
                user_agent=self.request.META.get("HTTP_USER_AGENT", ""),
            )
        return redirect("productions:register_done")


class UserRegistrationDoneView(TemplateView):
    template_name = "registration/register_done.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("productions:list")
        return super().dispatch(request, *args, **kwargs)


class UserAdminRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (request.user.is_superuser or request.user.has_role(Role.Codes.ADMIN)):
            raise PermissionDenied("Solo el administrador puede gestionar usuarios.")
        return super().dispatch(request, *args, **kwargs)


class UserListView(UserAdminRequiredMixin, ListView):
    model = User
    template_name = "productions/user_list.html"
    context_object_name = "managed_users"

    def get_queryset(self):
        return User.objects.filter(is_superuser=False).prefetch_related("roles").annotate(
            status_order=Case(
                When(registration_status=User.RegistrationStatus.PENDING, then=Value(0)),
                When(registration_status=User.RegistrationStatus.ACTIVE, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        ).order_by("status_order", "first_name", "last_name", "username")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pending_count"] = User.objects.filter(registration_status=User.RegistrationStatus.PENDING).count()
        context["active_count"] = User.objects.filter(registration_status=User.RegistrationStatus.ACTIVE, is_superuser=False).count()
        return context


class UserAccessUpdateView(UserAdminRequiredMixin, UpdateView):
    model = User
    form_class = UserAccessForm
    template_name = "productions/user_access_form.html"
    context_object_name = "managed_user"

    def get_queryset(self):
        queryset = User.objects.filter(is_superuser=False).exclude(pk=self.request.user.pk).prefetch_related("roles")
        if not self.request.user.is_superuser:
            queryset = queryset.exclude(
                roles__code__in=[
                    Role.Codes.ADMIN,
                    Role.Codes.PRODUCTION_MANAGER,
                    Role.Codes.MANAGEMENT,
                    Role.Codes.AUDITOR,
                ]
            )
        return queryset.distinct()

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "actor": self.request.user}

    def form_valid(self, form):
        before = {
            "registration_status": self.object.registration_status,
            "is_active": self.object.is_active,
            "roles": list(self.object.roles.values_list("code", flat=True)),
            "active_assignments": self.object.area_assignments.filter(active=True).count(),
        }
        with transaction.atomic():
            self.object = form.save(approved_by=self.request.user)
            after = {
                "registration_status": self.object.registration_status,
                "is_active": self.object.is_active,
                "roles": list(self.object.roles.values_list("code", flat=True)),
                "active_assignments": self.object.area_assignments.filter(active=True).count(),
            }
            AuditLog.objects.create(
                user=self.request.user,
                module="users",
                model_name=self.object._meta.label,
                record_pk=str(self.object.pk),
                action=AuditLog.Action.UPDATE,
                old_value=before,
                new_value=after,
                reason="Aprobación o actualización de accesos de usuario",
                ip_address=self.request.META.get("REMOTE_ADDR"),
                user_agent=self.request.META.get("HTTP_USER_AGENT", ""),
            )
        messages.success(self.request, f"La cuenta de {self.object.get_full_name() or self.object.username} fue actualizada.")
        return redirect("productions:user_list")


class ProductionListView(LoginRequiredMixin, ListView):
    model = ProductionOrder
    template_name = "productions/production_list.html"
    context_object_name = "productions"
    paginate_by = 25

    def get_queryset(self):
        queryset = ProductionOrder.objects.select_related("customer", "main_product", "template_version")
        user = self.request.user
        self.can_review_void = user.is_superuser or user.roles.filter(
            code__in=[
                Role.Codes.ADMIN,
                Role.Codes.PRODUCTION_MANAGER,
                Role.Codes.MANAGEMENT,
                Role.Codes.AUDITOR,
            ]
        ).exists()
        if not self.can_review_void:
            queryset = queryset.filter(assignments__user=user, assignments__shift=F("shift"), assignments__active=True).distinct()
        requested_view = self.request.GET.get("view", "active")
        self.current_view = "void" if requested_view == "void" and self.can_review_void else "active"
        if self.current_view == "void":
            queryset = queryset.filter(status=ProductionOrder.Status.VOID)
        else:
            queryset = queryset.exclude(status=ProductionOrder.Status.VOID)
        query = self.request.GET.get("q", "").strip()
        if query:
            filters = Q(plant_lot__icontains=query) | Q(customer_lot__icontains=query) | Q(customer__name__icontains=query)
            if query.isdigit():
                filters |= Q(number=int(query))
            queryset = queryset.filter(filters)
        if date_from := self.request.GET.get("date_from"):
            queryset = queryset.filter(production_date__gte=date_from)
        if date_to := self.request.GET.get("date_to"):
            queryset = queryset.filter(production_date__lte=date_to)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_manager"] = self.request.user.is_superuser or self.request.user.roles.filter(code__in=[Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER]).exists()
        context["can_review_void"] = self.can_review_void
        context["current_view"] = self.current_view
        context["void_count"] = ProductionOrder.objects.filter(status=ProductionOrder.Status.VOID).count() if self.can_review_void else 0
        context["editable_production_statuses"] = PRODUCTION_EDITABLE_STATUSES
        return context


class ProductionCreateView(FormTitleMixin, LoginRequiredMixin, CreateView):
    model = ProductionOrder
    form_class = ProductionOrderForm
    template_name = "productions/form.html"
    form_title = "Nuevo parte de producción"

    def dispatch(self, request, *args, **kwargs):
        require_roles(request.user, Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, "Parte de producción creado.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("productions:detail", args=[self.object.pk])


class ProductionUpdateView(FormTitleMixin, LoginRequiredMixin, UpdateView):
    model = ProductionOrder
    form_class = ProductionOrderForm
    template_name = "productions/form.html"
    context_object_name = "production"

    def dispatch(self, request, *args, **kwargs):
        require_roles(request.user, Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER)
        production = get_object_or_404(ProductionOrder, pk=kwargs["pk"])
        if production.status not in PRODUCTION_EDITABLE_STATUSES:
            raise PermissionDenied(
                "Este parte está aprobado, cerrado o anulado. Reábralo antes de editar sus datos principales."
            )
        return super().dispatch(request, *args, **kwargs)

    @property
    def form_title(self):
        return f"Editar PP {self.object.number}" if getattr(self, "object", None) else "Editar parte de producción"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["editing_production"] = True
        context["submit_label"] = "Guardar cambios"
        return context

    def form_valid(self, form):
        before = _production_order_payload(
            ProductionOrder.objects.select_related(
                "customer", "main_product", "template_version"
            ).get(pk=self.object.pk)
        )
        try:
            with transaction.atomic():
                self.object = form.save()
                AuditLog.objects.create(
                    user=self.request.user,
                    production=self.object,
                    module="production",
                    model_name=self.object._meta.label,
                    record_pk=str(self.object.pk),
                    action=AuditLog.Action.UPDATE,
                    old_value=before,
                    new_value=_production_order_payload(self.object),
                    reason="Corrección de los datos principales del parte",
                    ip_address=self.request.META.get("REMOTE_ADDR"),
                    user_agent=self.request.META.get("HTTP_USER_AGENT", ""),
                )
        except IntegrityError:
            form.add_error("number", "Ya existe otro parte activo con este número de PP.")
            return self.form_invalid(form)
        messages.success(self.request, f"Los datos del PP {self.object.number} fueron actualizados.")
        return redirect("productions:detail", pk=self.object.pk)


class ProductionDetailView(LoginRequiredMixin, DetailView):
    model = ProductionOrder
    template_name = "productions/production_detail.html"
    context_object_name = "production"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if not can_view_production(self.request.user, obj):
            raise PermissionDenied
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        production = self.object
        context["tunnel_reconciliation"] = tunnel_reconciliation(production)
        context["plate_reconciliation"] = plate_reconciliation(production)
        rack_totals = TunnelEntry.objects.filter(rack__fill=OuterRef("pk"), is_active=True).values("rack__fill").annotate(total=Sum("tray_count")).values("total")
        crew_totals = TunnelCrewEntry.objects.filter(fill=OuterRef("pk"), is_active=True).values("fill").annotate(total=Sum("tray_count")).values("total")
        context["fills"] = production.tunnel_fills.select_related("tunnel", "supervisor").annotate(
            rack_trays=Coalesce(Subquery(rack_totals, output_field=IntegerField()), 0),
            crew_trays=Coalesce(Subquery(crew_totals, output_field=IntegerField()), 0),
        )
        context["allowed_transitions"] = TRANSITIONS.get(production.status, set())
        status_labels = dict(ProductionOrder.Status.choices)
        context["allowed_transition_choices"] = [
            (value, status_labels[value])
            for value in context["allowed_transitions"]
            if value not in {ProductionOrder.Status.VOID, ProductionOrder.Status.DRAFT}
        ]
        context["latest_file"] = production.generated_files.filter(valid=True).first()
        context["is_manager"] = self.request.user.is_superuser or self.request.user.roles.filter(code__in=[Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER]).exists()
        context["can_edit"] = context["is_manager"] and production.status in PRODUCTION_EDITABLE_STATUSES
        context["can_delete"] = context["is_manager"] and ProductionOrder.Status.VOID in context["allowed_transitions"]
        context["can_restore"] = context["is_manager"] and production.status == ProductionOrder.Status.VOID and ProductionOrder.Status.DRAFT in context["allowed_transitions"]
        context["user_areas"] = set(production.assignments.filter(user=self.request.user, shift=production.shift, active=True).values_list("area", flat=True))
        context["areas"] = AreaAssignment.Area
        excel_mapping = mapping_capabilities(production.template_version)
        context["excel_preliminary_ready"] = excel_mapping["ready"]
        context["excel_final_ready"] = excel_mapping["ready"] and excel_mapping["scope"] == "full" and production.status in {ProductionOrder.Status.APPROVED, ProductionOrder.Status.CLOSED}
        context["excel_mapping_scope"] = excel_mapping["scope"]
        context["excel_mapping_error"] = excel_mapping["error"]
        return context


class ProductionTransitionView(LoginRequiredMixin, View):
    def post(self, request, pk):
        form = TransitionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Solicitud de cambio de estado inválida.")
            return redirect("productions:detail", pk=pk)
        try:
            target_status = form.cleaned_data["target_status"]
            transition_production(production_id=pk, target_status=target_status, user=request.user, expected_version=form.cleaned_data["expected_version"], reason=form.cleaned_data["reason"])
            if target_status == ProductionOrder.Status.VOID:
                messages.success(request, "Parte eliminado de la lista activa. Puede revisarlo y restaurarlo desde Eliminados.")
                return redirect("productions:list")
            if target_status == ProductionOrder.Status.DRAFT:
                messages.success(request, "Parte restaurado como borrador.")
            else:
                messages.success(request, "Estado actualizado.")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        return redirect("productions:detail", pk=pk)


class TunnelFillCreateView(FormTitleMixin, LoginRequiredMixin, CreateView):
    form_class = TunnelFillForm
    template_name = "productions/form.html"
    form_title = "Nueva llenada de túnel"

    def dispatch(self, request, *args, **kwargs):
        self.production = get_object_or_404(ProductionOrder, pk=kwargs["pk"])
        if not can_view_production(request.user, self.production):
            raise PermissionDenied
        if self.production.status in {ProductionOrder.Status.APPROVED, ProductionOrder.Status.CLOSED, ProductionOrder.Status.VOID}:
            raise PermissionDenied("La producción no admite nuevos registros en su estado actual.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["version"] = self.production.version
        kwargs["production"] = self.production
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        tunnel = form.cleaned_data["tunnel"]
        require_area_assignment(self.request.user, self.production, AreaAssignment.Area.TUNNEL, tunnel=tunnel)
        with transaction.atomic():
            production = ProductionOrder.objects.select_for_update().get(pk=self.production.pk)
            if production.version != form.cleaned_data["expected_version"]:
                form.add_error(None, "La producción cambió en otra sesión; recargue el formulario.")
                return self.form_invalid(form)
            form.instance.production = production
            form.instance.supervisor = self.request.user
            form.instance.date = production.production_date or production.reception_date
            form.instance.observation = ""
            try:
                self.object = form.save()
                rack_count = ensure_tunnel_racks(self.object)
            except IntegrityError:
                form.add_error(None, "La llenada ya existe.")
                return self.form_invalid(form)
        if rack_count:
            messages.success(self.request, f"Llenada creada con {rack_count} racks de la plantilla.")
        else:
            messages.warning(self.request, "Llenada creada, pero la plantilla no tiene racks configurados para esta combinación.")
        return redirect("productions:tunnel_batch", pk=self.production.pk, fill_pk=self.object.pk)


class LegacyTunnelEntryCreateView(FormTitleMixin, LoginRequiredMixin, CreateView):
    form_class = TunnelEntryForm
    template_name = "productions/form.html"
    form_title = "Registrar bandejas por rack"

    def dispatch(self, request, *args, **kwargs):
        self.production = get_object_or_404(ProductionOrder, pk=kwargs["pk"])
        if not can_view_production(request.user, self.production):
            raise PermissionDenied
        if self.production.status in {ProductionOrder.Status.APPROVED, ProductionOrder.Status.CLOSED, ProductionOrder.Status.VOID}:
            raise PermissionDenied("La producción no admite nuevos registros en su estado actual.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "production": self.production, "user": self.request.user}

    def form_valid(self, form):
        rack = form.cleaned_data["rack"]
        require_area_assignment(self.request.user, self.production, AreaAssignment.Area.TUNNEL, tunnel=rack.fill.tunnel)
        form.instance.production = self.production
        form.instance.responsible = self.request.user
        try:
            form.instance.full_clean()
            self.object = form.save()
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, "; ".join(exc.messages) if hasattr(exc, "messages") else "Registro duplicado o incompatible.")
            return self.form_invalid(form)
        messages.success(self.request, "Bandejas guardadas.")
        return redirect("productions:detail", pk=self.production.pk)


class TunnelEntryCreateView(LoginRequiredMixin, View):
    """Mantiene el enlace anterior, pero dirige a la captura masiva por llenada."""

    def get(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        fills = production.tunnel_fills.filter(
            status__in=[TunnelFill.Status.OPEN, TunnelFill.Status.REOPENED]
        ).select_related("tunnel")
        if not (request.user.is_superuser or request.user.has_role(Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER)):
            fills = fills.filter(
                tunnel__assignments__production=production,
                tunnel__assignments__user=request.user,
                tunnel__assignments__area=AreaAssignment.Area.TUNNEL,
                tunnel__assignments__shift=production.shift,
                tunnel__assignments__active=True,
            ).distinct()
        fills = list(fills)
        if not fills:
            messages.warning(request, "Primero cree una llenada y seleccione el túnel.")
            return redirect("productions:tunnel_fill_create", pk=production.pk)
        if len(fills) == 1:
            return redirect("productions:tunnel_batch", pk=production.pk, fill_pk=fills[0].pk)
        return render(request, "productions/tunnel_fill_select.html", {"production": production, "fills": fills})


class TunnelBatchEntryView(LoginRequiredMixin, View):
    template_name = "productions/tunnel_batch.html"

    def dispatch(self, request, *args, **kwargs):
        self.production = get_object_or_404(ProductionOrder, pk=kwargs["pk"])
        self.fill = get_object_or_404(
            TunnelFill.objects.select_related("tunnel", "production"),
            pk=kwargs["fill_pk"],
            production=self.production,
        )
        if not can_view_production(request.user, self.production):
            raise PermissionDenied
        if self.production.status in {ProductionOrder.Status.APPROVED, ProductionOrder.Status.CLOSED, ProductionOrder.Status.VOID}:
            raise PermissionDenied("La producción no admite nuevos registros en su estado actual.")
        if self.fill.status not in {TunnelFill.Status.OPEN, TunnelFill.Status.REOPENED}:
            raise PermissionDenied("La llenada está cerrada. Reábrala antes de modificar sus racks.")
        require_area_assignment(request.user, self.production, AreaAssignment.Area.TUNNEL, tunnel=self.fill.tunnel)
        ensure_tunnel_racks(self.fill)
        return super().dispatch(request, *args, **kwargs)

    def _racks(self):
        active_entries = TunnelEntry.objects.filter(is_active=True).select_related("product").order_by("product__description", "pk")
        racks = list(
            self.fill.racks.prefetch_related(Prefetch("entries", queryset=active_entries, to_attr="active_entries"))
            .annotate(current_total=Coalesce(Sum("entries__tray_count", filter=Q(entries__is_active=True)), 0))
        )
        def natural_rack_key(rack):
            match = re.search(r"\d+", rack.code)
            if not match:
                return (rack.code.casefold(), -1, "", rack.code.casefold())
            return (
                rack.code[:match.start()].casefold(),
                int(match.group()),
                rack.code[match.end():].casefold(),
                rack.code.casefold(),
            )

        return sorted(racks, key=natural_rack_key)

    @staticmethod
    def _attach_racks(formset, racks):
        by_id = {rack.pk: rack for rack in racks}
        for form in formset.forms:
            raw_id = form["rack_id"].value()
            try:
                form.rack = by_id.get(int(raw_id))
            except (TypeError, ValueError):
                form.rack = None

    def _context(self, formset, racks):
        self._attach_racks(formset, racks)
        return {
            "production": self.production,
            "fill": self.fill,
            "formset": formset,
        }

    def get(self, request, *args, **kwargs):
        racks = self._racks()
        if not racks:
            messages.error(request, "La plantilla no tiene racks configurados para esta llenada.")
            return redirect("productions:detail", pk=self.production.pk)
        formset = TunnelBatchFormSet(
            initial=[{"rack_id": rack.pk, "max_trays": rack.max_trays} for rack in racks],
            prefix="racks",
        )
        return render(request, self.template_name, self._context(formset, racks))

    def post(self, request, *args, **kwargs):
        racks = self._racks()
        formset = TunnelBatchFormSet(request.POST, prefix="racks")
        valid = formset.is_valid()
        allowed_ids = {rack.pk for rack in racks}
        posted_ids = [form.cleaned_data.get("rack_id") for form in formset.forms if hasattr(form, "cleaned_data")]
        if valid and (len(posted_ids) != len(allowed_ids) or set(posted_ids) != allowed_ids):
            formset._non_form_errors = formset.error_class(["La lista de racks cambió. Recargue la pantalla."])
            valid = False
        if not valid:
            return render(request, self.template_name, self._context(formset, racks), status=400)

        plans = []
        for form in formset.forms:
            rack = next(rack for rack in racks if rack.pk == form.cleaned_data["rack_id"])
            max_trays = form.cleaned_data["max_trays"]
            entry_id = form.cleaned_data.get("entry_id")
            product = form.cleaned_data.get("product")
            trays = form.cleaned_data.get("tray_count")
            if rack.current_total > max_trays:
                form.add_error("max_trays", f"El rack ya contiene {rack.current_total} bandejas; no puede reducirse a {max_trays}.")
            if not product:
                plans.append((rack, max_trays, None, None, None))
                continue
            if entry_id:
                existing = next((entry for entry in rack.active_entries if entry.pk == entry_id), None)
                if existing is None:
                    form.add_error(None, "El registro que intenta corregir cambió o fue eliminado. Recargue la pantalla.")
                    plans.append((rack, max_trays, product, trays, None))
                    continue
            else:
                existing = next((entry for entry in rack.active_entries if entry.product_id == product.pk), None)
            if existing and any(
                entry.pk != existing.pk and entry.product_id == product.pk
                for entry in rack.active_entries
            ):
                form.add_error("product", "Ese producto ya está guardado en este rack. Corrija directamente ese registro.")
            total_without_existing = rack.current_total - (existing.tray_count if existing else 0)
            if total_without_existing + trays > max_trays:
                form.add_error("tray_count", f"El rack superaría su capacidad de {max_trays} bandejas; actualmente tiene {rack.current_total}.")
            plans.append((rack, max_trays, product, trays, existing))
        if any(form.errors for form in formset.forms):
            return render(request, self.template_name, self._context(formset, racks), status=400)

        try:
            with transaction.atomic():
                locked_racks = {
                    rack.pk: rack
                    for rack in TunnelRack.objects.select_for_update().filter(pk__in=allowed_ids)
                }
                existing_ids = {existing.pk for _, _, _, _, existing in plans if existing}
                locked_entries = {
                    entry.pk: entry
                    for entry in TunnelEntry.objects.select_for_update().filter(pk__in=existing_ids, is_active=True)
                }
                saved = 0
                capacity_changes = 0
                for rack, max_trays, product, trays, existing in plans:
                    locked_rack = locked_racks[rack.pk]
                    if locked_rack.max_trays != max_trays:
                        old_capacity = locked_rack.max_trays
                        locked_rack.max_trays = max_trays
                        locked_rack.full_clean()
                        locked_rack.save(update_fields=["max_trays"])
                        AuditLog.objects.create(
                            user=request.user,
                            production=self.production,
                            module="tunnel_racks",
                            model_name=locked_rack._meta.label,
                            record_pk=str(locked_rack.pk),
                            action=AuditLog.Action.UPDATE,
                            old_value={"max_trays": old_capacity},
                            new_value={"max_trays": max_trays},
                            ip_address=request.META.get("REMOTE_ADDR"),
                            user_agent=request.META.get("HTTP_USER_AGENT", ""),
                        )
                        capacity_changes += 1
                    rack.max_trays = max_trays
                    if not product:
                        continue
                    old_value = None
                    if existing:
                        entry = locked_entries.get(existing.pk)
                        if entry is None:
                            raise ValidationError("Uno de los registros cambió mientras se guardaba. Recargue la pantalla.")
                        old_value = {"product": entry.product.description, "tray_count": entry.tray_count}
                        entry.product = product
                        entry.tray_count = trays
                        entry.date = self.fill.date
                        entry.observation = ""
                        entry.responsible = request.user
                        action = AuditLog.Action.UPDATE
                    else:
                        entry = TunnelEntry(
                            production=self.production,
                            responsible=request.user,
                            rack=rack,
                            product=product,
                            tray_count=trays,
                            date=self.fill.date,
                            observation="",
                        )
                        action = AuditLog.Action.CREATE
                    entry.full_clean()
                    entry.save()
                    AuditLog.objects.create(
                        user=request.user,
                        production=self.production,
                        module="tunnel_racks",
                        model_name=entry._meta.label,
                        record_pk=str(entry.pk),
                        action=action,
                        old_value=old_value,
                        new_value={"rack": rack.code, "product": product.description, "tray_count": trays},
                        ip_address=request.META.get("REMOTE_ADDR"),
                        user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    )
                    saved += 1
        except (ValidationError, IntegrityError) as exc:
            formset._non_form_errors = formset.error_class([
                "; ".join(exc.messages) if hasattr(exc, "messages") else "No se pudo guardar porque otro registro cambió. Recargue la pantalla."
            ])
            return render(request, self.template_name, self._context(formset, self._racks()), status=400)

        if saved:
            messages.success(request, f"Se guardaron {saved} registros de racks en {self.fill.tunnel.code}, llenada {self.fill.fill_number}.")
        elif capacity_changes:
            messages.success(request, f"Se actualizó la capacidad de {capacity_changes} rack(s).")
        else:
            messages.info(request, "No se ingresaron bandejas nuevas.")
        return redirect("productions:tunnel_batch", pk=self.production.pk, fill_pk=self.fill.pk)


class TunnelEntryDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk, fill_pk, entry_pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        fill = get_object_or_404(
            TunnelFill.objects.select_related("tunnel"),
            pk=fill_pk,
            production=production,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {
            ProductionOrder.Status.APPROVED,
            ProductionOrder.Status.CLOSED,
            ProductionOrder.Status.VOID,
        }:
            raise PermissionDenied("La producción no admite correcciones en su estado actual.")
        if fill.status not in {TunnelFill.Status.OPEN, TunnelFill.Status.REOPENED}:
            raise PermissionDenied("La llenada está cerrada. Reábrala antes de corregir sus racks.")
        require_area_assignment(request.user, production, AreaAssignment.Area.TUNNEL, tunnel=fill.tunnel)

        with transaction.atomic():
            entry = get_object_or_404(
                TunnelEntry.objects.select_for_update().select_related("rack", "product"),
                pk=entry_pk,
                rack__fill=fill,
                production=production,
                is_active=True,
            )
            old_value = {
                "rack": entry.rack.code,
                "product": entry.product.description,
                "tray_count": entry.tray_count,
            }
            entry.delete(user=request.user, reason="Corrección desde la captura por túnel")
            AuditLog.objects.create(
                user=request.user,
                production=production,
                module="tunnel_racks",
                model_name=entry._meta.label,
                record_pk=str(entry.pk),
                action=AuditLog.Action.VOID,
                old_value=old_value,
                new_value={"is_active": False},
                reason="Registro eliminado para corregir la captura por túnel",
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        messages.success(request, f"Se eliminó {old_value['product']} del rack {old_value['rack']}.")
        return redirect("productions:tunnel_batch", pk=production.pk, fill_pk=fill.pk)


class TunnelFillTransitionView(LoginRequiredMixin, View):
    def post(self, request, pk, fill_pk):
        fill = get_object_or_404(TunnelFill, pk=fill_pk, production_id=pk)
        if not can_view_production(request.user, fill.production):
            raise PermissionDenied
        try:
            transition_tunnel_fill(
                fill_id=fill.pk,
                target_status=request.POST.get("target_status", ""),
                user=request.user,
                expected_version=int(request.POST.get("expected_version", 0)),
                reason=request.POST.get("reason", ""),
            )
            messages.success(request, "Estado de la llenada actualizado.")
        except (ValidationError, PermissionDenied, ValueError) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        return redirect("productions:detail", pk=pk)


OPERATIONAL_MODULES = {
    "reception": {
        "model": ReceptionEntry,
        "form_class": ReceptionEntryForm,
        "area": AreaAssignment.Area.RECEPTION,
        "title": "Recepción de materia prima",
        "create_url_name": "productions:reception_create",
    },
    "nuqueras": {
        "model": NuqueraEntry,
        "form_class": NuqueraEntryForm,
        "area": AreaAssignment.Area.NUQUERAS,
        "title": "Nuqueras o perfilado",
        "create_url_name": "productions:nuquera_create",
    },
    "tunnel-crews": {
        "model": TunnelCrewEntry,
        "form_class": TunnelCrewEntryForm,
        "area": AreaAssignment.Area.TUNNEL_CREW,
        "title": "Bandejas por cuadrilla de túnel",
        "create_url_name": "productions:tunnel_crew_create",
    },
    "plates": {
        "model": PlateEntry,
        "form_class": PlateEntryForm,
        "area": AreaAssignment.Area.PLATES,
        "title": "Envasado en plaqueros",
        "create_url_name": "productions:plate_create",
    },
    "plate-crews": {
        "model": PlateCrewEntry,
        "form_class": PlateCrewEntryForm,
        "area": AreaAssignment.Area.PLATE_CREW,
        "title": "Cuadrillas de placas",
        "create_url_name": "productions:plate_crew_create",
    },
    "tunnel-pack": {
        "model": TunnelPackagingEntry,
        "form_class": TunnelPackagingEntryForm,
        "area": AreaAssignment.Area.TUNNEL_PACK,
        "title": "Empaque de túneles",
        "create_url_name": "productions:tunnel_pack_create",
    },
    "plate-pack": {
        "model": PlatePackagingEntry,
        "form_class": PlatePackagingEntryForm,
        "area": AreaAssignment.Area.PLATE_PACK,
        "title": "Empaque de placas",
        "create_url_name": "productions:plate_pack_create",
    },
    "materials": {
        "model": MaterialUsage,
        "form_class": MaterialUsageForm,
        "area": AreaAssignment.Area.MATERIALS,
        "title": "Materiales e insumos",
        "create_url_name": "productions:material_create",
    },
    "costs": {
        "model": CostEntry,
        "form_class": CostEntryForm,
        "area": AreaAssignment.Area.COSTS,
        "title": "Costos de producción",
        "create_url_name": "productions:cost_create",
    },
}


def _operational_config(module_key):
    config = OPERATIONAL_MODULES.get(module_key)
    if config is None:
        raise Http404("Módulo operativo no disponible.")
    return config


def _date_text(value):
    return value.strftime("%d/%m/%Y") if value else "Sin fecha"


def _operational_record_text(entry):
    if isinstance(entry, ReceptionEntry):
        title = f"{_date_text(entry.date)} · {entry.vehicle.plate} · Dino {entry.container}"
        detail = f"{entry.product.description} · {_format_kg(entry.weight_kg)} kg"
    elif isinstance(entry, NuqueraEntry):
        title = f"{_date_text(entry.date)} · {entry.crew.name}"
        detail = f"{entry.worker.full_name} · {entry.process} · {_format_kg(entry.weight_kg)} kg"
    elif isinstance(entry, TunnelCrewEntry):
        title = f"{entry.fill.tunnel.code} · Llenada {entry.fill.fill_number} · {entry.page_or_block}"
        detail = f"{entry.crew.name} · {entry.tray_count} bandejas"
    elif isinstance(entry, PlateEntry):
        title = f"{_date_text(entry.date)} · {entry.position.display_name}"
        detail = f"{entry.product.description} · {entry.tray_count} bandejas"
    elif isinstance(entry, PlateCrewEntry):
        title = f"{_date_text(entry.date)} · {entry.position.display_name} · {entry.page}"
        detail = f"{entry.crew.name} · {entry.tray_count} bandejas"
    elif isinstance(entry, (TunnelPackagingEntry, PlatePackagingEntry)):
        title = f"{_date_text(entry.date)} · Palé P{entry.pallet_number}"
        detail = f"{entry.product.description} · {entry.package_count} bultos"
    elif isinstance(entry, MaterialUsage):
        title = entry.material.name
        quantity = _format_kg(entry.quantity) if entry.material.unit.strip().lower() == "kg" else entry.quantity
        detail = f"{quantity} {entry.material.unit}"
    elif isinstance(entry, CostEntry):
        title = entry.concept
        detail = f"{entry.quantity} × S/ {entry.unit_cost} = S/ {entry.total}"
    else:
        title = f"Registro {entry.pk}"
        detail = ""
    return title, detail


def _operational_record_payload(entry):
    title, detail = _operational_record_text(entry)
    return {"title": title, "detail": detail}


def _operational_record_cards(config, production):
    queryset = (
        config["model"].objects.filter(production=production, is_active=True)
        .select_related()
        .order_by("-created_at", "-pk")
    )
    cards = []
    for entry in queryset:
        title, detail = _operational_record_text(entry)
        cards.append({"entry": entry, "title": title, "detail": detail})
    return cards


def _reception_record_groups(production):
    """Agrupa visualmente la recepcion como se mapea en la hoja R.M: por vehiculo."""
    queryset = (
        ReceptionEntry.objects.filter(production=production, is_active=True)
        .select_related("vehicle", "product", "crew", "responsible")
        .order_by("created_at", "pk")
    )
    groups_by_vehicle = {}
    for entry in queryset:
        group = groups_by_vehicle.setdefault(
            entry.vehicle_id,
            {
                "vehicle": entry.vehicle,
                "car_numbers": [],
                "entries": [],
                "total_weight": 0,
            },
        )
        car_number = (entry.car_number or "").strip()
        if car_number and car_number not in group["car_numbers"]:
            group["car_numbers"].append(car_number)
        group["entries"].append(entry)
        group["total_weight"] += entry.weight_kg

    groups = []
    for group in groups_by_vehicle.values():
        group["entries"].sort(
            key=lambda entry: (
                int(entry.container) if str(entry.container).isdigit() else 9999,
                str(entry.container),
                entry.pk,
            )
        )
        car_numbers = group.pop("car_numbers")
        group["car_number_values"] = car_numbers
        group["car_number"] = " / ".join(car_numbers) or "Sin número"
        group["dino_count"] = len(group["entries"])
        crew_totals = {}
        for entry in group["entries"]:
            crew_key = entry.crew_id or 0
            crew_total = crew_totals.setdefault(
                crew_key,
                {
                    "name": entry.crew.name if entry.crew else "Sin cuadrilla",
                    "dino_count": 0,
                    "total_weight": Decimal("0.00"),
                },
            )
            crew_total["dino_count"] += 1
            crew_total["total_weight"] += entry.weight_kg
        group["crew_totals"] = sorted(
            crew_totals.values(), key=lambda item: item["name"].casefold()
        )
        slots = set()
        group["has_dino_conflict"] = False
        for entry in group["entries"]:
            slot = (entry.crew_id, str(entry.container).strip())
            if slot in slots:
                group["has_dino_conflict"] = True
            slots.add(slot)
        group["has_multiple_car_numbers"] = len(car_numbers) > 1
        group["has_product_conflict"] = len(
            {entry.product_id for entry in group["entries"]}
        ) > 1
        group["has_crew_conflict"] = len(
            {entry.crew_id for entry in group["entries"] if entry.crew_id}
        ) > 2
        groups.append(group)

    car_usage = {}
    for group in groups:
        for car_number in group["car_number_values"]:
            car_usage.setdefault(car_number.casefold(), set()).add(group["vehicle"].pk)
    for group in groups:
        group["has_car_conflict"] = any(
            len(car_usage[car_number.casefold()]) > 1
            for car_number in group["car_number_values"]
        )
        group["has_mapping_conflict"] = any(
            (
                group["has_car_conflict"],
                group["has_multiple_car_numbers"],
                group["has_product_conflict"],
                group["has_crew_conflict"],
                group["has_dino_conflict"],
            )
        )

    return sorted(
        groups,
        key=lambda group: (
            int(group["car_number"]) if group["car_number"].isdigit() else 9999,
            group["car_number"],
            group["vehicle"].plate,
        ),
    )


class OperationalContextMixin:
    template_name = "productions/operational_form.html"
    module_key = None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if "production" in self.form_class.__init__.__code__.co_varnames:
            kwargs["production"] = self.production
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        config = _operational_config(self.module_key)
        context.update(
            {
                "production": self.production,
                "module_key": self.module_key,
                "module_title": config["title"],
                "module_create_url": reverse(config["create_url_name"], args=[self.production.pk]),
                "record_cards": _operational_record_cards(config, self.production),
                "reception_record_groups": (
                    _reception_record_groups(self.production) if self.module_key == "reception" else []
                ),
                "editing": bool(getattr(self, "object", None)),
                "operational_read_only": getattr(self, "operational_read_only", False),
            }
        )
        return context


class OperationalCreateView(OperationalContextMixin, FormTitleMixin, LoginRequiredMixin, CreateView):
    area = None

    def dispatch(self, request, *args, **kwargs):
        self.production = get_object_or_404(ProductionOrder, pk=kwargs["pk"])
        if not can_view_production(request.user, self.production):
            raise PermissionDenied
        self.operational_read_only = self.production.status in {
            ProductionOrder.Status.APPROVED,
            ProductionOrder.Status.CLOSED,
        }
        if self.operational_read_only and request.method not in {"GET", "HEAD", "OPTIONS"}:
            raise PermissionDenied("El parte está aprobado o cerrado. Reábralo antes de modificar registros.")
        if self.production.status == ProductionOrder.Status.VOID:
            raise PermissionDenied("La producción no admite nuevos registros en su estado actual.")
        require_area_assignment(request.user, self.production, self.area)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.production = self.production
        form.instance.responsible = self.request.user
        if hasattr(form.instance, "date") and not form.instance.date:
            if isinstance(form.instance, ReceptionEntry):
                form.instance.date = self.production.reception_date
            else:
                form.instance.date = self.production.production_date or self.production.reception_date
        try:
            with transaction.atomic():
                form.instance.full_clean()
                self.object = form.save()
                AuditLog.objects.create(
                    user=self.request.user,
                    production=self.production,
                    module=self.module_key,
                    model_name=self.object._meta.label,
                    record_pk=str(self.object.pk),
                    action=AuditLog.Action.CREATE,
                    new_value=_operational_record_payload(self.object),
                    ip_address=self.request.META.get("REMOTE_ADDR"),
                    user_agent=self.request.META.get("HTTP_USER_AGENT", ""),
                )
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, "; ".join(exc.messages) if hasattr(exc, "messages") else "Registro duplicado o incompatible.")
            return self.form_invalid(form)
        messages.success(self.request, "Registro guardado. Ahora puede corregirlo o eliminarlo desde esta misma pantalla.")
        config = _operational_config(self.module_key)
        return redirect(config["create_url_name"], pk=self.production.pk)


class OperationalEntryUpdateView(OperationalContextMixin, FormTitleMixin, LoginRequiredMixin, UpdateView):
    pk_url_kwarg = "entry_pk"

    def dispatch(self, request, *args, **kwargs):
        self.module_key = kwargs["module"]
        config = _operational_config(self.module_key)
        self.model = config["model"]
        self.form_class = config["form_class"]
        self.area = config["area"]
        self.form_title = f"Corregir {config['title'].lower()}"
        self.production = get_object_or_404(ProductionOrder, pk=kwargs["pk"])
        if not can_view_production(request.user, self.production):
            raise PermissionDenied
        if self.production.status in {ProductionOrder.Status.APPROVED, ProductionOrder.Status.CLOSED, ProductionOrder.Status.VOID}:
            raise PermissionDenied("La producción no admite correcciones en su estado actual.")
        require_area_assignment(request.user, self.production, self.area)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.model.objects.filter(production=self.production, is_active=True)

    def form_valid(self, form):
        previous = self.model.objects.select_related().get(pk=self.object.pk)
        old_value = _operational_record_payload(previous)
        form.instance.production = self.production
        form.instance.responsible = self.request.user
        try:
            with transaction.atomic():
                form.instance.full_clean()
                self.object = form.save()
                AuditLog.objects.create(
                    user=self.request.user,
                    production=self.production,
                    module=self.module_key,
                    model_name=self.object._meta.label,
                    record_pk=str(self.object.pk),
                    action=AuditLog.Action.UPDATE,
                    old_value=old_value,
                    new_value=_operational_record_payload(self.object),
                    ip_address=self.request.META.get("REMOTE_ADDR"),
                    user_agent=self.request.META.get("HTTP_USER_AGENT", ""),
                )
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, "; ".join(exc.messages) if hasattr(exc, "messages") else "Registro duplicado o incompatible.")
            return self.form_invalid(form)
        messages.success(self.request, "Corrección guardada correctamente.")
        config = _operational_config(self.module_key)
        return redirect(config["create_url_name"], pk=self.production.pk)


class OperationalEntryDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk, module, entry_pk):
        config = _operational_config(module)
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {ProductionOrder.Status.APPROVED, ProductionOrder.Status.CLOSED, ProductionOrder.Status.VOID}:
            raise PermissionDenied("La producción no admite correcciones en su estado actual.")
        require_area_assignment(request.user, production, config["area"])
        with transaction.atomic():
            entry = get_object_or_404(
                config["model"].objects.select_for_update().select_related(),
                pk=entry_pk,
                production=production,
                is_active=True,
            )
            old_value = _operational_record_payload(entry)
            entry.delete(user=request.user, reason=f"Corrección en {config['title']}")
            AuditLog.objects.create(
                user=request.user,
                production=production,
                module=module,
                model_name=entry._meta.label,
                record_pk=str(entry.pk),
                action=AuditLog.Action.VOID,
                old_value=old_value,
                new_value={"is_active": False},
                reason="Registro eliminado para corregir la captura",
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        messages.success(request, f"Se eliminó: {old_value['title']}.")
        return redirect(config["create_url_name"], pk=production.pk)


class ReceptionCreateView(OperationalCreateView):
    module_key = "reception"
    form_class = ReceptionEntryForm
    area = AreaAssignment.Area.RECEPTION
    form_title = "Registrar recepción de materia prima"

    def _selected_car_entry(self):
        if not hasattr(self, "_selected_car_entry_cache"):
            vehicle_id = self.request.GET.get("car", "")
            queryset = ReceptionEntry.objects.none()
            if vehicle_id.isdigit():
                queryset = (
                    ReceptionEntry.objects.filter(
                        production=self.production,
                        vehicle_id=int(vehicle_id),
                        is_active=True,
                    )
                    .select_related("vehicle", "product")
                    .order_by("created_at", "pk")
                )
            self._selected_car_entry_cache = queryset.first()
        return self._selected_car_entry_cache

    def get_initial(self):
        initial = super().get_initial()
        selected = self._selected_car_entry()
        if selected is not None:
            initial.update(
                {
                    "vehicle_text": selected.vehicle.plate,
                    "car_number": selected.car_number,
                    "product": selected.product_id,
                }
            )
        return initial

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if self._selected_car_entry() is not None:
            for field_name in ("vehicle_text", "car_number", "product"):
                form.fields[field_name].disabled = True
                form.fields[field_name].help_text = "Fijado al carro seleccionado para continuar llenando sus dinos."
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected = self._selected_car_entry()
        context["selected_reception_car"] = next(
            (
                group
                for group in context["reception_record_groups"]
                if selected is not None and group["vehicle"].pk == selected.vehicle_id
            ),
            None,
        )
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if getattr(self, "object", None) is not None and response.status_code == 302:
            return redirect(
                f"{reverse('productions:reception_create', args=[self.production.pk])}"
                f"?car={self.object.vehicle_id}#reception-entry-form"
            )
        return response


class NuqueraCreateView(OperationalCreateView):
    module_key = "nuqueras"
    form_class = NuqueraEntryForm
    area = AreaAssignment.Area.NUQUERAS
    form_title = "Registrar nuqueras o perfilado"


class TunnelCrewCreateView(OperationalCreateView):
    module_key = "tunnel-crews"
    form_class = TunnelCrewEntryForm
    area = AreaAssignment.Area.TUNNEL_CREW
    form_title = "Registrar bandejas por cuadrilla de túnel"


class PlateCreateView(OperationalCreateView):
    module_key = "plates"
    form_class = PlateEntryForm
    area = AreaAssignment.Area.PLATES
    form_title = "Registrar envasado en plaqueros"


class PlateCrewCreateView(OperationalCreateView):
    module_key = "plate-crews"
    form_class = PlateCrewEntryForm
    area = AreaAssignment.Area.PLATE_CREW
    form_title = "Registrar cuadrilla de placas"


class TunnelPackagingCreateView(OperationalCreateView):
    module_key = "tunnel-pack"
    form_class = TunnelPackagingEntryForm
    area = AreaAssignment.Area.TUNNEL_PACK
    form_title = "Registrar empaque de túneles"


class PlatePackagingCreateView(OperationalCreateView):
    module_key = "plate-pack"
    form_class = PlatePackagingEntryForm
    area = AreaAssignment.Area.PLATE_PACK
    form_title = "Registrar empaque de placas"


class MaterialUsageCreateView(OperationalCreateView):
    module_key = "materials"
    form_class = MaterialUsageForm
    area = AreaAssignment.Area.MATERIALS
    form_title = "Registrar materiales e insumos"


class CostEntryCreateView(OperationalCreateView):
    module_key = "costs"
    form_class = CostEntryForm
    area = AreaAssignment.Area.COSTS
    form_title = "Registrar costo de producción"


class CatalogDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "productions/catalogs.html"

    def dispatch(self, request, *args, **kwargs):
        require_roles(request.user, Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "customer_count": Customer.objects.filter(active=True).count(),
                "product_count": Product.objects.filter(active=True).count(),
                "vehicle_count": Vehicle.objects.filter(active=True).count(),
                "worker_count": Worker.objects.filter(active=True).count(),
                "rate_count": Rate.objects.filter(active=True).count(),
                "template_count": TemplateVersion.objects.filter(active=True).count(),
                "position_count": PlatePosition.objects.filter(active=True).count(),
                "user_count": User.objects.filter(is_superuser=False).count(),
                "pending_user_count": User.objects.filter(registration_status=User.RegistrationStatus.PENDING).count(),
            }
        )
        return context


class CatalogCreateView(FormTitleMixin, LoginRequiredMixin, CreateView):
    template_name = "productions/form.html"

    def dispatch(self, request, *args, **kwargs):
        require_roles(request.user, Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        messages.success(self.request, f"{self.form_title} guardado correctamente.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("productions:catalogs")


class CustomerCreateView(CatalogCreateView):
    form_class = CustomerForm
    form_title = "Nuevo cliente"


class VehicleCreateView(CatalogCreateView):
    form_class = VehicleForm
    form_title = "Nuevo vehículo"


class WorkerCreateView(CatalogCreateView):
    form_class = WorkerForm
    form_title = "Nuevo trabajador"


class RateCreateView(CatalogCreateView):
    form_class = RateForm
    form_title = "Nueva tarifa"


class ProductionReportView(LoginRequiredMixin, DetailView):
    model = ProductionOrder
    template_name = "productions/report.html"
    context_object_name = "production"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if not can_view_production(self.request.user, obj):
            raise PermissionDenied
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        production = self.object
        context.update(
            {
                "tunnel_reconciliation": tunnel_reconciliation(production),
                "plate_reconciliation": plate_reconciliation(production),
                "receptions": ReceptionEntry.objects.filter(production=production, is_active=True).select_related("vehicle", "product", "crew"),
                "reception_total": ReceptionEntry.objects.filter(production=production, is_active=True).aggregate(total=Sum("weight_kg"))["total"] or 0,
                "nuqueras": NuqueraEntry.objects.filter(production=production, is_active=True).select_related("worker", "crew"),
                "nuquera_total": NuqueraEntry.objects.filter(production=production, is_active=True).aggregate(total=Sum("weight_kg"))["total"] or 0,
                "tunnel_packaging": TunnelPackagingEntry.objects.filter(production=production, is_active=True).select_related("product"),
                "plate_packaging": PlatePackagingEntry.objects.filter(production=production, is_active=True).select_related("product"),
                "materials": MaterialUsage.objects.filter(production=production, is_active=True).select_related("material"),
                "costs": CostEntry.objects.filter(production=production, is_active=True).select_related("rate"),
                "audit_logs": production.audit_logs.select_related("user")[:20],
            }
        )
        return context


def _generated_file_response(request, generated):
    if not generated.file.storage.exists(generated.file.name):
        raise Http404("Archivo no disponible")
    AuditLog.objects.create(
        user=request.user,
        production=generated.production,
        module="excel",
        model_name=generated._meta.label,
        record_pk=str(generated.pk),
        action=AuditLog.Action.DOWNLOAD,
        new_value={"filename": generated.filename},
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    response = FileResponse(
        generated.file.open("rb"),
        as_attachment=True,
        filename=generated.filename,
        content_type="application/vnd.ms-excel.sheet.macroEnabled.12",
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


class GenerateExcelView(LoginRequiredMixin, View):
    def post(self, request, pk, kind):
        kind_value = GeneratedFile.Kind.FINAL if kind == "final" else GeneratedFile.Kind.PRELIMINARY
        try:
            generated = generate_production_workbook(production_id=pk, user=request.user, kind=kind_value)
            return _generated_file_response(request, generated)
        except (GenerationError, PermissionDenied) as exc:
            messages.error(request, str(exc))
        return redirect("productions:detail", pk=pk)


class DownloadGeneratedFileView(LoginRequiredMixin, View):
    def get(self, request, pk):
        generated = get_object_or_404(GeneratedFile.objects.select_related("production"), pk=pk, valid=True)
        if not can_view_production(request.user, generated.production):
            raise PermissionDenied
        return _generated_file_response(request, generated)


class ProductionPdfView(LoginRequiredMixin, View):
    def get(self, request, pk):
        production = get_object_or_404(ProductionOrder.objects.select_related("customer", "main_product", "template_version"), pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        payload = build_production_pdf(production)
        safe_lot = re.sub(r"[^A-Za-z0-9_-]+", "-", production.plant_lot).strip("-") or "SIN-LOTE"
        filename = f"PP_{production.number}_{safe_lot}.pdf"
        AuditLog.objects.create(user=request.user, production=production, module="pdf", model_name=production._meta.label, record_pk=str(production.pk), action=AuditLog.Action.DOWNLOAD, new_value={"filename": filename}, ip_address=request.META.get("REMOTE_ADDR"), user_agent=request.META.get("HTTP_USER_AGENT", ""))
        response = HttpResponse(payload, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


def health(request):
    return HttpResponse("ok", content_type="text/plain")


def manifest(request):
    return HttpResponse('{"name":"Partes de Producción","short_name":"PP Planta","start_url":"/","display":"standalone","background_color":"#f3f5f4","theme_color":"#124b3b","lang":"es-PE","icons":[{"src":"/static/icons/icon.svg","sizes":"any","type":"image/svg+xml","purpose":"any maskable"}]}', content_type="application/manifest+json")


def service_worker(request):
    content = """const CACHE='pp-shell-v2';const ASSETS=['/','/manifest.webmanifest','/static/css/app.css','/static/js/app.js','/static/icons/icon.svg'];self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS))));self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))));self.addEventListener('fetch',e=>{if(e.request.method==='GET')e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)))});"""
    response = HttpResponse(content, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    return response
