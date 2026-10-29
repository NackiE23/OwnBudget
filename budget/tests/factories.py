import datetime
from decimal import Decimal

from budget.models import AllocationCategory, LineType, Month, MonthAllocation, MonthLine

D = Decimal

TZ_EXAMPLE_PERCENTS = {
    "Загальні Накопичення": "30",
    "Їжа": "20",
    "Розваги": "10",
    "ЛПОТТ": "5",
    "На відпустку": "10",
    "Хата": "25",
}


def category(kind: str) -> AllocationCategory:
    return AllocationCategory.objects.get(kind=kind)


def by_name(name: str) -> AllocationCategory:
    return AllocationCategory.objects.get(name=name)


def make_month(period: datetime.date, salary="50000", percents: dict[str, str] | None = None) -> Month:
    """Місяць із розподілом як в умовному прикладі ТЗ (30/20/10/5/10/25)."""
    month = Month.objects.create(period=period, salary=D(salary))
    for name, percent in (percents or TZ_EXAMPLE_PERCENTS).items():
        MonthAllocation.objects.create(month=month, category=by_name(name), percent=D(percent))
    month.recalculate()
    return month


def add_line(month: Month, name: str, amount: str) -> MonthLine:
    return MonthLine.objects.create(month=month, line_type=LineType.objects.get(name=name), amount=D(amount))
