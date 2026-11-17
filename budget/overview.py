"""
Головна сторінка: місяці в розкладці Excel (B-10). Над кожною категорією
заголовок, під ним «Як Є» і «Ціль/Межа»; комірка «Як Є» розфарбована за
позначкою. Відкриті місяці редагуються просто в таблиці, крім відсотків.
Доходи (Additional incomes), Savings і Notes це списки записів: кожен запис
додається, редагується й видаляється окремо, у вікні поверх таблиці.
Біля «Як Є» і Total expenses відкритого місяця є підказка: витрати
OwnExpenses за цей місяць за категоріями, з яких можна скласти суму.
"""

import datetime
import json
from collections.abc import Iterable, Mapping
import dataclasses
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction

from budget import calculations, services
from budget.exports import table_columns
from budget.models import (
    AllocationCategory,
    Currency,
    LineType,
    Month,
    MonthAllocation,
    MonthLine,
    SavingsDestination,
    SavingsEntry,
)
from budget.expressions import ExpressionError, parse_amount, plain_number
from budget.parsing import PREFIX_SYMBOLS, SUFFIX_SYMBOLS

SALARY = "salary"
TOTAL_EXPENSES = "total_expenses"
SAVINGS = "savings"
NOTES = "notes"


def money_display(value: Decimal | None) -> str:
    """Сума з пробілом між тисячами, як в Excel: 12 500.00. Порожньо, якщо суми немає."""
    if value is None:
        return ""
    return f"{value:,.2f}".replace(",", " ")


def amount_value(value: Decimal | None) -> str:
    return "" if value is None else plain_number(value)


def line_field(line_type_id: int) -> str:
    return f"line-{line_type_id}"


def actual_field(category_id: int) -> str:
    return f"actual-{category_id}"


def limit_field(category_id: int) -> str:
    return f"limit-{category_id}"


def note_lines(month: Month) -> list[str]:
    """Нотатки місяця як записи: кожен непорожній рядок окремо."""
    return [line.strip() for line in month.notes.splitlines() if line.strip()]


def savings_amount_display(entry: SavingsEntry) -> str:
    if entry.amount is None:
        return ""
    return f"{PREFIX_SYMBOLS.get(entry.currency, '')}{money_display(entry.amount)}{SUFFIX_SYMBOLS.get(entry.currency, '')}"


@dataclass
class HeaderCell:
    label: str
    colspan: int = 1
    rowspan: int = 1


@dataclass
class Entry:
    """Запис у комірці-списку: дохід, накопичення або нотатка."""

    key: str  # pk запису; для нотаток номер рядка
    amount: str = ""
    text: str = ""
    values: dict[str, str] = field(default_factory=dict)  # початкові значення форми редагування

    @property
    def summary(self) -> str:
        return " ".join(part for part in (self.amount, self.text) if part)

    @property
    def values_json(self) -> str:
        return json.dumps(self.values, ensure_ascii=False)


@dataclass
class Cell:
    display: str = ""
    kind: str = "number"  # number, plain або entries
    mark: str = ""
    field: str = ""  # ім'я поля форми; порожнє означає, що комірка не редагується
    value: str = ""
    title: str = ""
    entry_type: str = ""  # income, savings або notes для комірок-списків
    label: str = ""
    # dataclasses.field: ім'я field у цьому класі зайняте полем форми.
    entries: list[Entry] = dataclasses.field(default_factory=list)
    total: str = ""
    suggest: bool = False  # чи є витрати OwnExpenses, з яких підказати суму


@dataclass
class Row:
    month: Month
    editable: bool
    cells: list[Cell] = field(default_factory=list)


class Overview:
    def __init__(self, months: Iterable[Month], suggest_periods: Iterable[datetime.date] = ()):
        self.months = list(months)
        self.line_types, self.categories = table_columns(self.months)
        # Місяці, для яких є витрати OwnExpenses: там біля сум є кнопка підказки.
        self.suggest_periods = set(suggest_periods)

    @property
    def header_top(self) -> list[HeaderCell]:
        """Верхній рядок заголовків без «Month»: категорії займають два стовпці."""
        cells = [HeaderCell("Total expenses", rowspan=2), HeaderCell("Salary", rowspan=2)]
        cells += [HeaderCell(line_type.name, rowspan=2) for line_type in self.line_types]
        cells += [HeaderCell("Savings", rowspan=2), HeaderCell("Notes", rowspan=2)]
        cells += [HeaderCell(category.name, colspan=2) for category in self.categories]
        cells += [HeaderCell("Нерозподілено", rowspan=2), HeaderCell("Статус", rowspan=2)]
        return cells

    @property
    def header_bottom(self) -> list[str]:
        labels = []
        for category in self.categories:
            limit = "Ціль" if category.kind == AllocationCategory.Kind.SAVINGS_POOL else "Межа"
            labels += ["Як Є", limit]
        return labels

    @property
    def rows(self) -> list[Row]:
        return [self.row(month) for month in self.months]

    def row(self, month: Month) -> Row:
        row = Row(month=month, editable=not month.is_closed)
        suggest = row.editable and month.period in self.suggest_periods
        row.cells.append(
            Cell(
                display=money_display(month.total_expenses_effective),
                field=TOTAL_EXPENSES,
                value=amount_value(month.total_expenses),
                title="" if month.total_expenses is not None else "Порожньо: показано суму рядків витрат.",
                label="Total expenses",
                suggest=suggest,
            )
        )
        row.cells.append(
            Cell(
                display=money_display(month.salary),
                field=SALARY,
                value=month.salary_expression or amount_value(month.salary),
                title=month.salary_expression,
            )
        )

        lines: dict[int, list[MonthLine]] = {}
        for line in month.lines.all():
            lines.setdefault(line.line_type_id, []).append(line)
        for line_type in self.line_types:
            own = lines.get(line_type.pk, [])
            if line_type.kind == LineType.Kind.INCOME:
                row.cells.append(income_cell(line_type, own))
                continue
            total = sum((line.amount for line in own), Decimal("0")) if own else None
            cell = Cell(
                display=money_display(total),
                title="; ".join(text for line in own for text in (line.amount_expression, line.note) if text),
            )
            if len(own) <= 1:
                cell.field = line_field(line_type.pk)
                cell.value = (own[0].amount_expression or amount_value(own[0].amount)) if own else ""
            else:
                cell.title = "Кілька рядків цього типу: редагуй на сторінці місяця."
            row.cells.append(cell)

        row.cells.append(savings_cell(month))
        row.cells.append(notes_cell(month))

        allocations = {a.category_id: a for a in month.allocations.all()}
        for category in self.categories:
            allocation = allocations.get(category.pk)
            actual = allocation.actual if allocation else None
            limit = allocation.limit_amount if allocation else None
            expression = allocation.actual_expression if allocation else ""
            row.cells.append(
                Cell(
                    display=money_display(actual),
                    mark=allocation.mark if allocation else "",
                    field=actual_field(category.pk),
                    value=expression or amount_value(actual),
                    title=" · ".join(text for text in (expression, allocation.mark_label if allocation else "") if text),
                    label=f"{category.name}: Як Є",
                    suggest=suggest,
                )
            )
            limit_cell = Cell(display=money_display(limit), value=amount_value(limit))
            if allocation and allocation.percent is not None:
                limit_cell.title = f"{plain_number(allocation.percent)}%: рахується автоматично."
            else:
                limit_cell.field = limit_field(category.pk)
            row.cells.append(limit_cell)

        row.cells.append(Cell(display=money_display(month.unallocated)))
        row.cells.append(Cell(display=month.get_status_display(), kind="plain"))
        return row


def expense_suggestions(months: Iterable[Month], breakdowns: Mapping[datetime.date, object]) -> dict[str, dict]:
    """
    Дані для вікна підказки: витрати OwnExpenses кожного місяця за категоріями,
    ключ це pk місяця. breakdowns дає expenses.analytics.monthly_expenses.
    """

    def item(total) -> dict:
        return {"key": total.key, "name": total.name, "amount": plain_number(total.amount), "count": total.count}

    result = {}
    for month in months:
        breakdown = breakdowns.get(month.period)
        if breakdown is None:
            continue
        result[str(month.pk)] = {
            "label": str(month),
            "total": plain_number(breakdown.total),
            "groups": [
                {**item(group), "children": [item(child) for child in group.children]} for group in breakdown.groups
            ],
        }
    return result


def income_cell(line_type: LineType, lines: list[MonthLine]) -> Cell:
    entries = [
        Entry(
            key=str(line.pk),
            amount=money_display(line.amount),
            text=line.note,
            values={"amount": line.amount_expression or amount_value(line.amount), "note": line.note},
        )
        for line in lines
    ]
    total = sum((line.amount for line in lines), Decimal("0"))
    return Cell(
        kind="entries",
        field=line_field(line_type.pk),
        entry_type="income",
        label=line_type.name,
        entries=entries,
        total=money_display(total) if len(lines) > 1 else "",
    )


def savings_cell(month: Month) -> Cell:
    entries = []
    for entry in month.savings_entries.all():
        destination = entry.destination.name if entry.destination else ""
        text = " · ".join(part for part in (destination, entry.note) if part)
        if entry.amount is None:
            # Рядок з Excel, який не вдалося розібрати: показуємо як є.
            text = entry.raw_text or text
        entries.append(
            Entry(
                key=str(entry.pk),
                amount=savings_amount_display(entry),
                text=text,
                values={
                    "amount": amount_value(entry.amount),
                    "currency": entry.currency,
                    "destination": destination,
                    "note": entry.note if entry.amount is not None else (entry.raw_text or entry.note),
                },
            )
        )
    return Cell(kind="entries", field=SAVINGS, entry_type="savings", label="Savings", entries=entries)


def notes_cell(month: Month) -> Cell:
    entries = [Entry(key=str(index), text=line, values={"text": line}) for index, line in enumerate(note_lines(month))]
    return Cell(kind="entries", field=NOTES, entry_type="notes", label="Notes", entries=entries)


def entries_cell(month: Month, kind: str) -> Cell:
    """Комірка-список одного місяця; month має бути з prefetch, як у months_queryset."""
    if kind == SAVINGS:
        return savings_cell(month)
    if kind == NOTES:
        return notes_cell(month)
    line_type = income_type(kind)
    return income_cell(line_type, [line for line in month.lines.all() if line.line_type_id == line_type.pk])


# --- збереження рядка ------------------------------------------------------


class OverviewError(ValueError):
    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


@transaction.atomic
def save_month(month: Month, data: Mapping[str, str]) -> None:
    """
    Зберігає рядок огляду. Оброблюються лише поля, що прийшли у формі.
    Помилка в будь-якому полі скасовує все збереження.
    """
    month.ensure_open()
    errors: list[str] = []

    def amount(name: str, label: str) -> tuple[Decimal | None, str]:
        try:
            value, expression = parse_amount(data[name])
        except ExpressionError as error:
            errors.append(f"{label}: {error}")
            return None, ""
        return (calculations.money(value) if value is not None else None), expression

    if SALARY in data:
        salary, expression = amount(SALARY, "Salary")
        month.salary = salary if salary is not None else Decimal("0")
        month.salary_expression = expression
    if TOTAL_EXPENSES in data:
        month.total_expenses, _ = amount(TOTAL_EXPENSES, "Total expenses")

    line_values = {}
    lines: dict[int, list[MonthLine]] = {}
    for line in month.lines.select_related("line_type"):
        lines.setdefault(line.line_type_id, []).append(line)
    line_types = {lt.pk: lt for lt in table_columns([month])[0]}
    for pk, line_type in line_types.items():
        name = line_field(pk)
        # Доходи редагуються записами (change_entry), а не полем рядка.
        if name in data and len(lines.get(pk, [])) <= 1 and line_type.kind == LineType.Kind.EXPENSE:
            line_values[pk] = amount(name, line_type.name)

    allocation_values = {}
    allocations = {a.category_id: a for a in month.allocations.select_related("category")}
    for category in table_columns([month])[1]:
        actual_name, limit_name = actual_field(category.pk), limit_field(category.pk)
        allocation = allocations.get(category.pk)
        values = {}
        if actual_name in data:
            values["actual"], values["actual_expression"] = amount(actual_name, f"{category.name}: Як Є")
        if limit_name in data and (allocation is None or allocation.percent is None):
            values["limit_amount"] = amount(limit_name, f"{category.name}: ліміт")[0]
        if values:
            allocation_values[category] = values

    if errors:
        raise OverviewError(errors)

    month.full_clean(exclude=["period"])
    month.save()

    for pk, (value, expression) in line_values.items():
        own = lines.get(pk, [])
        if own:
            own[0].amount = value or Decimal("0")
            own[0].amount_expression = expression
            own[0].save(update_fields=["amount", "amount_expression"])
        elif value is not None:
            MonthLine.objects.create(month=month, line_type=line_types[pk], amount=value, amount_expression=expression)

    for category, values in allocation_values.items():
        allocation = allocations.get(category.pk)
        if allocation is None:
            if all(value in (None, "") for value in values.values()):
                continue
            MonthAllocation.objects.create(month=month, category=category, **values)
            continue
        changed = [name for name, value in values.items() if getattr(allocation, name) != value]
        for name in changed:
            setattr(allocation, name, values[name])
        if changed:
            allocation.save(update_fields=changed)

    services.recalculate_with_following(month)


# --- записи в комірках-списках ----------------------------------------------

ADD, EDIT, DELETE = "add", "edit", "delete"
NOTE_MAX_LENGTH = 255
LINE_PREFIX = line_field(0).removesuffix("0")


def income_type(kind: str) -> LineType:
    """Тип доходу за ім'ям поля («line-7»); OverviewError, якщо це не дохід."""
    pk = kind.removeprefix(LINE_PREFIX)
    line_type = LineType.objects.filter(pk=pk, kind=LineType.Kind.INCOME).first() if pk.isdigit() else None
    if line_type is None:
        raise OverviewError(["Невідомий стовпець."])
    return line_type


@transaction.atomic
def change_entry(month: Month, kind: str, action: str, data: Mapping[str, str]) -> None:
    """
    Додає (add), змінює (edit) чи видаляє (delete) один запис у стовпці
    доходу, Savings або Notes. data["entry"] вказує запис для edit і delete.
    """
    month.ensure_open()
    if action not in (ADD, EDIT, DELETE):
        raise OverviewError(["Невідома дія."])
    if kind == SAVINGS:
        _change_savings(month, action, data)
    elif kind == NOTES:
        _change_note(month, action, data)
    else:
        _change_income(month, income_type(kind), action, data)


def _stale() -> OverviewError:
    return OverviewError(["Запис не знайдено: можливо, його вже змінили. Оновіть сторінку."])


def _required_amount(text: str) -> tuple[Decimal, str]:
    try:
        value, expression = parse_amount(text)
    except ExpressionError as error:
        raise OverviewError([f"Сума: {error}"]) from error
    if value is None:
        raise OverviewError(["Вкажіть суму."])
    return calculations.money(value), expression


def _note(data: Mapping[str, str]) -> str:
    note = " ".join(data.get("note", "").split())
    if len(note) > NOTE_MAX_LENGTH:
        raise OverviewError([f"Примітка довша за {NOTE_MAX_LENGTH} символів."])
    return note


def _change_income(month: Month, line_type: LineType, action: str, data: Mapping[str, str]) -> None:
    line = None
    if action != ADD:
        key = data.get("entry", "")
        line = month.lines.filter(line_type=line_type, pk=key).first() if key.isdigit() else None
        if line is None:
            raise _stale()
    if action == DELETE:
        line.delete()
        return
    amount, expression = _required_amount(data.get("amount", ""))
    note = _note(data)
    line = line or MonthLine(month=month, line_type=line_type)
    line.amount, line.amount_expression, line.note = amount, expression, note
    line.save()


def _change_savings(month: Month, action: str, data: Mapping[str, str]) -> None:
    entry = None
    if action != ADD:
        key = data.get("entry", "")
        entry = month.savings_entries.filter(pk=key).first() if key.isdigit() else None
        if entry is None:
            raise _stale()
    if action == DELETE:
        entry.delete()
        return
    amount, _ = _required_amount(data.get("amount", ""))
    currency = data.get("currency") or Currency.UAH
    if currency not in Currency.values:
        raise OverviewError(["Невідома валюта."])
    note = _note(data)
    destination = _destination(" ".join(data.get("destination", "").split()))
    entry = entry or SavingsEntry(month=month)
    entry.amount, entry.currency, entry.destination, entry.note = amount, currency, destination, note
    # Запис тепер структурований: рядок з Excel йому більше не відповідає.
    entry.raw_text = ""
    entry.save()


def _destination(name: str) -> SavingsDestination | None:
    """Призначення за назвою без урахування регістру; нова назва стає новим призначенням."""
    if not name:
        return None
    if len(name) > 100:
        raise OverviewError(["Призначення довше за 100 символів."])
    # Порівняння в Python: iexact у SQLite не знає регістру кирилиці.
    existing = next((d for d in SavingsDestination.objects.all() if d.name.casefold() == name.casefold()), None)
    return existing or SavingsDestination.objects.create(name=name)


def _change_note(month: Month, action: str, data: Mapping[str, str]) -> None:
    lines = note_lines(month)
    index = -1
    if action != ADD:
        key = data.get("entry", "")
        index = int(key) if key.isdigit() else -1
        # Нотатки адресуються номером рядка, тож перевіряємо, що рядок той самий.
        if not 0 <= index < len(lines) or lines[index] != data.get("original", "").strip():
            raise _stale()
    if action == DELETE:
        del lines[index]
    else:
        text = " ".join(data.get("text", "").split())
        if not text:
            raise OverviewError(["Нотатка порожня."])
        if action == ADD:
            lines.append(text)
        else:
            lines[index] = text
    month.notes = "\n".join(lines)
    month.save(update_fields=["notes", "updated_at"])
