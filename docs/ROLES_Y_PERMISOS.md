# Roles y permisos

Los permisos se validan en el backend mediante roles y `AreaAssignment`; ocultar botones es únicamente una mejora de interfaz.

| Rol | Alcance principal |
|---|---|
| Administrador | Configuración, catálogos y acceso total |
| Jefe de producción | Crear PP, asignar áreas, observar, aprobar, reabrir, cerrar y generar Excel |
| Recepción | Registros de R.M asignados |
| Nuqueras | Perfilado y productividad asignados |
| Supervisor de túnel | Solo el túnel y turno explícitamente asignados |
| Cuadrillas de túnel | Bandejas declaradas por cuadrilla |
| Envasado en placas | Posiciones exactas P1/P2/P3 |
| Cuadrillas de placas | Bandejas por cuadrilla y página |
| Empaque de túneles | Pallets y bultos EM-TUN |
| Empaque de placas | Pallets y bultos EM-PLA |
| Materiales | Consumos del PP |
| Costos | Tarifas y costos |
| Gerencia | Consulta integral sin escritura operativa |
| Auditor | Consulta de producción y bitácora inmutable |

Las producciones aprobadas, cerradas o anuladas no aceptan nuevos registros. Reabrir requiere al jefe y un motivo. Un cierre con diferencias exige justificación del jefe.
