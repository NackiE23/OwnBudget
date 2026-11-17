"""
Підсумки операцій OwnExpenses за категоріями. Категорії телефона не
прив'язані до категорій бюджету: ці суми лише показуються й пропонуються,
а куди їх вписати, вирішує людина.

Підкатегорії згортаються в категорію верхнього рівня; операції, записані
прямо на батьківську категорію, що має й підкатегорії, ідуть окремим рядком
«без підкатегорії».
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, QuerySet, Sum
from django.db.models.functions import TruncMonth

from expenses.models import Category, Operation, OperationType

WITHOUT_SUBCATEGORY = "без підкатегорії"


@dataclass
class CategoryTotal:
    key: str  # pk категорії; для «без підкатегорії» з суфіксом
    name: str
    amount: Decimal
    count: int = 0
    share: Decimal = Decimal("0")  # відсоток від усієї суми цього типу
    children: list["CategoryTotal"] = field(default_factory=list)


@dataclass
class Breakdown:
    total: Decimal
    groups: list[CategoryTotal]


def _build(sums: dict[int, tuple[int, int]], categories: dict[int, Category]) -> Breakdown:
    """sums: pk категорії → (сума в копійках, кількість операцій)."""
    groups: dict[int, list[tuple[Category, int, int]]] = {}
    for pk, (minor, count) in sums.items():
        category = categories[pk]
        groups.setdefault(category.parent_id or category.pk, []).append((category, minor, count))

    total_minor = sum(minor for minor, _ in sums.values())
    result = []
    for top_pk, items in groups.items():
        top = categories[top_pk]
        children = [
            CategoryTotal(
                key=str(category.pk) if category.parent_id else f"{category.pk}-own",
                name=category.name if category.parent_id else WITHOUT_SUBCATEGORY,
                amount=Decimal(minor) / 100,
                count=count,
            )
            for category, minor, count in items
        ]
        group = CategoryTotal(
            key=str(top.pk),
            name=top.name,
            amount=sum((child.amount for child in children), Decimal("0")),
            count=sum(child.count for child in children),
        )
        # Самі операції батьківської категорії без підкатегорій не розбиваються.
        if any(category.parent_id for category, _, _ in items):
            group.children = sorted(children, key=lambda child: (-child.amount, child.name))
        result.append(group)

    total = Decimal(total_minor) / 100
    for group in result:
        group.share = (group.amount * 100 / total).quantize(Decimal("0.1")) if total else Decimal("0")
        for child in group.children:
            child.share = (child.amount * 100 / total).quantize(Decimal("0.1")) if total else Decimal("0")
    result.sort(key=lambda group: (-group.amount, group.name))
    return Breakdown(total=total, groups=result)


def _categories(pks: set[int]) -> dict[int, Category]:
    categories = Category.objects.select_related("parent").in_bulk(pks)
    parents = {c.parent_id for c in categories.values() if c.parent_id} - categories.keys()
    categories.update(Category.objects.in_bulk(parents))
    return categories


def category_breakdown(queryset: QuerySet[Operation], operation_type: str = OperationType.EXPENSE) -> Breakdown:
    """Суми операцій одного типу за категоріями; queryset уже має бути без видалених."""
    rows = (
        queryset.filter(type=operation_type)
        .order_by()
        .values("category_id")
        .annotate(minor=Sum("amount_minor"), count=Count("pk"))
    )
    sums = {row["category_id"]: (row["minor"], row["count"]) for row in rows}
    return _build(sums, _categories(set(sums)))


def monthly_expenses(periods: list[datetime.date]) -> dict[datetime.date, Breakdown]:
    """Витрати за категоріями для кожного місяця з periods (перші дні місяців). Порожні місяці пропущено."""
    if not periods:
        return {}
    start = min(periods)
    end = max(periods)
    end = end.replace(year=end.year + 1, month=1) if end.month == 12 else end.replace(month=end.month + 1)
    rows = (
        Operation.objects.alive()
        .filter(type=OperationType.EXPENSE, date__gte=start, date__lt=end)
        .annotate(period=TruncMonth("date"))
        .order_by()
        .values("period", "category_id")
        .annotate(minor=Sum("amount_minor"), count=Count("pk"))
    )
    wanted = set(periods)
    by_month: dict[datetime.date, dict[int, tuple[int, int]]] = {}
    for row in rows:
        period = row["period"]
        if isinstance(period, datetime.datetime):
            period = period.date()
        if period in wanted:
            by_month.setdefault(period, {})[row["category_id"]] = (row["minor"], row["count"])
    categories = _categories({pk for sums in by_month.values() for pk in sums})
    return {period: _build(sums, categories) for period, sums in by_month.items()}
