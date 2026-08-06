# Activo de referencia

Esta carpeta contiene la plantilla PP-V2 validada que la aplicación utiliza para
reparar automáticamente los catálogos y el archivo privado persistente durante
un despliegue nuevo. Si cambia el hash del activo validado, el comando
`ensure_reference_data` actualiza únicamente la copia privada de la plantilla;
nunca reemplaza usuarios ni partes de producción existentes.
