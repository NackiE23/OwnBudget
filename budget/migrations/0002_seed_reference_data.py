"""Початкові довідники з таблиці Excel. Відсотки категорій лишаються порожніми: їх задає користувач."""

from decimal import Decimal

from django.db import migrations

CATEGORIES = [
    ("Загальні Накопичення", "savings_pool", None, 10),
    ("Їжа", "regular", None, 20),
    ("Розваги", "regular", None, 30),
    ("ЛПОТТ", "regular", None, 40),
    ("На відпустку", "regular", None, 50),
    ("Хата", "regular", None, 60),
    ("Знято з накопичень", "withdrawal", Decimal("20"), 100),
]

LINE_TYPES = [
    ("Additional incomes", "income", 10),
    ("Rent", "expense", 10),
    ("Utility services", "expense", 20),
    ("Taxes", "expense", 30),
]

DESTINATIONS = ["моно", "облігації", "Crypto"]


def seed(apps, schema_editor):
    AllocationCategory = apps.get_model("budget", "AllocationCategory")
    LineType = apps.get_model("budget", "LineType")
    SavingsDestination = apps.get_model("budget", "SavingsDestination")

    for name, kind, percent, order in CATEGORIES:
        AllocationCategory.objects.get_or_create(
            name=name, defaults={"kind": kind, "default_percent": percent, "sort_order": order}
        )
    for name, kind, order in LINE_TYPES:
        LineType.objects.get_or_create(name=name, defaults={"kind": kind, "sort_order": order})
    for name in DESTINATIONS:
        SavingsDestination.objects.get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [
        ("budget", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
