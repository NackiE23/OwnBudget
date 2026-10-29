"""
ЛПОТТ (ліки + побут + одяг + тварини + транспорт) розбивається на п'ять
категорій. Історія лишається на ЛПОТТ: категорія стає неактивною і далі
показується лише в старих місяцях. Відкриті місяці, де «Як Є» ЛПОТТ ще
порожнє, отримують нові категорії з відсотком ЛПОТТ, поділеним порівну.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations

OLD = "ЛПОТТ"
NEW = [("Ліки", 41), ("Побут", 42), ("Одяг", 43), ("Тварини", 44), ("Транспорт", 45)]
CENT = Decimal("0.01")


def share(salary, percent):
    return (salary * percent / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)


def split(apps, schema_editor):
    AllocationCategory = apps.get_model("budget", "AllocationCategory")
    MonthAllocation = apps.get_model("budget", "MonthAllocation")

    categories = [
        AllocationCategory.objects.get_or_create(name=name, defaults={"kind": "regular", "sort_order": order})[0]
        for name, order in NEW
    ]
    old = AllocationCategory.objects.filter(name=OLD).first()
    if old is None:
        return
    old.is_active = False
    old.save(update_fields=["is_active"])

    open_allocations = MonthAllocation.objects.filter(category=old, month__status="open", actual__isnull=True)
    for allocation in open_allocations.select_related("month"):
        month = allocation.month
        percent = (allocation.percent / len(categories)).quantize(CENT) if allocation.percent is not None else None
        for category in categories:
            MonthAllocation.objects.get_or_create(
                month=month,
                category=category,
                defaults={
                    "percent": percent,
                    "limit_amount": share(month.salary, percent) if percent is not None else None,
                },
            )
        allocation.delete()


def unsplit(apps, schema_editor):
    AllocationCategory = apps.get_model("budget", "AllocationCategory")
    MonthAllocation = apps.get_model("budget", "MonthAllocation")

    old = AllocationCategory.objects.filter(name=OLD).first()
    categories = list(AllocationCategory.objects.filter(name__in=[name for name, _ in NEW]))
    if old is not None:
        old.is_active = True
        old.save(update_fields=["is_active"])
        new_allocations = MonthAllocation.objects.filter(category__in=categories, month__status="open")
        for month_id in set(new_allocations.values_list("month_id", flat=True)):
            own = [a for a in new_allocations if a.month_id == month_id]
            if any(a.actual is not None for a in own):
                continue
            percents = [a.percent for a in own if a.percent is not None]
            MonthAllocation.objects.get_or_create(
                month_id=month_id, category=old, defaults={"percent": sum(percents) if percents else None}
            )
            MonthAllocation.objects.filter(pk__in=[a.pk for a in own]).delete()
    for category in categories:
        if not MonthAllocation.objects.filter(category=category).exists():
            category.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("budget", "0005_amount_expressions"),
    ]

    operations = [
        migrations.RunPython(split, unsplit),
    ]
