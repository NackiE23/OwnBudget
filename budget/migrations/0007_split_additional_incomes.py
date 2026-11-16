"""
Імпорт раніше складав усі рядки Additional incomes місяця в один запис:
сума гривень і примітка «3500 мама; 1293 облігації». Тепер кожен рядок це
окремий запис із сумою й приміткою. Розбиваються лише записи, які точно
вийшли з імпорту: без формули, примітка не обрізана, а суми частин дають
збережену суму. Решта лишається як є.
"""

from decimal import Decimal

from django.db import migrations

from budget.parsing import parse_income_line

SEPARATOR = "; "
NOTE_MAX_LENGTH = 255


def split(apps, schema_editor):
    MonthLine = apps.get_model("budget", "MonthLine")
    lines = MonthLine.objects.filter(line_type__kind="income", amount_expression="").exclude(note="")
    for line in lines:
        if len(line.note) >= NOTE_MAX_LENGTH:
            continue
        parts = [parse_income_line(part) for part in line.note.split(SEPARATOR) if part.strip()]
        if not parts or any(part.amount is None for part in parts):
            continue
        if sum((part.amount for part in parts), Decimal("0")) != line.amount:
            continue
        if len(parts) == 1 and parts[0].note == line.note:
            continue
        first, *rest = parts
        line.amount, line.note = first.amount, first.note
        line.save(update_fields=["amount", "note"])
        for part in rest:
            MonthLine.objects.create(month_id=line.month_id, line_type_id=line.line_type_id, amount=part.amount, note=part.note)


class Migration(migrations.Migration):
    dependencies = [
        ("budget", "0006_split_lpott"),
    ]

    operations = [
        migrations.RunPython(split, migrations.RunPython.noop),
    ]
