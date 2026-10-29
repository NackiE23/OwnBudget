"""
Розбір і запис значень у форматі Excel-таблиці: рядки Savings («$113 моно»)
і вирази Salary («=54815+33545»). Спільне для імпорту й редагування в огляді.
"""

import ast
import operator
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from budget.models import Currency, SavingsEntry

TERM_RE = re.compile(
    r"^(?P<prefix>[$€])?\s*(?P<number>\d+(?:[.,]\d+)?)\s*(?P<suffix>[$€]|zl|usdt)?(?=\s|$)\s*(?P<text>.*)$",
    re.IGNORECASE,
)
CURRENCY_SYMBOLS = {"$": Currency.USD, "€": Currency.EUR, "zl": Currency.PLN, "usdt": Currency.USDT}
PREFIX_SYMBOLS = {Currency.USD: "$", Currency.EUR: "€"}
SUFFIX_SYMBOLS = {Currency.PLN: "zl", Currency.USDT: " USDT"}


@dataclass
class Term:
    amount: Decimal
    currency: str
    text: str


@dataclass
class ParsedEntry:
    amount: Decimal | None
    currency: str
    destination: str
    raw_text: str


def parse_terms(line: str) -> list[Term] | None:
    """
    Розбирає рядок на доданки «сума [валюта] [текст]», розділені «+».
    Якщо після «+» йде не сума («1100 Петро + Іра»), весь рядок це один доданок.
    Повертає None, якщо рядок складніший (дужки, «|», мінус, текст на початку).
    """
    if "(" in line or "|" in line:
        return None
    matches = [TERM_RE.match(part.strip()) for part in line.split("+")]
    if not all(matches):
        matches = [TERM_RE.match(line.strip())]
        if not matches[0]:
            return None
    return [_term(match) for match in matches]


def _term(match: re.Match) -> Term:
    symbol = (match["prefix"] or match["suffix"] or "").lower()
    currency = CURRENCY_SYMBOLS.get(symbol, Currency.UAH)
    return Term(Decimal(match["number"].replace(",", ".")), currency, match["text"].strip())


def parse_savings_line(line: str) -> list[ParsedEntry]:
    """
    Рядок Savings → записи. «2000+4000 облігації» дає один запис на суму,
    «8000 + $200» два записи (валюти різні). Складний рядок стає записом без суми.
    """
    terms = parse_terms(line)
    if terms is None:
        return [ParsedEntry(None, Currency.UAH, "", line)]
    same_currency = len({term.currency for term in terms}) == 1
    if len(terms) > 1 and same_currency and not any(term.text for term in terms[:-1]):
        total = sum((term.amount for term in terms), Decimal("0"))
        return [ParsedEntry(total, terms[0].currency, terms[-1].text, line)]
    return [ParsedEntry(term.amount, term.currency, term.text, line) for term in terms]


def plain_number(value: Decimal) -> str:
    """113.00 → «113», 110.50 → «110.5»."""
    return format(value.normalize(), "f")


def entry_text(entry: SavingsEntry) -> str:
    """Запис накопичень у форматі рядка Savings, який parse_savings_line прочитає назад."""
    if entry.amount is None:
        return entry.raw_text
    if entry.raw_text and not entry.note:
        # Оригінальний рядок («2111+4363 облігації»), якщо він читається назад у цей самий запис.
        parsed = parse_savings_line(entry.raw_text)
        destination = entry.destination.name.lower() if entry.destination else ""
        if len(parsed) == 1 and (parsed[0].amount, parsed[0].currency, parsed[0].destination.lower()) == (
            entry.amount,
            entry.currency,
            destination,
        ):
            return entry.raw_text
    amount = plain_number(entry.amount)
    text = f"{PREFIX_SYMBOLS.get(entry.currency, '')}{amount}{SUFFIX_SYMBOLS.get(entry.currency, '')}"
    rest = " ".join(part for part in (entry.destination.name if entry.destination else "", entry.note) if part)
    return f"{text} {rest}".strip()


# --- вирази Salary ---------------------------------------------------------

_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


class ExpressionError(ValueError):
    pass


def evaluate(expression: str) -> Decimal:
    """Обчислює арифметичний вираз із чисел і + - * / та дужок. Нічого іншого не виконує."""
    source = expression.strip().lstrip("=").replace(",", ".").replace(" ", "").replace(" ", "")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as error:
        raise ExpressionError(f"Не вираз: «{expression}».") from error
    return _evaluate(tree.body, expression)


def _evaluate(node: ast.AST, expression: str) -> Decimal:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_evaluate(node.operand, expression)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left, right = _evaluate(node.left, expression), _evaluate(node.right, expression)
        try:
            return _OPERATORS[type(node.op)](left, right)
        except (ArithmeticError, InvalidOperation) as error:
            raise ExpressionError(f"Не вдалося обчислити «{expression}».") from error
    raise ExpressionError(f"У виразі «{expression}» дозволені лише числа, + - * / і дужки.")


def parse_amount(text: str) -> tuple[Decimal | None, str]:
    """
    Сума з поля вводу: «» → (None, «»), «1 200,50» → (1200.50, «»),
    «=54815+33545» або «54815+33545» → (88360, «=54815+33545»).
    """
    text = text.strip()
    if not text:
        return None, ""
    compact = text.lstrip("=").replace(" ", "").replace(" ", "")
    if re.fullmatch(r"-?\d+(?:[.,]\d+)?", compact):
        return Decimal(compact.replace(",", ".")), ""
    return evaluate(text), "=" + compact.replace(",", ".")
