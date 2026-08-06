# Arquitectura

```mermaid
flowchart LR
  U[Usuarios por área y turno] -->|HTTPS| N[Nginx]
  N --> D[Django + Templates + HTMX]
  D --> P[(PostgreSQL)]
  D --> A[Auditoría y conciliaciones]
  D --> G[Generador Open XML seguro]
  G --> T[Plantilla XLSM privada versionada]
  G --> I[Control de integridad]
  I --> F[XLSM generado privado]
  D -->|descarga autorizada| F
```

El motor de Excel trabaja sobre una copia temporal. Solo modifica XML de celdas autorizadas y `calcPr`; no abre Microsoft Excel ni toca `vbaProject.bin`, imágenes, estilos, dibujos o relaciones. El control compara hojas, estados, fórmulas, VBA, celdas no autorizadas y componentes del paquete antes de registrar un archivo como válido.
