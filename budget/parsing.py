"""
Розбір і запис рядків Savings у форматі Excel-таблиці («$113 моно»).
Спільне для імпорту й редагування в огляді.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from budget.expressions import plain_number
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


@dataclass
class ParsedIncome:
    amount: Decimal | None  # лише гривні; None, якщо рядок не розібрано
    note: str
    foreign: list[Term]  # доданки в іншій валюті: у суму не входять


def parse_income_line(line: str) -> ParsedIncome:
    """
    Рядок Additional incomes → один запис: «3500 мама» дає 3500 і «мама»,
    «1048 + 5408 облігації» дає 6456 і «облігації». Якщо текст стоїть не лише
    в кінці або є інша валюта, приміткою стає весь рядок, щоб нічого не загубити.
    """
    line = line.strip()
    terms = parse_terms(line)
    if terms is None:
        return ParsedIncome(None, line, [])
    uah = [term for term in terms if term.currency == Currency.UAH]
    foreign = [term for term in terms if term.currency != Currency.UAH]
    simple = not foreign and not any(term.text for term in terms[:-1])
    amount = sum((term.amount for term in uah), Decimal("0"))
    return ParsedIncome(amount, terms[-1].text if simple else line, foreign)


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
