"""
Чисті функції розрахунків бюджету. Працюють лише з Decimal і не знають про БД,
тож їх легко покрити модульними тестами.
"""

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
HUNDRED = Decimal("100")


def money(value: Decimal) -> Decimal:
    """Округлює суму до копійок за банківськими звичками Excel (половина вгору)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def share(base: Decimal, percent: Decimal) -> Decimal:
    """Частка `percent` відсотків від `base`, округлена до копійок."""
    return money(base * percent / HUNDRED)


def unallocated(salary: Decimal, limits: Iterable[Decimal | None]) -> Decimal:
    """Нерозподілений залишок: Salary мінус сума лімітів (порожні ліміти не рахуються)."""
    return money(salary - sum((limit for limit in limits if limit is not None), Decimal("0")))


def percents_total(percents: Iterable[Decimal]) -> Decimal:
    return sum(percents, Decimal("0"))


def percents_are_complete(percents: Iterable[Decimal]) -> bool:
    return percents_total(percents) == HUNDRED
