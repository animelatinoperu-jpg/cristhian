import datetime as dt
import itertools
import re
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Case, Count, F, IntegerField, OuterRef, Prefetch, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView

from .forms import (
    CustomerForm,
    MaterialUsageForm,
    CostEntryForm,
    NuqueraEntryForm,
    PlateCrewEntryForm,
    PlateEntryForm,
    PlatePackagingAllocationForm,
    PlatePackagingEntryForm,
    ProductionOrderForm,
    ReceptionEntryForm,
    ProductLaminaColorForm,
    TransitionForm,
    TunnelCrewEntryForm,
    TunnelBatchFormSet,
    active_crew_queryset,
    find_existing_crew_by_name,
    normalized_crew_name,
    TunnelEntryForm,
    TunnelFillForm,
    TunnelPackagingEntryForm,
    TroqueladoEntryForm,
    UserAccessForm,
    UserRegistrationForm,
    RateForm,
    VehicleForm,
    WorkerForm,
)
from .models import AreaAssignment, AuditLog, Crew, Customer, GeneratedFile, PlatePosition, PlatePositionTiming, PlateEntry, PlateCrewEntry, PlatePackagingAllocation, PlateCarryoverBalance, PlatePallet, PlatePalletConsumption, PlatePalletLine, Product, ProductionOrder, Rate, Role, TemplateVersion, Tunnel, TunnelFill, TunnelRack, TunnelEntry, TunnelCrewEntry, ReceptionEntry, ReceptionCarTiming, NuqueraEntry, TunnelPackagingEntry, PlatePackagingEntry, MaterialUsage, CostEntry, TroqueladoEntry, User, Vehicle, Worker
from .services.excel import GenerationError, generate_production_workbook, mapping_capabilities
from .services.permissions import can_view_crew_control, can_view_production, require_area_assignment, require_roles
from .services.pdf_report import build_production_pdf
from .services.plate_report import PlateReportError, build_plate_report_xlsx
from .services.plate_report_pdf import build_plate_report_pdf
from .services.tunnel_report import TunnelReportError, build_tunnel_report_xlsx
from .services.tunnel_report_pdf import build_tunnel_report_pdf
from .services.packaging_report import (
    PackagingReportError,
    build_plate_packaging_report_xlsx,
    build_tunnel_packaging_report_xlsx,
)
from .services.packaging_report_pdf import (
    build_plate_packaging_report_pdf,
    build_tunnel_packaging_report_pdf,
)
from .services.reception_report import build_reception_report_pdf, build_reception_report_xlsx
from .services.reception_tareo_report import ReceptionTareoReportError, build_reception_tareo_xlsx
from .services.reception_tareo_report_pdf import build_reception_tareo_pdf
from .services.nuquera_tareo_report import NuqueraTareoReportError, build_nuquera_tareo_xlsx
from .services.nuquera_tareo_report_pdf import build_nuquera_tareo_pdf
from .services.troquelado_report import TroqueladoReportError, build_troquelado_xlsx
from .services.troquelado_report_pdf import build_troquelado_pdf
from .services.reconciliation import plate_reconciliation, tunnel_reconciliation
from .services.layout import ensure_tunnel_racks
from .services.permanent_delete import permanently_delete_production
from .services.tunnel_transfer import transfer_tunnel_fill
from .services.crew_control import crew_control_summary, reception_cone_pota_summary
from .services.plate_balances import (
    auto_pack_product,
    plate_balance_dashboard,
    plate_pallet_dashboard,
    plate_product_availability,
    register_initial_plate_balance,
    register_manual_plate_balance,
    set_plate_pallet_status,
    void_auto_pack_line,
)
from .services.workflow import TRANSITIONS, transition_production, transition_tunnel_fill
from .request_context import suppress_automatic_audit


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


def _format_duration(started_at, completed_at):
    if not started_at or not completed_at:
        return None
    total_seconds = max(int((completed_at - started_at).total_seconds()), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} h {minutes:02d} min {seconds:02d} s"
    if minutes:
        return f"{minutes} min {seconds:02d} s"
    return f"{seconds} s"


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
                with suppress_automatic_audit():
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
        context["fills"] = production.tunnel_fills.filter(is_active=True).select_related("tunnel", "supervisor").annotate(
            rack_trays=Coalesce(Subquery(rack_totals, output_field=IntegerField()), 0),
            crew_trays=Coalesce(Subquery(crew_totals, output_field=IntegerField()), 0),
        )
        context["report_tunnels"] = production.tunnel_fills.filter(
            is_active=True,
            racks__entries__is_active=True,
        ).values("tunnel_id", "tunnel__code").distinct().order_by("tunnel__code")
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
        context["can_hard_delete"] = context["is_manager"] and production.status == ProductionOrder.Status.VOID
        context["can_restore"] = context["is_manager"] and production.status == ProductionOrder.Status.VOID and ProductionOrder.Status.DRAFT in context["allowed_transitions"]
        context["user_areas"] = set(production.assignments.filter(user=self.request.user, shift=production.shift, active=True).values_list("area", flat=True))
        context["areas"] = AreaAssignment.Area
        context["can_view_crew_control"] = can_view_crew_control(self.request.user, production)
        if context["can_view_crew_control"]:
            context["crew_control_preview"] = crew_control_summary(production)
        excel_mapping = mapping_capabilities(production.template_version)
        context["excel_preliminary_ready"] = excel_mapping["ready"]
        context["excel_final_ready"] = excel_mapping["ready"] and excel_mapping["scope"] == "full" and production.status in {ProductionOrder.Status.APPROVED, ProductionOrder.Status.CLOSED}
        context["excel_mapping_scope"] = excel_mapping["scope"]
        context["excel_mapping_error"] = excel_mapping["error"]
        return context


class CrewControlView(LoginRequiredMixin, DetailView):
    model = ProductionOrder
    template_name = "productions/crew_control.html"
    context_object_name = "production"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if not can_view_crew_control(self.request.user, obj):
            raise PermissionDenied
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["summary"] = crew_control_summary(self.object)
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


class ProductionHardDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not (request.user.is_superuser or request.user.roles.filter(code__in=[Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER]).exists()):
            raise PermissionDenied
        try:
            expected_version = int(request.POST.get("expected_version") or "")
        except (TypeError, ValueError):
            messages.error(request, "Solicitud de borrado inválida.")
            return redirect("productions:detail", pk=pk)
        reason = (request.POST.get("reason") or "").strip()
        if not reason:
            messages.error(request, "El borrado definitivo requiere un motivo.")
            return redirect("productions:detail", pk=pk)
        try:
            report = permanently_delete_production(
                production_id=pk,
                expected_version=expected_version,
            )
            messages.success(
                request,
                f"PP {report['number']} eliminado definitivamente con {report['audit_logs']} registros de historial "
                f"y {report['generated_files']} archivos generados.",
            )
            return redirect("productions:list")
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
            launched_at = timezone.localtime().replace(microsecond=0)
            form.instance.start_time = launched_at.time()
            form.instance.launch_time = launched_at.time()
            form.instance.end_time = (launched_at + timedelta(hours=12)).time().replace(microsecond=0)
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
        except (ValidationError, IntegrityError, PlateEntry.DoesNotExist) as exc:
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
            is_active=True,
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
            is_active=True,
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
        active_crew_entries = (
            TunnelCrewEntry.objects.filter(is_active=True)
            .select_related("crew", "product", "responsible")
            .order_by("product__description", "crew__name", "pk")
        )
        rack_queryset = self.fill.racks
        if self.fill.tunnel.code == "T4":
            rack_queryset = rack_queryset.exclude(code="R20")
        racks = list(
            rack_queryset.prefetch_related(
                Prefetch("entries", queryset=active_entries, to_attr="active_entries"),
                Prefetch("crew_entries", queryset=active_crew_entries, to_attr="active_crew_entries"),
            )
            .annotate(current_total=Coalesce(Sum("entries__tray_count", filter=Q(entries__is_active=True)), 0))
        )
        for rack in racks:
            rack.is_full = rack.current_total == rack.max_trays
            rack.is_closed = rack.status == TunnelRack.Status.CLOSED
            rack.is_complete = rack.is_full or rack.is_closed
            assigned_by_product = {}
            for assignment in rack.active_crew_entries:
                assigned_by_product[assignment.product_id] = (
                    assigned_by_product.get(assignment.product_id, 0)
                    + assignment.tray_count
                )
            rack.has_crew_balance = False
            rack.crew_pending_total = 0
            for physical_entry in rack.active_entries:
                physical_entry.remaining_for_crew = max(
                    physical_entry.tray_count
                    - assigned_by_product.get(physical_entry.product_id, 0),
                    0,
                )
                rack.crew_pending_total += physical_entry.remaining_for_crew
                if physical_entry.remaining_for_crew:
                    rack.has_crew_balance = True

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
        physical_total = sum(getattr(rack, "current_total", 0) or 0 for rack in racks)
        assigned_total = sum(
            sum(entry.tray_count for entry in getattr(rack, "active_crew_entries", []))
            for rack in racks
        )
        pending_racks = [
            {
                "id": rack.pk,
                "code": rack.code,
                "pending_total": rack.crew_pending_total,
            }
            for rack in racks
            if rack.crew_pending_total > 0
        ]
        crews = active_crew_queryset()
        return {
            "production": self.production,
            "fill": self.fill,
            "formset": formset,
            "is_manager": self.request.user.is_superuser
            or self.request.user.has_role(Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER),
            "crew_options": crews,
            "crew_suggestions": list(crews.values_list("pk", "name")),
            "transfer_tunnels": Tunnel.objects.filter(active=True)
            .exclude(pk=self.fill.tunnel_id)
            .order_by("code"),
            "open_rack_id": self.request.GET.get("open_rack") or "",
            "tunnel_crew_summary": {
                "physical_total": physical_total,
                "assigned_total": assigned_total,
                "pending_total": max(physical_total - assigned_total, 0),
                "unmatched_total": max(assigned_total - physical_total, 0),
                "pending_racks": pending_racks,
            },
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

    def _post_crew_assignment(self, request, racks):
        rack_id = request.POST.get("crew_rack_id")
        rack = next((item for item in racks if str(item.pk) == str(rack_id)), None)
        if rack is None:
            messages.error(request, "El rack ya no está disponible. Recargue la pantalla.")
            return redirect(f"{reverse('productions:tunnel_batch', args=[self.production.pk, self.fill.pk])}?open_rack={rack_id}#rack-{rack_id}")

        product_id = request.POST.get(f"crew_product_{rack.pk}") or ""
        crew_id = request.POST.get(f"crew_{rack.pk}") or ""
        crew_name = " ".join((request.POST.get(f"crew_name_{rack.pk}") or "").strip().upper().split())
        tray_text = request.POST.get(f"crew_trays_{rack.pk}") or ""
        crew_entry_id = request.POST.get(f"crew_entry_id_{rack.pk}") or ""
        assign_all = (
            request.GET.get("crew_assign_mode") == "all"
            or request.POST.get(f"crew_assign_all_{rack.pk}") == "1"
        )
        if not ((crew_id.isdigit() or crew_name) and (assign_all or (product_id.isdigit() and tray_text.isdigit()))):
            messages.error(request, "Seleccione producto, cuadrilla y bandejas para asignar el rack.")
            return redirect(f"{reverse('productions:tunnel_batch', args=[self.production.pk, self.fill.pk])}?open_rack={rack.pk}#rack-{rack.pk}")

        if crew_id.isdigit():
            crew = get_object_or_404(Crew, pk=int(crew_id), active=True)
        else:
            crew = find_existing_crew_by_name(crew_name)
            if crew is None:
                messages.error(request, "Esa cuadrilla todavía no existe. Créela primero y luego selecciónela.")
                return redirect(f"{reverse('productions:tunnel_batch', args=[self.production.pk, self.fill.pk])}?open_rack={rack.pk}#rack-{rack.pk}")
        try:
            with transaction.atomic():
                locked_rack = (
                    TunnelRack.objects.select_for_update()
                    .select_related("fill")
                    .get(pk=rack.pk, fill=self.fill)
                )
                if assign_all:
                    target_entries = list(locked_rack.entries.filter(is_active=True).select_related("product"))
                    if not target_entries:
                        messages.error(request, "Ese rack ya no tiene bandejas activas para asignar.")
                        return redirect(f"{reverse('productions:tunnel_batch', args=[self.production.pk, self.fill.pk])}?open_rack={rack.pk}#rack-{rack.pk}")
                else:
                    product = get_object_or_404(Product, pk=int(product_id), active=True)
                    product_entry = (
                        locked_rack.entries.filter(is_active=True, product=product)
                        .select_related("product")
                        .first()
                    )
                    if product_entry is None:
                        messages.error(request, "El producto seleccionado ya no está disponible en ese rack.")
                        return redirect(f"{reverse('productions:tunnel_batch', args=[self.production.pk, self.fill.pk])}?open_rack={rack.pk}#rack-{rack.pk}")
                    if int(tray_text) < 1:
                        messages.error(request, "Ingrese al menos 1 bandeja para la cuadrilla.")
                        return redirect(f"{reverse('productions:tunnel_batch', args=[self.production.pk, self.fill.pk])}?open_rack={rack.pk}#rack-{rack.pk}")
                    target_entries = [product_entry]

                saved = 0
                editing_entry = None
                if crew_entry_id.isdigit() and not assign_all:
                    editing_entry = (
                        locked_rack.crew_entries.filter(pk=int(crew_entry_id), is_active=True)
                        .select_related("crew", "product")
                        .first()
                    )
                    if editing_entry is None:
                        messages.error(request, "La asignaciÃ³n de cuadrilla que intenta corregir ya no existe.")
                        return redirect(f"{reverse('productions:tunnel_batch', args=[self.production.pk, self.fill.pk])}?open_rack={rack.pk}#rack-{rack.pk}")
                for physical_entry in target_entries:
                    existing_entry = editing_entry or locked_rack.crew_entries.filter(
                        is_active=True,
                        crew=crew,
                        product=physical_entry.product,
                    ).first()
                    if assign_all:
                        assigned_to_other_crews = (
                            locked_rack.crew_entries.filter(
                                is_active=True,
                                product=physical_entry.product,
                            )
                            .exclude(pk=existing_entry.pk if existing_entry else None)
                            .aggregate(total=Sum("tray_count"))["total"]
                            or 0
                        )
                        assigned_trays = max(
                            int(physical_entry.tray_count) - int(assigned_to_other_crews),
                            0,
                        )
                        if assigned_trays < 1:
                            continue
                    else:
                        assigned_trays = int(tray_text)
                    entry = existing_entry or TunnelCrewEntry(
                        production=self.production,
                        responsible=request.user,
                        fill=self.fill,
                        rack=locked_rack,
                        product=physical_entry.product,
                        crew=crew,
                        page_or_block=locked_rack.code,
                        date=self.fill.date,
                    )
                    entry.tray_count = assigned_trays
                    entry.production = self.production
                    entry.responsible = request.user
                    entry.fill = self.fill
                    entry.rack = locked_rack
                    entry.product = physical_entry.product
                    entry.crew = crew
                    entry.page_or_block = locked_rack.code
                    entry.date = self.fill.date
                    entry.full_clean()
                    with suppress_automatic_audit():
                        entry.save()
                    saved += 1
                if assign_all and saved == 0:
                    messages.error(request, "Todas las bandejas de ese rack ya están asignadas.")
                    return redirect(f"{reverse('productions:tunnel_batch', args=[self.production.pk, self.fill.pk])}?open_rack={rack.pk}#rack-{rack.pk}")
                AuditLog.objects.create(
                    user=request.user,
                    production=self.production,
                    module="tunnel-crews",
                    model_name=entry._meta.label,
                    record_pk=str(entry.pk),
                    action=AuditLog.Action.CREATE,
                    new_value=_operational_record_payload(entry),
                    ip_address=request.META.get("REMOTE_ADDR"),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                )
        except (ValidationError, IntegrityError) as exc:
            messages.error(
                request,
                "; ".join(exc.messages) if hasattr(exc, "messages") else "No se pudo asignar la cuadrilla. Revise si ya existe o si supera las bandejas disponibles.",
            )
        else:
            if assign_all:
                messages.success(request, f"Se asignó {crew.name} a todas las bandejas del rack {locked_rack.code}.")
            else:
                messages.success(request, f"Se asignó {crew.name} al rack {locked_rack.code}.")
        return redirect(f"{reverse('productions:tunnel_batch', args=[self.production.pk, self.fill.pk])}?open_rack={rack.pk}#rack-{rack.pk}")

    def post(self, request, *args, **kwargs):
        racks = self._racks()
        if "crew_rack_id" in request.POST:
            return self._post_crew_assignment(request, racks)
        post_data = request.POST.copy()
        save_rack_id = post_data.get("save_rack_id") or ""
        if save_rack_id.isdigit():
            for index in range(int(post_data.get("racks-TOTAL_FORMS", 0) or 0)):
                rack_id_key = f"racks-{index}-rack_id"
                if post_data.get(rack_id_key) == save_rack_id:
                    continue
                post_data[f"racks-{index}-entry_id"] = ""
                post_data[f"racks-{index}-product"] = ""
                post_data[f"racks-{index}-tray_count"] = ""
        formset = TunnelBatchFormSet(post_data, prefix="racks")
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
            if rack.status == TunnelRack.Status.CLOSED:
                # Los racks cerrados siguen formando parte del formset para que
                # la lista no cambie, pero deben ser filas de solo lectura. No
                # pueden impedir que se guarden bandejas en los racks abiertos.
                if entry_id or product or trays:
                    form.add_error(None, "Este rack está cerrado. Reábralo antes de modificarlo.")
                plans.append((rack, max_trays, None, None, None))
                continue
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
                    with suppress_automatic_audit():
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
        open_rack_id = request.POST.get("open_rack_id") or ""
        base_url = reverse("productions:tunnel_batch", args=[self.production.pk, self.fill.pk])
        if str(open_rack_id).isdigit():
            return redirect(f"{base_url}?open_rack={open_rack_id}#rack-{open_rack_id}")
        return redirect(base_url)


class TunnelEntryDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk, fill_pk, entry_pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        fill = get_object_or_404(
            TunnelFill.objects.select_related("tunnel"),
            pk=fill_pk,
            production=production,
            is_active=True,
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
            with suppress_automatic_audit():
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


class TunnelRackTransitionView(LoginRequiredMixin, View):
    def get(self, request, pk, fill_pk, rack_pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        fill = get_object_or_404(TunnelFill, pk=fill_pk, production=production, is_active=True)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        messages.info(request, "Volvió a la captura del túnel. Use el botón del rack para cambiar su estado.")
        return redirect("productions:tunnel_batch", pk=production.pk, fill_pk=fill.pk)

    def post(self, request, pk, fill_pk, rack_pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        fill = get_object_or_404(TunnelFill.objects.select_related("tunnel"), pk=fill_pk, production=production, is_active=True)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {ProductionOrder.Status.APPROVED, ProductionOrder.Status.CLOSED, ProductionOrder.Status.VOID}:
            raise PermissionDenied("La producción no admite cambios en sus racks.")
        if fill.status not in {TunnelFill.Status.OPEN, TunnelFill.Status.REOPENED}:
            raise PermissionDenied("La llenada está cerrada. Reábrala antes de modificar sus racks.")
        target_status = request.POST.get("target_status")
        reason_field = "close_reason" if target_status == TunnelRack.Status.CLOSED else "reopen_reason"
        reason = (
            request.POST.get("reason")
            or request.POST.get(f"{reason_field}_{rack_pk}")
            or ""
        ).strip()
        try:
            with transaction.atomic():
                rack = get_object_or_404(
                    TunnelRack.objects.select_for_update(), pk=rack_pk, fill=fill
                )
                total = (
                    TunnelEntry.objects.filter(rack=rack, is_active=True)
                    .aggregate(total=Sum("tray_count"))["total"]
                    or 0
                )
                old_status = rack.status
                if target_status == TunnelRack.Status.CLOSED:
                    require_area_assignment(request.user, production, AreaAssignment.Area.TUNNEL, tunnel=fill.tunnel)
                    product_id = request.POST.get("product")
                    trays = request.POST.get("tray_count")
                    capacity = request.POST.get("max_trays")
                    if bool(product_id) != bool(trays):
                        raise ValidationError("Seleccione producto y bandejas, o deje ambos vacíos.")
                    if capacity:
                        rack.max_trays = int(capacity)
                        rack.full_clean()
                    if product_id:
                        product = Product.objects.get(pk=int(product_id), active=True)
                        tray_count = int(trays)
                        existing = TunnelEntry.objects.filter(
                            rack=rack, product=product, is_active=True
                        ).first()
                        total_without_existing = total - (existing.tray_count if existing else 0)
                        if tray_count < 1 or total_without_existing + tray_count > rack.max_trays:
                            raise ValidationError("Las bandejas superan la capacidad del rack.")
                        entry = existing or TunnelEntry(
                            production=production, responsible=request.user, rack=rack, product=product,
                            date=fill.date, observation="",
                        )
                        entry.tray_count = tray_count
                        entry.responsible = request.user
                        entry.full_clean()
                        entry.save()
                        total = total_without_existing + tray_count
                    if not total:
                        raise ValidationError("No puede cerrar un rack sin bandejas registradas.")
                    if total < rack.max_trays and not reason:
                        reason = (
                            f"Cierre manual incompleto: {total}/{rack.max_trays} bandejas."
                        )
                    rack.status = TunnelRack.Status.CLOSED
                    rack.closed_at = timezone.now()
                    rack.closed_by = request.user
                    rack.close_reason = reason
                    action = "cerrado"
                elif target_status == TunnelRack.Status.OPEN:
                    require_roles(request.user, Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER)
                    if not reason:
                        raise ValidationError("La reapertura del rack requiere un motivo.")
                    rack.status = TunnelRack.Status.OPEN
                    rack.closed_at = None
                    rack.closed_by = None
                    rack.close_reason = ""
                    action = "reabierto"
                else:
                    raise ValidationError("Estado de rack no permitido.")
                rack.save(update_fields=["max_trays", "status", "closed_at", "closed_by", "close_reason"])
                AuditLog.objects.create(
                    user=request.user,
                    production=production,
                    module="tunnel_racks",
                    model_name=rack._meta.label,
                    record_pk=str(rack.pk),
                    action=AuditLog.Action.TRANSITION,
                    old_value={"status": old_status, "trays": total},
                    new_value={"status": rack.status, "trays": total},
                    reason=reason,
                    ip_address=request.META.get("REMOTE_ADDR"),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                )
        except (ValidationError, PermissionDenied, Product.DoesNotExist, TypeError, ValueError) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        else:
            messages.success(request, f"Rack {rack.code} {action}.")
        return redirect(f"{reverse('productions:tunnel_batch', args=[production.pk, fill.pk])}?open_rack={rack_pk}#rack-{rack_pk}")


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


class TunnelFillTransferView(LoginRequiredMixin, View):
    def post(self, request, pk, fill_pk):
        fill = get_object_or_404(
            TunnelFill.objects.select_related("production", "tunnel"),
            pk=fill_pk,
            production_id=pk,
            is_active=True,
        )
        if not can_view_production(request.user, fill.production):
            raise PermissionDenied
        require_roles(request.user, Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER)
        if fill.production.status not in PRODUCTION_EDITABLE_STATUSES:
            raise PermissionDenied("El PP no admite transferencias en su estado actual.")

        try:
            target_tunnel_id = int(request.POST.get("target_tunnel", ""))
            transferred = transfer_tunnel_fill(
                fill_id=fill.pk,
                target_tunnel_id=target_tunnel_id,
                user=request.user,
                reason=request.POST.get("reason", ""),
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        except (
            ValidationError,
            Tunnel.DoesNotExist,
            TunnelFill.DoesNotExist,
            TypeError,
            ValueError,
        ) as exc:
            messages.error(
                request,
                "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc),
            )
        else:
            messages.success(
                request,
                f"Llenada transferida completa a {transferred.tunnel.code}. "
                "Productos, racks, cuadrillas, horas y reportes conservaron su información.",
            )
        return redirect("productions:tunnel_batch", pk=pk, fill_pk=fill_pk)


class TunnelFillDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk, fill_pk):
        fill = get_object_or_404(
            TunnelFill.objects.select_related("production", "tunnel"),
            pk=fill_pk,
            production_id=pk,
            is_active=True,
        )
        production = fill.production
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status not in PRODUCTION_EDITABLE_STATUSES:
            raise PermissionDenied("El PP no admite eliminar llenadas en su estado actual.")
        if not (request.user.is_superuser or request.user.roles.filter(code__in=[Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER]).exists() or production.assignments.filter(user=request.user, area=AreaAssignment.Area.TUNNEL, shift=production.shift, active=True).exists()):
            raise PermissionDenied("No tiene permiso para eliminar llenadas.")

        has_entries = TunnelEntry.objects.filter(rack__fill=fill, is_active=True).exists()
        has_crew_entries = TunnelCrewEntry.objects.filter(fill=fill, is_active=True).exists()
        if has_entries or has_crew_entries:
            messages.error(
                request,
                "No se puede eliminar esta llenada porque ya tiene bandejas o cuadrillas registradas.",
            )
            return redirect("productions:detail", pk=production.pk)

        old_value = {
            "tunnel": fill.tunnel.code,
            "fill_number": fill.fill_number,
            "status": fill.status,
            "rack_count": fill.racks.count(),
        }
        try:
            with transaction.atomic():
                fill.is_active = False
                fill.voided_at = timezone.now()
                fill.voided_by = request.user
                fill.void_reason = request.POST.get("reason", "") or "Llenada eliminada desde el detalle del PP"
                fill.save(update_fields=["is_active", "voided_at", "voided_by", "void_reason", "version", "updated_at"])
                AuditLog.objects.create(
                    user=request.user,
                    production=production,
                    module="tunnel-fill",
                    model_name=TunnelFill._meta.label,
                    record_pk=str(fill_pk),
                    action=AuditLog.Action.VOID,
                    old_value=old_value,
                    reason=fill.void_reason,
                    ip_address=request.META.get("REMOTE_ADDR"),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                )
        except ProtectedError:
            messages.error(
                request,
                "No se pudo eliminar: esta llenada tiene datos relacionados. Elimine primero esos registros.",
            )
        else:
            messages.success(
                request,
                f"Llenada {fill.tunnel.code} · {fill.fill_number} eliminada.",
            )
        return redirect("productions:detail", pk=production.pk)


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
        "model": PlatePackagingAllocation,
        "form_class": PlatePackagingAllocationForm,
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
    "troquelado": {
        "model": TroqueladoEntry,
        "form_class": TroqueladoEntryForm,
        "area": AreaAssignment.Area.TROQUELADO,
        "title": "Troquelado",
        "create_url_name": "productions:troquelado_create",
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
        location = entry.rack.code if entry.rack_id else entry.page_or_block
        title = f"{entry.fill.tunnel.code} · Llenada {entry.fill.fill_number} · {location}"
        product = entry.product.description if entry.product_id else "Producto no identificado"
        detail = f"{entry.crew.name} · {product} · {entry.tray_count} bandejas"
    elif isinstance(entry, PlateEntry):
        title = f"{_date_text(entry.date)} · {entry.position.operational_label}"
        detail = f"{entry.product.description} · {entry.tray_count} bandejas"
    elif isinstance(entry, PlateCrewEntry):
        title = f"{_date_text(entry.date)} · {entry.position.operational_label} · {entry.page}"
        product = entry.product.description if entry.product_id else "Producto no identificado"
        detail = f"{entry.crew.name} · {product} · {entry.tray_count} bandejas"
    elif isinstance(entry, (TunnelPackagingEntry, PlatePackagingEntry)):
        title = f"{_date_text(entry.date)} · Palé P{entry.pallet_number}"
        detail = f"{entry.product.description} · {entry.package_count} bultos"
    elif isinstance(entry, PlatePackagingAllocation):
        title = (
            f"{entry.source_entry.position.operational_label} · "
            f"{entry.source_entry.product.code} · Palé P{entry.pallet_number}"
        )
        detail = (
            f"{entry.source_entry.product.description} · {entry.package_count} bultos · "
            f"{entry.tray_count} bandejas"
        )
    elif isinstance(entry, MaterialUsage):
        title = entry.material.name
        quantity = _format_kg(entry.quantity) if entry.material.unit.strip().lower() == "kg" else entry.quantity
        detail = f"{quantity} {entry.material.unit}"
    elif isinstance(entry, CostEntry):
        title = entry.concept
        detail = f"{entry.quantity} × S/ {entry.unit_cost} = S/ {entry.total}"
    elif isinstance(entry, TroqueladoEntry):
        crew_label = entry.crew.name if entry.crew else "Sin cuadrilla"
        category_label = (
            TroqueladoEntry.ProductType(entry.product_type).label
            if entry.product_type
            else "Sin categoría"
        )
        title = f"{_date_text(entry.date)} · {entry.worker.full_name} · {crew_label}"
        detail = (
            f"{category_label} · {entry.cajas} cajas × {_format_kg(entry.kg_por_caja)} kg = "
            f"{_format_kg(entry.weight_kg)} kg"
        )
    else:
        title = f"Registro {entry.pk}"
        detail = ""
    return title, detail


def _operational_record_payload(entry):
    title, detail = _operational_record_text(entry)
    return {"title": title, "detail": detail}


def _plate_timing_payload(timing):
    return {
        "position": timing.position.operational_label,
        "load_started_at": (
            timing.load_started_at.isoformat() if timing.load_started_at else None
        ),
        "load_completed_at": (
            timing.load_completed_at.isoformat() if timing.load_completed_at else None
        ),
        "launched_at": timing.launched_at.isoformat() if timing.launched_at else None,
        "unloaded_at": timing.unloaded_at.isoformat() if timing.unloaded_at else None,
    }


def _clear_empty_plate_timing(*, production, position_id, user, request, reason):
    """Remove a plaquero clock only when no physical or crew records remain."""
    if PlateEntry.objects.filter(
        production=production,
        position_id=position_id,
        is_active=True,
    ).exists():
        return False
    if PlateCrewEntry.objects.filter(
        production=production,
        position_id=position_id,
        is_active=True,
    ).exists():
        return False

    timing = (
        PlatePositionTiming.objects.select_for_update()
        .select_related("position")
        .filter(production=production, position_id=position_id)
        .first()
    )
    if timing is None:
        return False

    timing_pk = timing.pk
    old_value = _plate_timing_payload(timing)
    model_name = timing._meta.label
    timing.delete()
    AuditLog.objects.create(
        user=user,
        production=production,
        module="plate_timing",
        model_name=model_name,
        record_pk=str(timing_pk),
        action=AuditLog.Action.VOID,
        old_value=old_value,
        new_value={"deleted": True, "empty_position": True},
        reason=reason,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    return True


def _lock_tunnel_crew_racks(instance, *, previous=None):
    """Serialize crew assignments that affect the same physical rack.

    Validation compares assigned trays against the trays actually filled in the
    rack. Locking the rack prevents two simultaneous requests from both reading
    the same available balance and exceeding it when they save.
    """
    rack_ids = {
        rack_id
        for rack_id in (
            getattr(instance, "rack_id", None),
            getattr(previous, "rack_id", None),
        )
        if rack_id is not None
    }
    if not rack_ids:
        return
    locked_racks = {
        rack.pk: rack
        for rack in (
            TunnelRack.objects.select_for_update()
            .select_related("fill")
            .filter(pk__in=sorted(rack_ids))
            .order_by("pk")
        )
    }
    if len(locked_racks) != len(rack_ids):
        raise ValidationError({"rack": "Uno de los racks seleccionados ya no está disponible."})
    selected_rack = locked_racks.get(instance.rack_id)
    if selected_rack is not None:
        instance.rack = selected_rack
        instance.fill = selected_rack.fill
        instance.production = selected_rack.fill.production


def _lock_plate_positions(instance, *, previous=None):
    """Serialize physical and crew changes that affect the same plaquero."""
    position_ids = {
        position_id
        for position_id in (
            getattr(instance, "position_id", None),
            getattr(previous, "position_id", None),
        )
        if position_id is not None
    }
    if not position_ids:
        return
    locked_positions = {
        position.pk: position
        for position in (
            PlatePosition.objects.select_for_update()
            .select_related("template_version")
            .filter(pk__in=sorted(position_ids))
            .order_by("pk")
        )
    }
    if len(locked_positions) != len(position_ids):
        raise ValidationError({"position": "Una de las posiciones seleccionadas ya no está disponible."})
    selected_position = locked_positions.get(instance.position_id)
    if selected_position is not None:
        instance.position = selected_position


def _validate_plate_physical_move(instance, previous):
    if not isinstance(instance, PlateEntry) or previous.position_id == instance.position_id:
        return
    remaining_physical = (
        PlateEntry.objects.filter(
            production=instance.production,
            position_id=previous.position_id,
            is_active=True,
        )
        .exclude(pk=previous.pk)
        .aggregate(total=Sum("tray_count"))["total"]
        or 0
    )
    assigned_total = (
        PlateCrewEntry.objects.filter(
            production=instance.production,
            position_id=previous.position_id,
            is_active=True,
        ).aggregate(total=Sum("tray_count"))["total"]
        or 0
    )
    if assigned_total > remaining_physical:
        raise ValidationError(
            {
                "position": (
                    f"No puede mover este registro: la posición anterior quedaría con "
                    f"{remaining_physical} bandejas físicas y {assigned_total} asignadas a cuadrillas."
                )
            }
        )


def _operational_record_cards(config, production):
    ownership_filter = (
        {"fill__production": production}
        if config["model"] is TunnelCrewEntry
        else {"production": production}
    )
    queryset = config["model"].objects.filter(
        **ownership_filter, is_active=True
    ).select_related().order_by("-created_at", "-pk")
    cards = []
    for entry in queryset:
        title, detail = _operational_record_text(entry)
        cards.append({"entry": entry, "title": title, "detail": detail})
    return cards


def _entry_hours(start, end):
    if not start or not end:
        return Decimal("0")
    try:
        start_dt = dt.datetime.combine(dt.date.today(), start)
        end_dt = dt.datetime.combine(dt.date.today(), end)
    except TypeError:
        return Decimal("0")
    delta = (end_dt - start_dt).total_seconds() / 3600
    if delta <= 0:
        return Decimal("0")
    return Decimal(str(round(delta, 2)))


def _troquelado_dashboard(production):
    entries = TroqueladoEntry.objects.filter(
        production=production,
        is_active=True,
    ).select_related("worker", "crew")
    totals = entries.aggregate(
        record_count=Count("pk"),
        cajas_total=Coalesce(Sum("cajas"), 0),
        kg_total=Coalesce(Sum("weight_kg"), Decimal("0.00")),
    )
    kg_total = Decimal(totals["kg_total"] or 0)
    max_kg = kg_total or Decimal("1")

    by_crew = {}
    for entry in entries.order_by("crew__name", "worker__full_name"):
        crew_key = entry.crew_id or 0
        crew = by_crew.setdefault(
            crew_key,
            {
                "crew_name": entry.crew.name if entry.crew else "Sin cuadrilla",
                "kg": Decimal("0"),
                "workers": {},
            },
        )
        worker = crew["workers"].setdefault(
            entry.worker_id,
            {
                "name": entry.worker.full_name if entry.worker else "Sin nombre",
                "kg": Decimal("0"),
                "categories": {},
            },
        )
        hours = _entry_hours(entry.start_time, entry.end_time)
        label = TroqueladoEntry.ProductType(entry.product_type).label if entry.product_type else "Sin categoría"
        category = worker["categories"].setdefault(
            label,
            {"label": label, "kg": Decimal("0"), "hours": Decimal("0")},
        )
        weight = Decimal(entry.weight_kg or 0)
        category["kg"] += weight
        category["hours"] += hours
        worker["kg"] += weight
        crew["kg"] += weight

    per_crew = []
    for crew_key, crew in sorted(by_crew.items(), key=lambda item: item[1]["crew_name"].casefold()):
        workers = []
        for worker_id, worker in sorted(
            crew["workers"].items(), key=lambda item: item[1]["name"].casefold()
        ):
            categories = []
            for label, category in sorted(
                worker["categories"].items(), key=lambda item: item[1]["kg"], reverse=True
            ):
                categories.append(
                    {
                        "label": category["label"],
                        "kg": category["kg"],
                        "kg_per_hour": (
                            category["kg"] / category["hours"]
                            if category["hours"] > 0
                            else Decimal("0")
                        ),
                    }
                )
            workers.append(
                {
                    "name": worker["name"],
                    "kg": worker["kg"],
                    "cajas": 0,
                    "record_count": 0,
                    "categories": categories,
                    "percent": int((worker["kg"] / max_kg) * 100),
                    "initial": worker["name"].strip()[:1].upper(),
                }
            )
        per_crew.append(
            {
                "crew_name": crew["crew_name"],
                "kg": crew["kg"],
                "percent": int((crew["kg"] / max_kg) * 100),
                "workers": workers,
            }
        )

    return {
        "record_count": totals["record_count"],
        "cajas_total": totals["cajas_total"],
        "kg_total": kg_total,
        "per_crew": per_crew,
    }


def _natural_rack_key(rack):
    match = re.search(r"\d+", rack.code)
    if not match:
        return (rack.code.casefold(), -1, "", rack.code.casefold())
    return (
        rack.code[:match.start()].casefold(),
        int(match.group()),
        rack.code[match.end():].casefold(),
        rack.code.casefold(),
    )


def _tunnel_crew_rack_groups(fill):
    physical_entries = TunnelEntry.objects.filter(is_active=True).select_related("product").order_by("product__description", "pk")
    crew_entries = (
        TunnelCrewEntry.objects.filter(is_active=True)
        .select_related("crew", "product", "responsible")
        .order_by("crew__name", "product__description", "pk")
    )
    racks = list(
        fill.racks.prefetch_related(
            Prefetch("entries", queryset=physical_entries, to_attr="active_physical_entries"),
            Prefetch("crew_entries", queryset=crew_entries, to_attr="active_crew_entries"),
        )
    )
    groups = []
    totals_by_crew = {}
    physical_total = 0
    assigned_total = 0
    legacy_entries = list(
        TunnelCrewEntry.objects.filter(fill=fill, rack__isnull=True, is_active=True)
        .select_related("crew", "responsible")
        .order_by("crew__name", "pk")
    )
    for entry in legacy_entries:
        item = totals_by_crew.setdefault(
            entry.crew_id,
            {"crew": entry.crew, "tray_count": 0, "weight_kg": Decimal("0.00")},
        )
        item["tray_count"] += entry.tray_count
        item["weight_kg"] += Decimal(entry.tray_count) * Decimal("10.00")
        assigned_total += entry.tray_count
    for rack in sorted(racks, key=_natural_rack_key):
        rack_physical = sum(entry.tray_count for entry in rack.active_physical_entries)
        rack_assigned = sum(entry.tray_count for entry in rack.active_crew_entries)
        if rack_physical == 0 and rack_assigned == 0:
            continue
        physical_total += rack_physical
        assigned_total += rack_assigned
        for entry in rack.active_crew_entries:
            item = totals_by_crew.setdefault(
                entry.crew_id,
                {"crew": entry.crew, "tray_count": 0, "weight_kg": Decimal("0.00")},
            )
            item["tray_count"] += entry.tray_count
            item["weight_kg"] += Decimal(entry.tray_count) * Decimal("10.00")
        groups.append(
            {
                "rack": rack,
                "entries": rack.active_crew_entries,
                "physical_entries": rack.active_physical_entries,
                "physical_total": rack_physical,
                "assigned_total": rack_assigned,
                "pending_total": max(rack_physical - rack_assigned, 0),
            }
        )
    return {
        "groups": groups,
        "legacy_entries": legacy_entries,
        "crew_totals": sorted(totals_by_crew.values(), key=lambda item: item["crew"].name.casefold()),
        "physical_total": physical_total,
        "assigned_total": assigned_total,
        "pending_total": max(physical_total - assigned_total, 0),
    }


def _natural_plate_position_key(position):
    return (
        position.batch_number or 9999,
        position.plaquero_number or 9999,
        position.position_key.casefold(),
    )


def _plate_crew_position_groups(production):
    packaging_by_position = {
        group["position"].pk: group
        for group in _plate_packaging_trace_data(production)["groups"]
    }
    physical_entries = (
        PlateEntry.objects.filter(production=production, is_active=True)
        .select_related("product", "responsible")
        .order_by("product__description", "pk")
    )
    crew_entries = (
        PlateCrewEntry.objects.filter(production=production, is_active=True)
        .select_related("crew", "product", "responsible")
        .order_by("crew__name", "product__description", "page", "pk")
    )
    timings_by_position = {
        timing.position_id: timing
        for timing in PlatePositionTiming.objects.filter(production=production).select_related(
            "load_started_by",
            "load_completed_by",
            "launched_by",
            "unloaded_by",
        )
    }
    positions = list(
        PlatePosition.objects.filter(
            template_version=production.template_version,
            active=True,
        )
        .filter(
            Q(entries__production=production, entries__is_active=True)
            | Q(crew_entries__production=production, crew_entries__is_active=True)
            | Q(production_timings__production=production)
        )
        .distinct()
        .prefetch_related(
            Prefetch("entries", queryset=physical_entries, to_attr="active_physical_entries"),
            Prefetch("crew_entries", queryset=crew_entries, to_attr="active_crew_entries"),
        )
    )
    groups = []
    totals_by_crew = {}
    physical_total = 0
    assigned_total = 0
    capacity_total = 0
    for position in sorted(positions, key=_natural_plate_position_key):
        position_physical = sum(entry.tray_count for entry in position.active_physical_entries)
        position_assigned = sum(entry.tray_count for entry in position.active_crew_entries)
        timing = timings_by_position.get(position.pk)
        physical_total += position_physical
        assigned_total += position_assigned
        capacity_total += position.max_trays
        for entry in position.active_crew_entries:
            item = totals_by_crew.setdefault(
                entry.crew_id,
                {
                    "crew": entry.crew,
                    "tray_count": 0,
                    "weight_kg": Decimal("0.00"),
                    "product_totals": {},
                },
            )
            item["tray_count"] += entry.tray_count
            item["weight_kg"] += Decimal(entry.tray_count) * Decimal("10.00")
            product_key = entry.product_id or 0
            product_item = item["product_totals"].setdefault(
                product_key,
                {
                    "product": entry.product,
                    "tray_count": 0,
                    "weight_kg": Decimal("0.00"),
                },
            )
            product_item["tray_count"] += entry.tray_count
            product_item["weight_kg"] += Decimal(entry.tray_count) * Decimal("10.00")
        groups.append(
            {
                "position": position,
                "physical_entries": position.active_physical_entries,
                "entries": position.active_crew_entries,
                "physical_total": position_physical,
                "assigned_total": position_assigned,
                "pending_total": max(position_physical - position_assigned, 0),
                "capacity": position.max_trays,
                "is_physical_full": position_physical == position.max_trays,
                "is_assignment_complete": position_physical > 0 and position_assigned == position_physical,
                "packaging": packaging_by_position.get(
                    position.pk,
                    {
                        "physical_total": position_physical,
                        "packed_total": 0,
                        "pending_total": position_physical,
                        "possible_packages": position_physical // int(
                            production.template_version.rules.get("package_trays", 2)
                        ),
                        "balance_trays": position_physical % int(
                            production.template_version.rules.get("package_trays", 2)
                        ),
                        "registered_balance_total": 0,
                    },
                ),
                "timing": timing,
                "automatic_shift": (
                    ProductionOrder.Shift.from_datetime(timing.load_started_at)
                    if timing and timing.load_started_at
                    else None
                ),
                "automatic_shift_label": (
                    ProductionOrder.Shift(
                        ProductionOrder.Shift.from_datetime(timing.load_started_at)
                    ).label
                    if timing and timing.load_started_at
                    else None
                ),
                "filling_duration": _format_duration(
                    timing.load_started_at if timing else None,
                    timing.load_completed_at if timing else None,
                ),
                "process_duration": _format_duration(
                    timing.launched_at if timing else None,
                    timing.unloaded_at if timing else None,
                ),
            }
        )
    crew_totals = sorted(
        totals_by_crew.values(), key=lambda item: item["crew"].name.casefold()
    )
    for item in crew_totals:
        item["products"] = sorted(
            item.pop("product_totals").values(),
            key=lambda product_item: (
                product_item["product"].description.casefold()
                if product_item["product"] is not None
                else ""
            ),
        )
    return {
        "groups": groups,
        "crew_totals": crew_totals,
        "physical_total": physical_total,
        "assigned_total": assigned_total,
        "pending_total": max(physical_total - assigned_total, 0),
        "capacity_total": capacity_total,
    }


def _plate_packaging_trace_data(production):
    allocation_queryset = (
        PlatePackagingAllocation.objects.filter(
            production=production,
            is_active=True,
        )
        .select_related("responsible", "source_entry__product", "source_entry__position")
        .order_by("pallet_number", "pk")
    )
    source_entries = (
        PlateEntry.objects.filter(production=production, is_active=True)
        .select_related("position", "product", "responsible")
        .prefetch_related(
            Prefetch(
                "packaging_allocations",
                queryset=allocation_queryset,
                to_attr="active_packaging_allocations",
            ),
            Prefetch(
                "pallet_consumptions",
                queryset=PlatePalletConsumption.objects.filter(
                    line__production=production,
                    line__is_active=True,
                ).select_related("line__pallet", "line__responsible"),
                to_attr="active_pallet_consumptions",
            ),
        )
    )
    timings_by_position = {
        timing.position_id: timing
        for timing in PlatePositionTiming.objects.filter(
            production=production
        ).select_related("unloaded_by")
    }
    carryovers_by_source = {
        balance.source_entry_id: balance
        for balance in PlateCarryoverBalance.objects.filter(
            origin_production=production,
            is_active=True,
        ).exclude(status=PlateCarryoverBalance.Status.CANCELLED)
    }
    groups_by_position = {}
    package_trays = production.template_version.rules.get("package_trays", 2)
    package_kg = Decimal(
        str(production.template_version.rules.get("package_kg", 20))
    )
    total_physical = 0
    total_packed = 0
    total_pending = 0
    for source in source_entries:
        group = groups_by_position.setdefault(
            source.position_id,
            {
                "position": source.position,
                "timing": timings_by_position.get(source.position_id),
                "sources": [],
                "physical_total": 0,
                "packed_total": 0,
                "pending_total": 0,
                "registered_balance_total": 0,
            },
        )
        manual_package_count = sum(
            allocation.package_count
            for allocation in source.active_packaging_allocations
        )
        automatic_trays = sum(
            consumption.tray_count
            for consumption in source.active_pallet_consumptions
        )
        packed_trays = manual_package_count * package_trays + automatic_trays
        carryover = carryovers_by_source.get(source.pk)
        balance_trays = min(
            max(source.tray_count - packed_trays, 0),
            carryover.initial_trays,
        ) if carryover else 0
        pending_trays = max(source.tray_count - packed_trays - balance_trays, 0)
        source.packaged_package_count = Decimal(packed_trays) / Decimal(package_trays)
        source.packaged_tray_count = packed_trays
        source.packaged_kg = Decimal(packed_trays) * Decimal("10.00")
        source.pending_tray_count = pending_trays
        source.pending_kg = Decimal(pending_trays) * Decimal("10.00")
        source.calculated_package_count = pending_trays // package_trays
        source.calculated_package_tray_count = (
            source.calculated_package_count * package_trays
        )
        source.calculated_package_kg = (
            Decimal(source.calculated_package_count) * package_kg
        )
        source.calculated_balance_trays = pending_trays % package_trays
        source.calculated_balance_kg = (
            Decimal(source.calculated_balance_trays) * Decimal("10.00")
        )
        source.is_packaging_complete = pending_trays == 0
        source.has_ten_kg_balance = pending_trays == 1
        source.registered_balance_trays = balance_trays
        group["sources"].append(source)
        group["physical_total"] += source.tray_count
        group["packed_total"] += packed_trays
        group["pending_total"] += pending_trays
        group["registered_balance_total"] += source.registered_balance_trays
        total_physical += source.tray_count
        total_packed += packed_trays
        total_pending += pending_trays

    groups = []
    for group in groups_by_position.values():
        group["sources"].sort(
            key=lambda source: (
                0 if source.pending_tray_count > 0 else 1,
                -source.pending_tray_count,
                source.product.code.casefold(),
                source.product.description.casefold(),
            )
        )
        remaining_by_product = {}
        for source in group["sources"]:
            remaining_by_product[source.product_id] = (
                remaining_by_product.get(source.product_id, 0)
                + source.pending_tray_count
            )
        group["possible_packages"] = sum(
            trays // package_trays for trays in remaining_by_product.values()
        )
        group["balance_trays"] = sum(
            trays % package_trays for trays in remaining_by_product.values()
        )
        group["is_unloaded"] = bool(
            group["timing"] and group["timing"].unloaded_at
        )
        group["is_complete"] = (
            group["physical_total"] > 0 and group["pending_total"] == 0
        )
        group["has_only_ten_kg_balances"] = (
            group["pending_total"] > 0
            and all(
                source.pending_tray_count in {0, 1}
                for source in group["sources"]
            )
        )
        groups.append(group)
    groups.sort(
        key=lambda group: (
            0 if group["pending_total"] > 0 else 1,
            -group["pending_total"],
            _natural_plate_position_key(group["position"]),
        )
    )
    legacy_entries = list(
        PlatePackagingEntry.objects.filter(
            production=production,
            is_active=True,
        )
        .select_related("product", "responsible")
        .order_by("pallet_number", "product__code", "pk")
    )
    return {
        "groups": groups,
        "physical_total": total_physical,
        "packed_total": total_packed,
        "pending_total": total_pending,
        "legacy_entries": legacy_entries,
    }


def _plate_pack_product_totals(pallet_dashboard):
    totals = {}
    for pallet_data in pallet_dashboard:
        for product_row in pallet_data["products"]:
            product = product_row["product"]
            item = totals.setdefault(
                product.pk,
                {
                    "product": product,
                    "package_count": 0,
                    "tray_count": 0,
                    "kg": Decimal("0.00"),
                    "pallet_numbers": set(),
                },
            )
            item["package_count"] += int(product_row["package_count"] or 0)
            item["tray_count"] += int(product_row["tray_count"] or 0)
            item["kg"] += Decimal(product_row["kg"] or 0)
            item["pallet_numbers"].add(pallet_data["pallet_number"])
    rows = []
    for item in totals.values():
        item["pallet_count"] = len(item["pallet_numbers"])
        item["pallet_label"] = ", ".join(
            f"P{number}" for number in sorted(item["pallet_numbers"])
        )
        rows.append(item)
    rows.sort(key=lambda item: item["product"].description.casefold())
    return rows


def _tunnel_package_trays(production):
    return int(production.template_version.rules.get("package_trays", 2))


def _tunnel_package_kg(production):
    return Decimal(str(production.template_version.rules.get("package_kg", 20)))


def _tunnel_pallet_capacity(production):
    return int(
        production.template_version.rules.get(
            "tunnel_pallet_package_capacity",
            production.template_version.rules.get("plate_pallet_package_capacity", 56),
        )
    )


def _tunnel_product_availability(production):
    package_trays = _tunnel_package_trays(production)
    package_kg = _tunnel_package_kg(production)
    physical_rows = list(
        TunnelEntry.objects.filter(production=production, is_active=True)
        .values(
            "product",
            "rack__fill__tunnel__code",
            "rack__fill__fill_number",
            "rack__code",
        )
        .annotate(total=Sum("tray_count"))
        .order_by(
            "product",
            "rack__fill__tunnel__code",
            "rack__fill__fill_number",
            "rack__code",
        )
    )
    physical_by_product = {
        product_id: sum(int(row["total"] or 0) for row in rows)
        for product_id, rows in itertools.groupby(
            physical_rows,
            key=lambda row: row["product"],
        )
    }
    if not physical_by_product:
        return []
    packed_by_product = {
        row["product"]: int(row["total"] or 0)
        for row in (
            TunnelPackagingEntry.objects.filter(production=production, is_active=True)
            .values("product")
            .annotate(total=Sum("package_count"))
        )
    }
    products = {
        product.pk: product
        for product in Product.objects.filter(pk__in=physical_by_product)
    }
    rows = []
    for product_id, physical_trays in physical_by_product.items():
        product = products.get(product_id)
        if product is None:
            continue
        packed_packages = packed_by_product.get(product_id, 0)
        packed_trays = packed_packages * package_trays
        pending_trays = max(physical_trays - packed_trays, 0)
        possible_packages = pending_trays // package_trays
        consumed_trays = packed_trays
        source_rows = []
        for row in [item for item in physical_rows if item["product"] == product_id]:
            row_trays = int(row["total"] or 0)
            if consumed_trays >= row_trays:
                consumed_trays -= row_trays
                continue
            pending_from_source = row_trays - consumed_trays
            consumed_trays = 0
            if pending_from_source <= 0:
                continue
            source_rows.append(
                {
                    "tunnel": row["rack__fill__tunnel__code"],
                    "fill_number": row["rack__fill__fill_number"],
                    "rack": row["rack__code"],
                    "pending_trays": pending_from_source,
                    "possible_packages": pending_from_source // package_trays,
                    "balance_trays": pending_from_source % package_trays,
                }
            )
        rows.append(
            {
                "product": product,
                "physical_trays": physical_trays,
                "packed_packages": packed_packages,
                "packed_trays": packed_trays,
                "pending_trays": pending_trays,
                "possible_packages": possible_packages,
                "possible_kg": Decimal(possible_packages) * package_kg,
                "balance_trays": pending_trays % package_trays,
                "sources": source_rows,
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            item["product"].code.casefold(),
            item["product"].description.casefold(),
        ),
    )


def _tunnel_pallet_dashboard(production):
    capacity = _tunnel_pallet_capacity(production)
    package_trays = _tunnel_package_trays(production)
    package_kg = _tunnel_package_kg(production)
    products_by_pallet = defaultdict(list)
    for entry in (
        TunnelPackagingEntry.objects.filter(production=production, is_active=True)
        .select_related("product", "responsible")
        .order_by("pallet_number", "product__code", "pk")
    ):
        products_by_pallet[entry.pallet_number].append(entry)
    result = []
    for pallet_number, entries in sorted(products_by_pallet.items()):
        package_count = sum(entry.package_count for entry in entries)
        result.append(
            {
                "pallet_number": pallet_number,
                "entries": entries,
                "package_count": package_count,
                "tray_count": package_count * package_trays,
                "kg": Decimal(package_count) * package_kg,
                "capacity": capacity,
                "available_packages": max(capacity - package_count, 0),
                "is_full": package_count >= capacity,
            }
        )
    return result


def _next_tunnel_pallet_number(production):
    capacity = _tunnel_pallet_capacity(production)
    maximum_pallet = int(production.template_version.rules.get("tunnel_pallet_max", 50) or 50)
    used_packages = {
        row["pallet_number"]: int(row["total"] or 0)
        for row in (
            TunnelPackagingEntry.objects.filter(production=production, is_active=True)
            .values("pallet_number")
            .annotate(total=Sum("package_count"))
        )
    }
    for pallet_number in range(1, maximum_pallet + 1):
        if used_packages.get(pallet_number, 0) < capacity:
            return pallet_number
    return maximum_pallet


def _tunnel_packaging_data(production):
    availability = _tunnel_product_availability(production)
    pallets = _tunnel_pallet_dashboard(production)
    return {
        "availability": availability,
        "pallets": pallets,
        "physical_total": sum(item["physical_trays"] for item in availability),
        "packed_total": sum(item["packed_trays"] for item in availability),
        "pending_total": sum(item["pending_trays"] for item in availability),
        "pallet_capacity": _tunnel_pallet_capacity(production),
        "pallet_max": production.template_version.rules.get("tunnel_pallet_max", 50),
    }


def _reception_record_groups(production):
    """Agrupa visualmente la recepcion como se mapea en la hoja R.M: por vehiculo."""
    queryset = (
        ReceptionEntry.objects.filter(production=production, is_active=True)
        .select_related("vehicle", "product", "crew", "responsible")
        .order_by("created_at", "pk")
    )
    timings_by_vehicle = {
        timing.vehicle_id: timing
        for timing in ReceptionCarTiming.objects.filter(production=production)
        .select_related("vehicle", "product")
        .prefetch_related("crews")
    }
    reception_crews = list(
        Crew.objects.filter(active=True, code__startswith="RM-CUAD-").order_by("code")
    )
    groups_by_vehicle = {
        timing.vehicle_id: {
            "vehicle": timing.vehicle,
            "car_numbers": [timing.car_number] if timing.car_number else [],
            "entries": [],
            "total_weight": Decimal("0.00"),
            "product": timing.product,
            "configured_crew_ids": {crew.pk for crew in timing.crews.all()},
        }
        for timing in timings_by_vehicle.values()
    }
    for entry in queryset:
        group = groups_by_vehicle.setdefault(
            entry.vehicle_id,
            {
                "vehicle": entry.vehicle,
                "car_numbers": [],
                "entries": [],
                "total_weight": Decimal("0.00"),
                "product": entry.product,
                "configured_crew_ids": set(),
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
        timing = timings_by_vehicle.get(group["vehicle"].pk)
        started_at = timing.started_at if timing else group["entries"][0].created_at
        group["timing"] = timing
        group["started_at"] = started_at
        group["closed_at"] = timing.closed_at if timing else None
        group["duration_text"] = _format_duration(started_at, timing.closed_at) if timing and timing.closed_at else None
        crew_totals = {}
        for entry in group["entries"]:
            crew_key = entry.crew_id or 0
            crew_total = crew_totals.setdefault(
                crew_key,
                {
                    "crew_id": entry.crew_id,
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
        used_crew_ids = {crew_id for crew_id in crew_totals if crew_id}
        crew_cards = []
        configured_crew_ids = group.get("configured_crew_ids", set())
        visible_crews = [
            crew for crew in reception_crews
            if not configured_crew_ids or crew.pk in configured_crew_ids
        ]
        for crew in visible_crews:
            if crew.pk not in used_crew_ids and len(used_crew_ids) >= 2:
                continue
            crew_cards.append(
                crew_totals.get(
                    crew.pk,
                    {
                        "crew_id": crew.pk,
                        "name": crew.name,
                        "dino_count": 0,
                        "total_weight": Decimal("0.00"),
                    },
                )
            )
        group["crew_cards"] = crew_cards
        slots = set()
        group["has_dino_conflict"] = False
        for entry in group["entries"]:
            slot = str(entry.container).strip()
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
                "is_manager": (
                    self.request.user.is_superuser
                    or self.request.user.roles.filter(
                        code__in=[Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER]
                    ).exists()
                ),
                "user_areas": set(
                    self.production.assignments.filter(
                        user=self.request.user,
                        shift=self.production.shift,
                        active=True,
                    ).values_list("area", flat=True)
                ),
                "areas": AreaAssignment.Area,
                "editing": bool(getattr(self, "object", None)),
                "operational_read_only": getattr(self, "operational_read_only", False),
            }
        )
        if self.module_key == "tunnel-crews" and getattr(self, "object", None) is not None:
            context["module_create_url"] = (
                f"{reverse('productions:tunnel_crew_create', args=[self.production.pk])}"
                f"?fill={self.object.fill_id}"
            )
            context["crew_suggestions"] = getattr(context.get("form"), "crew_suggestions", [])
            context["selected_fill"] = self.object.fill
        if self.module_key == "nuqueras":
            nuq_workers = Worker.objects.filter(
                active=True, internal_code__startswith="NUQ-W"
            ).order_by("full_name")
            context["nuquera_worker_suggestions"] = list(
                nuq_workers.values_list("pk", "full_name")
            )
            context["nuquera_crew_suggestions"] = list(
                _nuquera_production_crews(self.production).values_list("pk", "name")
            )
        if self.module_key == "troquelado":
            troq_workers = Worker.objects.filter(
                active=True, internal_code__startswith="TROQ-W"
            ).order_by("full_name")
            context["troquelado_worker_suggestions"] = list(
                troq_workers.values_list("pk", "full_name")
            )
            context["troquelado_crew_suggestions"] = list(
                _troquelado_production_crews(self.production).values_list("pk", "name")
            )
            context["troquelado_dashboard"] = _troquelado_dashboard(self.production)
        if self.module_key in {"plates", "plate-crews"}:
            context["plate_crew_data"] = _plate_crew_position_groups(self.production)
        if self.module_key == "plate-pack":
            context["plate_packaging_data"] = _plate_packaging_trace_data(self.production)
            context["plate_pack_legacy_form_visible"] = bool(
                context.get("form")
                and context["form"].is_bound
                and context["form"].errors
            )
            product_availability = plate_product_availability(self.production)
            pallet_dashboard = plate_pallet_dashboard(self.production)
            context["plate_product_availability"] = product_availability
            context["plate_pallet_dashboard"] = pallet_dashboard
            context["plate_pack_product_totals"] = _plate_pack_product_totals(
                pallet_dashboard
            )
            context["plate_pallet_capacity"] = self.production.template_version.rules.get(
                "plate_pallet_package_capacity",
                56,
            )
            context["plate_pallet_max"] = self.production.template_version.rules.get(
                "plate_pallet_max",
                50,
            )
            context["plate_balance_product_options"] = Product.objects.filter(
                active=True
            ).order_by("description", "code")
            selected_product = self.request.GET.get("product")
            selected_product_id = (
                int(selected_product) if str(selected_product).isdigit() else None
            )
            available_product_ids = [
                item["product"].pk
                for item in product_availability
                if item["possible_packages"] > 0
            ]
            if selected_product_id not in available_product_ids:
                selected_product_id = (
                    available_product_ids[0] if available_product_ids else None
                )
            context["selected_plate_pack_product_id"] = selected_product_id

            selected_pallet = self.request.GET.get("pallet")
            selected_pallet_number = (
                int(selected_pallet) if str(selected_pallet).isdigit() else None
            )
            open_pallets = [
                item
                for item in pallet_dashboard
                if item["status"] != PlatePallet.Status.CLOSED
                and item["available_packages"] > 0
            ]
            open_pallet_numbers = {
                item["pallet_number"] for item in open_pallets
            }
            if selected_pallet_number not in open_pallet_numbers:
                selected_pallet_number = (
                    open_pallets[0]["pallet_number"] if open_pallets else None
                )
            if selected_pallet_number is None:
                used_pallet_numbers = {
                    item["pallet_number"] for item in pallet_dashboard
                }
                selected_pallet_number = next(
                    (
                        number
                        for number in range(1, context["plate_pallet_max"] + 1)
                        if number not in used_pallet_numbers
                    ),
                    1,
                )
            context["selected_plate_pallet_number"] = selected_pallet_number
            context["selected_plate_pallet"] = next(
                (
                    item
                    for item in pallet_dashboard
                    if item["pallet_number"] == selected_pallet_number
                ),
                None,
            )
        if self.module_key == "tunnel-pack":
            tunnel_packaging_data = _tunnel_packaging_data(self.production)
            context["tunnel_packaging_data"] = tunnel_packaging_data
            available_product_ids = [
                item["product"].pk
                for item in tunnel_packaging_data["availability"]
                if item["possible_packages"] > 0
            ]
            selected_product = self.request.GET.get("product")
            selected_product_id = (
                int(selected_product) if str(selected_product).isdigit() else None
            )
            if selected_product_id not in available_product_ids:
                selected_product_id = (
                    available_product_ids[0] if available_product_ids else None
                )
            context["selected_tunnel_pack_product_id"] = selected_product_id
            selected_pallet = self.request.GET.get("pallet")
            selected_pallet_number = (
                int(selected_pallet) if str(selected_pallet).isdigit() else None
            )
            pallet_capacity = int(tunnel_packaging_data["pallet_capacity"])
            pallets_by_number = {
                item["pallet_number"]: item for item in tunnel_packaging_data["pallets"]
            }
            selected_pallet = pallets_by_number.get(selected_pallet_number)
            if (
                selected_pallet_number is None
                or selected_pallet_number < 1
                or selected_pallet_number > int(tunnel_packaging_data["pallet_max"])
                or (
                    selected_pallet is not None
                    and selected_pallet["package_count"] >= pallet_capacity
                )
            ):
                selected_pallet_number = _next_tunnel_pallet_number(self.production)
            context["selected_tunnel_pallet_number"] = selected_pallet_number
        if self.module_key == "plate-crews":
            context["crew_suggestions"] = getattr(context.get("form"), "crew_suggestions", [])
            if getattr(self, "object", None) is not None:
                context["selected_plate_position"] = self.object.position
                context["module_create_url"] = (
                    f"{reverse('productions:plate_crew_create', args=[self.production.pk])}"
                    f"?position={self.object.position_id}"
                )
        if self.module_key == "plate-pack":
            selected_position = getattr(self, "_selected_plate_pack_position", None)
            if callable(selected_position):
                selected_position = selected_position()
            if getattr(self, "object", None) is not None:
                selected_position = self.object.source_entry.position
            context["selected_plate_pack_position"] = selected_position
            context["selected_plate_pack_group"] = next(
                (
                    group
                    for group in context["plate_packaging_data"]["groups"]
                    if selected_position is not None
                    and group["position"].pk == selected_position.pk
                ),
                None,
            )
            if selected_position is not None:
                context["module_create_url"] = (
                    f"{reverse('productions:plate_pack_create', args=[self.production.pk])}"
                    f"?position={selected_position.pk}"
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
                if isinstance(form.instance, ReceptionEntry):
                    timing, _ = ReceptionCarTiming.objects.select_for_update().get_or_create(
                        production=self.production,
                        vehicle=form.instance.vehicle,
                        defaults={
                            "started_at": timezone.now(),
                            "car_number": form.instance.car_number,
                            "product": form.instance.product,
                        },
                    )
                    timing_updates = []
                    if not timing.car_number:
                        timing.car_number = form.instance.car_number
                        timing_updates.append("car_number")
                    if not timing.product_id:
                        timing.product = form.instance.product
                        timing_updates.append("product")
                    if timing_updates:
                        timing.save(update_fields=timing_updates)
                    if timing.closed_at:
                        raise ValidationError(
                            "Este carro ya fue cerrado. Reábralo antes de agregar otro dino."
                        )
                    form.instance.time = timezone.localtime(timing.started_at).time().replace(
                        microsecond=0
                    )
                if isinstance(form.instance, TunnelCrewEntry):
                    _lock_tunnel_crew_racks(form.instance)
                if isinstance(form.instance, (PlateEntry, PlateCrewEntry)):
                    _lock_plate_positions(form.instance)
                if isinstance(form.instance, PlateEntry):
                    timing = (
                        PlatePositionTiming.objects.select_for_update()
                        .filter(
                            production=self.production,
                            position_id=form.instance.position_id,
                        )
                        .first()
                    )
                    if timing is None or not timing.load_started_at:
                        raise ValidationError(
                            "Primero inicie el llenado del plaquero y tome la hora; después registre sus productos."
                        )
                    if timing.load_completed_at:
                        raise ValidationError(
                            "La carga de este plaquero ya fue finalizada. No puede agregar más productos."
                        )
                if isinstance(form.instance, PlatePackagingAllocation):
                    form.instance.source_entry = (
                        PlateEntry.objects.select_for_update()
                        .select_related("position", "product")
                        .get(
                            pk=form.instance.source_entry_id,
                            production=self.production,
                            is_active=True,
                        )
                    )
                form.instance.full_clean()
                with suppress_automatic_audit():
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
        except (ValidationError, IntegrityError, PlateEntry.DoesNotExist) as exc:
            form.add_error(None, "; ".join(exc.messages) if hasattr(exc, "messages") else "Registro duplicado o incompatible.")
            return self.form_invalid(form)
        messages.success(self.request, "Registro guardado. Ahora puede corregirlo o eliminarlo desde esta misma pantalla.")
        config = _operational_config(self.module_key)
        if self.request.POST.get("volver"):
            return redirect("productions:detail", pk=self.production.pk)
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
        if self.model is TunnelCrewEntry:
            return self.model.objects.filter(
                fill__production=self.production,
                is_active=True,
            )
        return self.model.objects.filter(production=self.production, is_active=True)

    def form_valid(self, form):
        form.instance.production = self.production
        form.instance.responsible = self.request.user
        try:
            with transaction.atomic():
                previous = (
                    self.model.objects.select_for_update()
                    .select_related()
                    .get(pk=self.object.pk)
                )
                old_value = _operational_record_payload(previous)
                if isinstance(form.instance, TunnelCrewEntry):
                    _lock_tunnel_crew_racks(form.instance, previous=previous)
                if isinstance(form.instance, (PlateEntry, PlateCrewEntry)):
                    _lock_plate_positions(form.instance, previous=previous)
                if isinstance(form.instance, PlatePackagingAllocation):
                    form.instance.source_entry = (
                        PlateEntry.objects.select_for_update()
                        .select_related("position", "product")
                        .get(
                            pk=form.instance.source_entry_id,
                            production=self.production,
                            is_active=True,
                        )
                    )
                _validate_plate_physical_move(form.instance, previous)
                form.instance.full_clean()
                with suppress_automatic_audit():
                    self.object = form.save()
                if (
                    isinstance(self.object, PlateEntry)
                    and previous.position_id != self.object.position_id
                ):
                    _clear_empty_plate_timing(
                        production=self.production,
                        position_id=previous.position_id,
                        user=self.request.user,
                        request=self.request,
                        reason=(
                            "Control horario eliminado porque el último producto "
                            "fue movido a otro plaquero."
                        ),
                    )
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
        if isinstance(self.object, TunnelCrewEntry):
            return redirect(
                f"{reverse(config['create_url_name'], args=[self.production.pk])}"
                f"?fill={self.object.fill_id}#operational-entry-form"
            )
        if isinstance(self.object, PlateCrewEntry):
            return redirect(
                f"{reverse(config['create_url_name'], args=[self.production.pk])}"
                f"?position={self.object.position_id}#operational-entry-form"
            )
        if isinstance(self.object, PlatePackagingAllocation):
            return redirect(
                f"{reverse(config['create_url_name'], args=[self.production.pk])}"
                f"?position={self.object.source_entry.position_id}"
                f"&source={self.object.source_entry_id}#operational-entry-form"
            )
        return redirect(config["create_url_name"], pk=self.production.pk)


class OperationalEntryDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk, module, entry_pk):
        config = _operational_config(module)
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {ProductionOrder.Status.APPROVED, ProductionOrder.Status.CLOSED, ProductionOrder.Status.VOID}:
            raise PermissionDenied("La producción no admite correcciones en su estado actual.")
        with transaction.atomic():
            ownership_filter = (
                {"fill__production": production}
                if config["model"] is TunnelCrewEntry
                else {"production": production}
            )
            entry = get_object_or_404(
                config["model"].objects.select_for_update().select_related(),
                pk=entry_pk,
                is_active=True,
                **ownership_filter,
            )
            if isinstance(entry, TunnelCrewEntry):
                try:
                    require_area_assignment(
                        request.user,
                        production,
                        AreaAssignment.Area.TUNNEL_CREW,
                        tunnel=entry.fill.tunnel,
                    )
                except PermissionDenied:
                    require_area_assignment(
                        request.user,
                        production,
                        AreaAssignment.Area.TUNNEL,
                        tunnel=entry.fill.tunnel,
                    )
            else:
                require_area_assignment(request.user, production, config["area"])
            old_value = _operational_record_payload(entry)
            tunnel_fill_id = entry.fill_id if isinstance(entry, TunnelCrewEntry) else None
            plate_position_id = (
                entry.position_id
                if isinstance(entry, (PlateEntry, PlateCrewEntry))
                else None
            )
            if isinstance(entry, PlatePackagingAllocation):
                plate_position_id = entry.source_entry.position_id
            if isinstance(entry, PlateEntry):
                _lock_plate_positions(entry)
                packed_packages = (
                    PlatePackagingAllocation.objects.filter(
                        source_entry=entry,
                        is_active=True,
                    ).aggregate(total=Sum("package_count"))["total"]
                    or 0
                )
                if packed_packages:
                    messages.error(
                        request,
                        (
                            f"No se eliminó el código porque ya tiene {packed_packages} "
                            "bultos registrados en empaque. Corrija o elimine primero "
                            "esos movimientos."
                        ),
                    )
                    return redirect(config["create_url_name"], pk=production.pk)
                remaining_physical = (
                    PlateEntry.objects.filter(
                        production=production,
                        position=entry.position,
                        is_active=True,
                    )
                    .exclude(pk=entry.pk)
                    .aggregate(total=Sum("tray_count"))["total"]
                    or 0
                )
                assigned_total = (
                    PlateCrewEntry.objects.filter(
                        production=production,
                        position=entry.position,
                        is_active=True,
                    ).aggregate(total=Sum("tray_count"))["total"]
                    or 0
                )
                if assigned_total > remaining_physical:
                    messages.error(
                        request,
                        (
                            f"No se eliminó el registro porque {assigned_total} bandejas ya están "
                            "asignadas a cuadrillas. Corrija o elimine primero esas asignaciones."
                        ),
                    )
                    return redirect(config["create_url_name"], pk=production.pk)
            with suppress_automatic_audit():
                entry.delete(user=request.user, reason=f"Corrección en {config['title']}")
            timing_cleared = False
            if isinstance(entry, PlateEntry):
                timing_cleared = _clear_empty_plate_timing(
                    production=production,
                    position_id=entry.position_id,
                    user=request.user,
                    request=request,
                    reason=(
                        "Control horario eliminado automáticamente porque el "
                        "plaquero quedó sin productos ni bandejas."
                    ),
                )
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
        success_message = f"Se eliminó: {old_value['title']}."
        if timing_cleared:
            success_message += " También se limpió el control horario del plaquero vacío."
        messages.success(request, success_message)
        next_url = request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return redirect(next_url)
        if tunnel_fill_id is not None:
            return redirect(
                f"{reverse(config['create_url_name'], args=[production.pk])}"
                f"?fill={tunnel_fill_id}#operational-entry-form"
            )
        if plate_position_id is not None:
            return redirect(
                f"{reverse(config['create_url_name'], args=[production.pk])}"
                f"?position={plate_position_id}#operational-entry-form"
            )
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
                timing = (
                    ReceptionCarTiming.objects.filter(
                        production=self.production,
                        vehicle_id=int(vehicle_id),
                    )
                    .select_related("vehicle", "product")
                    .first()
                )
                if timing is not None:
                    self._selected_car_entry_cache = timing
                    return self._selected_car_entry_cache
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

    def _selected_car_crew(self):
        """Return only a crew that is already assigned to the chosen car.

        A crew id from the URL is deliberately not trusted on its own: it must
        belong to this car in this PP.  That makes the quick cards fast without
        letting a user accidentally add a third crew to the car.
        """
        if not hasattr(self, "_selected_car_crew_cache"):
            selected = self._selected_car_entry()
            crew_id = self.request.GET.get("crew", "")
            crew = None
            if selected is not None and crew_id.isdigit():
                configured_crew_ids = set()
                if isinstance(selected, ReceptionCarTiming):
                    configured_crew_ids = set(selected.crews.values_list("pk", flat=True))
                used_crew_ids = set(
                    ReceptionEntry.objects.filter(
                        production=self.production,
                        vehicle_id=selected.vehicle_id,
                        is_active=True,
                    )
                    .exclude(crew_id=None)
                    .values_list("crew_id", flat=True)
                )
                requested_crew_id = int(crew_id)
                if (
                    (not configured_crew_ids or requested_crew_id in configured_crew_ids)
                    and (requested_crew_id in used_crew_ids or len(used_crew_ids) < 2)
                ):
                    crew = Crew.objects.filter(
                        pk=requested_crew_id,
                        active=True,
                        code__startswith="RM-CUAD-",
                    ).first()
            self._selected_car_crew_cache = crew
        return self._selected_car_crew_cache

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
        crew = self._selected_car_crew()
        if crew is not None:
            initial["crew"] = crew.pk
        return initial

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if self._selected_car_entry() is not None:
            for field_name in ("vehicle_text", "car_number", "product"):
                form.fields[field_name].disabled = True
                form.fields[field_name].help_text = "Fijado al carro seleccionado para continuar llenando sus dinos."
        if self._selected_car_crew() is not None:
            form.fields["crew"].disabled = True
            form.fields["crew"].help_text = "Fijada a la cuadrilla seleccionada."
        elif not getattr(self, "object", None):
            # The first screen creates the car and starts its clock. Dino and
            # kilos are intentionally requested only after a crew is selected.
            for field_name in ("crew", "container", "weight_kg"):
                form.fields[field_name].required = False
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
        context["selected_reception_crew"] = self._selected_car_crew()
        context["reception_car_create_url"] = reverse(
            "productions:reception_car_create", args=[self.production.pk]
        )
        context["reception_car_crew_choices"] = Crew.objects.filter(
            active=True, code__startswith="RM-CUAD-"
        ).order_by("code")
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if getattr(self, "object", None) is not None and response.status_code == 302:
            return redirect(
                f"{reverse('productions:reception_create', args=[self.production.pk])}"
                f"?car={self.object.vehicle_id}#reception-entry-form"
            )
        return response


class ReceptionCarCreateView(LoginRequiredMixin, View):
    """Create an empty reception car; dinos are captured after crew selection."""

    def post(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {ProductionOrder.Status.APPROVED, ProductionOrder.Status.CLOSED, ProductionOrder.Status.VOID}:
            raise PermissionDenied("El parte no admite cambios en su estado actual.")
        require_area_assignment(request.user, production, AreaAssignment.Area.RECEPTION)

        vehicle_text = " ".join((request.POST.get("vehicle_text") or "").upper().split())
        car_number = (request.POST.get("car_number") or "").strip()
        product_id = request.POST.get("product") or ""
        crew_ids = request.POST.getlist("initial_crews")
        if not vehicle_text:
            messages.error(request, "Ingrese la placa o identificación del carro.")
            return redirect("productions:reception_create", pk=production.pk)
        if not car_number.isdigit() or not 1 <= int(car_number) <= 9:
            messages.error(request, "Ingrese un número de carro del 1 al 9.")
            return redirect("productions:reception_create", pk=production.pk)
        product = (
            Product.objects.filter(pk=int(product_id), active=True, code__startswith="RM-").first()
            if product_id.isdigit()
            else None
        )
        if product is None:
            messages.error(request, "Seleccione el producto del carro.")
            return redirect("productions:reception_create", pk=production.pk)
        selected_crews = list(
            Crew.objects.filter(
                pk__in=crew_ids, active=True, code__startswith="RM-CUAD-"
            ).order_by("code")
        )
        if not selected_crews or len(selected_crews) != len(set(crew_ids)) or len(selected_crews) > 2:
            messages.error(request, "Seleccione una o dos cuadrillas para el carro.")
            return redirect("productions:reception_create", pk=production.pk)

        with transaction.atomic():
            vehicle, _ = Vehicle.objects.get_or_create(
                plate=vehicle_text,
                defaults={"description": "Ingresado manualmente desde Recepción"},
            )
            conflicting_entry = ReceptionEntry.objects.filter(
                production=production, car_number=car_number, is_active=True
            ).exclude(vehicle=vehicle).select_related("vehicle").first()
            conflicting_timing = ReceptionCarTiming.objects.filter(
                production=production, car_number=car_number
            ).exclude(vehicle=vehicle).select_related("vehicle").first()
            conflicting = conflicting_entry or conflicting_timing
            if conflicting:
                messages.error(request, f"El carro {car_number} ya pertenece al vehículo {conflicting.vehicle.plate}.")
                return redirect("productions:reception_create", pk=production.pk)
            timing, created = ReceptionCarTiming.objects.select_for_update().get_or_create(
                production=production,
                vehicle=vehicle,
                defaults={
                    "car_number": str(int(car_number)),
                    "product": product,
                    "started_at": timezone.now(),
                },
            )
            if not created:
                messages.info(request, f"El carro {timing.car_number or car_number} ya estaba creado; continúe con su cuadrilla.")
            else:
                timing.crews.set(selected_crews)
                AuditLog.objects.create(
                    user=request.user,
                    production=production,
                    module="reception",
                    model_name=ReceptionCarTiming._meta.label,
                    record_pk=str(timing.pk),
                    action=AuditLog.Action.CREATE,
                    new_value={"vehicle": vehicle.plate, "car_number": timing.car_number, "product": product.description},
                    ip_address=request.META.get("REMOTE_ADDR"),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                )
        messages.success(request, f"Carro {timing.car_number} creado. La hora de recepción ya inició.")
        return redirect(
            f"{reverse('productions:reception_create', args=[production.pk])}"
            f"?car={vehicle.pk}#reception-entry-form"
        )


class ReceptionCarDeleteView(LoginRequiredMixin, View):
    """Remove an empty reception car created with the wrong data."""

    def get(self, request, pk, vehicle_pk):
        return redirect("productions:reception_create", pk=pk)

    def post(self, request, pk, vehicle_pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {ProductionOrder.Status.APPROVED, ProductionOrder.Status.CLOSED, ProductionOrder.Status.VOID}:
            raise PermissionDenied("El parte no admite cambios en su estado actual.")
        require_area_assignment(request.user, production, AreaAssignment.Area.RECEPTION)

        with transaction.atomic():
            timing = (
                ReceptionCarTiming.objects.select_for_update()
                .select_related("vehicle")
                .filter(production=production, vehicle_id=vehicle_pk)
                .first()
            )
            if timing is None:
                raise Http404
            if ReceptionEntry.objects.filter(
                production=production, vehicle_id=vehicle_pk, is_active=True
            ).exists():
                messages.error(
                    request,
                    "No se puede eliminar este carro porque ya tiene dinos registrados. Corrija o elimine los dinos primero.",
                )
                return redirect(
                    f"{reverse('productions:reception_create', args=[production.pk])}"
                    f"?car={vehicle_pk}#reception-entry-form"
                )
            car_label = timing.car_number or "sin número"
            vehicle_label = timing.vehicle.plate
            AuditLog.objects.create(
                user=request.user,
                production=production,
                module="reception",
                model_name=ReceptionCarTiming._meta.label,
                record_pk=str(timing.pk),
                action=AuditLog.Action.VOID,
                old_value={"vehicle": vehicle_label, "car_number": car_label},
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
            timing.delete()
        messages.success(request, f"Carro {car_label} eliminado.")
        return redirect("productions:reception_create", pk=production.pk)


class ReceptionCarCloseView(LoginRequiredMixin, View):
    """Finish the automatic clock of one reception car."""

    def get(self, request, pk, vehicle_pk):
        return redirect("productions:reception_create", pk=pk)

    def post(self, request, pk, vehicle_pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {ProductionOrder.Status.APPROVED, ProductionOrder.Status.CLOSED, ProductionOrder.Status.VOID}:
            raise PermissionDenied("El parte no admite cambios en su estado actual.")
        require_area_assignment(request.user, production, AreaAssignment.Area.RECEPTION)

        with transaction.atomic():
            timing = (
                ReceptionCarTiming.objects.select_for_update()
                .select_related("vehicle")
                .filter(production=production, vehicle_id=vehicle_pk)
                .first()
            )
            first_entry = (
                ReceptionEntry.objects.select_for_update()
                .filter(production=production, vehicle_id=vehicle_pk, is_active=True)
                .order_by("created_at", "pk")
                .first()
            )
            if timing is None and first_entry is None:
                raise Http404
            if timing is None:
                timing = ReceptionCarTiming.objects.create(
                    production=production,
                    vehicle=first_entry.vehicle,
                    car_number=first_entry.car_number,
                    product=first_entry.product,
                    started_at=first_entry.created_at,
                )
            vehicle_label = timing.vehicle.plate
            car_label = timing.car_number or (first_entry.car_number if first_entry else "")
            if timing.closed_at is None:
                timing.closed_at = timezone.now()
                timing.closed_by = request.user
                timing.save(update_fields=["closed_at", "closed_by"])
                AuditLog.objects.create(
                    user=request.user,
                    production=production,
                    module="reception",
                    model_name=ReceptionCarTiming._meta.label,
                    record_pk=str(timing.pk),
                    action=AuditLog.Action.TRANSITION,
                    old_value={"vehicle": vehicle_label, "status": "OPEN"},
                    new_value={"vehicle": vehicle_label, "status": "CLOSED"},
                    ip_address=request.META.get("REMOTE_ADDR"),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                )

        messages.success(request, f"Carro {car_label} cerrado. Se guardó su tiempo de recepción.")
        return redirect(
            f"{reverse('productions:reception_create', args=[production.pk])}"
            f"?car={vehicle_pk}#carro-{vehicle_pk}"
        )


def _nuquera_production_crews(production):
    """Cuadrillas relevantes a un parte de producción de nuqueras: las que ya
    tienen pesos en este parte más las creadas o usadas desde su captura."""
    entry_crews = NuqueraEntry.objects.filter(
        production=production,
        is_active=True,
    ).values_list("crew_id", flat=True)
    audited_codes = [
        value
        for value in AuditLog.objects.filter(
            production=production,
            module="nuquera_worker_catalog",
        ).values_list("new_value__crew__code", flat=True)
        if value
    ]
    return (
        Crew.objects.filter(active=True)
        .filter(Q(pk__in=entry_crews) | Q(code__in=audited_codes))
        .distinct()
        .order_by("name", "code")
    )


def _nuquera_crew_worker_queryset(production, crew):
    """Trabajadores de una cuadrilla de nuqueras: los que ya pesaron para ella
    en este parte más los asignados a ella en el catálogo."""
    entry_workers = NuqueraEntry.objects.filter(
        production=production,
        crew=crew,
        is_active=True,
    ).values_list("worker_id", flat=True)
    return (
        Worker.objects.filter(active=True, internal_code__startswith="NUQ-W")
        .filter(Q(pk__in=entry_workers) | Q(crew_id=crew.pk))
        .distinct()
        .order_by("full_name")
    )


def _troquelado_production_crews(production):
    """Cuadrillas relevantes a un parte de producción de troquelado: las que ya
    tienen pesos en este parte más las creadas o usadas desde su captura."""
    entry_crews = TroqueladoEntry.objects.filter(
        production=production,
        is_active=True,
    ).values_list("crew_id", flat=True)
    audited_codes = [
        value
        for value in AuditLog.objects.filter(
            production=production,
            module="troquelado_worker_catalog",
        ).values_list("new_value__crew__code", flat=True)
        if value
    ]
    return (
        Crew.objects.filter(active=True)
        .filter(Q(pk__in=entry_crews) | Q(code__in=audited_codes))
        .distinct()
        .order_by("name", "code")
    )


def _troquelado_crew_worker_queryset(production, crew):
    """Trabajadores de una cuadrilla de troquelado: los que ya pesaron para ella
    en este parte más los asignados a ella en el catálogo."""
    entry_workers = TroqueladoEntry.objects.filter(
        production=production,
        crew=crew,
        is_active=True,
    ).values_list("worker_id", flat=True)
    return (
        Worker.objects.filter(active=True, internal_code__startswith="TROQ-W")
        .filter(Q(pk__in=entry_workers) | Q(crew_id=crew.pk))
        .distinct()
        .order_by("full_name")
    )


def _troquelado_quick_workers(production, crew):
    """Filas para la captura rápida de una cuadrilla: por cada trabajador su
    nombre, el kg acumulado en este parte y los valores de su último registro
    (turno, categoría y horas) para precargar el panel sin recargar la página."""
    queryset = _troquelado_crew_worker_queryset(production, crew)
    entries = list(
        TroqueladoEntry.objects.filter(
            production=production,
            crew=crew,
            is_active=True,
        )
        .order_by("worker_id", "-created_at", "-pk")
    )
    kg_by_worker = {}
    defaults = {}
    for entry in entries:
        weight = Decimal(entry.weight_kg or 0)
        kg_by_worker[entry.worker_id] = kg_by_worker.get(entry.worker_id, Decimal("0")) + weight
        if entry.worker_id not in defaults:
            defaults[entry.worker_id] = {
                "shift": entry.shift,
                "product_type": entry.product_type or "",
                "start_time": entry.start_time.strftime("%H:%M") if entry.start_time else "06:00",
                "end_time": entry.end_time.strftime("%H:%M") if entry.end_time else "18:00",
            }
    workers = []
    for worker in queryset:
        kg = kg_by_worker.get(worker.pk, Decimal("0"))
        base = defaults.get(worker.pk, {})
        workers.append(
            {
                "pk": worker.pk,
                "name": worker.full_name,
                "initial": worker.full_name.strip()[:1].upper(),
                "kg_display": f"{kg:.2f}",
                "has_entries": worker.pk in kg_by_worker,
                "shift": base.get("shift") or production.shift,
                "product_type": base.get("product_type", ""),
                "start_time": base.get("start_time", "06:00"),
                "end_time": base.get("end_time", "18:00"),
            }
        )
    return workers


def _troquelado_quick_stats(production, worker, crew):
    """Totales del trabajador, su cuadrilla y el parte, para refrescar el
    resumen por cuadrilla tras guardar una entrada sin recargar la página."""
    worker_kg = Decimal("0")
    categories = {}
    for entry in TroqueladoEntry.objects.filter(
        production=production,
        worker=worker,
        is_active=True,
    ):
        label = (
            TroqueladoEntry.ProductType(entry.product_type).label
            if entry.product_type
            else "Sin categoría"
        )
        hours = _entry_hours(entry.start_time, entry.end_time)
        category = categories.setdefault(
            label, {"label": label, "kg": Decimal("0"), "hours": Decimal("0")}
        )
        weight = Decimal(entry.weight_kg or 0)
        category["kg"] += weight
        category["hours"] += hours
        worker_kg += weight
    crew_kg = Decimal(
        TroqueladoEntry.objects.filter(
            production=production,
            crew=crew,
            is_active=True,
        ).aggregate(total=Coalesce(Sum("weight_kg"), Decimal("0")))["total"]
        or 0
    )
    grand_totals = TroqueladoEntry.objects.filter(
        production=production,
        is_active=True,
    ).aggregate(
        record_count=Count("pk"),
        cajas_total=Coalesce(Sum("cajas"), 0),
        total=Coalesce(Sum("weight_kg"), Decimal("0")),
    )
    grand_total = Decimal(grand_totals["total"] or 0)
    max_kg = grand_total or Decimal("1")
    cat_list = sorted(
        (
            {
                "label": category["label"],
                "kg": category["kg"],
                "kg_per_hour": (
                    category["kg"] / category["hours"]
                    if category["hours"] > 0
                    else Decimal("0")
                ),
            }
            for category in categories.values()
        ),
        key=lambda item: item["kg"],
        reverse=True,
    )
    return {
        "worker_name": worker.full_name if worker else "",
        "crew_name": crew.name if crew else "Sin cuadrilla",
        "worker_kg_display": f"{worker_kg:.2f}",
        "percent": int((worker_kg / max_kg) * 100),
        "categories": [
            {
                "label": item["label"],
                "kg_display": f"{item['kg']:.2f}",
                "kg_per_hour_display": f"{item['kg_per_hour']:.2f}",
            }
            for item in cat_list
        ],
        "crew_kg_display": f"{crew_kg:.2f}",
        "crew_percent": int((crew_kg / max_kg) * 100),
        "record_count": grand_totals["record_count"],
        "cajas_total": grand_totals["cajas_total"],
        "grand_total_display": f"{grand_total:.2f}",
    }


def _next_troquelado_crew_code():
    counter = 1
    while Crew.objects.filter(code=f"TROQ-{counter:02d}").exists():
        counter += 1
    return f"TROQ-{counter:02d}"


def _next_troquelado_worker_code():
    counter = 1
    while Worker.objects.filter(internal_code=f"TROQ-W{counter}").exists():
        counter += 1
    return f"TROQ-W{counter}"


class NuqueraCreateView(OperationalCreateView):
    module_key = "nuqueras"
    form_class = NuqueraEntryForm
    area = AreaAssignment.Area.NUQUERAS
    form_title = "Registrar nuqueras o perfilado"

    def _crew_param(self):
        crew_id = self.request.GET.get("crew")
        if not crew_id:
            return None
        try:
            return Crew.objects.get(pk=crew_id, active=True)
        except (Crew.DoesNotExist, ValueError, TypeError):
            return None

    def _worker_param(self, crew):
        worker_id = self.request.GET.get("worker")
        if not worker_id:
            return None
        queryset = Worker.objects.filter(active=True, internal_code__startswith="NUQ-W")
        if crew is not None:
            queryset = _nuquera_crew_worker_queryset(self.production, crew)
        try:
            return queryset.get(pk=worker_id)
        except (Worker.DoesNotExist, ValueError, TypeError):
            return None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        crew = self._crew_param()
        if crew is not None:
            kwargs["crew_id"] = crew.pk
            kwargs["worker_queryset"] = _nuquera_crew_worker_queryset(self.production, crew)
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        crew = self._crew_param()
        if crew is not None:
            initial["crew"] = crew.pk
        worker = self._worker_param(crew)
        if worker is not None:
            initial["worker"] = worker.pk
            last_entry = (
                NuqueraEntry.objects.filter(
                    production=self.production,
                    worker=worker,
                    is_active=True,
                )
                .order_by("-created_at", "-pk")
                .first()
            )
            if last_entry is not None:
                initial["shift"] = last_entry.shift
                initial["process"] = last_entry.process
                initial["start_time"] = last_entry.start_time
                initial["end_time"] = last_entry.end_time
            else:
                initial["shift"] = self.production.shift
                initial["process"] = self.production.process
                initial["start_time"] = dt.time(6, 0)
                initial["end_time"] = dt.time(18, 0)
        elif crew is not None:
            initial["shift"] = self.production.shift
            initial["process"] = self.production.process
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        crew = self._crew_param()
        if (
            crew is not None
            and getattr(self, "object", None) is None
            and not getattr(self, "operational_read_only", False)
        ):
            context["nuquera_crew_mode"] = True
            context["nuquera_crew_id"] = crew.pk
            context["nuquera_crew_name"] = crew.name
            context["nuquera_crew_workers"] = list(
                _nuquera_crew_worker_queryset(self.production, crew).values_list("pk", "full_name")
            )
            context["nuquera_clear_url"] = reverse(
                "productions:nuquera_create", args=[self.production.pk]
            )
            worker = self._worker_param(crew)
            if worker is not None:
                context["nuquera_selected_worker_id"] = worker.pk
                context["nuquera_repeat_worker_name"] = worker.full_name
                context["nuquera_focused_worker"] = True
                context["nuquera_repeat_shift_label"] = dict(
                    ProductionOrder.Shift.choices
                ).get(self.get_initial().get("shift"), "")
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if getattr(self, "object", None) is not None and response.status_code == 302:
            return redirect(
                f"{reverse('productions:nuquera_create', args=[self.production.pk])}"
                f"?crew={self.object.crew_id}#operational-entry-form"
            )
        return response


class TunnelCrewCreateView(OperationalCreateView):
    module_key = "tunnel-crews"
    form_class = TunnelCrewEntryForm
    area = AreaAssignment.Area.TUNNEL_CREW
    form_title = "Registrar bandejas por cuadrilla de túnel"

    def dispatch(self, request, *args, **kwargs):
        production = get_object_or_404(ProductionOrder, pk=kwargs["pk"])
        fill_id = request.GET.get("fill") or request.POST.get("fill")
        fills = production.tunnel_fills.filter(is_active=True).select_related("tunnel").order_by("tunnel__code", "fill_number")
        if fill_id:
            self.selected_fill = get_object_or_404(fills, pk=fill_id)
        else:
            self.selected_fill = fills.first()
        if self.selected_fill is not None:
            ensure_tunnel_racks(self.selected_fill)
            if request.user.is_authenticated and can_view_production(request.user, production):
                require_area_assignment(
                    request.user,
                    production,
                    AreaAssignment.Area.TUNNEL_CREW,
                    tunnel=self.selected_fill.tunnel,
                )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["selected_fill"] = self.selected_fill
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        if self.selected_fill is not None:
            initial["fill"] = self.selected_fill
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["selected_fill"] = self.selected_fill
        context["crew_suggestions"] = context["form"].crew_suggestions
        if self.selected_fill is not None:
            context["module_create_url"] = (
                f"{reverse('productions:tunnel_crew_create', args=[self.production.pk])}"
                f"?fill={self.selected_fill.pk}"
            )
            context["tunnel_crew_data"] = _tunnel_crew_rack_groups(self.selected_fill)
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if getattr(self, "object", None) is not None and response.status_code == 302:
            return redirect(
                f"{reverse('productions:tunnel_crew_create', args=[self.production.pk])}"
                f"?fill={self.object.fill_id}#operational-entry-form"
            )
        return response


def _new_crew_code(name):
    stem = slugify(name).upper() or "NUEVA"
    base = f"CUAD-{stem}"[:30]
    candidate = base
    counter = 2
    while Crew.objects.filter(code=candidate).exists():
        suffix = f"-{counter}"
        candidate = f"{base[:30 - len(suffix)]}{suffix}"
        counter += 1
    return candidate


def _next_nuquera_crew_code():
    counter = 1
    while Crew.objects.filter(code=f"NUQ-{counter:02d}").exists():
        counter += 1
    return f"NUQ-{counter:02d}"


def _next_nuquera_worker_code():
    counter = 1
    while Worker.objects.filter(internal_code=f"NUQ-W{counter}").exists():
        counter += 1
    return f"NUQ-W{counter}"


def _find_existing_worker_by_name(name, code_prefix=None):
    """Busca un trabajador por nombre normalizado. Con code_prefix (p. ej.
    "TROQ-W") el catálogo queda acotado a esa área; sin él busca en todo el
    sistema. Nunca crea duplicados dentro del mismo catálogo."""
    normalized = normalized_crew_name(name)
    queryset = Worker.objects.order_by("full_name", "pk")
    if code_prefix:
        queryset = queryset.filter(internal_code__startswith=code_prefix)
    for worker in queryset:
        if normalized_crew_name(worker.full_name) == normalized:
            return worker
    return None


def _find_existing_crew_by_name(name, code_prefix):
    """Cuadrilla del área (prefijo, p. ej. "TROQ-") por nombre normalizado.
    Incluye las inactivas para poder reactivarlas. El nombre puede repetirse
    en otra área, así que se busca solo dentro del prefijo indicado."""
    normalized = normalized_crew_name(name)
    queryset = Crew.objects.filter(code__startswith=code_prefix).order_by("name", "code")
    for crew in queryset:
        if normalized_crew_name(crew.name) == normalized:
            return crew
    return None


class TunnelCrewQuickCreateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        fill = get_object_or_404(
            TunnelFill.objects.select_related("tunnel"),
            pk=request.POST.get("fill"),
            production=production,
            is_active=True,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {
            ProductionOrder.Status.APPROVED,
            ProductionOrder.Status.CLOSED,
            ProductionOrder.Status.VOID,
        }:
            raise PermissionDenied("El PP no admite nuevas cuadrillas en su estado actual.")
        if fill.status not in {TunnelFill.Status.OPEN, TunnelFill.Status.REOPENED}:
            raise PermissionDenied("La llenada está cerrada. Reábrala antes de registrar cuadrillas.")
        require_area_assignment(
            request.user,
            production,
            AreaAssignment.Area.TUNNEL_CREW,
            tunnel=fill.tunnel,
        )
        name = " ".join((request.POST.get("name") or "").strip().upper().split())
        if not name:
            messages.error(request, "Escriba el nombre de la nueva cuadrilla.")
        elif len(name) > 100:
            messages.error(request, "El nombre de la cuadrilla es demasiado largo.")
        else:
            existing = find_existing_crew_by_name(name)
            if existing is not None and not existing.code.startswith("CUAD-"):
                messages.error(request, "Ese nombre ya está utilizado por una cuadrilla de otra área.")
            else:
                with transaction.atomic():
                    created = existing is None
                    crew = existing or Crew.objects.create(code=_new_crew_code(name), name=name)
                    if not crew.active:
                        crew.active = True
                        crew.save(update_fields=["active", "updated_at"])
                    AuditLog.objects.create(
                        user=request.user,
                        production=production,
                        module="tunnel_crew_catalog",
                        model_name=crew._meta.label,
                        record_pk=str(crew.pk),
                        action=AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
                        new_value={"code": crew.code, "name": crew.name, "active": crew.active},
                        reason="Cuadrilla creada desde la captura por túnel" if created else "Cuadrilla reactivada",
                        ip_address=request.META.get("REMOTE_ADDR"),
                        user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    )
                if created:
                    messages.success(request, f"La cuadrilla {crew.name} fue creada y ya está disponible.")
                else:
                    messages.warning(
                        request,
                        f"Esta cuadrilla ya existe como {crew.name}. No se creó un duplicado; use la existente.",
                    )
        next_url = request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return redirect(next_url)
        return redirect(
            f"{reverse('productions:tunnel_crew_create', args=[production.pk])}"
            f"?fill={fill.pk}#operational-entry-form"
        )


class NuqueraWorkerQuickCreateView(LoginRequiredMixin, View):
    """Crea (o reutiliza) un trabajador de nuqueras y su cuadrilla desde la
    pantalla de captura. Solo los deja disponibles en el listado; no los
    preselecciona en el formulario."""

    def post(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {
            ProductionOrder.Status.APPROVED,
            ProductionOrder.Status.CLOSED,
            ProductionOrder.Status.VOID,
        }:
            raise PermissionDenied("El PP no admite nuevos trabajadores en su estado actual.")
        require_area_assignment(request.user, production, AreaAssignment.Area.NUQUERAS)

        name = " ".join((request.POST.get("name") or "").strip().upper().split())
        crew_name = " ".join((request.POST.get("crew") or "").strip().upper().split())
        if not name:
            messages.error(request, "Escriba el nombre del trabajador.")
        elif not crew_name:
            messages.error(request, "Escriba la cuadrilla del trabajador.")
        elif len(name) > 180:
            messages.error(request, "El nombre del trabajador es demasiado largo.")
        elif len(crew_name) > 100:
            messages.error(request, "El nombre de la cuadrilla es demasiado largo.")
        else:
            with transaction.atomic():
                existing_crew = _find_existing_crew_by_name(crew_name, "NUQ-")
                worker = _find_existing_worker_by_name(name, "NUQ-W")
                created_worker = worker is None
                if worker is None:
                    worker = Worker.objects.create(
                        internal_code=_next_nuquera_worker_code(),
                        full_name=name,
                        position="Nuquera",
                        active=True,
                        crew=existing_crew,
                    )
                elif not worker.active:
                    worker.active = True
                    worker.save(update_fields=["active", "updated_at"])
                created_crew = existing_crew is None
                crew = existing_crew or Crew.objects.create(
                    code=_next_nuquera_crew_code(),
                    name=crew_name,
                )
                if not crew.active:
                    crew.active = True
                    crew.save(update_fields=["active", "updated_at"])
                if created_worker and worker.crew_id != crew.pk:
                    worker.crew = crew
                    worker.save(update_fields=["crew", "updated_at"])
                AuditLog.objects.create(
                    user=request.user,
                    production=production,
                    module="nuquera_worker_catalog",
                    model_name=worker._meta.label,
                    record_pk=str(worker.pk),
                    action=AuditLog.Action.CREATE if created_worker else AuditLog.Action.UPDATE,
                    new_value={
                        "worker": {"code": worker.internal_code, "name": worker.full_name},
                        "crew": {"code": crew.code, "name": crew.name},
                    },
                    reason=(
                        "Trabajador de nuqueras creado desde la captura"
                        if created_worker
                        else "Trabajador de nuqueras reactivado"
                    ),
                    ip_address=request.META.get("REMOTE_ADDR"),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                )
            if created_worker and created_crew:
                messages.success(
                    request,
                    f"{worker.full_name} y la cuadrilla {crew.name} fueron creados y ya están disponibles.",
                )
            elif created_worker:
                messages.success(
                    request,
                    f"{worker.full_name} fue creado y ya está disponible en la cuadrilla {crew.name}.",
                )
            else:
                worker_crew = worker.crew.name if worker.crew else "sin cuadrilla"
                messages.warning(
                    request,
                    f"{worker.full_name} ya existía en {worker_crew}; se usó el registro actual sin crear duplicados.",
                )
        return redirect(reverse("productions:nuquera_create", args=[production.pk]))


class PlateCreateView(OperationalCreateView):
    module_key = "plates"
    form_class = PlateEntryForm
    area = AreaAssignment.Area.PLATES
    form_title = "Registrar envasado en plaqueros"

    def get_initial(self):
        initial = super().get_initial()
        position_id = self.request.GET.get("position", "")
        if position_id.isdigit() and PlatePosition.objects.filter(
            pk=int(position_id),
            template_version=self.production.template_version,
            active=True,
        ).exists():
            initial["position"] = int(position_id)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        position_id = self.request.GET.get("position", "")
        context["plate_card_open_position_id"] = (
            int(position_id) if position_id.isdigit() else None
        )
        timings = PlatePositionTiming.objects.filter(production=self.production)
        context["plate_capture_started_positions"] = ",".join(
            str(position_id)
            for position_id in timings.filter(
                load_started_at__isnull=False,
            ).values_list("position_id", flat=True)
        )
        context["plate_capture_open_positions"] = ",".join(
            str(position_id)
            for position_id in timings.filter(
                load_started_at__isnull=False,
                load_completed_at__isnull=True,
            ).values_list("position_id", flat=True)
        )
        context["plate_capture_completed_positions"] = ",".join(
            str(position_id)
            for position_id in timings.filter(
                load_completed_at__isnull=False,
            ).values_list("position_id", flat=True)
        )
        started_timings = list(
            timings.filter(load_started_at__isnull=False).order_by("position_id")
        )
        context["plate_capture_position_shifts"] = ";".join(
            (
                f"{timing.position_id}="
                f"{ProductionOrder.Shift(ProductionOrder.Shift.from_datetime(timing.load_started_at)).label}"
            )
            for timing in started_timings
        )
        context["plate_capture_position_starts"] = ";".join(
            f"{timing.position_id}={timezone.localtime(timing.load_started_at):%H:%M:%S}"
            for timing in started_timings
        )
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if getattr(self, "object", None) is not None and response.status_code == 302:
            return redirect(
                f"{reverse('productions:plate_create', args=[self.production.pk])}"
                f"?position={self.object.position_id}#operational-entry-form"
            )
        return response


class PlateTimingActionView(LoginRequiredMixin, View):
    event = None

    def post(self, request, pk, position_pk=None):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {
            ProductionOrder.Status.APPROVED,
            ProductionOrder.Status.CLOSED,
            ProductionOrder.Status.VOID,
        }:
            raise PermissionDenied("El PP no admite registrar horas en su estado actual.")
        require_area_assignment(request.user, production, AreaAssignment.Area.PLATES)

        if position_pk is None:
            selected_position = request.POST.get("position", "")
            if not selected_position.isdigit():
                messages.error(
                    request,
                    "Seleccione el plaquero antes de iniciar el llenado.",
                )
                return redirect("productions:plate_create", pk=production.pk)
            position_pk = int(selected_position)

        with transaction.atomic():
            position = get_object_or_404(
                PlatePosition.objects.select_for_update(),
                pk=position_pk,
                template_version=production.template_version,
                active=True,
            )
            physical_total = (
                PlateEntry.objects.filter(
                    production=production,
                    position=position,
                    is_active=True,
                ).aggregate(total=Sum("tray_count"))["total"]
                or 0
            )
            if self.event == "load" and physical_total <= 0:
                messages.error(request, "Primero registre las bandejas cargadas en este plaquero.")
                return redirect(
                    f"{reverse('productions:plate_create', args=[production.pk])}"
                    f"?position={position.pk}#operational-entry-form"
                )

            timing = PlatePositionTiming.objects.select_for_update().filter(
                production=production,
                position=position,
            ).first()
            if self.event == "load" and (
                timing is None or not timing.load_started_at
            ):
                messages.error(request, "Primero pulse «Iniciar llenado» para este plaquero.")
                return redirect("productions:plate_create", pk=production.pk)
            if self.event == "launch" and (
                timing is None or not timing.load_completed_at
            ):
                messages.error(request, "Primero pulse «Finalizar carga» para este plaquero.")
                return redirect("productions:plate_create", pk=production.pk)
            if self.event == "unload" and (
                timing is None or not timing.launched_at
            ):
                messages.error(request, "Primero registre el lanzamiento de este plaquero.")
                return redirect("productions:plate_create", pk=production.pk)
            created = timing is None
            if timing is None:
                timing = PlatePositionTiming(
                    production=production,
                    position=position,
                )
            old_value = {
                "load_started_at": (
                    timing.load_started_at.isoformat() if timing.load_started_at else None
                ),
                "load_completed_at": (
                    timing.load_completed_at.isoformat() if timing.load_completed_at else None
                ),
                "launched_at": timing.launched_at.isoformat() if timing.launched_at else None,
                "unloaded_at": timing.unloaded_at.isoformat() if timing.unloaded_at else None,
            }
            recorded_at = timezone.now()
            if self.event == "start":
                if timing.load_started_at:
                    messages.info(request, "La hora de inicio de llenado ya estaba registrada.")
                    return redirect("productions:plate_create", pk=production.pk)
                if timing.load_completed_at:
                    messages.info(
                        request,
                        "Este registro anterior ya tiene fin de carga y no permite agregar un inicio posterior.",
                    )
                    return redirect("productions:plate_create", pk=production.pk)
                timing.load_started_at = recorded_at
                timing.load_started_by = request.user
                success_message = "Inicio de llenado"
            elif self.event == "load":
                if timing.load_completed_at:
                    messages.info(request, "La hora de fin de carga ya estaba registrada.")
                    return redirect("productions:plate_create", pk=production.pk)
                timing.load_completed_at = recorded_at
                timing.load_completed_by = request.user
                success_message = "Fin de carga"
            elif self.event == "launch":
                if timing.launched_at:
                    messages.info(request, "La hora de lanzamiento ya estaba registrada.")
                    return redirect("productions:plate_create", pk=production.pk)
                timing.launched_at = recorded_at
                timing.launched_by = request.user
                success_message = "Lanzamiento"
            elif self.event == "unload":
                if timing.unloaded_at:
                    messages.info(request, "La hora de descarga ya estaba registrada.")
                    return redirect("productions:plate_create", pk=production.pk)
                timing.unloaded_at = recorded_at
                timing.unloaded_by = request.user
                success_message = "Descarga"
            else:
                raise Http404("Acción de plaquero no disponible.")

            timing.full_clean()
            timing.save()
            local_time = timezone.localtime(recorded_at)
            AuditLog.objects.create(
                user=request.user,
                production=production,
                module="plate_timing",
                model_name=timing._meta.label,
                record_pk=str(timing.pk),
                action=AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
                old_value=old_value,
                new_value={
                    "position": position.operational_label,
                    "physical_total": physical_total,
                    "automatic_shift": (
                        ProductionOrder.Shift.from_datetime(timing.load_started_at)
                        if timing.load_started_at
                        else None
                    ),
                    "load_started_at": (
                        timing.load_started_at.isoformat()
                        if timing.load_started_at
                        else None
                    ),
                    "load_completed_at": (
                        timing.load_completed_at.isoformat()
                        if timing.load_completed_at
                        else None
                    ),
                    "launched_at": timing.launched_at.isoformat() if timing.launched_at else None,
                    "unloaded_at": timing.unloaded_at.isoformat() if timing.unloaded_at else None,
                },
                reason="Hora tomada automáticamente por la aplicación (America/Lima)",
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        automatic_shift_label = (
            ProductionOrder.Shift(
                ProductionOrder.Shift.from_datetime(timing.load_started_at)
            ).label
            if self.event == "start" and timing.load_started_at
            else None
        )
        messages.success(
            request,
            (
                f"{success_message} registrado automáticamente a las "
                f"{local_time:%H:%M:%S}"
                f"{f' · Turno {automatic_shift_label}' if automatic_shift_label else ''}."
            ),
        )
        anchor = "operational-entry-form" if self.event == "start" else f"plate-position-{position.pk}"
        return redirect(
            f"{reverse('productions:plate_create', args=[production.pk])}"
            f"?position={position.pk}#{anchor}"
        )


class PlateLoadStartView(PlateTimingActionView):
    event = "start"


class PlateLoadCompleteView(PlateTimingActionView):
    event = "load"


class PlateLaunchRegisterView(PlateTimingActionView):
    event = "launch"


class PlateUnloadRegisterView(PlateTimingActionView):
    event = "unload"


class PlateTimingResetView(LoginRequiredMixin, View):
    def post(self, request, pk, position_pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {
            ProductionOrder.Status.APPROVED,
            ProductionOrder.Status.CLOSED,
            ProductionOrder.Status.VOID,
        }:
            raise PermissionDenied("El PP no admite limpiar controles en su estado actual.")
        require_area_assignment(request.user, production, AreaAssignment.Area.PLATES)
        position = get_object_or_404(
            PlatePosition,
            pk=position_pk,
            template_version=production.template_version,
            active=True,
        )

        with transaction.atomic():
            cleared = _clear_empty_plate_timing(
                production=production,
                position_id=position.pk,
                user=request.user,
                request=request,
                reason="Control horario vacío eliminado manualmente desde la captura.",
            )

        if cleared:
            messages.success(
                request,
                f"Se eliminó el control vacío de {position.operational_label}.",
            )
        elif PlateEntry.objects.filter(
            production=production,
            position=position,
            is_active=True,
        ).exists() or PlateCrewEntry.objects.filter(
            production=production,
            position=position,
            is_active=True,
        ).exists():
            messages.error(
                request,
                "No se puede limpiar porque el plaquero todavía tiene bandejas o cuadrillas registradas.",
            )
        else:
            messages.info(request, "El control vacío ya no existe.")

        return redirect(
            f"{reverse('productions:plate_create', args=[production.pk])}"
            "#operational-entry-form"
        )


class PlateCrewCreateView(OperationalCreateView):
    module_key = "plate-crews"
    form_class = PlateCrewEntryForm
    area = AreaAssignment.Area.PLATE_CREW
    form_title = "Repartir bandejas por cuadrilla en plaqueros"

    def _selected_position(self):
        if not hasattr(self, "_selected_plate_position_cache"):
            position_id = self.request.GET.get("position") or self.request.POST.get("position")
            queryset = PlatePosition.objects.filter(
                template_version=self.production.template_version,
                active=True,
                entries__production=self.production,
                entries__is_active=True,
            ).distinct()
            selected_position = (
                queryset.filter(pk=position_id).first() if str(position_id).isdigit() else None
            )
            if selected_position is not None:
                physical_total = (
                    PlateEntry.objects.filter(
                        production=self.production,
                        position=selected_position,
                        is_active=True,
                    ).aggregate(total=Sum("tray_count"))["total"]
                    or 0
                )
                assigned_total = (
                    PlateCrewEntry.objects.filter(
                        production=self.production,
                        position=selected_position,
                        is_active=True,
                    ).aggregate(total=Sum("tray_count"))["total"]
                    or 0
                )
                if assigned_total >= physical_total:
                    selected_position = None
            self._selected_plate_position_cache = selected_position
        return self._selected_plate_position_cache

    def get_initial(self):
        initial = super().get_initial()
        selected = self._selected_position()
        if selected is not None:
            initial["position"] = selected
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["selected_plate_position"] = self._selected_position()
        context["crew_suggestions"] = context["form"].crew_suggestions
        if context["selected_plate_position"] is not None:
            context["module_create_url"] = (
                f"{reverse('productions:plate_crew_create', args=[self.production.pk])}"
                f"?position={context['selected_plate_position'].pk}"
            )
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if getattr(self, "object", None) is not None and response.status_code == 302:
            position_groups = _plate_crew_position_groups(self.production)["groups"]
            pending_group = next(
                (
                    group
                    for group in position_groups
                    if group["position"].pk == self.object.position_id
                    and group["pending_total"] > 0
                ),
                None,
            )
            if pending_group is None:
                pending_group = next(
                    (group for group in position_groups if group["pending_total"] > 0),
                    None,
                )
            position_query = (
                f"?position={pending_group['position'].pk}" if pending_group else ""
            )
            return redirect(
                f"{reverse('productions:plate_crew_create', args=[self.production.pk])}"
                f"{position_query}#operational-entry-form"
            )
        return response


class PlateCrewQuickCreateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {
            ProductionOrder.Status.APPROVED,
            ProductionOrder.Status.CLOSED,
            ProductionOrder.Status.VOID,
        }:
            raise PermissionDenied("El PP no admite nuevas cuadrillas en su estado actual.")
        require_area_assignment(request.user, production, AreaAssignment.Area.PLATE_CREW)
        position_id = request.POST.get("position", "")
        selected_position = (
            PlatePosition.objects.filter(
                pk=position_id,
                template_version=production.template_version,
                active=True,
                entries__production=production,
                entries__is_active=True,
            )
            .distinct()
            .first()
            if str(position_id).isdigit()
            else None
        )
        name = " ".join((request.POST.get("name") or "").strip().upper().split())
        if not name:
            messages.error(request, "Escriba el nombre de la nueva cuadrilla.")
        elif len(name) > 100:
            messages.error(request, "El nombre de la cuadrilla es demasiado largo.")
        else:
            existing = find_existing_crew_by_name(name)
            if existing is not None and not existing.code.startswith("CUAD-"):
                messages.error(request, "Ese nombre ya está utilizado por una cuadrilla de otra área.")
            else:
                with transaction.atomic():
                    created = existing is None
                    crew = existing or Crew.objects.create(code=_new_crew_code(name), name=name)
                    if not crew.active:
                        crew.active = True
                        crew.save(update_fields=["active", "updated_at"])
                    AuditLog.objects.create(
                        user=request.user,
                        production=production,
                        module="plate_crew_catalog",
                        model_name=crew._meta.label,
                        record_pk=str(crew.pk),
                        action=AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
                        new_value={"code": crew.code, "name": crew.name, "active": crew.active},
                        reason=(
                            "Cuadrilla creada desde el reparto de plaqueros"
                            if created
                            else "Cuadrilla reactivada"
                        ),
                        ip_address=request.META.get("REMOTE_ADDR"),
                        user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    )
                if created:
                    messages.success(request, f"La cuadrilla {crew.name} fue creada y ya está disponible.")
                else:
                    messages.warning(
                        request,
                        f"Esta cuadrilla ya existe como {crew.name}. No se creó un duplicado; use la existente.",
                    )
        url = reverse("productions:plate_crew_create", args=[production.pk])
        if selected_position is not None:
            url = f"{url}?position={selected_position.pk}"
        return redirect(f"{url}#operational-entry-form")


class TunnelPackagingCreateView(OperationalCreateView):
    module_key = "tunnel-pack"
    form_class = TunnelPackagingEntryForm
    area = AreaAssignment.Area.TUNNEL_PACK
    form_title = "Registrar empaque de túneles"


class TunnelAutoPackagingView(LoginRequiredMixin, View):
    def post(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status not in PRODUCTION_EDITABLE_STATUSES:
            raise PermissionDenied("El PP no admite nuevos empaques en su estado actual.")
        require_area_assignment(
            request.user,
            production,
            AreaAssignment.Area.TUNNEL_PACK,
        )
        product_id = request.POST.get("product")
        if not str(product_id).isdigit():
            messages.error(request, "Seleccione producto para empacar túneles.")
            return redirect("productions:tunnel_pack_create", pk=production.pk)
        pallet_number = _next_tunnel_pallet_number(production)
        maximum_pallet = production.template_version.rules.get("tunnel_pallet_max", 50)
        if pallet_number < 1 or (maximum_pallet and pallet_number > maximum_pallet):
            messages.error(request, f"No hay pallets disponibles entre P1 y P{maximum_pallet}.")
            return redirect("productions:tunnel_pack_create", pk=production.pk)

        try:
            with transaction.atomic():
                product = Product.objects.select_for_update().get(pk=int(product_id), active=True)
                availability = next(
                    (
                        item
                        for item in _tunnel_product_availability(production)
                        if item["product"].pk == product.pk
                    ),
                    None,
                )
                if availability is None or availability["possible_packages"] <= 0:
                    raise ValidationError(
                        f"{product.code} todavía no reúne bandejas pendientes para formar un bulto completo."
                    )
                capacity = _tunnel_pallet_capacity(production)
                used_packages = (
                    TunnelPackagingEntry.objects.select_for_update()
                    .filter(production=production, pallet_number=pallet_number, is_active=True)
                    .aggregate(total=Sum("package_count"))["total"]
                    or 0
                )
                available_capacity = max(capacity - int(used_packages), 0)
                if available_capacity <= 0:
                    raise ValidationError(f"El pallet P{pallet_number} ya alcanzó {capacity} bultos.")
                package_count = min(availability["possible_packages"], available_capacity)
                entry = (
                    TunnelPackagingEntry.objects.select_for_update()
                    .filter(
                        production=production,
                        pallet_number=pallet_number,
                        product=product,
                        is_active=True,
                    )
                    .first()
                )
                action = AuditLog.Action.UPDATE if entry else AuditLog.Action.CREATE
                old_value = _operational_record_payload(entry) if entry else None
                if entry is None:
                    entry = TunnelPackagingEntry(
                        production=production,
                        responsible=request.user,
                        date=production.packaging_date
                        or production.production_date
                        or production.reception_date,
                        pallet_number=pallet_number,
                        product=product,
                        package_count=package_count,
                        observation="Cálculo automático desde envasado en túneles",
                    )
                else:
                    entry.package_count += package_count
                    entry.observation = "Cálculo automático desde envasado en túneles"
                entry.full_clean()
                with suppress_automatic_audit():
                    entry.save()
                AuditLog.objects.create(
                    user=request.user,
                    production=production,
                    module="tunnel-pack-auto",
                    model_name=entry._meta.label,
                    record_pk=str(entry.pk),
                    action=action,
                    old_value=old_value,
                    new_value=_operational_record_payload(entry),
                    ip_address=request.META.get("REMOTE_ADDR"),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                )
        except (Product.DoesNotExist, ValidationError, IntegrityError) as exc:
            detail = (
                "; ".join(exc.messages)
                if hasattr(exc, "messages")
                else "No se pudo registrar el empaque automático de túneles."
            )
            messages.error(request, detail)
        else:
            messages.success(
                request,
                (
                    f"Se empacó {product.code} en P{pallet_number}: "
                    f"{package_count} bulto(s), {package_count * _tunnel_package_kg(production)} kg."
                ),
            )
        url = reverse("productions:tunnel_pack_create", args=[production.pk])
        if str(product_id).isdigit():
            url = f"{url}?product={product_id}&pallet={pallet_number}"
        return redirect(f"{url}#tunnel-auto-pack-form")


class PlatePackagingCreateView(OperationalCreateView):
    module_key = "plate-pack"
    form_class = PlatePackagingAllocationForm
    area = AreaAssignment.Area.PLATE_PACK
    form_title = "Registrar empaque de placas"

    def _selected_plate_pack_position(self):
        if not hasattr(self, "_selected_plate_pack_position_cache"):
            position_id = self.request.GET.get("position")
            self._selected_plate_pack_position_cache = (
                PlatePosition.objects.filter(
                    pk=position_id,
                    template_version=self.production.template_version,
                    active=True,
                    entries__production=self.production,
                    entries__is_active=True,
                )
                .distinct()
                .first()
                if str(position_id).isdigit()
                else None
            )
        return self._selected_plate_pack_position_cache

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["position"] = self._selected_plate_pack_position()
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        source_id = self.request.GET.get("source")
        selected_position = self._selected_plate_pack_position()
        if str(source_id).isdigit() and PlateEntry.objects.filter(
            pk=source_id,
            production=self.production,
            position=selected_position,
            is_active=True,
        ).exists():
            initial["source_entry"] = int(source_id)
        elif selected_position is not None:
            available_sources = list(
                PlateEntry.objects.filter(
                    production=self.production,
                    position=selected_position,
                    is_active=True,
                ).order_by("product__code", "pk")
            )
            package_trays = self.production.template_version.rules.get(
                "package_trays", 2
            )
            used_packages = dict(
                PlatePackagingAllocation.objects.filter(
                    production=self.production,
                    source_entry__in=available_sources,
                    is_active=True,
                )
                .values_list("source_entry_id")
                .annotate(total=Sum("package_count"))
            )
            initial_source = next(
                (
                    source
                    for source in available_sources
                    if source.tray_count
                    - used_packages.get(source.pk, 0) * package_trays
                    >= package_trays
                ),
                None,
            )
            if initial_source is not None:
                initial["source_entry"] = initial_source
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        if getattr(self, "object", None) is not None and response.status_code == 302:
            return redirect(
                f"{reverse('productions:plate_pack_create', args=[self.production.pk])}"
                f"?position={self.object.source_entry.position_id}"
                f"&source={self.object.source_entry_id}#operational-entry-form"
            )
        return response


class PlateAutoPackagingView(LoginRequiredMixin, View):
    def post(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status not in PRODUCTION_EDITABLE_STATUSES:
            raise PermissionDenied(
                "El PP no admite nuevos empaques en su estado actual."
            )
        require_area_assignment(
            request.user,
            production,
            AreaAssignment.Area.PLATE_PACK,
        )
        product_id = request.POST.get("product")
        pallet_number = request.POST.get("pallet_number")
        try:
            result = auto_pack_product(
                production=production,
                product_id=int(product_id),
                pallet_number=int(pallet_number),
                user=request.user,
            )
        except (TypeError, ValueError):
            messages.error(
                request,
                "Seleccione el producto e ingrese un número de pallet válido.",
            )
        except (ValidationError, Product.DoesNotExist) as exc:
            detail = (
                "; ".join(exc.messages)
                if hasattr(exc, "messages")
                else "El producto seleccionado no está disponible."
            )
            messages.error(request, detail)
        else:
            line = result["line"]
            AuditLog.objects.create(
                user=request.user,
                production=production,
                module="plate-pack-auto",
                model_name=line._meta.label,
                record_pk=str(line.pk),
                action=AuditLog.Action.CREATE,
                new_value={
                    "product": result["product"].code,
                    "pallet": result["pallet"].pallet_number,
                    "packages": result["package_count"],
                    "trays": result["tray_count"],
                    "carryover_trays": result["carryover_used"],
                    "current_trays": result["current_used"],
                },
                reason="Cálculo automático de bultos y consumo de saldos compatibles",
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
            messages.success(
                request,
                (
                    f"P{result['pallet'].pallet_number}: se formaron "
                    f"{result['package_count']} bultos de {result['product'].code} "
                    f"({result['tray_count']} bandejas · {result['kg']:.2f} kg). "
                    f"El pallet tiene {result['pallet_total']} de "
                    f"{result['pallet_capacity']} bultos."
                ),
            )
            if result["carryover_used"]:
                messages.info(
                    request,
                    (
                        f"Se utilizaron {result['carryover_used']} bandeja(s) de "
                        "saldo anterior compatible."
                    ),
                )
        url = reverse("productions:plate_pack_create", args=[production.pk])
        if str(product_id).isdigit():
            url = f"{url}?product={product_id}"
            if "result" not in locals() and str(pallet_number).isdigit():
                url = f"{url}&pallet={pallet_number}"
        return redirect(f"{url}#auto-pack-form")


class PlateManualBalanceCreateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status not in PRODUCTION_EDITABLE_STATUSES:
            raise PermissionDenied("El PP no admite nuevos saldos en su estado actual.")
        require_area_assignment(
            request.user,
            production,
            AreaAssignment.Area.PLATE_PACK,
        )
        source_id = request.POST.get("source_entry")
        tray_count = request.POST.get("tray_count")
        observation = request.POST.get("observation", "")
        try:
            balance = register_manual_plate_balance(
                production=production,
                source_entry_id=int(source_id),
                tray_count=tray_count,
                observation=observation,
                user=request.user,
            )
        except (TypeError, ValueError, PlateEntry.DoesNotExist):
            messages.error(request, "Seleccione un código válido para registrar saldo.")
            return redirect("productions:plate_pack_create", pk=production.pk)
        except ValidationError as exc:
            messages.error(
                request,
                "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc),
            )
            position_id = request.POST.get("position")
            url = reverse("productions:plate_pack_create", args=[production.pk])
            if str(position_id).isdigit():
                url = f"{url}?position={position_id}"
            return redirect(f"{url}#plate-source-{source_id}")

        AuditLog.objects.create(
            user=request.user,
            production=production,
            module="plate-pack-balance",
            model_name=balance._meta.label,
            record_pk=str(balance.pk),
            action=AuditLog.Action.CREATE,
            new_value={
                "source_entry": balance.source_entry_id,
                "product": balance.product.code,
                "trays": balance.initial_trays,
                "available_trays": balance.available_trays,
                "observation": balance.observation,
            },
            reason="Saldo manual registrado desde empaque de placas",
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        messages.success(
            request,
            (
                f"Saldo guardado: {balance.product.code} · "
                f"{balance.available_trays} bandeja(s)."
            ),
        )
        return redirect(
            f"{reverse('productions:plate_pack_create', args=[production.pk])}"
            f"?position={balance.source_entry.position_id}#plate-source-{balance.source_entry_id}"
        )


class PlateInitialBalanceCreateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status not in PRODUCTION_EDITABLE_STATUSES:
            raise PermissionDenied("El PP no admite nuevos saldos en su estado actual.")
        require_area_assignment(
            request.user,
            production,
            AreaAssignment.Area.PLATE_PACK,
        )
        try:
            balance = register_initial_plate_balance(
                production=production,
                product_id=int(request.POST.get("product")),
                tray_count=request.POST.get("tray_count"),
                observation=request.POST.get("observation", ""),
                user=request.user,
            )
        except (TypeError, ValueError, Product.DoesNotExist):
            messages.error(request, "Seleccione producto e ingrese bandejas válidas para el saldo inicial.")
        except ValidationError as exc:
            messages.error(
                request,
                "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc),
            )
        else:
            AuditLog.objects.create(
                user=request.user,
                production=production,
                module="plate-pack-initial-balance",
                model_name=balance._meta.label,
                record_pk=str(balance.pk),
                action=AuditLog.Action.CREATE,
                new_value={
                    "product": balance.product.code,
                    "trays": balance.initial_trays,
                    "available_trays": balance.available_trays,
                    "observation": balance.observation,
                },
                reason="Saldo inicial manual cargado sin historial anterior",
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
            messages.success(
                request,
                f"Saldo inicial guardado: {balance.product.code} · {balance.available_trays} bandeja(s).",
            )
        return redirect(f"{reverse('productions:plate_pack_create', args=[production.pk])}#initial-balance-form")


class PlatePalletStatusView(LoginRequiredMixin, View):
    def post(self, request, pk, pallet_pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status not in PRODUCTION_EDITABLE_STATUSES:
            raise PermissionDenied("El PP no admite cambios en sus pallets.")
        require_area_assignment(
            request.user,
            production,
            AreaAssignment.Area.PLATE_PACK,
        )
        target_status = request.POST.get("status")
        try:
            pallet = set_plate_pallet_status(
                pallet_id=pallet_pk,
                production=production,
                target_status=target_status,
                user=request.user,
            )
        except (ValidationError, PlatePallet.DoesNotExist) as exc:
            detail = (
                "; ".join(exc.messages)
                if hasattr(exc, "messages")
                else "El pallet solicitado no existe."
            )
            messages.error(request, detail)
        else:
            messages.success(
                request,
                f"El pallet P{pallet.pallet_number} quedó {pallet.get_status_display().lower()}.",
            )
        return redirect(
            f"{reverse('productions:plate_pack_create', args=[production.pk])}"
            "#pallet-control"
        )


class PlatePalletLineDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk, line_pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status not in PRODUCTION_EDITABLE_STATUSES:
            raise PermissionDenied("El PP no admite correcciones en sus empaques.")
        require_area_assignment(
            request.user,
            production,
            AreaAssignment.Area.PLATE_PACK,
        )
        try:
            line = void_auto_pack_line(
                line_id=line_pk,
                production=production,
                user=request.user,
            )
        except (ValidationError, PlatePalletLine.DoesNotExist) as exc:
            detail = (
                "; ".join(exc.messages)
                if hasattr(exc, "messages")
                else "El movimiento solicitado no existe."
            )
            messages.error(request, detail)
        else:
            AuditLog.objects.create(
                user=request.user,
                production=production,
                module="plate-pack-auto",
                model_name=line._meta.label,
                record_pk=str(line.pk),
                action=AuditLog.Action.VOID,
                old_value={
                    "product": line.product.code,
                    "pallet": line.pallet.pallet_number,
                    "packages": line.package_count,
                },
                reason="Corrección de empaque automático y devolución de saldos",
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
            messages.success(
                request,
                (
                    f"Se eliminó el movimiento de {line.product.code} en "
                    f"P{line.pallet.pallet_number}; los saldos fueron devueltos."
                ),
            )
        return redirect(
            f"{reverse('productions:plate_pack_create', args=[production.pk])}"
            "#pallet-control"
        )


class PlateBalanceView(LoginRequiredMixin, DetailView):
    model = ProductionOrder
    template_name = "productions/plate_balances.html"
    context_object_name = "production"

    def dispatch(self, request, *args, **kwargs):
        self.production = get_object_or_404(ProductionOrder, pk=kwargs["pk"])
        if not can_view_production(request.user, self.production):
            raise PermissionDenied
        require_area_assignment(
            request.user,
            self.production,
            AreaAssignment.Area.PLATE_PACK,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return self.production

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(plate_balance_dashboard(self.production))
        context["plate_pallet_dashboard"] = plate_pallet_dashboard(self.production)
        return context


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


class TroqueladoCreateView(OperationalCreateView):
    module_key = "troquelado"
    form_class = TroqueladoEntryForm
    area = AreaAssignment.Area.TROQUELADO
    form_title = "Registrar troquelado"

    def _crew_param(self):
        crew_id = self.request.GET.get("crew")
        if not crew_id:
            return None
        try:
            return Crew.objects.get(pk=crew_id, active=True)
        except (Crew.DoesNotExist, ValueError, TypeError):
            return None

    def _worker_param(self, crew):
        worker_id = self.request.GET.get("worker")
        if not worker_id:
            return None
        queryset = Worker.objects.filter(active=True, internal_code__startswith="TROQ-W")
        if crew is not None:
            queryset = _troquelado_crew_worker_queryset(self.production, crew)
        try:
            return queryset.get(pk=worker_id)
        except (Worker.DoesNotExist, ValueError, TypeError):
            return None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        crew = self._crew_param()
        if crew is not None:
            kwargs["crew_id"] = crew.pk
            kwargs["worker_queryset"] = _troquelado_crew_worker_queryset(self.production, crew)
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        crew = self._crew_param()
        if crew is not None:
            initial["crew"] = crew.pk
        worker = self._worker_param(crew)
        if worker is not None:
            initial["worker"] = worker.pk
            last_entry = (
                TroqueladoEntry.objects.filter(
                    production=self.production,
                    worker=worker,
                    is_active=True,
                )
                .order_by("-created_at", "-pk")
                .first()
            )
            if last_entry is not None:
                initial["shift"] = last_entry.shift
                initial["product_type"] = last_entry.product_type
                initial["start_time"] = last_entry.start_time
                initial["end_time"] = last_entry.end_time
            else:
                initial["shift"] = self.production.shift
                initial["start_time"] = dt.time(6, 0)
                initial["end_time"] = dt.time(18, 0)
        elif crew is not None:
            initial["shift"] = self.production.shift
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["troquelado_product_type_choices"] = TroqueladoEntry.ProductType.choices
        context["troquelado_shift_choices"] = ProductionOrder.Shift.choices
        crew = self._crew_param()
        if (
            crew is not None
            and getattr(self, "object", None) is None
            and not getattr(self, "operational_read_only", False)
        ):
            context["troquelado_crew_mode"] = True
            context["troquelado_crew_id"] = crew.pk
            context["troquelado_crew_name"] = crew.name
            context["troquelado_crew_workers"] = list(
                _troquelado_crew_worker_queryset(self.production, crew).values_list("pk", "full_name")
            )
            context["troquelado_clear_url"] = reverse(
                "productions:troquelado_create", args=[self.production.pk]
            )
            context["troquelado_quick_workers"] = _troquelado_quick_workers(
                self.production, crew
            )
            worker = self._worker_param(crew)
            if worker is not None:
                context["troquelado_selected_worker_id"] = worker.pk
                context["troquelado_repeat_worker_name"] = worker.full_name
                context["troquelado_focused_worker"] = True
                context["troquelado_repeat_shift_label"] = dict(
                    ProductionOrder.Shift.choices
                ).get(self.get_initial().get("shift"), "")
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if (
            getattr(self, "object", None) is not None
            and response.status_code == 302
            and not self.request.POST.get("volver")
        ):
            return redirect(
                f"{reverse('productions:troquelado_create', args=[self.production.pk])}"
                f"?crew={self.object.crew_id}#operational-entry-form"
            )
        return response


class TroqueladoWorkerQuickCreateView(LoginRequiredMixin, View):
    """Crea (o reutiliza) un trabajador de troquelado y su cuadrilla desde la
    pantalla de captura. Solo los deja disponibles en el listado; no los
    preselecciona en el formulario."""

    def post(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {
            ProductionOrder.Status.APPROVED,
            ProductionOrder.Status.CLOSED,
            ProductionOrder.Status.VOID,
        }:
            raise PermissionDenied("El PP no admite nuevos trabajadores en su estado actual.")
        require_area_assignment(request.user, production, AreaAssignment.Area.TROQUELADO)

        name = " ".join((request.POST.get("name") or "").strip().upper().split())
        crew_name = " ".join((request.POST.get("crew") or "").strip().upper().split())
        if not name:
            messages.error(request, "Escriba el nombre del trabajador.")
        elif not crew_name:
            messages.error(request, "Escriba la cuadrilla del trabajador.")
        elif len(name) > 180:
            messages.error(request, "El nombre del trabajador es demasiado largo.")
        elif len(crew_name) > 100:
            messages.error(request, "El nombre de la cuadrilla es demasiado largo.")
        else:
            with transaction.atomic():
                existing_crew = _find_existing_crew_by_name(crew_name, "TROQ-")
                worker = _find_existing_worker_by_name(name, "TROQ-W")
                created_worker = worker is None
                if worker is None:
                    worker = Worker.objects.create(
                        internal_code=_next_troquelado_worker_code(),
                        full_name=name,
                        position="Troquelador",
                        active=True,
                        crew=existing_crew,
                    )
                elif not worker.active:
                    worker.active = True
                    worker.save(update_fields=["active", "updated_at"])
                created_crew = existing_crew is None
                crew = existing_crew or Crew.objects.create(
                    code=_next_troquelado_crew_code(),
                    name=crew_name,
                )
                if not crew.active:
                    crew.active = True
                    crew.save(update_fields=["active", "updated_at"])
                if created_worker and worker.crew_id != crew.pk:
                    worker.crew = crew
                    worker.save(update_fields=["crew", "updated_at"])
                AuditLog.objects.create(
                    user=request.user,
                    production=production,
                    module="troquelado_worker_catalog",
                    model_name=worker._meta.label,
                    record_pk=str(worker.pk),
                    action=AuditLog.Action.CREATE if created_worker else AuditLog.Action.UPDATE,
                    new_value={
                        "worker": {"code": worker.internal_code, "name": worker.full_name},
                        "crew": {"code": crew.code, "name": crew.name},
                    },
                    reason=(
                        "Trabajador de troquelado creado desde la captura"
                        if created_worker
                        else "Trabajador de troquelado reactivado"
                    ),
                    ip_address=request.META.get("REMOTE_ADDR"),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                )
            if created_worker and created_crew:
                messages.success(
                    request,
                    f"{worker.full_name} y la cuadrilla {crew.name} fueron creados y ya están disponibles.",
                )
            elif created_worker:
                messages.success(
                    request,
                    f"{worker.full_name} fue creado y ya está disponible en la cuadrilla {crew.name}.",
                )
            else:
                worker_crew = worker.crew.name if worker.crew else "sin cuadrilla"
                messages.warning(
                    request,
                    f"{worker.full_name} ya existía en {worker_crew}; se usó el registro actual sin crear duplicados.",
                )
        return redirect(reverse("productions:troquelado_create", args=[production.pk]))


class TroqueladoQuickCaptureView(LoginRequiredMixin, View):
    """Guarda una entrada de troquelado desde el panel de captura rápida
    (AJAX). Devuelve JSON con los totales actualizados del trabajador, su
    cuadrilla y el parte para refrescar la pantalla sin recargar la página."""

    def post(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        if production.status in {
            ProductionOrder.Status.APPROVED,
            ProductionOrder.Status.CLOSED,
        }:
            raise PermissionDenied(
                "El parte está aprobado o cerrado. Reábralo antes de registrar troquelados."
            )
        if production.status == ProductionOrder.Status.VOID:
            raise PermissionDenied("La producción no admite nuevos registros en su estado actual.")
        require_area_assignment(request.user, production, AreaAssignment.Area.TROQUELADO)
        worker_id = request.POST.get("worker")
        try:
            worker = Worker.objects.get(pk=worker_id, active=True, internal_code__startswith="TROQ-W")
        except (Worker.DoesNotExist, ValueError, TypeError):
            return JsonResponse(
                {"ok": False, "errors": {"worker": "Trabajador no disponible."}},
                status=400,
            )
        if not worker.crew_id:
            return JsonResponse(
                {"ok": False, "errors": {"worker": "El trabajador no tiene cuadrilla asignada."}},
                status=400,
            )
        form = TroqueladoEntryForm(
            request.POST,
            crew_id=worker.crew_id,
            worker_queryset=_troquelado_crew_worker_queryset(production, worker.crew),
            initial={"crew": worker.crew_id},
        )
        if not form.is_valid():
            return JsonResponse({"ok": False, "errors": dict(form.errors)}, status=400)
        try:
            with transaction.atomic():
                entry = form.save(commit=False)
                entry.production = production
                entry.responsible = request.user
                entry.date = production.production_date or production.reception_date
                entry.full_clean()
                entry.save()
                AuditLog.objects.create(
                    user=request.user,
                    production=production,
                    module="troquelado",
                    model_name=entry._meta.label,
                    record_pk=str(entry.pk),
                    action=AuditLog.Action.CREATE,
                    new_value=_operational_record_payload(entry),
                    ip_address=request.META.get("REMOTE_ADDR"),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                )
        except (ValidationError, IntegrityError) as exc:
            messages_value = getattr(exc, "messages", None)
            detail = list(messages_value) if messages_value else [str(exc)]
            return JsonResponse(
                {"ok": False, "errors": {"__all__": detail}},
                status=400,
            )
        title, detail = _operational_record_text(entry)
        return JsonResponse(
            {
                "ok": True,
                "entry_id": entry.pk,
                "record_card": {
                    "entry_id": entry.pk,
                    "title": title,
                    "detail": detail,
                    "edit_url": reverse(
                        "productions:operational_entry_update",
                        args=[production.pk, "troquelado", entry.pk],
                    ),
                    "delete_url": reverse(
                        "productions:operational_entry_delete",
                        args=[production.pk, "troquelado", entry.pk],
                    ),
                },
                **_troquelado_quick_stats(production, worker, worker.crew),
            }
        )


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


class ProductLaminaColorListView(LoginRequiredMixin, View):
    """Catálogo operativo para corregir la lámina sin modificar el producto."""

    template_name = "productions/product_lamina_colors.html"

    def dispatch(self, request, *args, **kwargs):
        require_roles(request.user, Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        products = Product.objects.filter(active=True, code__startswith="PP-").order_by("code", "description")
        return render(request, self.template_name, {"products": products, "color_form": ProductLaminaColorForm()})

    def post(self, request):
        product = get_object_or_404(Product, pk=request.POST.get("product_id"), active=True)
        form = ProductLaminaColorForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, f"Lámina de {product.code} actualizada a {product.lamina_color or 'sin color'}.")
        else:
            messages.error(request, "No se pudo actualizar el color de la lámina.")
        return redirect("productions:product_lamina_colors")


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
        plate_packaging = list(
            PlatePackagingEntry.objects.filter(
                production=production,
                is_active=True,
            ).select_related("product")
        )
        plate_packaging.extend(
            PlatePackagingAllocation.objects.filter(
                production=production,
                is_active=True,
            ).select_related("source_entry__position", "source_entry__product")
        )
        plate_packaging.sort(
            key=lambda entry: (
                entry.pallet_number,
                entry.product.code,
                entry.pk,
            )
        )
        context.update(
            {
                "tunnel_reconciliation": tunnel_reconciliation(production),
                "plate_reconciliation": plate_reconciliation(production),
                "receptions": ReceptionEntry.objects.filter(production=production, is_active=True).select_related("vehicle", "product", "crew"),
                "reception_total": ReceptionEntry.objects.filter(production=production, is_active=True).aggregate(total=Sum("weight_kg"))["total"] or 0,
                "reception_cone_pota_summary": reception_cone_pota_summary(production),
                "nuqueras": NuqueraEntry.objects.filter(production=production, is_active=True).select_related("worker", "crew"),
                "nuquera_total": NuqueraEntry.objects.filter(production=production, is_active=True).aggregate(total=Sum("weight_kg"))["total"] or 0,
                "troquelados": TroqueladoEntry.objects.filter(production=production, is_active=True).select_related("worker"),
                "troquelado_total": TroqueladoEntry.objects.filter(production=production, is_active=True).aggregate(total=Sum("weight_kg"))["total"] or 0,
                "tunnel_packaging": TunnelPackagingEntry.objects.filter(production=production, is_active=True).select_related("product"),
                "plate_packaging": plate_packaging,
                "materials": MaterialUsage.objects.filter(production=production, is_active=True).select_related("material"),
                "costs": CostEntry.objects.filter(production=production, is_active=True).select_related("rate"),
                "audit_logs": production.audit_logs.select_related("user")[:20],
            }
        )
        return context


class ReceptionReportXlsxView(LoginRequiredMixin, View):
    def get(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        payload = build_reception_report_xlsx(production)
        filename = f"RECEPCION_PP_{production.number}_{production.reception_date:%d%m%Y}.xlsx"
        response = HttpResponse(
            payload,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ReceptionTareoXlsxView(LoginRequiredMixin, View):
    def get(self, request, pk):
        production = get_object_or_404(
            ProductionOrder.objects.select_related("customer", "main_product", "template_version"),
            pk=pk,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        try:
            payload = build_reception_tareo_xlsx(production)
        except ReceptionTareoReportError as exc:
            messages.error(request, str(exc))
            return redirect("productions:reception_create", pk=production.pk)

        filename = f"FILETEROS-POTA_TAREO_PP_{production.number}_{production.reception_date:%d%m%Y}.xlsx"
        AuditLog.objects.create(
            user=request.user,
            production=production,
            module="reception-tareo-report",
            model_name=production._meta.label,
            record_pk=str(production.pk),
            action=AuditLog.Action.DOWNLOAD,
            new_value={"filename": filename},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = HttpResponse(
            payload,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class ReceptionTareoPdfView(LoginRequiredMixin, View):
    def get(self, request, pk):
        production = get_object_or_404(
            ProductionOrder.objects.select_related("customer", "main_product", "template_version"),
            pk=pk,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        try:
            payload = build_reception_tareo_pdf(production)
        except ReceptionTareoReportError as exc:
            messages.error(request, str(exc))
            return redirect("productions:reception_create", pk=production.pk)

        filename = f"FILETEROS-POTA_TAREO_PP_{production.number}_{production.reception_date:%d%m%Y}.pdf"
        AuditLog.objects.create(
            user=request.user,
            production=production,
            module="reception-tareo-report-pdf",
            model_name=production._meta.label,
            record_pk=str(production.pk),
            action=AuditLog.Action.DOWNLOAD,
            new_value={"filename": filename},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = HttpResponse(payload, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class ReceptionReportPdfView(LoginRequiredMixin, View):
    def get(self, request, pk):
        production = get_object_or_404(ProductionOrder, pk=pk)
        if not can_view_production(request.user, production):
            raise PermissionDenied
        payload = build_reception_report_pdf(production)
        filename = f"RECEPCION_PP_{production.number}_{production.reception_date:%d%m%Y}.pdf"
        response = HttpResponse(payload, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class NuqueraTareoXlsxView(LoginRequiredMixin, View):
    def get(self, request, pk):
        production = get_object_or_404(
            ProductionOrder.objects.select_related("customer", "main_product", "template_version"),
            pk=pk,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        try:
            payload = build_nuquera_tareo_xlsx(production)
        except NuqueraTareoReportError as exc:
            messages.error(request, str(exc))
            return redirect("productions:nuquera_create", pk=production.pk)

        filename = f"NUQUERAS_TAREO_PP_{production.number}_{production.reception_date:%d%m%Y}.xlsx"
        AuditLog.objects.create(
            user=request.user,
            production=production,
            module="nuqueras-tareo-report",
            model_name=production._meta.label,
            record_pk=str(production.pk),
            action=AuditLog.Action.DOWNLOAD,
            new_value={"filename": filename},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = HttpResponse(
            payload,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class NuqueraTareoPdfView(LoginRequiredMixin, View):
    def get(self, request, pk):
        production = get_object_or_404(
            ProductionOrder.objects.select_related("customer", "main_product", "template_version"),
            pk=pk,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        try:
            payload = build_nuquera_tareo_pdf(production)
        except NuqueraTareoReportError as exc:
            messages.error(request, str(exc))
            return redirect("productions:nuquera_create", pk=production.pk)

        filename = f"NUQUERAS_TAREO_PP_{production.number}_{production.reception_date:%d%m%Y}.pdf"
        AuditLog.objects.create(
            user=request.user,
            production=production,
            module="nuqueras-tareo-report-pdf",
            model_name=production._meta.label,
            record_pk=str(production.pk),
            action=AuditLog.Action.DOWNLOAD,
            new_value={"filename": filename},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = HttpResponse(payload, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class TroqueladoReportXlsxView(LoginRequiredMixin, View):
    def get(self, request, pk):
        production = get_object_or_404(
            ProductionOrder.objects.select_related("customer", "main_product", "template_version"),
            pk=pk,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        try:
            payload = build_troquelado_xlsx(production)
        except TroqueladoReportError as exc:
            messages.error(request, str(exc))
            return redirect("productions:troquelado_create", pk=production.pk)

        filename = f"CONTROL_TROQUELADO_PP_{production.number}_{production.reception_date:%d%m%Y}.xlsx"
        AuditLog.objects.create(
            user=request.user,
            production=production,
            module="troquelado-report",
            model_name=production._meta.label,
            record_pk=str(production.pk),
            action=AuditLog.Action.DOWNLOAD,
            new_value={"filename": filename},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = HttpResponse(
            payload,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class TroqueladoReportPdfView(LoginRequiredMixin, View):
    def get(self, request, pk):
        production = get_object_or_404(
            ProductionOrder.objects.select_related("customer", "main_product", "template_version"),
            pk=pk,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        try:
            payload = build_troquelado_pdf(production)
        except TroqueladoReportError as exc:
            messages.error(request, str(exc))
            return redirect("productions:troquelado_create", pk=production.pk)

        filename = f"CONTROL_TROQUELADO_PP_{production.number}_{production.reception_date:%d%m%Y}.pdf"
        AuditLog.objects.create(
            user=request.user,
            production=production,
            module="troquelado-report-pdf",
            model_name=production._meta.label,
            record_pk=str(production.pk),
            action=AuditLog.Action.DOWNLOAD,
            new_value={"filename": filename},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = HttpResponse(payload, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


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


class PlateReportXlsxView(LoginRequiredMixin, View):
    def get(self, request, pk):
        production = get_object_or_404(
            ProductionOrder.objects.select_related("customer", "template_version"),
            pk=pk,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        try:
            payload = build_plate_report_xlsx(production)
        except PlateReportError as exc:
            messages.error(request, str(exc))
            return redirect("productions:plate_create", pk=production.pk)

        safe_lot = slugify(production.customer_lot or production.plant_lot) or "sin-lote"
        filename = f"ENVASADO_PLAQUEROS_PP_{production.number}_{safe_lot}.xlsx"
        AuditLog.objects.create(
            user=request.user,
            production=production,
            module="plate-report",
            model_name=production._meta.label,
            record_pk=str(production.pk),
            action=AuditLog.Action.DOWNLOAD,
            new_value={"filename": filename},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = HttpResponse(
            payload,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class TunnelReportXlsxView(LoginRequiredMixin, View):
    def get(self, request, pk, tunnel_pk=None):
        production = get_object_or_404(
            ProductionOrder.objects.select_related("customer", "template_version"),
            pk=pk,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        tunnel = None
        if tunnel_pk is not None:
            tunnel = get_object_or_404(Tunnel, pk=tunnel_pk, fills__production=production)
        try:
            payload = build_tunnel_report_xlsx(production, tunnel=tunnel)
        except TunnelReportError as exc:
            messages.error(request, str(exc))
            return redirect("productions:detail", pk=production.pk)

        safe_lot = slugify(production.customer_lot or production.plant_lot) or "sin-lote"
        tunnel_label = tunnel.code if tunnel else "TODOS"
        filename = f"ENVASADO_{tunnel_label}_PP_{production.number}_{safe_lot}.xlsx"
        AuditLog.objects.create(
            user=request.user, production=production, module="tunnel-report",
            model_name=production._meta.label, record_pk=str(production.pk),
            action=AuditLog.Action.DOWNLOAD,
            new_value={"filename": filename, "tunnel": tunnel_label},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = HttpResponse(payload, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class TunnelReportPdfView(LoginRequiredMixin, View):
    def get(self, request, pk, tunnel_pk=None):
        production = get_object_or_404(
            ProductionOrder.objects.select_related("customer", "template_version"),
            pk=pk,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        tunnel = None
        if tunnel_pk is not None:
            tunnel = get_object_or_404(Tunnel, pk=tunnel_pk, fills__production=production)
        try:
            payload = build_tunnel_report_pdf(production, tunnel=tunnel)
        except TunnelReportError as exc:
            messages.error(request, str(exc))
            return redirect("productions:detail", pk=production.pk)

        safe_lot = slugify(production.customer_lot or production.plant_lot) or "sin-lote"
        tunnel_label = tunnel.code if tunnel else "TODOS"
        filename = f"ENVASADO_{tunnel_label}_PP_{production.number}_{safe_lot}.pdf"
        AuditLog.objects.create(
            user=request.user,
            production=production,
            module="tunnel-report-pdf",
            model_name=production._meta.label,
            record_pk=str(production.pk),
            action=AuditLog.Action.DOWNLOAD,
            new_value={"filename": filename, "tunnel": tunnel_label},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = HttpResponse(payload, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class PlateReportPdfView(LoginRequiredMixin, View):
    def get(self, request, pk):
        production = get_object_or_404(
            ProductionOrder.objects.select_related("customer", "template_version"),
            pk=pk,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        try:
            payload = build_plate_report_pdf(production)
        except PlateReportError as exc:
            messages.error(request, str(exc))
            return redirect("productions:plate_create", pk=production.pk)

        safe_lot = slugify(production.customer_lot or production.plant_lot) or "sin-lote"
        filename = f"ENVASADO_PLAQUEROS_PP_{production.number}_{safe_lot}.pdf"
        AuditLog.objects.create(
            user=request.user,
            production=production,
            module="plate-report-pdf",
            model_name=production._meta.label,
            record_pk=str(production.pk),
            action=AuditLog.Action.DOWNLOAD,
            new_value={"filename": filename},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = HttpResponse(payload, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class PackagingReportXlsxView(LoginRequiredMixin, View):
    report_kind = "tunnel"

    def get(self, request, pk):
        production = get_object_or_404(
            ProductionOrder.objects.select_related("customer", "template_version"),
            pk=pk,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        redirect_url = "productions:plate_pack_create" if self.report_kind == "plate" else "productions:tunnel_pack_create"
        try:
            if self.report_kind == "plate":
                payload = build_plate_packaging_report_xlsx(production)
                label = "PLAQUEROS"
            else:
                payload = build_tunnel_packaging_report_xlsx(production)
                label = "TUNEL"
        except PackagingReportError as exc:
            messages.error(request, str(exc))
            return redirect(redirect_url, pk=production.pk)

        safe_lot = slugify(production.customer_lot or production.plant_lot) or "sin-lote"
        filename = f"EMPAQUE_{label}_PP_{production.number}_{safe_lot}.xlsx"
        AuditLog.objects.create(
            user=request.user,
            production=production,
            module="packaging-report",
            model_name=production._meta.label,
            record_pk=str(production.pk),
            action=AuditLog.Action.DOWNLOAD,
            new_value={"filename": filename, "kind": label},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = HttpResponse(
            payload,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class PackagingReportPdfView(LoginRequiredMixin, View):
    report_kind = "tunnel"

    def get(self, request, pk):
        production = get_object_or_404(
            ProductionOrder.objects.select_related("customer", "template_version"),
            pk=pk,
        )
        if not can_view_production(request.user, production):
            raise PermissionDenied
        redirect_url = "productions:plate_pack_create" if self.report_kind == "plate" else "productions:tunnel_pack_create"
        try:
            if self.report_kind == "plate":
                payload = build_plate_packaging_report_pdf(production)
                label = "PLAQUEROS"
            else:
                payload = build_tunnel_packaging_report_pdf(production)
                label = "TUNEL"
        except PackagingReportError as exc:
            messages.error(request, str(exc))
            return redirect(redirect_url, pk=production.pk)

        safe_lot = slugify(production.customer_lot or production.plant_lot) or "sin-lote"
        filename = f"EMPAQUE_{label}_PP_{production.number}_{safe_lot}.pdf"
        AuditLog.objects.create(
            user=request.user,
            production=production,
            module="packaging-report-pdf",
            model_name=production._meta.label,
            record_pk=str(production.pk),
            action=AuditLog.Action.DOWNLOAD,
            new_value={"filename": filename, "kind": label},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = HttpResponse(payload, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


def health(request):
    return HttpResponse("ok", content_type="text/plain")


def csrf_token(request):
    response = JsonResponse({"csrfToken": get_token(request)})
    response["Cache-Control"] = "no-store"
    return response


def csrf_failure(request, reason=""):
    return render(
        request,
        "csrf_failure.html",
        {"retry_url": request.META.get("HTTP_REFERER") or reverse("productions:list")},
        status=403,
    )


def manifest(request):
    return HttpResponse('{"name":"Partes de Producción","short_name":"PP Planta","start_url":"/","display":"standalone","background_color":"#f3f5f4","theme_color":"#124b3b","lang":"es-PE","icons":[{"src":"/static/icons/icon.svg","sizes":"any","type":"image/svg+xml","purpose":"any maskable"}]}', content_type="application/manifest+json")


def service_worker(request):
    content = """const CACHE='pp-shell-v5';const ASSETS=['/','/manifest.webmanifest','/static/css/app.css','/static/js/app.js?v=20260724-delete-action-2','/static/icons/icon.svg'];self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS)))});self.addEventListener('activate',e=>e.waitUntil(Promise.all([self.clients.claim(),caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))])));self.addEventListener('fetch',e=>{if(e.request.method==='GET')e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)))});"""
    response = HttpResponse(content, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response
