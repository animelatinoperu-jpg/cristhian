from django.db import migrations


PP_LAMINA_COLORS = {
    "PP-001": "AZUL", "PP-002": "AMARILLO", "PP-003": "NARANJA", "PP-004": "BLANCO",
    "PP-005": "CRISTAL", "PP-006": "NARANJA", "PP-007": "AZUL", "PP-008": "VERDE",
    "PP-009": "NARANJA", "PP-010": "CRISTAL", "PP-011": "VERDE", "PP-012": "ROSADO",
    "PP-013": "CRISTAL", "PP-014": "LILA", "PP-015": "ROJO", "PP-016": "ROSADO",
    "PP-017": "ROJO", "PP-018": "ROSADO", "PP-019": "BLANCO", "PP-020": "NARANJA",
    "PP-021": "LILA", "PP-022": "AZUL", "PP-023": "NARANJA", "PP-024": "LILA",
    "PP-025": "AZUL", "PP-026": "ROJO", "PP-027": "LILA", "PP-028": "CRISTAL",
    "PP-029": "NARANJA", "PP-030": "ROSADO", "PP-031": "CRISTAL", "PP-032": "ROJO",
    "PP-033": "LILA", "PP-034": "CREMA", "PP-035": "AZUL", "PP-036": "AMARILLO",
    "PP-037": "VERDE", "PP-038": "VERDE", "PP-039": "VERDE", "PP-040": "VERDE",
    "PP-041": "VERDE", "PP-042": "VERDE", "PP-043": "NARANJA", "PP-044": "NARANJA",
    "PP-045": "AMARILLO", "PP-046": "AMARILLO", "PP-047": "LILA", "PP-048": "AMARILLO",
}


def link_lamina_colors(apps, schema_editor):
    Product = apps.get_model("productions", "Product")
    for code, color in PP_LAMINA_COLORS.items():
        Product.objects.filter(code=code).update(color=color)


class Migration(migrations.Migration):
    dependencies = [("productions", "0021_remove_t4_r20_from_template_rules")]

    operations = [migrations.RunPython(link_lamina_colors, migrations.RunPython.noop)]
