from __future__ import annotations

import hashlib
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


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
        default=RegistrationStatus.ACTIVE,
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


class Crew(TimestampedModel):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100, unique=True)
    active = models.BooleanField(default=True)

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
        NIGHT = "NIGHT", "Noche"
        MIXED = "MIXED", "Mixto"

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

        if container and vehicle_entries.filter(
            crew_id=self.crew_id,
            container=container,
        ).exists():
            crew_name = self.crew.name if self.crew_id else "sin cuadrilla"
            errors["container"] = (
                f"El Dino {container} ya fue registrado para la cuadrilla "
                f"{crew_name} en este carro."
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

    class Meta:
        constraints = [models.UniqueConstraint(fields=["production", "tunnel", "fill_number"], name="uniq_tunnel_fill")]
        ordering = ["tunnel__code", "fill_number"]

    def __str__(self):
        return f"{self.production} · {self.tunnel} · llenada {self.fill_number}"


class TunnelRack(TimestampedModel):
    fill = models.ForeignKey(TunnelFill, on_delete=models.PROTECT, related_name="racks")
    code = models.CharField(max_length=20)
    position_key = models.CharField(max_length=80, help_text="Clave exacta del mapa Excel")
    max_trays = models.PositiveSmallIntegerField(default=50)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["fill", "code"], name="uniq_rack_per_fill")]

    def __str__(self):
        return f"{self.fill.tunnel.code}/{self.fill.fill_number}/{self.code}"

    def clean(self):
        if self.max_trays not in {50, 70}:
            raise ValidationError({"max_trays": "La capacidad del rack debe ser 50 o 70 bandejas."})


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
            maximum = self.rack.max_trays
            others = self.rack.entries.filter(is_active=True).exclude(pk=self.pk).aggregate(total=models.Sum("tray_count"))["total"] or 0
            if others + self.tray_count > maximum:
                raise ValidationError({"tray_count": f"El rack supera el máximo configurable de {maximum} bandejas (actual: {others})."})


class TunnelCrewEntry(OperationalRecord):
    fill = models.ForeignKey(TunnelFill, on_delete=models.PROTECT, related_name="crew_entries")
    crew = models.ForeignKey(Crew, on_delete=models.PROTECT)
    page_or_block = models.CharField(max_length=50)
    tray_count = models.PositiveIntegerField()
    date = models.DateField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["fill", "crew", "page_or_block"], condition=Q(is_active=True), name="uniq_tunnel_crew_block")]

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

    def __str__(self):
        return f"{self.plate_rack} · {self.display_name}"


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
        if self.position_id and self.tray_count > self.position.max_trays:
            raise ValidationError({"tray_count": f"Máximo {self.position.max_trays} bandejas para esta posición."})
        if self.production_id and self.position_id and self.production.template_version_id != self.position.template_version_id:
            raise ValidationError({"position": "La posición pertenece a otra versión de plantilla."})


class PlateCrewEntry(OperationalRecord):
    position = models.ForeignKey(PlatePosition, on_delete=models.PROTECT, related_name="crew_entries")
    page = models.CharField(max_length=50)
    crew = models.ForeignKey(Crew, on_delete=models.PROTECT)
    tray_count = models.PositiveIntegerField()
    date = models.DateField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["production", "position", "page", "crew"], condition=Q(is_active=True), name="uniq_plate_crew_page")]

    @property
    def equivalent_kg(self):
        return self.tray_count * 10


class PackagingEntry(OperationalRecord):
    date = models.DateField()
    pallet_number = models.PositiveSmallIntegerField()
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    package_count = models.PositiveIntegerField()

    class Meta:
        abstract = True


class TunnelPackagingEntry(PackagingEntry):
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
