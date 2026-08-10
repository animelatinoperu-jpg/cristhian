import datetime as dt
from decimal import Decimal

import unicodedata

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.db.models import Case, IntegerField, Q, Sum, When
from django.utils import timezone

from .models import (
    AreaAssignment,
    EXCEL_CREW_SLOT_LIMIT,
    Customer,
    Material,
    MaterialUsage,
    CostEntry,
    Crew,
    NuqueraEntry,
    PlateCrewEntry,
    PlateEntry,
    PlatePositionTiming,
    PlatePackagingAllocation,
    PlatePackagingEntry,
    Product,
    ProductionOrder,
    ReceptionEntry,
    TunnelCrewEntry,
    TunnelEntry,
    TunnelFill,
    TunnelRack,
    TunnelPackagingEntry,
    Rate,
    Role,
    Tunnel,
    User,
    Vehicle,
    Worker,
    TroqueladoEntry,
    active_excel_crew_ids,
)

from .services.template_catalog import template_plate_codes


SPANISH_FIELD_LABELS = {
    "number": "Número de PP",
    "plant_lot": "Lote de planta",
    "customer_lot": "Lote del cliente",
    "customer": "Cliente",
    "process": "Proceso",
    "main_product": "Producto principal",
    "reception_date": "Fecha de recepción",
    "production_date": "Fecha de producción",
    "packaging_date": "Fecha de empaque",
    "shift": "Turno",
    "series": "Serie",
    "vehicle_notes": "Observaciones del vehículo",
    "plate_notes": "Observaciones de placas",
    "observations": "Observaciones",
    "template_version": "Versión de plantilla",
    "tunnel": "Túnel",
    "fill_number": "Número de llenada",
    "date": "Fecha",
    "start_time": "Hora de inicio",
    "launch_time": "Hora de lanzamiento",
    "end_time": "Hora de término",
    "observation": "Observación",
    "rack": "Rack / estante",
    "product": "Producto",
    "tray_count": "Cantidad de bandejas",
    "vehicle": "Vehículo",
    "vehicle_text": "Vehículo",
    "car_number": "Número de carro",
    "crew": "Cuadrilla",
    "container": "Contenedor",
    "weight_kg": "Peso (kg)",
    "time": "Hora",
    "worker": "Trabajador",
    "fill": "Llenada",
    "page_or_block": "Página o bloque",
    "position": "Posición",
    "page": "Página",
    "pallet_number": "Número de palé",
    "package_count": "Cantidad de bultos",
    "source_entry": "Código descargado del plaquero",
    "material": "Material",
    "quantity": "Cantidad",
    "concept": "Concepto",
    "unit_cost": "Costo unitario",
    "rate": "Tarifa",
    "expected_version": "Versión esperada",
    "target_status": "Estado de destino",
    "reason": "Motivo",
    "name": "Nombre",
    "tax_id": "RUC o documento fiscal",
    "active": "Activo",
    "plate": "Placa",
    "description": "Descripción",
    "internal_code": "Código interno",
    "document": "Documento",
    "full_name": "Nombres completos",
    "amount": "Monto",
    "unit": "Unidad",
    "effective_from": "Vigente desde",
    "effective_to": "Vigente hasta",
}


class PlantAuthenticationForm(AuthenticationForm):
    """Muestra el estado real cuando la contraseña es correcta pero la cuenta aún no está activa."""

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username and password:
            candidate = User.objects.filter(username__iexact=username).first()
            if candidate is not None and candidate.check_password(password):
                if candidate.registration_status == User.RegistrationStatus.PENDING:
                    raise ValidationError(
                        "Su cuenta está pendiente de aprobación. Solicite al administrador que active sus accesos.",
                        code="pending_approval",
                    )
                if candidate.registration_status == User.RegistrationStatus.REJECTED:
                    raise ValidationError(
                        "Esta solicitud de cuenta no fue aprobada. Consulte con el administrador.",
                        code="registration_rejected",
                    )
                if not candidate.is_active:
                    raise ValidationError(
                        "Esta cuenta está desactivada. Consulte con el administrador.",
                        code="inactive_account",
                    )
                if candidate.locked_until and candidate.locked_until > timezone.now():
                    raise ValidationError(
                        "La cuenta está bloqueada temporalmente por varios intentos fallidos. Intente nuevamente más tarde.",
                        code="account_locked",
                    )
        return super().clean()


def active_product_queryset():
    exact = Product.objects.filter(active=True, code__startswith="PP-")
    queryset = exact if exact.exists() else Product.objects.filter(active=True)
    return queryset.order_by("code", "description")


class ProductColorSelect(forms.Select):
    """Expone el color de lámina en cada opción sin cambiar su valor ni su lógica."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        product = getattr(value, "instance", None)
        if product and product.color:
            option["attrs"]["data-lamina-color"] = product.lamina_color
        return option


def normalized_crew_name(value):
    normalized = unicodedata.normalize("NFD", (value or "").strip().upper())
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return " ".join(normalized.split())


def find_existing_crew_by_name(name):
    normalized_name = normalized_crew_name(name)
    for crew in Crew.objects.filter(active=True).order_by("name", "code"):
        if normalized_crew_name(crew.name) == normalized_name:
            return crew
    return None


def active_crew_queryset():
    exact = Crew.objects.filter(active=True, code__startswith="CUAD-")
    queryset = exact if exact.exists() else Crew.objects.filter(active=True)
    selected_ids = []
    seen_names = set()
    for crew in queryset.order_by("name", "code"):
        key = normalized_crew_name(crew.name)
        if key in seen_names:
            continue
        seen_names.add(key)
        selected_ids.append(crew.pk)
    return Crew.objects.filter(pk__in=selected_ids).order_by("name", "code")


def plate_positions_by_batch(queryset):
    positions = sorted(
        queryset,
        key=lambda position: (
            position.batch_number or 9999,
            position.plaquero_number or 9999,
            position.pk,
        ),
    )
    if not positions:
        return queryset.none()
    operational_order = Case(
        *[When(pk=position.pk, then=index) for index, position in enumerate(positions)],
        output_field=IntegerField(),
    )
    return (
        queryset.filter(pk__in=[position.pk for position in positions])
        .annotate(_operational_order=operational_order)
        .order_by("_operational_order")
    )


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = SPANISH_FIELD_LABELS.get(name, field.label)
            if isinstance(field.widget, forms.CheckboxInput):
                css = "form-check-input"
            elif isinstance(field.widget, forms.Select):
                css = "form-select"
            else:
                css = "form-control"
            field.widget.attrs["class"] = f"{field.widget.attrs.get('class', '')} {css}".strip()
            if isinstance(field, forms.ModelChoiceField):
                field.empty_label = "Seleccione una opción"
            elif isinstance(field, forms.ChoiceField):
                choices = list(field.choices)
                if choices and choices[0][0] == "":
                    choices[0] = ("", "Seleccione una opción")
                    field.choices = choices
            if isinstance(field.widget, forms.DateInput):
                field.widget.input_type = "date"
            elif isinstance(field.widget, forms.TimeInput):
                field.widget.input_type = "time"
            elif isinstance(field.widget, forms.NumberInput):
                field.widget.attrs.setdefault("min", "0")
            if name == "observation" and isinstance(field.widget, forms.Textarea):
                field.widget.attrs["rows"] = 2
            if name in {"product", "main_product"} and isinstance(field, forms.ModelChoiceField):
                field.queryset = active_product_queryset()
            if name == "crew" and isinstance(field, forms.ModelChoiceField):
                field.queryset = active_crew_queryset()
        if not self.is_bound and "date" in self.fields:
            self.fields["date"].initial = timezone.localdate()


REQUESTABLE_ROLE_CODES = {
    code
    for code, _label in Role.Codes.choices
    if code not in {Role.Codes.ADMIN, Role.Codes.PRODUCTION_MANAGER}
}


class UserRegistrationForm(UserCreationForm):
    requested_role = forms.ChoiceField(
        label="Cargo o área solicitada",
        choices=[
            (code, label)
            for code, label in Role.Codes.choices
            if code in REQUESTABLE_ROLE_CODES
        ],
    )
    email = forms.EmailField(label="Correo electrónico", required=True)
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "requested_role")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Usuario"
        self.fields["first_name"].label = "Nombres"
        self.fields["last_name"].label = "Apellidos"
        self.fields["password1"].label = "Contraseña"
        self.fields["password2"].label = "Repita la contraseña"
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True
        self.fields["username"].help_text = "Use letras y números, sin espacios. Este será su usuario para ingresar."
        self.fields["requested_role"].help_text = "El administrador confirmará el rol antes de activar la cuenta."
        for field in self.fields.values():
            if isinstance(field.widget, forms.HiddenInput):
                continue
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"

    def clean_username(self):
        username = self.cleaned_data["username"].strip().lower()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Este nombre de usuario ya está registrado.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Este correo ya está registrado.")
        return email

    def clean_website(self):
        value = self.cleaned_data.get("website", "")
        if value:
            raise forms.ValidationError("No se pudo completar el registro.")
        return value

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_active = False
        user.registration_status = User.RegistrationStatus.PENDING
        if commit:
            user.save()
        return user


ROLE_AREA_MAP = {
    Role.Codes.RECEPTION: AreaAssignment.Area.RECEPTION,
    Role.Codes.NUQUERAS: AreaAssignment.Area.NUQUERAS,
    Role.Codes.TUNNEL: AreaAssignment.Area.TUNNEL,
    Role.Codes.TUNNEL_CREW: AreaAssignment.Area.TUNNEL_CREW,
    Role.Codes.PLATES: AreaAssignment.Area.PLATES,
    Role.Codes.PLATE_CREW: AreaAssignment.Area.PLATE_CREW,
    Role.Codes.TUNNEL_PACK: AreaAssignment.Area.TUNNEL_PACK,
    Role.Codes.PLATE_PACK: AreaAssignment.Area.PLATE_PACK,
    Role.Codes.MATERIALS: AreaAssignment.Area.MATERIALS,
    Role.Codes.COSTS: AreaAssignment.Area.COSTS,
    Role.Codes.TROQUELADO: AreaAssignment.Area.TROQUELADO,
}

GENERAL_ACCESS_ROLE_LABELS = {
    Role.Codes.ADMIN: "Administrador",
    Role.Codes.PRODUCTION_MANAGER: "Jefe de producción",
    Role.Codes.MANAGEMENT: "Gerencia o consulta",
    Role.Codes.AUDITOR: "Auditor",
}

OPERATIONAL_ACCESS_ROLE_LABELS = {
    Role.Codes.RECEPTION: "Recepción de materia prima (R.M)",
    Role.Codes.NUQUERAS: "Nuqueras o perfilado (NUQ)",
    Role.Codes.TUNNEL: "Llenado y supervisión de túneles",
    Role.Codes.TUNNEL_CREW: "Bandejas por cuadrilla de túnel",
    Role.Codes.PLATES: "Envasado en placas (P1–P3)",
    Role.Codes.PLATE_CREW: "Cuadrillas de placas",
    Role.Codes.TUNNEL_PACK: "Empaque de túneles",
    Role.Codes.PLATE_PACK: "Empaque de placas",
    Role.Codes.MATERIALS: "Materiales e insumos",
    Role.Codes.COSTS: "Costos de producción",
    Role.Codes.TROQUELADO: "Troquelado",
}


class UserAccessForm(forms.ModelForm):
    productions = forms.ModelMultipleChoiceField(
        label="Partes de producción permitidos",
        queryset=ProductionOrder.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    tunnels = forms.ModelMultipleChoiceField(
        label="Túneles permitidos",
        queryset=Tunnel.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "registration_status", "roles")
        widgets = {"roles": forms.CheckboxSelectMultiple}

    def __init__(self, *args, actor=None, **kwargs):
        self.actor = actor
        original_instance = kwargs.get("instance")
        self._previous_registration_status = (
            original_instance.registration_status if original_instance is not None else None
        )
        super().__init__(*args, **kwargs)
        self.fields["first_name"].label = "Nombres"
        self.fields["last_name"].label = "Apellidos"
        self.fields["email"].label = "Correo electrónico"
        self.fields["registration_status"].label = "Estado de la cuenta"
        self.fields["roles"].label = "Roles autorizados"
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True
        self.fields["email"].required = True
        role_queryset = Role.objects.order_by("name")
        if actor is not None and not actor.is_superuser:
            role_queryset = role_queryset.exclude(
                code__in=[
                    Role.Codes.ADMIN,
                    Role.Codes.PRODUCTION_MANAGER,
                    Role.Codes.MANAGEMENT,
                    Role.Codes.AUDITOR,
                ]
            )
        self.fields["roles"].queryset = role_queryset
        available_roles = {role.code: role for role in role_queryset}
        if self.is_bound:
            selected_role_ids = {
                str(value) for value in self.data.getlist(self.add_prefix("roles"))
            }
        elif self.instance.pk:
            selected_role_ids = {
                str(value) for value in self.instance.roles.values_list("pk", flat=True)
            }
        else:
            selected_role_ids = set()

        def grouped_options(labels):
            return [
                {
                    "role": available_roles[code],
                    "label": label,
                    "selected": str(available_roles[code].pk) in selected_role_ids,
                }
                for code, label in labels.items()
                if code in available_roles
            ]

        self.operational_role_options = grouped_options(OPERATIONAL_ACCESS_ROLE_LABELS)
        self.general_role_options = grouped_options(GENERAL_ACCESS_ROLE_LABELS)
        self.fields["productions"].queryset = ProductionOrder.objects.exclude(
            status=ProductionOrder.Status.VOID
        ).order_by("-production_date", "-number")
        self.fields["tunnels"].queryset = Tunnel.objects.filter(active=True).order_by("code")
        self.fields["productions"].help_text = "Seleccione los PP que esta persona podrá abrir y llenar."
        self.fields["tunnels"].help_text = "Solo es obligatorio para el rol Supervisor de túnel."
        for name in ("first_name", "last_name", "email", "registration_status"):
            field = self.fields[name]
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"

        if self.instance.pk:
            active_assignments = self.instance.area_assignments.filter(active=True)
            self.fields["productions"].initial = active_assignments.values_list("production_id", flat=True).distinct()
            self.fields["tunnels"].initial = active_assignments.filter(tunnel__isnull=False).values_list("tunnel_id", flat=True).distinct()

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("registration_status")
        roles = cleaned.get("roles")
        role_codes = set(roles.values_list("code", flat=True)) if roles is not None else set()
        productions = cleaned.get("productions")
        tunnels = cleaned.get("tunnels")
        if status == User.RegistrationStatus.ACTIVE:
            if not role_codes:
                self.add_error("roles", "Seleccione al menos un rol antes de activar la cuenta.")
            if role_codes.intersection(ROLE_AREA_MAP) and not productions:
                self.add_error("productions", "Seleccione al menos un parte de producción para este rol operativo.")
            if Role.Codes.TUNNEL in role_codes and not tunnels:
                self.add_error("tunnels", "Seleccione uno o más túneles para el supervisor.")
        return cleaned

    @staticmethod
    def _activate_assignment(*, user, production, area, tunnel=None):
        assignment = AreaAssignment.objects.filter(
            production=production,
            user=user,
            area=area,
            shift=production.shift,
            tunnel=tunnel,
        ).order_by("pk").first()
        if assignment is None:
            AreaAssignment.objects.create(
                production=production,
                user=user,
                area=area,
                shift=production.shift,
                tunnel=tunnel,
                active=True,
            )
        elif not assignment.active:
            assignment.active = True
            assignment.save(update_fields=["active"])

    def _sync_assignments(self, user):
        user.area_assignments.filter(active=True).update(active=False)
        if user.registration_status != User.RegistrationStatus.ACTIVE:
            return
        role_codes = set(user.roles.values_list("code", flat=True))
        productions = self.cleaned_data["productions"]
        tunnels = self.cleaned_data["tunnels"]
        for production in productions:
            for role_code in role_codes.intersection(ROLE_AREA_MAP):
                area = ROLE_AREA_MAP[role_code]
                if area == AreaAssignment.Area.TUNNEL:
                    for tunnel in tunnels:
                        self._activate_assignment(user=user, production=production, area=area, tunnel=tunnel)
                else:
                    self._activate_assignment(user=user, production=production, area=area)

    def save(self, commit=True, approved_by=None):
        previous_status = self._previous_registration_status
        user = super().save(commit=False)
        user.is_active = user.registration_status == User.RegistrationStatus.ACTIVE
        user.failed_login_attempts = 0
        user.locked_until = None
        if user.registration_status == User.RegistrationStatus.ACTIVE and previous_status != User.RegistrationStatus.ACTIVE:
            user.approved_by = approved_by
            user.approved_at = timezone.now()
        elif user.registration_status == User.RegistrationStatus.PENDING:
            user.approved_by = None
            user.approved_at = None
        if commit:
            user.save()
            self._save_m2m()
            self._sync_assignments(user)
        return user


class ProductionOrderForm(StyledModelForm):
    class Meta:
        model = ProductionOrder
        fields = [
            "number", "plant_lot", "customer_lot", "customer", "process", "main_product",
            "reception_date", "packaging_date", "series",
            "template_version",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = self.fields["customer"].queryset.filter(active=True).order_by("name")
        main_products = Product.objects.filter(
            active=True,
            code="POTA-GRANEL",
            description__iexact="POTA A GRANEL",
        )
        self.fields["main_product"].queryset = main_products
        self.fields["main_product"].empty_label = None
        self.fields["main_product"].label_from_instance = lambda product: product.description
        self.fields["template_version"].queryset = self.fields["template_version"].queryset.filter(active=True)
        self.fields["customer"].help_text = "Seleccione un cliente del catálogo."
        self.fields["main_product"].help_text = "Producto principal fijo: Pota a granel."
        self.fields["template_version"].help_text = "Plantilla privada usada para generar el Excel final."
        self.fields["customer_lot"].required = False
        self.fields["customer_lot"].help_text = "Automático: PPF + fecha de recepción en ddmmaaaa."
        self.fields["customer_lot"].widget.attrs["readonly"] = "readonly"
        self.fields["packaging_date"].required = False
        self.fields["packaging_date"].help_text = "Automático: un día después de la recepción."
        self.fields["packaging_date"].widget.attrs["readonly"] = "readonly"
        self.fields["series"].required = False
        self.fields["series"].help_text = "Automático: siempre 001."
        self.fields["series"].widget.attrs["readonly"] = "readonly"
        if not self.is_bound:
            today = timezone.localdate()
            self.fields["reception_date"].initial = today
            self.fields["packaging_date"].initial = today + dt.timedelta(days=1)
            self.fields["customer_lot"].initial = self._customer_lot_for_date(today)
            self.fields["series"].initial = "001"
            self.fields["main_product"].initial = main_products.values_list("pk", flat=True).first()

    @staticmethod
    def _customer_lot_for_date(value):
        return f"PPF{value:%d%m%Y}"

    def clean(self):
        cleaned = super().clean()
        reception_date = cleaned.get("reception_date")
        if reception_date:
            cleaned["packaging_date"] = reception_date + dt.timedelta(days=1)
            cleaned["customer_lot"] = self._customer_lot_for_date(reception_date)
        cleaned["series"] = "001"
        return cleaned

    def save(self, commit=True):
        self.instance.production_date = self.cleaned_data["reception_date"]
        self.instance.packaging_date = self.cleaned_data["packaging_date"]
        self.instance.customer_lot = self.cleaned_data["customer_lot"]
        self.instance.series = self.cleaned_data["series"]
        self.instance.shift = ProductionOrder.Shift.DAY
        return super().save(commit=commit)


class VersionedFormMixin:
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, version=None, **kwargs):
        super().__init__(*args, **kwargs)
        if "expected_version" not in self.fields:
            self.fields["expected_version"] = forms.IntegerField(widget=forms.HiddenInput)
        if version is not None:
            self.fields["expected_version"].initial = version


class TunnelFillForm(VersionedFormMixin, StyledModelForm):
    class Meta:
        model = TunnelFill
        fields = ["tunnel", "fill_number"]

    def __init__(self, *args, production=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if production is not None and user is not None and not (user.is_superuser or user.has_role("ADMIN", "JEFE_PROD")):
            self.fields["tunnel"].queryset = self.fields["tunnel"].queryset.filter(assignments__production=production, assignments__user=user, assignments__area="TUNNEL", assignments__shift=production.shift, assignments__active=True).distinct()


class TunnelEntryForm(StyledModelForm):
    class Meta:
        model = TunnelEntry
        fields = ["rack", "product", "tray_count", "date", "observation"]
        widgets = {"observation": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, production=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if production is not None:
            self.fields["rack"].queryset = self.fields["rack"].queryset.filter(fill__production=production, fill__status__in=[TunnelFill.Status.OPEN, TunnelFill.Status.REOPENED]).select_related("fill__tunnel")
            if user is not None and not (user.is_superuser or user.has_role("ADMIN", "JEFE_PROD")):
                self.fields["rack"].queryset = self.fields["rack"].queryset.filter(fill__tunnel__assignments__production=production, fill__tunnel__assignments__user=user, fill__tunnel__assignments__area="TUNNEL", fill__tunnel__assignments__shift=production.shift, fill__tunnel__assignments__active=True).distinct()


class TunnelBatchRowForm(forms.Form):
    rack_id = forms.IntegerField(widget=forms.HiddenInput)
    entry_id = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput(attrs={"data-rack-entry-id": ""}),
    )
    max_trays = forms.TypedChoiceField(
        label="Capacidad del rack",
        choices=(
            (49, "49 bandejas (excepcional)"),
            (50, "50 bandejas"),
            (70, "70 bandejas"),
        ),
        coerce=int,
        widget=forms.Select(attrs={"class": "form-select", "data-rack-capacity-select": ""}),
    )
    product = forms.ModelChoiceField(
        label="Producto",
        required=False,
        queryset=Product.objects.none(),
        widget=ProductColorSelect(attrs={"class": "form-select", "data-rack-product-select": ""}),
    )
    tray_count = forms.IntegerField(
        label="Bandejas",
        required=False,
        min_value=1,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "min": 1,
                "inputmode": "numeric",
                "placeholder": "0",
                "data-rack-tray-input": "",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = active_product_queryset()
        self.fields["product"].empty_label = "Seleccione el producto"

    def clean(self):
        cleaned = super().clean()
        entry_id = cleaned.get("entry_id")
        product = cleaned.get("product")
        trays = cleaned.get("tray_count")
        if bool(product) != bool(trays):
            raise forms.ValidationError("Seleccione el producto e ingrese las bandejas, o deje ambos campos vacíos.")
        if entry_id and not product:
            raise forms.ValidationError("El registro que está corrigiendo necesita producto y bandejas.")
        return cleaned


TunnelBatchFormSet = forms.formset_factory(TunnelBatchRowForm, extra=0)


class ReceptionEntryForm(StyledModelForm):
    vehicle_text = forms.CharField(
        label="Vehículo",
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Escriba la placa o identificación",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = ReceptionEntry
        fields = ["vehicle_text", "car_number", "product", "crew", "container", "weight_kg", "time", "observation"]

    def __init__(self, *args, production=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.production = production or getattr(self.instance, "production", None)
        if production is not None:
            self.instance.production = production
        if self.instance.pk and self.instance.vehicle_id:
            self.fields["vehicle_text"].initial = self.instance.vehicle.plate
        self.fields["car_number"].required = True
        self.fields["car_number"].help_text = "Use un número del 1 al 9. No puede repetirse para otro vehículo dentro del PP."
        self.fields["crew"].required = True
        self.fields["vehicle_text"].help_text = "Escriba el vehículo manualmente. Se colocará en la fila PLACA de la hoja R.M del Excel."
        self.fields["crew"].queryset = Crew.objects.filter(
            active=True,
            code__startswith="RM-CUAD-",
        ).order_by("code")
        self.fields["crew"].help_text = "La hoja R.M admite únicamente LUIS, FELIX y CHARLES."
        raw_materials = Product.objects.filter(active=True, code__startswith="RM-").order_by("description", "code")
        if raw_materials.exists():
            self.fields["product"].queryset = raw_materials
        self.fields["container"].label = "N° de dino"
        self.fields["container"].help_text = ""

        self.fields["weight_kg"].widget.attrs.update(
            {"step": "0.01", "inputmode": "decimal", "placeholder": "0.00"}
        )
        self.fields["weight_kg"].help_text = ""

    def clean_vehicle_text(self):
        value = " ".join(self.cleaned_data["vehicle_text"].split()).upper()
        if not value:
            raise forms.ValidationError("Ingrese la placa o identificación del vehículo.")
        return value

    def clean_car_number(self):
        value = self.cleaned_data["car_number"].strip()
        if not value.isdigit() or not 1 <= int(value) <= 9:
            raise forms.ValidationError("Ingrese un número de carro del 1 al 9.")
        return str(int(value))

    def clean(self):
        cleaned = super().clean()
        vehicle_text = cleaned.get("vehicle_text")
        if vehicle_text and not self._errors:
            vehicle = Vehicle.objects.filter(plate__iexact=vehicle_text).first()
            if vehicle is None:
                vehicle = Vehicle.objects.create(
                    plate=vehicle_text,
                    description="Ingresado manualmente desde Recepción",
                )
            self.instance.vehicle = vehicle
        return cleaned

    def clean_container(self):
        value = self.cleaned_data["container"].strip()
        if not value.isdigit() or not 1 <= int(value) <= 67:
            raise forms.ValidationError("Ingrese un número de dino entre 1 y 67.")
        return str(int(value))


class NuqueraEntryForm(StyledModelForm):
    class Meta:
        model = NuqueraEntry
        fields = ["shift", "crew", "worker", "process", "weight_kg", "start_time", "end_time", "observation"]

    def __init__(self, *args, crew_id=None, worker_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["weight_kg"].widget.attrs.update(
            {"step": "0.01", "inputmode": "decimal", "placeholder": "0.00"}
        )
        self.fields["weight_kg"].help_text = "Ingrese los kilos con máximo 2 decimales (ejemplo: 120.50)."
        workers = Worker.objects.filter(active=True, internal_code__startswith="NUQ-W").order_by("full_name")
        crews = Crew.objects.filter(active=True).filter(
            Q(code__startswith="NUQ-") | Q(workers__in=workers)
        ).distinct().order_by("name")
        if crews.exists():
            self.fields["crew"].queryset = crews
        if crew_id is not None and crews.filter(pk=crew_id).exists():
            self.fields["crew"].disabled = True
            self.fields["crew"].help_text = "Fijada a la cuadrilla seleccionada. Use 'Elegir otra cuadrilla' para cambiarla."
        if worker_queryset is not None and worker_queryset.exists():
            self.fields["worker"].queryset = worker_queryset
        elif workers.exists():
            self.fields["worker"].queryset = workers


class TunnelCrewEntryForm(StyledModelForm):
    crew_name = forms.CharField(
        label="Cuadrilla",
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "list": "tunnel-crew-options",
                "autocomplete": "off",
                "placeholder": "Escriba para buscar la cuadrilla",
            }
        ),
        help_text="Escriba parte del nombre. Si no existe, créela desde esta misma pantalla.",
    )
    crew = forms.ModelChoiceField(queryset=Crew.objects.none(), widget=forms.HiddenInput(), required=False)

    class Meta:
        model = TunnelCrewEntry
        fields = ["fill", "rack", "product", "crew_name", "crew", "tray_count"]

    def __init__(self, *args, production=None, selected_fill=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.production = production
        crews = active_crew_queryset()
        self.fields["crew"].queryset = crews
        self.crew_suggestions = list(crews.values_list("pk", "name"))
        self.fields["rack"].required = True
        self.fields["product"].required = False
        self.fields["product"].empty_label = "Seleccione el producto del rack"
        self.fields["product"].help_text = "Solo aparecen productos realmente guardados en los racks de esta llenada."
        if production is not None:
            self.fields["fill"].queryset = self.fields["fill"].queryset.filter(production=production)
            self.fields["rack"].queryset = TunnelRack.objects.filter(
                fill__production=production,
                entries__is_active=True,
            ).distinct()
            product_entries = TunnelEntry.objects.filter(
                production=production,
                is_active=True,
            )
            selected_rack = (
                self.data.get(self.add_prefix("rack"))
                if self.is_bound
                else self.initial.get("rack") or self.instance.rack_id
            )
            if str(selected_rack).isdigit():
                product_entries = product_entries.filter(rack_id=int(selected_rack))
            product_totals = {
                row["product_id"]: int(row["total"] or 0)
                for row in product_entries.values("product_id").annotate(total=Sum("tray_count"))
            }
            assigned_totals = {}
            if str(selected_rack).isdigit():
                assigned_totals = {
                    row["product_id"]: int(row["total"] or 0)
                    for row in TunnelCrewEntry.objects.filter(
                        production=production,
                        is_active=True,
                        rack_id=int(selected_rack),
                        product_id__isnull=False,
                    ).values("product_id").annotate(total=Sum("tray_count"))
                }
            product_ids = {
                product_id
                for product_id, total in product_totals.items()
                if total > assigned_totals.get(product_id, 0)
            }
            if self.instance.pk and self.instance.product_id:
                product_ids.add(self.instance.product_id)
            self.fields["product"].queryset = Product.objects.filter(
                pk__in=product_ids,
                active=True,
            ).order_by("description", "code")
            self.fields["product"].label_from_instance = lambda product: (
                f"{product.code} - {product.description}"
            )
        if selected_fill is None and self.instance.pk:
            selected_fill = self.instance.fill
        if selected_fill is not None:
            self.fields["fill"].queryset = self.fields["fill"].queryset.filter(pk=selected_fill.pk)
            self.fields["fill"].initial = selected_fill
            self.fields["fill"].widget = forms.HiddenInput()
            self.fields["rack"].queryset = self.fields["rack"].queryset.filter(fill=selected_fill)
            self.fields["product"].queryset = self.fields["product"].queryset.filter(tunnel_entries__rack__fill=selected_fill).distinct()
        self.fields["rack"].label_from_instance = lambda rack: (
            f"{rack.code} — {sum(entry.tray_count for entry in rack.entries.filter(is_active=True))} bandejas llenadas"
        )
        if self.instance.pk and self.instance.crew_id:
            self.fields["crew_name"].initial = self.instance.crew.name
            self.fields["crew"].initial = self.instance.crew_id

    def clean_crew_name(self):
        return " ".join(self.cleaned_data["crew_name"].strip().upper().split())

    def clean(self):
        cleaned = super().clean()
        name = cleaned.get("crew_name")
        if name:
            crew = active_crew_queryset().filter(name__iexact=name).first()
            if crew is None:
                self.add_error(
                    "crew_name",
                    "Esta cuadrilla todavía no existe. Use «Crear nueva cuadrilla» y luego selecciónela.",
                )
            else:
                cleaned["crew"] = crew
                self.instance.crew = crew
                production_id = (
                    self.production.pk
                    if self.production is not None
                    else getattr(self.instance, "production_id", None)
                )
                if production_id:
                    crew_ids = active_excel_crew_ids(
                        production_id,
                        exclude_tunnel_pk=self.instance.pk,
                    )
                    crew_ids.add(crew.pk)
                    if len(crew_ids) > EXCEL_CREW_SLOT_LIMIT:
                        self.add_error(
                            "crew_name",
                            (
                                "La plantilla Excel actual admite como máximo "
                                f"{EXCEL_CREW_SLOT_LIMIT} cuadrillas participantes "
                                "entre túneles y placas."
                            ),
                        )
        fill = cleaned.get("fill")
        rack = cleaned.get("rack")
        product = cleaned.get("product")
        if fill is not None and rack is not None and rack.fill_id != fill.pk:
            self.add_error("rack", "El rack no pertenece a la llenada seleccionada.")
        if rack is not None and product is not None:
            if not rack.entries.filter(is_active=True, product=product).exists():
                self.add_error("product", "El producto no pertenece al rack seleccionado.")
        if rack is not None and product is None:
            rack_products = list(
                Product.objects.filter(tunnel_entries__rack=rack, tunnel_entries__is_active=True)
                .distinct()
                .order_by("description", "code")
            )
            if len(rack_products) == 1:
                cleaned["product"] = rack_products[0]
                self.instance.product = rack_products[0]
            elif len(rack_products) > 1:
                self.add_error("product", "Seleccione el producto trabajado por la cuadrilla.")
        self.instance.page_or_block = "PAGINA 1"
        return cleaned


class PlateEntryForm(StyledModelForm):
    class Meta:
        model = PlateEntry
        fields = ["shift", "position", "product", "tray_count", "observation"]

    def __init__(self, *args, production=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.production = production or getattr(self.instance, "production", None)
        self.fields["shift"].required = False
        self.fields["shift"].widget = forms.HiddenInput()
        if self.instance.pk and self.instance.shift:
            self.fields["shift"].initial = self.instance.shift
        elif self.production is not None:
            self.fields["shift"].initial = self.production.shift
        if not self.instance.pk:
            self.fields["product"].required = False
            self.fields["tray_count"].required = False
            self.fields["product"].widget.attrs.update(
                {
                    "class": "form-select",
                    "data-plate-product-select": "",
                }
            )
            self.fields["tray_count"].widget = forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                    "inputmode": "numeric",
                    "placeholder": "0",
                    "data-plate-tray-input": "",
                }
            )
        if production is not None:
            plate_codes = template_plate_codes(production.template_version)
            if plate_codes:
                self.fields["product"].queryset = Product.objects.filter(
                    code__in=plate_codes,
                    active=True,
                ).order_by("code", "description")
        if production is not None:
            positions = plate_positions_by_batch(
                self.fields["position"].queryset.filter(
                    template_version=production.template_version,
                    active=True,
                )
            )
            totals = dict(
                PlateEntry.objects.filter(production=production, is_active=True)
                .values_list("position_id")
                .annotate(total=Sum("tray_count"))
            )
            closed_position_ids = set(
                PlatePositionTiming.objects.filter(
                    production=production,
                    load_completed_at__isnull=False,
                ).values_list("position_id", flat=True)
            )
            # A full or closed plaquero cannot receive another product. Keeping
            # it out of the selector avoids a dead-end choice in the capture
            # form. Its current position remains selectable while an existing
            # entry is being corrected, so historical data can still be edited.
            current_position_id = self.instance.position_id if self.instance.pk else None
            available_position_ids = [
                position.pk
                for position in positions
                if (
                    position.pk == current_position_id
                    or (
                        position.pk not in closed_position_ids
                        and totals.get(position.pk, 0) < position.max_trays
                    )
                )
            ]
            self.fields["position"].queryset = positions.filter(
                pk__in=available_position_ids
            )
            self.fields["position"].label_from_instance = lambda position: (
                f"{position.color_marker} {position.operational_label} — "
                f"{totals.get(position.pk, 0)} / {position.max_trays} bandejas"
            )
            self.fields["tray_count"].help_text = (
                "Puede registrar uno o varios productos, pero el total acumulado "
                "de esta posición no puede superar 189 bandejas."
            )

    def clean(self):
        cleaned = super().clean()
        position = cleaned.get("position")
        shift = cleaned.get("shift")
        product = cleaned.get("product")
        tray_count = cleaned.get("tray_count")
        if not self.instance.pk and bool(product) != bool(tray_count):
            raise forms.ValidationError(
                "Seleccione el producto e ingrese las bandejas, o deje ambos campos vacíos."
            )
        if self.production is not None and position is not None:
            timing = PlatePositionTiming.objects.filter(
                production=self.production,
                position=position,
                load_started_at__isnull=False,
            ).first()
            if timing is not None:
                shift = ProductionOrder.Shift.from_datetime(timing.load_started_at)
        shift = shift or getattr(self.instance, "shift", None)
        if not shift and self.production is not None:
            shift = self.production.shift
        cleaned["shift"] = shift
        self.instance.shift = shift
        return cleaned


class PlateCrewProductSelect(forms.Select):
    """Deshabilita las opciones de productos sin bandejas pendientes de asignar."""

    disabled_product_ids = None

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        product = getattr(value, "instance", None)
        if product and self.disabled_product_ids and product.pk in self.disabled_product_ids:
            option["attrs"]["disabled"] = ""
        return option


class PlateCrewEntryForm(StyledModelForm):
    crew_name = forms.CharField(
        label="Cuadrilla",
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "list": "plate-crew-options",
                "autocomplete": "off",
                "placeholder": "Escriba para buscar la cuadrilla",
            }
        ),
        help_text="Escriba parte del nombre. Si no existe, créela desde esta misma pantalla.",
    )
    crew = forms.ModelChoiceField(queryset=Crew.objects.none(), widget=forms.HiddenInput(), required=False)

    class Meta:
        model = PlateCrewEntry
        fields = ["position", "product", "crew_name", "crew", "tray_count"]
        widgets = {"product": PlateCrewProductSelect}

    def __init__(self, *args, production=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.production = production
        crews = active_crew_queryset()
        self.fields["crew"].queryset = crews
        self.crew_suggestions = list(crews.values_list("pk", "name"))
        self.fields["product"].required = True
        self.fields["product"].empty_label = "Seleccione el producto envasado"
        self.fields["product"].help_text = (
            "Solo aparecen productos realmente registrados en el plaquero seleccionado."
        )
        if production is not None:
            physical_totals = dict(
                PlateEntry.objects.filter(production=production, is_active=True)
                .values_list("position_id")
                .annotate(total=Sum("tray_count"))
            )
            assigned_totals = dict(
                PlateCrewEntry.objects.filter(production=production, is_active=True)
                .values_list("position_id")
                .annotate(total=Sum("tray_count"))
            )
            position_ids = {
                position_id
                for position_id, physical_total in physical_totals.items()
                if assigned_totals.get(position_id, 0) < physical_total
            }
            if self.instance.pk and self.instance.position_id:
                position_ids.add(self.instance.position_id)
            positions = plate_positions_by_batch(
                self.fields["position"].queryset.filter(
                    template_version=production.template_version,
                    active=True,
                    pk__in=position_ids,
                )
            )
            self.fields["position"].queryset = positions
            self.fields["position"].empty_label = (
                "Seleccione un plaquero pendiente"
                if positions.exists()
                else "Todos los plaqueros fueron asignados"
            )
            self.fields["position"].label_from_instance = lambda position: (
                f"{position.color_marker} {position.operational_label} — "
                f"{physical_totals.get(position.pk, 0)} físicas / "
                f"{assigned_totals.get(position.pk, 0)} asignadas / "
                f"{max(physical_totals.get(position.pk, 0) - assigned_totals.get(position.pk, 0), 0)} pendientes"
            )
            selected_position = (
                self.data.get("position")
                if self.is_bound
                else self.initial.get("position") or self.instance.position_id
            )
            selected_position_id = getattr(selected_position, "pk", selected_position)
            physical_products = PlateEntry.objects.filter(
                production=production,
                is_active=True,
            )
            if str(selected_position_id).isdigit():
                physical_products = physical_products.filter(
                    position_id=int(selected_position_id)
                )
            product_ids = set(physical_products.values_list("product_id", flat=True))
            if self.instance.pk and self.instance.product_id:
                product_ids.add(self.instance.product_id)
            self.fields["product"].queryset = Product.objects.filter(
                pk__in=product_ids,
                active=True,
            ).order_by("description", "code")
            product_availability = {}
            if str(selected_position_id).isdigit():
                position_id = int(selected_position_id)
                position_physical = dict(
                    PlateEntry.objects.filter(
                        production=production,
                        position_id=position_id,
                        is_active=True,
                    )
                    .values_list("product_id")
                    .annotate(total=Sum("tray_count"))
                )
                position_assigned = (
                    PlateCrewEntry.objects.filter(
                        production=production,
                        position_id=position_id,
                        is_active=True,
                    )
                )
                if self.instance.pk:
                    position_assigned = position_assigned.exclude(pk=self.instance.pk)
                position_assigned = dict(
                    position_assigned.values_list("product_id").annotate(
                        total=Sum("tray_count")
                    )
                )
                for product_id in product_ids:
                    physical = position_physical.get(product_id, 0)
                    assigned = position_assigned.get(product_id, 0)
                    product_availability[product_id] = {
                        "physical": physical,
                        "assigned": assigned,
                        "pending": max(physical - assigned, 0),
                    }
            self._product_availability = product_availability
            self.fields["product"].widget.disabled_product_ids = {
                product_id
                for product_id, availability in product_availability.items()
                if availability["pending"] <= 0
            }
            self.fields["product"].label_from_instance = self._product_label
            self.fields["tray_count"].help_text = (
                "La aplicación controla el total del plaquero y también el total "
                "disponible del producto seleccionado."
            )
        if self.instance.pk and self.instance.crew_id:
            self.fields["crew_name"].initial = self.instance.crew.name
            self.fields["crew"].initial = self.instance.crew_id

    def _product_label(self, product):
        availability = self._product_availability.get(product.pk)
        if availability is None:
            return f"{product.code} — {product.description}"
        if availability["pending"] <= 0:
            return (
                f"{product.code} — {product.description} — COMPLETO"
            )
        return (
            f"{product.code} — {product.description} — "
            f"{availability['pending']} disponibles de {availability['physical']}"
        )

    def clean_crew_name(self):
        return " ".join(self.cleaned_data["crew_name"].strip().upper().split())

    def clean(self):
        cleaned = super().clean()
        self.instance.page = "PAGINA 1"
        name = cleaned.get("crew_name")
        if name:
            crew = active_crew_queryset().filter(name__iexact=name).first()
            if crew is None:
                self.add_error(
                    "crew_name",
                    "Esta cuadrilla todavía no existe. Use «Crear nueva cuadrilla» y luego selecciónela.",
                )
            else:
                cleaned["crew"] = crew
                self.instance.crew = crew
                production_id = (
                    self.production.pk
                    if self.production is not None
                    else getattr(self.instance, "production_id", None)
                )
                if production_id:
                    crew_ids = active_excel_crew_ids(
                        production_id,
                        exclude_plate_pk=self.instance.pk,
                    )
                    crew_ids.add(crew.pk)
                    if len(crew_ids) > EXCEL_CREW_SLOT_LIMIT:
                        self.add_error(
                            "crew_name",
                            (
                                "La plantilla Excel actual admite como máximo "
                                f"{EXCEL_CREW_SLOT_LIMIT} cuadrillas participantes "
                                "entre túneles y placas."
                            ),
                        )
        return cleaned


class TunnelPackagingEntryForm(StyledModelForm):
    class Meta:
        model = TunnelPackagingEntry
        fields = ["pallet_number", "product", "package_count", "observation"]


class PlatePackagingEntryForm(StyledModelForm):
    class Meta:
        model = PlatePackagingEntry
        fields = ["pallet_number", "product", "package_count", "observation"]


class PlatePackagingAllocationForm(StyledModelForm):
    class Meta:
        model = PlatePackagingAllocation
        fields = ["source_entry", "pallet_number", "package_count", "observation"]

    def __init__(self, *args, production=None, position=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.production = production
        queryset = PlateEntry.objects.none()
        if production is not None:
            queryset = (
                PlateEntry.objects.filter(
                    production=production,
                    is_active=True,
                    position__production_timings__production=production,
                    position__production_timings__unloaded_at__isnull=False,
                )
                .select_related("position", "product")
                .distinct()
            )
            if position is not None:
                queryset = queryset.filter(position=position)
            if self.instance.pk and self.instance.source_entry_id:
                queryset = (
                    PlateEntry.objects.filter(
                        Q(pk__in=queryset.values("pk"))
                        | Q(pk=self.instance.source_entry_id)
                    )
                    .select_related("position", "product")
                    .distinct()
                )
            allocated_packages = dict(
                PlatePackagingAllocation.objects.filter(
                    production=production,
                    is_active=True,
                )
                .values_list("source_entry_id")
                .annotate(total=Sum("package_count"))
            )
            package_trays = production.template_version.rules.get(
                "package_trays", 2
            )

            def source_label(source):
                used_trays = allocated_packages.get(source.pk, 0) * package_trays
                pending_trays = max(source.tray_count - used_trays, 0)
                return (
                    f"{source.position.operational_label} · {source.product.code} — "
                    f"{source.product.description} · {pending_trays} bandejas pendientes"
                )

            self.fields["source_entry"].label_from_instance = source_label
        self.fields["source_entry"].queryset = queryset
        self.fields["source_entry"].help_text = (
            "Solo aparecen códigos pertenecientes a plaqueros cuya descarga ya fue registrada."
        )
        self.fields["pallet_number"].help_text = (
            "Seleccione el pallet de la hoja EM-PLA (P1 a P50)."
        )
        self.fields["package_count"].help_text = (
            "Cada bulto equivale a 2 bandejas y 20 kg."
        )

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("source_entry")
        package_count = cleaned.get("package_count")
        if self.production is None or source is None or package_count is None:
            return cleaned
        if source.production_id != self.production.pk or not source.is_active:
            self.add_error(
                "source_entry",
                "El código seleccionado ya no está disponible en este PP.",
            )
            return cleaned
        if not source.position.production_timings.filter(
            production=self.production,
            unloaded_at__isnull=False,
        ).exists():
            self.add_error(
                "source_entry",
                "Primero registre la descarga de este plaquero.",
            )
        other_allocations = PlatePackagingAllocation.objects.filter(
            source_entry=source,
            is_active=True,
        )
        if self.instance.pk:
            other_allocations = other_allocations.exclude(pk=self.instance.pk)
        already_packed = (
            other_allocations.aggregate(total=Sum("package_count"))["total"] or 0
        )
        package_trays = self.production.template_version.rules.get(
            "package_trays", 2
        )
        available_trays = max(
            source.tray_count - already_packed * package_trays,
            0,
        )
        if package_count * package_trays > available_trays:
            self.add_error(
                "package_count",
                (
                    f"Solo quedan {available_trays} bandejas: puede registrar "
                    f"como máximo {available_trays // package_trays} bultos completos."
                ),
            )
        return cleaned


class MaterialUsageForm(StyledModelForm):
    class Meta:
        model = MaterialUsage
        fields = ["material", "quantity", "observation"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        allowed_names = MaterialUsage.EXCEL_INPUT_MATERIAL_NAMES
        display_order = Case(
            *[When(name=name, then=position) for position, name in enumerate(allowed_names)],
            default=len(allowed_names),
            output_field=IntegerField(),
        )
        self.fields["material"].queryset = (
            Material.objects.filter(active=True, name__in=allowed_names)
            .annotate(_excel_input_order=display_order)
            .order_by("_excel_input_order")
        )
        self.fields["material"].label_from_instance = (
            lambda material: "Plumón" if material.name == "Plumones" else material.name
        )
        self.fields["material"].help_text = (
            "Solo se ingresan manualmente Strech film, Rafia, Plumón e Hielo; "
            "los demás insumos los calcula la plantilla de Excel."
        )

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        material = self.cleaned_data.get("material")
        if material and material.unit.strip().lower() == "kg":
            if quantity.as_tuple().exponent < -2:
                raise forms.ValidationError("Cuando la unidad es kg, use como máximo 2 decimales.")
            return quantity.quantize(Decimal("0.01"))
        return quantity


class CostEntryForm(StyledModelForm):
    class Meta:
        model = CostEntry
        fields = ["concept", "quantity", "unit_cost", "rate", "observation"]


class TroqueladoEntryForm(StyledModelForm):
    class Meta:
        model = TroqueladoEntry
        fields = [
            "shift",
            "crew",
            "worker",
            "product_type",
            "cajas",
            "kg_por_caja",
            "start_time",
            "end_time",
            "observation",
        ]

    def __init__(self, *args, crew_id=None, worker_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        workers = Worker.objects.filter(active=True, internal_code__startswith="TROQ-W").order_by("full_name")
        crews = Crew.objects.filter(active=True).filter(
            Q(code__startswith="TROQ-") | Q(workers__in=workers)
        ).distinct().order_by("name")
        if crews.exists():
            self.fields["crew"].queryset = crews
        if crew_id is not None and crews.filter(pk=crew_id).exists():
            self.fields["crew"].disabled = True
            self.fields["crew"].help_text = "Fijada a la cuadrilla seleccionada. Use 'Elegir otra cuadrilla' para cambiarla."
        if worker_queryset is not None and worker_queryset.exists():
            self.fields["worker"].queryset = worker_queryset
        elif workers.exists():
            self.fields["worker"].queryset = workers
        self.fields["product_type"].required = True
        self.fields["product_type"].label = "Tipo de producto"
        self.fields["cajas"].widget.attrs.update(
            {"min": "1", "step": "1", "inputmode": "numeric", "autocomplete": "off"}
        )
        self.fields["kg_por_caja"].widget.attrs.update(
            {"min": "0", "step": "0.01", "inputmode": "decimal", "autocomplete": "off"}
        )
        self.fields["cajas"].label = "N° de cajas"
        self.fields["kg_por_caja"].label = "Peso por caja (kg)"


class CustomerForm(StyledModelForm):
    class Meta:
        model = Customer
        fields = ["name", "tax_id", "active"]


class VehicleForm(StyledModelForm):
    class Meta:
        model = Vehicle
        fields = ["plate", "description", "active"]


class WorkerForm(StyledModelForm):
    class Meta:
        model = Worker
        fields = ["internal_code", "document", "full_name", "crew", "position", "active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["crew"].queryset = self.fields["crew"].queryset.filter(active=True).order_by("name")


class RateForm(StyledModelForm):
    class Meta:
        model = Rate
        fields = ["process", "amount", "unit", "effective_from", "effective_to", "active"]


LAMINA_COLOR_CHOICES = [
    ("", "Sin color asignado"),
    ("AZUL", "Azul"),
    ("NARANJA", "Naranja"),
    ("AMARILLO", "Amarillo"),
    ("VERDE", "Verde"),
    ("ROSADO", "Rosado"),
    ("CRISTAL", "Cristal"),
    ("ROJO", "Rojo"),
    ("ROJO LIMPIO", "Rojo limpio"),
    ("LILA", "Lila"),
    ("BLANCO", "Blanco"),
    ("BLANCO SUCIO", "Blanco sucio"),
    ("CREMA", "Crema"),
]


class ProductLaminaColorForm(StyledModelForm):
    color = forms.ChoiceField(label="Color de lámina", choices=LAMINA_COLOR_CHOICES, required=False)

    class Meta:
        model = Product
        fields = ["color"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["color"].choices = LAMINA_COLOR_CHOICES


class TransitionForm(forms.Form):
    expected_version = forms.IntegerField(widget=forms.HiddenInput)
    target_status = forms.ChoiceField(choices=ProductionOrder.Status.choices, widget=forms.HiddenInput)
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Motivo (obligatorio para reabrir)"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = SPANISH_FIELD_LABELS.get(name, field.label)
