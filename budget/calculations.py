"""
Чисті функції розрахунків бюджету. Працюють лише з Decimal і не знають про БД,
тож їх легко покрити модульними тестами.
"""

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
# Наскільки «Як Є» може відхилитися від ліміту й лишитися «в межах очікуваного».
MARK_TOLERANCE = Decimal("1000")


def money(value: Decimal) -> Decimal:
    """Округлює суму до копійок за банківськими звичками Excel (половина вгору)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def share(base: Decimal, percent: Decimal) -> Decimal:
    """Частка `percent` відсотків від `base`, округлена до копійок."""
    return money(base * percent / HUNDRED)


def unallocated(salary: Decimal, limits: Iterable[Decimal | None]) -> Decimal:
    """Нерозподілений залишок: Salary мінус сума лімітів (порожні ліміти не рахуються)."""
    return money(salary - sum((limit for limit in limits if limit is not None), Decimal("0")))


def allocation_mark(actual: Decimal | None, limit: Decimal | None, *, more_is_better: bool) -> str:
    """
    Колір комірки «Як Є»: green — у межах ±MARK_TOLERANCE від ліміту,
    gold — краще за план, red — гірше. Для накопичень краще більше,
    для витрат краще менше.
    """
    if actual is None or limit is None:
        return ""
    difference = actual - limit
    if abs(difference) <= MARK_TOLERANCE:
        return "green"
    better = difference > 0 if more_is_better else difference < 0
    return "gold" if better else "red"


def percents_total(percents: Iterable[Decimal]) -> Decimal:
    return sum(percents, Decimal("0"))


def percents_are_complete(percents: Iterable[Decimal]) -> bool:
    return percents_total(percents) == HUNDRED
