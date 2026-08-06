from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from . import models


@admin.register(models.User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        (
            "Planta",
            {
                "fields": (
                    "roles",
                    "registration_status",
                    "requested_role",
                    "approved_by",
                    "approved_at",
                    "failed_login_attempts",
                    "locked_until",
                )
            },
        ),
    )
    filter_horizontal = UserAdmin.filter_horizontal + ("roles",)
    readonly_fields = ("approved_by", "approved_at")


@admin.register(models.ProductionOrder)
class ProductionOrderAdmin(admin.ModelAdmin):
    list_display = ("number", "plant_lot", "customer", "production_date", "shift", "status", "template_version")
    list_filter = ("status", "shift", "template_version")
    search_fields = ("number", "plant_lot", "customer_lot", "customer__name")
    list_select_related = ("customer", "main_product", "template_version")


@admin.register(models.AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "user", "production", "module", "action", "model_name", "record_pk")
    list_filter = ("action", "module", "timestamp")
    search_fields = ("production__plant_lot", "user__username", "record_pk")
    readonly_fields = tuple(field.name for field in models.AuditLog._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


for model in (
    models.Role, models.Customer, models.TemplateVersion, models.Product, models.Crew,
    models.Worker, models.Vehicle, models.Tunnel, models.AreaAssignment,
    models.ReceptionEntry, models.NuqueraEntry, models.TunnelFill, models.TunnelRack,
    models.TunnelEntry, models.TunnelCrewEntry, models.PlatePosition, models.PlateEntry,
    models.PlatePositionTiming, models.PlateCrewEntry, models.TunnelPackagingEntry, models.PlatePackagingEntry,
    models.PlatePackagingAllocation,
    models.PlatePallet, models.PlatePalletLine, models.PlatePalletConsumption,
    models.PlateCarryoverBalance,
    models.Material, models.MaterialUsage, models.Rate, models.CostEntry,
    models.Approval, models.Observation, models.GeneratedFile, models.ExcelCellMapping,
    models.TroqueladoEntry,
):
    admin.site.register(model)
