from __future__ import annotations

import hashlib
import re
import unicodedata
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


EXCEL_CREW_SLOT_LIMIT = 11


class SoftDeleteQuerySet(models.QuerySet):
    def delete(self):
        return self.update(is_active=False, voided_at=timezone.now())


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    pass


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.get("force_insert"):
            self.version += 1
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"version", "updated_at"}
        return super().save(*args, **kwargs)


class SoftDeleteModel(TimestampedModel):
    is_active = models.BooleanField(default=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="voided_%(class)s_records",
    )
    void_reason = models.TextField(blank=True)
    objects = SoftDeleteManager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False, *, user=None, reason=""):
        self.is_active = False
        self.voided_at = timezone.now()
        self.voided_by = user
        self.void_reason = reason
        self.save(update_fields=["is_active", "voided_at", "voided_by", "void_reason", "version", "updated_at"])
        return 1, {self._meta.label: 1}


class Role(models.Model):
    class Codes(models.TextChoices):
        ADMIN = "ADMIN", "Administrador"
        PRODUCTION_MANAGER = "JEFE_PROD", "Jefe de producción"
        RECEPTION = "RECEPCION", "Responsable de recepción"
        NUQUERAS = "NUQUERAS", "Responsable de nuqueras o perfilado"
        TUNNEL = "TUNEL", "Supervisor de túnel"
        TUNNEL_CREW = "CUAD_TUNEL", "Responsable de bandejas por cuadrilla"
        PLATES = "ENV_PLACAS", "Responsable de envasado en plaqueros"
        PLATE_CREW = "CUAD_PLACAS", "Responsable de cuadrillas de placas"
        TUNNEL_PACK = "EMP_TUNEL", "Responsable de empaque de túneles"
        PLATE_PACK = "EMP_PLACAS", "Responsable de empaque de placas"
        MATERIALS = "MATERIALES", "Responsable de materiales"
        COSTS = "COSTOS", "Responsable de costos"
        TROQUELADO = "TROQUELADO", "Responsable de troquelado"
        MANAGEMENT = "GERENCIA", "Gerencia o consulta"
        AUDITOR = "AUDITOR", "Auditor"

    code = models.CharField(max_length=24, choices=Codes.choices, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class User(AbstractUser):
    class RegistrationStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Activa"
        PENDING = "PENDING", "Pendiente de aprobación"
        REJECTED = "REJECTED", "Rechazada"

    roles = models.ManyToManyField(Role, blank=True, related_name="users")
    registration_status = models.CharField(
        max_length=12,
        choices=RegistrationStatus.choices,
        default=RegistrationStatus.PENDING,
    )
    requested_role = models.CharField(max_length=24, choices=Role.Codes.choices, blank=True)
    approved_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_users",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    def has_role(self, *codes: str) -> bool:
        return self.is_superuser or self.roles.filter(code__in=codes).exists()


class Customer(TimestampedModel):
    name = models.CharField(max_length=160, unique=True)
    tax_id = models.CharField(max_length=24, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


def template_upload_to(instance, filename):
    return f"templates/{instance.code}/{filename}"


def default_template_rules():
    return {
        "tray_kg": 10,
        "rack_max_trays": 50,
        "plate_rack_max_trays": 189,
        "package_trays": 2,
        "package_kg": 20,
        "plate_pallet_package_capacity": 56,
        "tunnel_pallet_max": None,
        "plate_pallet_max": None,
    }


class TemplateVersion(TimestampedModel):
    code = models.CharField(max_length=30, unique=True)
    file = models.FileField(upload_to=template_upload_to)
    original_filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64, unique=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="template_versions")
    active = models.BooleanField(default=True)
    observations = models.TextField(blank=True)
    mapping_version = models.CharField(max_length=30, default="v1")
    rules = models.JSONField(default=default_template_rules)

    def __str__(self):
        return self.code

    def clean(self):
        super().clean()
        if self.file:
            filename = self.original_filename or self.file.name
            if Path(filename).suffix.lower() != ".xlsm":
                raise ValidationError({"file": "La plantilla debe tener extensión .xlsm."})
            size = getattr(self.file, "size", None)
            if size is not None and size > settings.MAX_TEMPLATE_SIZE:
                raise ValidationError({"file": f"La plantilla supera el máximo de {settings.MAX_TEMPLATE_SIZE // (1024 * 1024)} MB."})

    def save(self, *args, **kwargs):
        if self.file and getattr(self.file, "_file", None) is not None:
            digest = hashlib.sha256()
            for chunk in self.file.chunks():
                digest.update(chunk)
            self.sha256 = digest.hexdigest()
            if not self.original_filename:
                self.original_filename = Path(self.file.name).name
        return super().save(*args, **kwargs)


class Product(TimestampedModel):
    code = models.CharField(max_length=100)
    description = models.CharField(max_length=220)
    color = models.CharField(max_length=30, blank=True)
    presentation = models.CharField(max_length=100, blank=True)
    standard_weight_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    plus_weight_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    packaging_weight_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["description", "code"]
        constraints = [
            models.UniqueConstraint(fields=["description", "code"], name="uniq_product_description_code"),
        ]

    def __str__(self):
        return f"{self.code} — {self.description}"

    @property
    def lamina_color(self):
        """Nombre normalizado de la lámina de identificación del producto."""
        return " ".join((self.color or "").strip().upper().split())


class Crew(TimestampedModel):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    active = models.BooleanField(default=True)

    @staticmethod
    def normalized_name(value):
        normalized = unicodedata.normalize("NFD", (value or "").strip().upper())
        normalized = "".join(
            character
            for character in normalized
            if unicodedata.category(character) != "Mn"
        )
        return " ".join(normalized.split())

    @staticmethod
    def area_key(code):
        """Prefijo del área derivado del código (sin dígitos), p. ej. TROQ-, NUQ-, CUAD-."""
        return re.sub(r"[0-9]+", "", (code or "").strip())

    def clean(self):
        super().clean()
        normalized = self.normalized_name(self.name)
        if not normalized:
            return
        area = self.area_key(self.code)
        duplicate = next(
            (
                crew
                for crew in Crew.objects.exclude(pk=self.pk).only("pk", "name", "code")
                if self.area_key(crew.code) == area
                and self.normalized_name(crew.name) == normalized
            ),
            None,
        )
        if duplicate is None:
            return
        original_name = (
            Crew.objects.filter(pk=self.pk).values_list("name", flat=True).first()
            if self.pk
            else None
        )
        if original_name and self.normalized_name(original_name) == normalized:
            return
        raise ValidationError(
            {"name": f"Esta cuadrilla ya existe como «{duplicate.name}». Use la existente."}
        )

    def __str__(self):
        return self.name


class Worker(TimestampedModel):
    internal_code = models.CharField(max_length=30, unique=True)
    document = models.CharField(max_length=20, blank=True)
    full_name = models.CharField(max_length=180)
    crew = models.ForeignKey(Crew, null=True, blank=True, on_delete=models.PROTECT, related_name="workers")
    position = models.CharField(max_length=100, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.full_name


class ProductionOrder(TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        OPEN = "OPEN", "Abierta"
        IN_PROGRESS = "IN_PROGRESS", "En proceso"
        REVIEW = "REVIEW", "Pendiente de revisión"
        OBSERVED = "OBSERVED", "Observada"
        APPROVED = "APPROVED", "Aprobada"
        CLOSED = "CLOSED", "Cerrada"
        VOID = "VOID", "Anulada"

    class Shift(models.TextChoices):
        DAY = "DAY", "Día"
        AFTERNOON = "AFTERNOON", "Tarde"
        NIGHT = "NIGHT", "Noche"
        MIXED = "MIXED", "Mixto"

        @classmethod
        def from_datetime(cls, value):
            """Classify an aware datetime using the plant's local timezone."""
            hour = timezone.localtime(value).hour
            if 6 <= hour < 14:
                return cls.DAY
            if 14 <= hour < 22:
                return cls.AFTERNOON
            return cls.NIGHT

    number = models.PositiveIntegerField(db_index=True)
    plant_lot = models.CharField(max_length=80, db_index=True)
    customer_lot = models.CharField(max_length=80, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="production_orders")
    process = models.CharField(max_length=120)
    main_product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="main_production_orders")
    reception_date = models.DateField()
    production_date = models.DateField()
    packaging_date = models.DateField(null=True, blank=True)
    shift = models.CharField(max_length=10, choices=Shift.choices)
    series = models.CharField(max_length=40, blank=True)
    vehicle_notes = models.CharField(max_length=255, blank=True)
    plate_notes = models.CharField(max_length=255, blank=True)
    observations = models.TextField(blank=True)
    template_version = models.ForeignKey(TemplateVersion, on_delete=models.PROTECT, related_name="production_orders")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_production_orders")
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-production_date", "-number"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"],
                condition=~Q(status="VOID"),
                name="uniq_active_production_number",
            )
        ]

    def __str__(self):
        return f"PP {self.number} · {self.plant_lot}"


class Vehicle(TimestampedModel):
    plate = models.CharField(max_length=20, unique=True)
    description = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.plate


class Tunnel(models.Model):
    code = models.CharField(max_length=2, unique=True)
    name = models.CharField(max_length=60)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class AreaAssignment(TimestampedModel):
    class Area(models.TextChoices):
        RECEPTION = "RECEPTION", "Recepción"
        NUQUERAS = "NUQUERAS", "Nuqueras"
        TUNNEL = "TUNNEL", "Túnel"
        TUNNEL_CREW = "TUNNEL_CREW", "Cuadrillas de túnel"
        PLATES = "PLATES", "Envasado en placas"
        PLATE_CREW = "PLATE_CREW", "Cuadrillas de placas"
        TUNNEL_PACK = "TUNNEL_PACK", "Empaque de túneles"
        PLATE_PACK = "PLATE_PACK", "Empaque de placas"
        MATERIALS = "MATERIALS", "Materiales"
        COSTS = "COSTS", "Costos"
        TROQUELADO = "TROQUELADO", "Troquelado"

    production = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="area_assignments")
    area = models.CharField(max_length=20, choices=Area.choices)
    shift = models.CharField(max_length=10, choices=ProductionOrder.Shift.choices)
    tunnel = models.ForeignKey(Tunnel, null=True, blank=True, on_delete=models.PROTECT, related_name="assignments")
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["production", "user", "area", "shift", "tunnel"], name="uniq_area_assignment"),
            models.CheckConstraint(condition=Q(area="TUNNEL", tunnel__isnull=False) | ~Q(area="TUNNEL"), name="tunnel_required_for_tunnel_area"),
        ]


class OperationalRecord(SoftDeleteModel):
    production = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT)
    responsible = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    observation = models.TextField(blank=True)

    class Meta:
        abstract = True


class ReceptionEntry(OperationalRecord):
    date = models.DateField()
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT)
    car_number = models.CharField(max_length=30, blank=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    crew = models.ForeignKey(Crew, null=True, blank=True, on_delete=models.PROTECT)
    container = models.CharField(max_length=40, blank=True)
    weight_kg = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    time = models.TimeField(null=True, blank=True)

    def clean(self):
        super().clean()
        if not self.production_id or not self.vehicle_id or not self.is_active:
            return

        errors = {}
        car_number = (self.car_number or "").strip()
        container = (self.container or "").strip()
        active_entries = ReceptionEntry.objects.filter(
            production_id=self.production_id,
            is_active=True,
        ).exclude(pk=self.pk)

        if not car_number:
            errors["car_number"] = "Ingrese el número de carro para ubicarlo correctamente en R.M."
        elif not car_number.isdigit() or not 1 <= int(car_number) <= 9:
            errors["car_number"] = "Ingrese un número de carro del 1 al 9."
        else:
            another_vehicle = active_entries.filter(
                car_number__iexact=car_number,
            ).exclude(vehicle_id=self.vehicle_id).select_related("vehicle").first()
            if another_vehicle:
                errors["car_number"] = (
                    f"El carro {car_number} ya pertenece al vehículo "
                    f"{another_vehicle.vehicle.plate}. Use otro número de carro."
                )

        vehicle_entries = active_entries.filter(vehicle_id=self.vehicle_id)
        if car_number and vehicle_entries.exclude(car_number="").exclude(
            car_number__iexact=car_number
        ).exists():
            current_car = vehicle_entries.exclude(car_number="").values_list(
                "car_number", flat=True
            ).first()
            errors["car_number"] = (
                f"El vehículo {self.vehicle.plate} ya está registrado como carro "
                f"{current_car}. Corrija ese carro o use el mismo número."
            )

        if self.product_id and vehicle_entries.exclude(product_id=self.product_id).exists():
            errors["product"] = (
                f"El vehículo {self.vehicle.plate} ya tiene otro producto. "
                "R.M admite un solo producto por carro."
            )

        if container and (
            used_dino := vehicle_entries.filter(container=container)
            .select_related("crew")
            .first()
        ):
            crew_name = used_dino.crew.name if used_dino.crew_id else "sin cuadrilla"
            errors["container"] = (
                f"El Dino {container} ya fue registrado en este carro por la cuadrilla "
                f"{crew_name}. Cada dino pertenece a una sola cuadrilla."
            )

        crew_ids = set(
            vehicle_entries.exclude(crew_id=None).values_list("crew_id", flat=True)
        )
        if self.crew_id:
            crew_ids.add(self.crew_id)
        if len(crew_ids) > 2:
            errors["crew"] = "R.M permite como máximo dos cuadrillas por carro."

        existing_vehicle_ids = set(active_entries.values_list("vehicle_id", flat=True))
        if self.vehicle_id not in existing_vehicle_ids and len(existing_vehicle_ids) >= 9:
            errors["car_number"] = "R.M permite como máximo 9 carros por parte de producción."

        if errors:
            raise ValidationError(errors)


class ReceptionCarTiming(TimestampedModel):
    """One automatic start/end clock for all dinos captured in a car."""

    production = models.ForeignKey(
        ProductionOrder,
        on_delete=models.PROTECT,
        related_name="reception_car_timings",
    )
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT)
    car_number = models.CharField(max_length=30, blank=True)
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.PROTECT)
    crews = models.ManyToManyField(Crew, blank=True, related_name="reception_car_timings")
    started_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="closed_reception_cars",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["production", "vehicle"],
                name="uniq_reception_car_timing",
            )
        ]


class NuqueraEntry(OperationalRecord):
    date = models.DateField()
    shift = models.CharField(max_length=10, choices=ProductionOrder.Shift.choices)
    crew = models.ForeignKey(Crew, on_delete=models.PROTECT)
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT)
    process = models.CharField(max_length=100)
    weight_kg = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    start_time = models.TimeField()
    end_time = models.TimeField()

    def clean(self):
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "La hora de término debe ser posterior al inicio."})


class TunnelFill(TimestampedModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Abierta"
        CLOSED = "CLOSED", "Cerrada"
        REOPENED = "REOPENED", "Reabierta"

    production = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="tunnel_fills")
    tunnel = models.ForeignKey(Tunnel, on_delete=models.PROTECT, related_name="fills")
    fill_number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(2)])
    date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    launch_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    supervisor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="supervised_tunnel_fills")
    observation = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    closed_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="voided_tunnel_fills",
    )
    void_reason = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["production", "tunnel", "fill_number"],
                condition=Q(is_active=True),
                name="uniq_active_tunnel_fill",
            )
        ]
        ordering = ["tunnel__code", "fill_number"]

    def __str__(self):
        return f"{self.production} · {self.tunnel} · llenada {self.fill_number}"


class TunnelRack(TimestampedModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Abierto"
        CLOSED = "CLOSED", "Cerrado"

    fill = models.ForeignKey(TunnelFill, on_delete=models.PROTECT, related_name="racks")
    code = models.CharField(max_length=20)
    position_key = models.CharField(max_length=80, help_text="Clave exacta del mapa Excel")
    max_trays = models.PositiveSmallIntegerField(default=50)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="closed_tunnel_racks",
    )
    close_reason = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["fill", "code"], name="uniq_rack_per_fill")]

    def __str__(self):
        return f"{self.fill.tunnel.code}/{self.fill.fill_number}/{self.code}"

    def clean(self):
        if self.max_trays not in {49, 50, 70}:
            raise ValidationError(
                {"max_trays": "La capacidad del rack debe ser 49, 50 o 70 bandejas."}
            )


class TunnelEntry(OperationalRecord):
    rack = models.ForeignKey(TunnelRack, on_delete=models.PROTECT, related_name="entries")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="tunnel_entries")
    tray_count = models.PositiveSmallIntegerField()
    date = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["rack", "product"], condition=Q(is_active=True), name="uniq_active_product_per_rack"),
        ]

    def clean(self):
        if self.production_id and self.rack_id and self.production_id != self.rack.fill.production_id:
            raise ValidationError("El rack no pertenece a la producción indicada.")
        if self.rack_id:
            if self.rack.status == TunnelRack.Status.CLOSED:
                raise ValidationError({"rack": "El rack está cerrado. Reábralo antes de registrar bandejas."})
            maximum = self.rack.max_trays
            others = self.rack.entries.filter(is_active=True).exclude(pk=self.pk).aggregate(total=models.Sum("tray_count"))["total"] or 0
            if others + self.tray_count > maximum:
                raise ValidationError({"tray_count": f"El rack supera el máximo configurable de {maximum} bandejas (actual: {others})."})


class TunnelCrewEntry(OperationalRecord):
    fill = models.ForeignKey(TunnelFill, on_delete=models.PROTECT, related_name="crew_entries")
    rack = models.ForeignKey(
        TunnelRack,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="crew_entries",
    )
    product = models.ForeignKey(
        Product,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tunnel_crew_entries",
    )
    crew = models.ForeignKey(Crew, on_delete=models.PROTECT)
    page_or_block = models.CharField(max_length=50)
    tray_count = models.PositiveIntegerField()
    date = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["fill", "rack", "product", "crew"],
                condition=Q(is_active=True, rack__isnull=False),
                name="uniq_active_tunnel_rack_product_crew",
            ),
            models.UniqueConstraint(
                fields=["fill", "crew", "page_or_block"],
                condition=Q(is_active=True, rack__isnull=True),
                name="uniq_legacy_tunnel_crew_block",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.production_id and self.fill_id and self.production_id != self.fill.production_id:
            errors["fill"] = "La llenada no pertenece al PP seleccionado."
        if self.rack_id:
            if self.fill_id and self.rack.fill_id != self.fill_id:
                errors["rack"] = "El rack no pertenece a la llenada seleccionada."
            physical_total = (
                self.rack.entries.filter(
                    is_active=True,
                    **({"product_id": self.product_id} if self.product_id else {}),
                ).aggregate(total=models.Sum("tray_count"))["total"] or 0
            )
            assigned_total = (
                self.rack.crew_entries.filter(is_active=True)
                .exclude(pk=self.pk)
                .filter(**({"product_id": self.product_id} if self.product_id else {}))
                .aggregate(total=models.Sum("tray_count"))["total"]
                or 0
            )
            if self.product_id and not self.rack.entries.filter(is_active=True, product_id=self.product_id).exists():
                errors["product"] = "El producto no pertenece a este rack."
            if self.tray_count is not None and assigned_total + self.tray_count > physical_total:
                errors["tray_count"] = (
                    f"El rack tiene {physical_total} bandejas llenadas y ya se asignaron "
                    f"{assigned_total}. Solo quedan {max(physical_total - assigned_total, 0)} por asignar."
                )
        production_id = self.fill.production_id if self.fill_id else self.production_id
        if production_id and self.crew_id:
            crew_ids = active_excel_crew_ids(
                production_id,
                exclude_tunnel_pk=self.pk,
            )
            crew_ids.add(self.crew_id)
            if len(crew_ids) > EXCEL_CREW_SLOT_LIMIT:
                errors["crew"] = (
                    f"La plantilla Excel actual admite como máximo {EXCEL_CREW_SLOT_LIMIT} cuadrillas "
                    "participantes entre túneles y placas."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Keep the PP derived from the selected tunnel fill.

        ``fill`` is the authoritative relation for this record.  Keeping the
        duplicated ``production`` foreign key in sync prevents a valid crew
        assignment from being visible in the fill screen but omitted from the
        generated workbook.
        """
        if self.fill_id:
            canonical_production_id = TunnelFill.objects.filter(pk=self.fill_id).values_list(
                "production_id", flat=True
            ).first()
            if canonical_production_id is not None:
                self.production_id = canonical_production_id
                update_fields = kwargs.get("update_fields")
                if update_fields is not None:
                    kwargs["update_fields"] = set(update_fields) | {"production"}
        return super().save(*args, **kwargs)

    @property
    def equivalent_kg(self):
        return self.tray_count * 10


class PlatePosition(TimestampedModel):
    class PlateRack(models.TextChoices):
        P1 = "P1", "P1"
        P2 = "P2", "P2"
        P3 = "P3", "P3"

    template_version = models.ForeignKey(TemplateVersion, on_delete=models.PROTECT, related_name="plate_positions")
    plate_rack = models.CharField(max_length=2, choices=PlateRack.choices)
    position_key = models.CharField(max_length=80)
    display_name = models.CharField(max_length=100)
    max_trays = models.PositiveSmallIntegerField(default=189)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["template_version", "plate_rack", "position_key"], name="uniq_plate_position")]

    @property
    def plaquero_number(self):
        match = re.search(r"\d+", self.plate_rack or "")
        return int(match.group()) if match else 0

    @property
    def color_marker(self):
        return {
            1: "🔵",
            2: "🟠",
            3: "🟣",
        }.get(self.plaquero_number, "⚪")

    @property
    def batch_number(self):
        position_match = re.search(r"!([A-Z]+)\d+$", (self.position_key or "").upper())
        if position_match:
            column_number = 0
            for character in position_match.group(1):
                column_number = (column_number * 26) + (ord(character) - ord("A") + 1)
            if 5 <= column_number <= 28:
                return ((column_number - 5) // 3) + 1
        display_name = self.display_name or ""
        match = re.search(r"Bachada\s+(\d+)", display_name, flags=re.IGNORECASE)
        if match is None:
            match = re.search(r"posición\s+(\d+)", display_name, flags=re.IGNORECASE)
        if match is None:
            match = re.search(r"posici[oó]n\s+(\d+)", display_name, flags=re.IGNORECASE)
        return int(match.group(1)) if match else 0

    @property
    def operational_label(self):
        if self.batch_number and self.plaquero_number:
            return f"Bachada {self.batch_number} · Plaquero {self.plaquero_number}"
        return self.display_name

    def __str__(self):
        return self.operational_label


class PlateEntry(OperationalRecord):
    date = models.DateField()
    shift = models.CharField(max_length=10, choices=ProductionOrder.Shift.choices)
    position = models.ForeignKey(PlatePosition, on_delete=models.PROTECT, related_name="entries")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    tray_count = models.PositiveSmallIntegerField()
    crew = models.ForeignKey(Crew, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["production", "position", "product"], condition=Q(is_active=True), name="uniq_plate_product_position")]

    def clean(self):
        errors = {}
        if (
            self.production_id
            and self.position_id
            and self.production.template_version_id != self.position.template_version_id
        ):
            errors["position"] = "La posición pertenece a otra versión de plantilla."
        if self.production_id and self.position_id and self.tray_count is not None and self.is_active:
            other_entries = PlateEntry.objects.filter(
                production_id=self.production_id,
                position_id=self.position_id,
                is_active=True,
            )
            if self.pk:
                other_entries = other_entries.exclude(pk=self.pk)
            other_total = other_entries.aggregate(total=models.Sum("tray_count"))["total"] or 0
            physical_total = other_total + self.tray_count
            if physical_total > self.position.max_trays:
                errors["tray_count"] = (
                    f"Este plaquero admite como máximo {self.position.max_trays} bandejas. "
                    f"Ya tiene {other_total}; solo quedan "
                    f"{max(self.position.max_trays - other_total, 0)} disponibles."
                )
            assigned_total = (
                PlateCrewEntry.objects.filter(
                    production_id=self.production_id,
                    position_id=self.position_id,
                    is_active=True,
                ).aggregate(total=models.Sum("tray_count"))["total"]
                or 0
            )
            if physical_total < assigned_total:
                errors["tray_count"] = (
                    f"No puede dejar el plaquero en {physical_total} bandejas porque "
                    f"ya existen {assigned_total} asignadas a cuadrillas."
                )
            if self.pk:
                packed_packages = (
                    PlatePackagingAllocation.objects.filter(
                        source_entry_id=self.pk,
                        is_active=True,
                    ).aggregate(total=models.Sum("package_count"))["total"]
                    or 0
                )
                automatic_packed_trays = (
                    PlatePalletConsumption.objects.filter(
                        source_entry_id=self.pk,
                        line__is_active=True,
                    ).aggregate(total=models.Sum("tray_count"))["total"]
                    or 0
                )
                package_trays = self.production.template_version.rules.get(
                    "package_trays", 2
                )
                packed_trays = (
                    packed_packages * package_trays
                    + automatic_packed_trays
                )
                if self.tray_count < packed_trays:
                    errors["tray_count"] = (
                        f"Este código ya tiene {packed_trays} bandejas asignadas a empaque. "
                        "No puede reducirlo por debajo de esa cantidad."
                    )
                previous = PlateEntry.objects.filter(pk=self.pk).values(
                    "position_id", "product_id"
                ).first()
                if packed_trays and previous and (
                    previous["position_id"] != self.position_id
                    or previous["product_id"] != self.product_id
                ):
                    errors["position"] = (
                        "No puede cambiar el plaquero ni el producto porque este "
                        "código ya tiene bultos registrados en empaque."
                    )
        if errors:
            raise ValidationError(errors)


class PlatePositionTiming(TimestampedModel):
    production = models.ForeignKey(
        ProductionOrder,
        on_delete=models.PROTECT,
        related_name="plate_position_timings",
    )
    position = models.ForeignKey(
        PlatePosition,
        on_delete=models.PROTECT,
        related_name="production_timings",
    )
    load_started_at = models.DateTimeField(null=True, blank=True)
    load_started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="started_plate_loads",
    )
    load_completed_at = models.DateTimeField(null=True, blank=True)
    load_completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="completed_plate_loads",
    )
    launched_at = models.DateTimeField(null=True, blank=True)
    launched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="registered_plate_launches",
    )
    unloaded_at = models.DateTimeField(null=True, blank=True)
    unloaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="registered_plate_unloads",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["production", "position"],
                name="uniq_plate_position_timing",
            )
        ]

    def clean(self):
        errors = {}
        if (
            self.production_id
            and self.position_id
            and self.production.template_version_id != self.position.template_version_id
        ):
            errors["position"] = "La posición pertenece a otra versión de plantilla."
        if (
            self.load_started_at
            and self.load_completed_at
            and self.load_completed_at < self.load_started_at
        ):
            errors["load_completed_at"] = "El fin de carga no puede ser anterior al inicio del llenado."
        if self.launched_at and not self.load_completed_at:
            errors["launched_at"] = "Primero debe registrarse el fin de carga del plaquero."
        if (
            self.launched_at
            and self.load_completed_at
            and self.launched_at < self.load_completed_at
        ):
            errors["launched_at"] = "El lanzamiento no puede ser anterior al fin de carga."
        if self.unloaded_at and not self.launched_at:
            errors["unloaded_at"] = "Primero debe registrarse el lanzamiento del plaquero."
        if (
            self.unloaded_at
            and self.launched_at
            and self.unloaded_at < self.launched_at
        ):
            errors["unloaded_at"] = "La descarga no puede ser anterior al lanzamiento."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.production} · {self.position.operational_label}"


class PlateCrewEntry(OperationalRecord):
    position = models.ForeignKey(PlatePosition, on_delete=models.PROTECT, related_name="crew_entries")
    page = models.CharField(max_length=50)
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="plate_crew_entries",
        null=True,
        blank=True,
    )
    crew = models.ForeignKey(Crew, on_delete=models.PROTECT)
    tray_count = models.PositiveIntegerField()
    date = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["production", "position", "page", "crew", "product"],
                condition=Q(is_active=True, product__isnull=False),
                name="uniq_plate_crew_product_page",
            )
        ]

    def clean(self):
        errors = {}
        if (
            self.production_id
            and self.position_id
            and self.production.template_version_id != self.position.template_version_id
        ):
            errors["position"] = "La posición pertenece a otra versión de plantilla."
        if self.production_id and self.crew_id:
            crew_ids = active_excel_crew_ids(
                self.production_id,
                exclude_plate_pk=self.pk,
            )
            crew_ids.add(self.crew_id)
            if len(crew_ids) > EXCEL_CREW_SLOT_LIMIT:
                errors["crew"] = (
                    f"La plantilla Excel actual admite como máximo {EXCEL_CREW_SLOT_LIMIT} cuadrillas "
                    "participantes entre túneles y placas."
                )
        if self.production_id and self.position_id and self.tray_count is not None and self.is_active:
            physical_total = (
                PlateEntry.objects.filter(
                    production_id=self.production_id,
                    position_id=self.position_id,
                    is_active=True,
                ).aggregate(total=models.Sum("tray_count"))["total"]
                or 0
            )
            other_entries = PlateCrewEntry.objects.filter(
                production_id=self.production_id,
                position_id=self.position_id,
                is_active=True,
            )
            if self.pk:
                other_entries = other_entries.exclude(pk=self.pk)
            assigned_elsewhere = other_entries.aggregate(total=models.Sum("tray_count"))["total"] or 0
            assigned_total = assigned_elsewhere + self.tray_count
            if assigned_total > physical_total:
                errors["tray_count"] = (
                    f"Este plaquero tiene {physical_total} bandejas realmente envasadas. "
                    f"Ya se asignaron {assigned_elsewhere}; solo quedan "
                    f"{max(physical_total - assigned_elsewhere, 0)} por repartir."
                )
            if self.product_id:
                product_physical_total = (
                    PlateEntry.objects.filter(
                        production_id=self.production_id,
                        position_id=self.position_id,
                        product_id=self.product_id,
                        is_active=True,
                    ).aggregate(total=models.Sum("tray_count"))["total"]
                    or 0
                )
                product_entries = other_entries.filter(product_id=self.product_id)
                product_assigned_elsewhere = (
                    product_entries.aggregate(total=models.Sum("tray_count"))["total"] or 0
                )
                product_assigned_total = product_assigned_elsewhere + self.tray_count
                if product_physical_total <= 0:
                    errors["product"] = (
                        "Este producto no fue registrado dentro del plaquero seleccionado."
                    )
                elif product_assigned_total > product_physical_total:
                    errors["tray_count"] = (
                        f"De {self.product.description} se envasaron "
                        f"{product_physical_total} bandejas. Ya se asignaron "
                        f"{product_assigned_elsewhere}; solo quedan "
                        f"{max(product_physical_total - product_assigned_elsewhere, 0)} "
                        "para repartir entre cuadrillas."
                    )
        if errors:
            raise ValidationError(errors)

    @property
    def equivalent_kg(self):
        return self.tray_count * 10


def active_excel_crew_ids(
    production_id,
    *,
    exclude_tunnel_pk=None,
    exclude_plate_pk=None,
):
    tunnel_entries = TunnelCrewEntry.objects.filter(
        fill__production_id=production_id,
        is_active=True,
    )
    if exclude_tunnel_pk is not None:
        tunnel_entries = tunnel_entries.exclude(pk=exclude_tunnel_pk)
    plate_entries = PlateCrewEntry.objects.filter(
        production_id=production_id,
        is_active=True,
    )
    if exclude_plate_pk is not None:
        plate_entries = plate_entries.exclude(pk=exclude_plate_pk)
    return set(tunnel_entries.values_list("crew_id", flat=True)) | set(
        plate_entries.values_list("crew_id", flat=True)
    )


class PackagingEntry(OperationalRecord):
    date = models.DateField()
    pallet_number = models.PositiveSmallIntegerField()
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    package_count = models.PositiveIntegerField()

    class Meta:
        abstract = True


class TunnelPackagingEntry(PackagingEntry):
    # Bultos empacados por tunel de origen: {"T1": 3, "T2": 5}. La vista de
    # empaque de tuneles lo escribe y lo lee para mostrar el desglose; faltaba
    # el campo en el modelo y eso dejaba el modulo caido con error 500.
    source_breakdown = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["production", "pallet_number", "product"], condition=Q(is_active=True), name="uniq_tunnel_pack_product_pallet"),
        ]

    def clean(self):
        maximum = self.production.template_version.rules.get("tunnel_pallet_max") if self.production_id else None
        if maximum and self.pallet_number > maximum:
            raise ValidationError({"pallet_number": f"La plantilla permite como máximo P{maximum}."})

    @property
    def tray_count(self):
        factor = self.production.template_version.rules.get("package_trays", 2)
        return self.package_count * factor

    @property
    def kilos(self):
        factor = self.production.template_version.rules.get("package_kg", 20)
        return self.package_count * factor


class PlatePackagingEntry(PackagingEntry):
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["production", "pallet_number", "product"], condition=Q(is_active=True), name="uniq_plate_pack_product_pallet"),
        ]

    def clean(self):
        maximum = self.production.template_version.rules.get("plate_pallet_max") if self.production_id else None
        if maximum and self.pallet_number > maximum:
            raise ValidationError({"pallet_number": f"La plantilla permite como máximo P{maximum}."})

    @property
    def tray_count(self):
        factor = self.production.template_version.rules.get("package_trays", 2)
        return self.package_count * factor

    @property
    def kilos(self):
        factor = self.production.template_version.rules.get("package_kg", 20)
        return self.package_count * factor


class PlatePackagingAllocation(OperationalRecord):
    """Bultos empacados conservando el plaquero y código de procedencia."""

    date = models.DateField()
    source_entry = models.ForeignKey(
        PlateEntry,
        on_delete=models.PROTECT,
        related_name="packaging_allocations",
    )
    pallet_number = models.PositiveSmallIntegerField()
    package_count = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["production", "source_entry", "pallet_number"],
                condition=Q(is_active=True),
                name="uniq_plate_pack_source_pallet",
            ),
        ]

    def clean(self):
        errors = {}
        if self.production_id and self.source_entry_id:
            if self.source_entry.production_id != self.production_id:
                errors["source_entry"] = "El código seleccionado pertenece a otro PP."
            if not self.source_entry.is_active:
                errors["source_entry"] = "El código seleccionado fue eliminado."
            timing = PlatePositionTiming.objects.filter(
                production_id=self.production_id,
                position_id=self.source_entry.position_id,
                unloaded_at__isnull=False,
            ).first()
            if timing is None:
                errors["source_entry"] = (
                    "Primero registre la descarga del plaquero antes de empacar sus códigos."
                )
        maximum = (
            self.production.template_version.rules.get("plate_pallet_max")
            if self.production_id
            else None
        )
        if maximum and self.pallet_number > maximum:
            errors["pallet_number"] = f"La plantilla permite como máximo P{maximum}."
        if (
            self.production_id
            and self.source_entry_id
            and self.package_count is not None
            and self.is_active
        ):
            other_packages = PlatePackagingAllocation.objects.filter(
                source_entry_id=self.source_entry_id,
                is_active=True,
            )
            if self.pk:
                other_packages = other_packages.exclude(pk=self.pk)
            already_packed = (
                other_packages.aggregate(total=models.Sum("package_count"))["total"]
                or 0
            )
            package_trays = self.production.template_version.rules.get(
                "package_trays", 2
            )
            automatic_packed_trays = (
                PlatePalletConsumption.objects.filter(
                    source_entry_id=self.source_entry_id,
                    line__is_active=True,
                ).aggregate(total=models.Sum("tray_count"))["total"]
                or 0
            )
            requested_trays = (
                (already_packed + self.package_count) * package_trays
                + automatic_packed_trays
            )
            if requested_trays > self.source_entry.tray_count:
                available_trays = max(
                    self.source_entry.tray_count
                    - already_packed * package_trays
                    - automatic_packed_trays,
                    0,
                )
                errors["package_count"] = (
                    f"Este código tiene {self.source_entry.tray_count} bandejas. "
                    f"Solo quedan {available_trays} bandejas disponibles para empacar "
                    f"({available_trays // package_trays} bultos completos)."
                )
        if errors:
            raise ValidationError(errors)

    @property
    def product(self):
        return self.source_entry.product

    @property
    def tray_count(self):
        factor = self.production.template_version.rules.get("package_trays", 2)
        return self.package_count * factor

    @property
    def kilos(self):
        factor = self.production.template_version.rules.get("package_kg", 20)
        return self.package_count * factor


class PlatePallet(SoftDeleteModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Abierto"
        CLOSED = "CLOSED", "Cerrado"

    production = models.ForeignKey(
        ProductionOrder,
        on_delete=models.PROTECT,
        related_name="plate_pallets",
    )
    pallet_number = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="closed_plate_pallets",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["production", "pallet_number"],
                condition=Q(is_active=True),
                name="uniq_active_plate_pallet",
            )
        ]

    @property
    def max_packages(self):
        return self.production.template_version.rules.get(
            "plate_pallet_package_capacity",
            56,
        )

    def clean(self):
        maximum = (
            self.production.template_version.rules.get("plate_pallet_max")
            if self.production_id
            else None
        )
        if maximum and self.pallet_number > maximum:
            raise ValidationError(
                {"pallet_number": f"La plantilla permite como máximo P{maximum}."}
            )

    def __str__(self):
        return f"{self.production} · P{self.pallet_number}"


class PlatePalletLine(OperationalRecord):
    date = models.DateField()
    pallet = models.ForeignKey(
        PlatePallet,
        on_delete=models.PROTECT,
        related_name="lines",
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    package_count = models.PositiveIntegerField()

    def clean(self):
        errors = {}
        if self.production_id and self.pallet_id:
            if self.pallet.production_id != self.production_id:
                errors["pallet"] = "El pallet pertenece a otro PP."
            if self.pallet.status == PlatePallet.Status.CLOSED:
                errors["pallet"] = "El pallet está cerrado. Reábralo para corregirlo."
            capacity = int(
                self.production.template_version.rules.get(
                    "plate_pallet_package_capacity",
                    56,
                )
            )
            legacy_packages = (
                PlatePackagingEntry.objects.filter(
                    production_id=self.production_id,
                    pallet_number=self.pallet.pallet_number,
                    is_active=True,
                ).aggregate(total=models.Sum("package_count"))["total"]
                or 0
            )
            manual_packages = (
                PlatePackagingAllocation.objects.filter(
                    production_id=self.production_id,
                    pallet_number=self.pallet.pallet_number,
                    is_active=True,
                ).aggregate(total=models.Sum("package_count"))["total"]
                or 0
            )
            other_lines = PlatePalletLine.objects.filter(
                production_id=self.production_id,
                pallet_id=self.pallet_id,
                is_active=True,
            )
            if self.pk:
                other_lines = other_lines.exclude(pk=self.pk)
            automatic_packages = (
                other_lines.aggregate(total=models.Sum("package_count"))["total"]
                or 0
            )
            if (
                self.package_count is not None
                and legacy_packages
                + manual_packages
                + automatic_packages
                + self.package_count
                > capacity
            ):
                errors["package_count"] = (
                    f"El pallet admite como máximo {capacity} bultos."
                )
        if errors:
            raise ValidationError(errors)

    @property
    def tray_count(self):
        factor = self.production.template_version.rules.get("package_trays", 2)
        return self.package_count * factor

    @property
    def kilos(self):
        factor = self.production.template_version.rules.get("package_kg", 20)
        return self.package_count * factor


class PlateCarryoverBalance(SoftDeleteModel):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Disponible"
        CONSUMED = "CONSUMED", "Utilizado"
        HELD = "HELD", "Retenido"
        WASTE = "WASTE", "Merma"
        CANCELLED = "CANCELLED", "Cancelado por reapertura"

    origin_production = models.ForeignKey(
        ProductionOrder,
        on_delete=models.PROTECT,
        related_name="generated_plate_balances",
    )
    source_entry = models.OneToOneField(
        PlateEntry,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="carryover_balance",
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    initial_trays = models.PositiveSmallIntegerField()
    available_trays = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.AVAILABLE,
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="generated_plate_balances",
    )
    generated_at = models.DateTimeField(default=timezone.now)
    last_used_in_production = models.ForeignKey(
        ProductionOrder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="used_plate_balances",
    )
    last_used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="used_plate_balances",
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    observation = models.TextField(blank=True)

    def clean(self):
        errors = {}
        if self.source_entry_id:
            if self.source_entry.production_id != self.origin_production_id:
                errors["source_entry"] = "El origen del saldo no coincide con el PP."
            if self.source_entry.product_id != self.product_id:
                errors["product"] = "El producto no coincide con el código de origen."
        if self.available_trays > self.initial_trays:
            errors["available_trays"] = "El saldo disponible no puede superar el saldo inicial."
        if errors:
            raise ValidationError(errors)

    @property
    def available_kg(self):
        return self.available_trays * Decimal("10.00")

    def __str__(self):
        return (
            f"{self.product.code} · {self.available_trays} bandejas · "
            f"PP {self.origin_production.number}"
        )


class PlatePalletConsumption(TimestampedModel):
    line = models.ForeignKey(
        PlatePalletLine,
        on_delete=models.PROTECT,
        related_name="consumptions",
    )
    source_entry = models.ForeignKey(
        PlateEntry,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pallet_consumptions",
    )
    carryover_balance = models.ForeignKey(
        PlateCarryoverBalance,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pallet_consumptions",
    )
    tray_count = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(source_entry__isnull=False, carryover_balance__isnull=True)
                    | Q(source_entry__isnull=True, carryover_balance__isnull=False)
                ),
                name="plate_consumption_has_one_origin",
            )
        ]

    def clean(self):
        errors = {}
        origin_product_id = None
        if self.source_entry_id:
            origin_product_id = self.source_entry.product_id
            if self.source_entry.production_id != self.line.production_id:
                errors["source_entry"] = "El código físico pertenece a otro PP."
        if self.carryover_balance_id:
            origin_product_id = self.carryover_balance.product_id
        if origin_product_id and origin_product_id != self.line.product_id:
            errors["line"] = "El consumo no pertenece al producto del pallet."
        if bool(self.source_entry_id) == bool(self.carryover_balance_id):
            errors["source_entry"] = "Seleccione exactamente un origen para las bandejas."
        if errors:
            raise ValidationError(errors)


class Material(TimestampedModel):
    name = models.CharField(max_length=100, unique=True)
    unit = models.CharField(max_length=30)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class MaterialUsage(OperationalRecord):
    EXCEL_INPUT_MATERIAL_NAMES = ("Strech film", "Rafia", "Plumones", "Hielo")

    material = models.ForeignKey(Material, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(0)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["production", "material"], condition=Q(is_active=True), name="uniq_material_usage")]


class Rate(TimestampedModel):
    process = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])
    unit = models.CharField(max_length=30)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["process", "-effective_from"]

    def __str__(self):
        return f"{self.process} · {self.amount} {self.unit}"


class CostEntry(OperationalRecord):
    concept = models.CharField(max_length=160)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(0)])
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])
    rate = models.ForeignKey(Rate, null=True, blank=True, on_delete=models.PROTECT)

    @property
    def total(self):
        return self.quantity * self.unit_cost


class TroqueladoEntry(OperationalRecord):
    class ProductType(models.TextChoices):
        ANILLAS_BLANCAS = "ANILLAS BLANCAS", "Anillas blancas"
        MORDIDAS_BLANCAS = "MORDIDAS BLANCAS", "Mordidas blancas"
        ANILLAS_AMARILLAS = "ANILLAS AMARILLAS", "Anillas amarillas"
        MORDIDAS_AMARILLAS = "MORDIDAS AMARILLAS", "Mordidas amarillas"
        BOTON = "BOTÓN", "Botón"
        RECORTE = "RECORTE", "Recorte"

    date = models.DateField()
    shift = models.CharField(max_length=10, choices=ProductionOrder.Shift.choices)
    crew = models.ForeignKey(Crew, on_delete=models.PROTECT, related_name="troquelado_entries")
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT)
    product_type = models.CharField(
        max_length=40,
        choices=ProductType.choices,
        null=True,
        blank=True,
    )
    cajas = models.IntegerField(validators=[MinValueValidator(1)])
    kg_por_caja = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    weight_kg = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    start_time = models.TimeField()
    end_time = models.TimeField()

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "La hora de término debe ser posterior al inicio."})
        if self.cajas and self.kg_por_caja is not None:
            self.weight_kg = (Decimal(self.cajas) * self.kg_por_caja).quantize(Decimal("0.01"))
        if self.crew_id and self.worker_id and self.worker.crew_id and self.worker.crew_id != self.crew_id:
            raise ValidationError(
                {"crew": "El trabajador pertenece a otra cuadrilla. Corrija la cuadrilla o el trabajador."}
            )


class Approval(TimestampedModel):
    class Decision(models.TextChoices):
        APPROVE = "APPROVE", "Aprobar"
        OBSERVE = "OBSERVE", "Observar"
        REOPEN = "REOPEN", "Reabrir"

    production = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="approvals")
    module = models.CharField(max_length=40)
    decision = models.CharField(max_length=10, choices=Decision.choices)
    reason = models.TextField(blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class Observation(TimestampedModel):
    production = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="review_observations")
    module = models.CharField(max_length=40)
    text = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_observations")
    resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="resolved_observations")
    resolved_at = models.DateTimeField(null=True, blank=True)


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "CREATE", "Creación"
        UPDATE = "UPDATE", "Modificación"
        VOID = "VOID", "Anulación"
        TRANSITION = "TRANSITION", "Cambio de estado"
        DOWNLOAD = "DOWNLOAD", "Descarga"
        GENERATE = "GENERATE", "Generación"
        LOGIN = "LOGIN", "Inicio de sesión"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    production = models.ForeignKey(ProductionOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_logs")
    module = models.CharField(max_length=50)
    model_name = models.CharField(max_length=100, blank=True)
    record_pk = models.CharField(max_length=80, blank=True)
    action = models.CharField(max_length=20, choices=Action.choices)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp"]


def generated_upload_to(instance, filename):
    return f"generated/{instance.production_id}/{filename}"


class GeneratedFile(TimestampedModel):
    class Kind(models.TextChoices):
        PRELIMINARY = "PRELIMINARY", "Preliminar"
        FINAL = "FINAL", "Final"

    production = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="generated_files")
    template_version = models.ForeignKey(TemplateVersion, on_delete=models.PROTECT)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    sequence = models.PositiveIntegerField(default=1)
    file = models.FileField(upload_to=generated_upload_to)
    filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    integrity_report = models.JSONField(default=dict)
    valid = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["production", "kind", "sequence"], name="uniq_generated_sequence")]
        ordering = ["-created_at"]


class ExcelCellMapping(TimestampedModel):
    template_version = models.ForeignKey(TemplateVersion, on_delete=models.PROTECT, related_name="cell_mappings")
    sheet = models.CharField(max_length=100)
    module = models.CharField(max_length=60)
    field_key = models.CharField(max_length=160)
    cell_or_range = models.CharField(max_length=80)
    data_type = models.CharField(max_length=30)
    editable = models.BooleanField(default=False)
    contains_formula = models.BooleanField(default=False)
    validation_rule = models.JSONField(default=dict, blank=True)
    conversions = models.JSONField(default=dict, blank=True)
    dependencies = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["template_version", "sheet", "field_key", "cell_or_range"], name="uniq_excel_cell_mapping")
        ]


class TunnelManualBalance(SoftDeleteModel):
    production = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="tunnel_manual_balances")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class TunnelPackWorker(SoftDeleteModel):
    tunnel_entry = models.ForeignKey("TunnelEntry", on_delete=models.CASCADE, related_name="pack_workers")
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class PlatePackWorker(SoftDeleteModel):
    plate_entry = models.ForeignKey("PlateEntry", on_delete=models.CASCADE, related_name="pack_workers")
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
