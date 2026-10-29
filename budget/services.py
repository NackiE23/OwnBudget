"""Операції над місяцями, спільні для адмінки, API й команд."""

import datetime

from django.db import transaction

from budget.models import AllocationCategory, LineType, Month, MonthAllocation, MonthLine


def first_day(value: datetime.date) -> datetime.date:
    return value.replace(day=1)


def add_month(value: datetime.date) -> datetime.date:
    year, month = divmod(value.month, 12)
    return datetime.date(value.year + year, month + 1, 1)


def next_period() -> datetime.date:
    """Місяць після останнього наявного; якщо місяців немає, то поточний."""
    latest = Month.objects.order_by("-period").values_list("period", flat=True).first()
    return add_month(latest) if latest else first_day(datetime.date.today())


@transaction.atomic
def populate_from_previous(month: Month) -> None:
    """
    Заповнює порожній місяць розподілом і рядками з попереднього місяця
    (вимога B-1). Якщо попереднього немає, бере активні довідники.
    Суми рядків не копіюються: тип рядка лишається, суму треба внести.
    """
    previous = month.previous()

    if not month.allocations.exists():
        if previous and previous.allocations.exists():
            templates = [
                (a.category, a.percent, None if a.percent is not None else a.limit_amount)
                for a in previous.allocations.select_related("category")
                if a.category.is_active
            ]
        else:
            templates = [(c, c.default_percent, None) for c in AllocationCategory.objects.filter(is_active=True)]
        for category, percent, limit_amount in templates:
            MonthAllocation.objects.create(month=month, category=category, percent=percent, limit_amount=limit_amount)

    if not month.lines.exists():
        if previous and previous.lines.exists():
            line_types = [line.line_type for line in previous.lines.select_related("line_type") if line.line_type.is_active]
        else:
            line_types = list(LineType.objects.filter(is_active=True))
        for line_type in line_types:
            MonthLine.objects.create(month=month, line_type=line_type)

    month.recalculate()


@transaction.atomic
def create_month(period: datetime.date, salary=None) -> Month:
    """Створює місяць; Salary за замовчуванням береться з попереднього місяця."""
    month = Month(period=first_day(period))
    previous = month.previous()
    if salary is not None:
        month.salary = salary
    elif previous:
        month.salary = previous.salary
    month.save()
    populate_from_previous(month)
    return month


def create_next_month() -> Month:
    return create_month(next_period())


@transaction.atomic
def close_month(month: Month) -> None:
    """Фіксує суми місяця (вимога B-8). Після закриття зміни відсотків їх не чіпають."""
    if month.is_closed:
        return
    month.recalculate()
    month.status = Month.Status.CLOSED
    month.save(update_fields=["status", "updated_at"])


@transaction.atomic
def reopen_month(month: Month) -> None:
    if not month.is_closed:
        return
    month.status = Month.Status.OPEN
    month.save(update_fields=["status", "updated_at"])
    month.recalculate()


def recalculate_with_following(month: Month) -> None:
    """
    Перераховує місяць і наступний за ним: «Знято з накопичень» наступного
    місяця залежить від «Загальні Накопичення» цього.
    """
    month.recalculate()
    following = month.following()
    if following:
        following.recalculate()
