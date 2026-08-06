# Inconsistencias del Excel

## Hallazgos automáticos

- VBA: presente.
- Hojas mencionadas en el encargo pero no detectadas: ninguna.
- Nombres definidos dañados: _xleta.SUM, CODIGOS, CODIGOS, CODIGOS, CODIGOS, CODIGOS, CODIGOS, LISTA, LISTA.
- `PP!F13` contiene `#DIV/0!`.
- `RECEPCION (2)!Z22` contiene `#DIV/0!`.
- `RECEPCION (2)!AA22` contiene `#DIV/0!`.
- `RECEPCION (2)!AB22` contiene `#DIV/0!`.
- `T1 (2)!CM56` contiene `#DIV/0!`.
- `T6!CI56` contiene `#DIV/0!`.
- `T6 (2)!CI56` contiene `#DIV/0!`.
- `PROYECTADO!BE13` contiene `#DIV/0!`.
- `RENDIMIENTO!I6` contiene `#VALUE!`.
- `RENDIMIENTO!J6` contiene `#VALUE!`.

## Revisión manual requerida

- Confirmar qué celdas sin fórmula son realmente campos de entrada.
- Confirmar objetos ActiveX/Form Controls y comportamiento de macros en Microsoft Excel.
- Verificar visualmente impresión, saltos de página y fórmulas tras recalcular.
