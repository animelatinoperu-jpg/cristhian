# Modelo de base de datos

PostgreSQL es la fuente de verdad. El XLSM es una salida regenerable y nunca recibe edición concurrente.

## Núcleo

- `User`, `Role`: identidad y función.
- `ProductionOrder`: PP, lote único heredado, fechas, estado y versión de plantilla.
- `TemplateVersion`: archivo privado, SHA-256, reglas y versión del mapa.
- `AreaAssignment`: usuario, PP, área, turno y túnel opcional.
- `AuditLog`, `Approval`, `Observation`, `GeneratedFile`: trazabilidad y control.

## Operación

- Recepción: `Vehicle`, `ReceptionEntry`.
- Perfilado: `Crew`, `Worker`, `NuqueraEntry`.
- Túneles: `Tunnel`, `TunnelFill`, `TunnelRack`, `TunnelEntry`, `TunnelCrewEntry`.
- Placas: `PlatePosition`, `PlateEntry`, `PlateCrewEntry`.
- Empaque: `TunnelPackagingEntry`, `PlatePackagingEntry`.
- Materiales y costos: `Material`, `MaterialUsage`, `Rate`, `CostEntry`.

Todos los registros operativos incluyen responsable, marcas de tiempo, versión optimista, estado activo y anulación lógica. Las restricciones únicas evitan duplicados activos y las transacciones con `select_for_update` protegen cambios de estado y generación.
