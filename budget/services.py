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
def add_blank_rows(month: Month) -> None:
    """
    Додає в порожній місяць по рядку на кожну активну категорію й тип рядка,
    щоб дані було куди вносити протягом місяця. Нічого не копіюється з інших
    місяців: суми порожні, відсоток лише той, що заданий у довіднику категорії.
    """
    if not month.allocations.exists():
        for category in AllocationCategory.objects.filter(is_active=True):
            MonthAllocation.objects.create(month=month, category=category, percent=category.default_percent)

    if not month.lines.exists():
        for line_type in LineType.objects.filter(is_active=True):
            MonthLine.objects.create(month=month, line_type=line_type)

    month.recalculate()


@transaction.atomic
def create_month(period: datetime.date, salary=None) -> Month:
    """Створює порожній місяць; Salary вноситься вручну."""
    month = Month(period=first_day(period))
    if salary is not None:
        month.salary = salary
    month.save()
    add_blank_rows(month)
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
