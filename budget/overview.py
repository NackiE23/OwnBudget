"""
Головна сторінка: місяці в розкладці Excel (B-10). Над кожною категорією
заголовок, під ним «Як Є» і «Ціль/Межа»; комірка «Як Є» розфарбована за
позначкою. Відкриті місяці редагуються просто в таблиці, крім відсотків.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction

from budget import calculations, services
from budget.exports import table_columns
from budget.models import (
    AllocationCategory,
    Month,
    MonthAllocation,
    MonthLine,
    SavingsDestination,
    SavingsEntry,
)
from budget.parsing import ExpressionError, entry_text, parse_amount, parse_savings_line, plain_number

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


def savings_text(month: Month) -> str:
    return "\n".join(entry_text(entry) for entry in month.savings_entries.all())


@dataclass
class HeaderCell:
    label: str
    colspan: int = 1
    rowspan: int = 1


@dataclass
class Cell:
    display: str = ""
    kind: str = "number"  # number, text або plain
    mark: str = ""
    field: str = ""  # ім'я поля форми; порожнє означає, що комірка не редагується
    value: str = ""
    widget: str = "input"  # input або textarea
    title: str = ""


@dataclass
class Row:
    month: Month
    editable: bool
    cells: list[Cell] = field(default_factory=list)


class Overview:
    def __init__(self, months: Iterable[Month]):
        self.months = list(months)
        self.line_types, self.categories = table_columns(self.months)

    @property
    def header_top(self) -> list[HeaderCell]:
        """Верхній рядок заголовків без «Month»: категорії займають два стовпці."""
        cells = [HeaderCell("Salary", rowspan=2)]
        cells += [HeaderCell(line_type.name, rowspan=2) for line_type in self.line_types]
        cells += [HeaderCell("Total expenses", rowspan=2), HeaderCell("Savings", rowspan=2), HeaderCell("Notes", rowspan=2)]
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
            total = sum((line.amount for line in own), Decimal("0")) if own else None
            cell = Cell(display=money_display(total), title="; ".join(line.note for line in own if line.note))
            if len(own) <= 1:
                cell.field = line_field(line_type.pk)
                cell.value = amount_value(own[0].amount) if own else ""
            else:
                cell.title = "Кілька рядків цього типу: редагуй на сторінці місяця."
            row.cells.append(cell)

        row.cells.append(
            Cell(
                display=money_display(month.total_expenses_effective),
                field=TOTAL_EXPENSES,
                value=amount_value(month.total_expenses),
                title="" if month.total_expenses is not None else "Порожньо: показано суму рядків витрат.",
            )
        )
        text = savings_text(month)
        row.cells.append(Cell(display=text, kind="text", field=SAVINGS, value=text, widget="textarea"))
        row.cells.append(Cell(display=month.notes, kind="text", field=NOTES, value=month.notes, widget="textarea"))

        allocations = {a.category_id: a for a in month.allocations.all()}
        for category in self.categories:
            allocation = allocations.get(category.pk)
            actual = allocation.actual if allocation else None
            limit = allocation.limit_amount if allocation else None
            row.cells.append(
                Cell(
                    display=money_display(actual),
                    mark=allocation.mark if allocation else "",
                    field=actual_field(category.pk),
                    value=amount_value(actual),
                    title=allocation.mark_label if allocation else "",
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
    if NOTES in data:
        month.notes = "\n".join(line.rstrip() for line in data[NOTES].strip().splitlines())

    line_values = {}
    lines: dict[int, list[MonthLine]] = {}
    for line in month.lines.select_related("line_type"):
        lines.setdefault(line.line_type_id, []).append(line)
    line_types = {lt.pk: lt for lt in table_columns([month])[0]}
    for pk, line_type in line_types.items():
        name = line_field(pk)
        if name in data and len(lines.get(pk, [])) <= 1:
            line_values[pk] = amount(name, line_type.name)[0]

    allocation_values = {}
    allocations = {a.category_id: a for a in month.allocations.select_related("category")}
    for category in table_columns([month])[1]:
        actual_name, limit_name = actual_field(category.pk), limit_field(category.pk)
        allocation = allocations.get(category.pk)
        values = {}
        if actual_name in data:
            values["actual"] = amount(actual_name, f"{category.name}: Як Є")[0]
        if limit_name in data and (allocation is None or allocation.percent is None):
            values["limit_amount"] = amount(limit_name, f"{category.name}: ліміт")[0]
        if values:
            allocation_values[category] = values

    if errors:
        raise OverviewError(errors)

    month.full_clean(exclude=["period"])
    month.save()

    for pk, value in line_values.items():
        own = lines.get(pk, [])
        if own:
            own[0].amount = value or Decimal("0")
            own[0].save(update_fields=["amount"])
        elif value is not None:
            MonthLine.objects.create(month=month, line_type=line_types[pk], amount=value)

    for category, values in allocation_values.items():
        allocation = allocations.get(category.pk)
        if allocation is None:
            if all(value is None for value in values.values()):
                continue
            MonthAllocation.objects.create(month=month, category=category, **values)
            continue
        changed = [name for name, value in values.items() if getattr(allocation, name) != value]
        for name in changed:
            setattr(allocation, name, values[name])
        if changed:
            allocation.save(update_fields=changed)

    if SAVINGS in data:
        _save_savings(month, data[SAVINGS])

    services.recalculate_with_following(month)


def _save_savings(month: Month, text: str) -> None:
    """Перезаписує записи накопичень, лише якщо текст справді змінився."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines == savings_text(month).splitlines():
        return
    month.savings_entries.all().delete()
    destinations = list(SavingsDestination.objects.all())
    for line in lines:
        for entry in parse_savings_line(line):
            destination, note = _destination(entry.destination, destinations)
            SavingsEntry.objects.create(
                month=month,
                amount=calculations.money(entry.amount) if entry.amount is not None else None,
                currency=entry.currency,
                destination=destination,
                raw_text=entry.raw_text[:500],
                note=note[:255],
            )


def _destination(text: str, destinations: list[SavingsDestination]) -> tuple[SavingsDestination | None, str]:
    """
    Призначення за початком тексту («Crypto на USDT» → Crypto, примітка «на USDT»).
    Невідомий текст стає новим призначенням.
    """
    if not text:
        return None, ""
    lowered = text.lower()
    for destination in sorted(destinations, key=lambda d: len(d.name), reverse=True):
        name = destination.name.lower()
        if lowered == name or lowered.startswith(name + " "):
            return destination, text[len(name):].strip()
    destination = SavingsDestination.objects.create(name=text[:100])
    destinations.append(destination)
    return destination, ""
