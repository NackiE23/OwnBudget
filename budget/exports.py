"""
Табличне представлення місяців у розкладці Excel: один рядок на місяць.
Спільне для експорту в CSV (B-11) і сторінки огляду в адмінці (B-10).
"""

import csv
from collections.abc import Iterable
from decimal import Decimal

from django.db.models import Prefetch, QuerySet
from django.http import HttpResponse

from budget.models import AllocationCategory, LineType, Month, MonthAllocation, MonthLine, SavingsEntry


def months_queryset(queryset: QuerySet[Month] | None = None) -> QuerySet[Month]:
    queryset = queryset if queryset is not None else Month.objects.all()
    return queryset.order_by("period").prefetch_related(
        Prefetch("allocations", queryset=MonthAllocation.objects.select_related("category")),
        Prefetch("lines", queryset=MonthLine.objects.select_related("line_type")),
        Prefetch("savings_entries", queryset=SavingsEntry.objects.select_related("destination")),
    )


def format_savings_entry(entry: SavingsEntry) -> str:
    if entry.amount is None:
        return entry.raw_text
    parts = [f"{entry.amount} {entry.currency}"]
    if entry.destination:
        parts.append(entry.destination.name)
    if entry.note:
        parts.append(f"({entry.note})")
    return " ".join(parts)


class MonthTable:
    """Заголовки й рядки таблиці місяців; значення лишаються Decimal або None."""

    def __init__(self, months: Iterable[Month]):
        self.months = list(months)
        used_line_types = {line.line_type_id for month in self.months for line in month.lines.all()}
        used_categories = {a.category_id for month in self.months for a in month.allocations.all()}
        self.line_types = [lt for lt in LineType.objects.all() if lt.is_active or lt.pk in used_line_types]
        self.categories = [c for c in AllocationCategory.objects.all() if c.is_active or c.pk in used_categories]

    @property
    def headers(self) -> list[str]:
        headers = ["Month", "Salary"]
        headers += [line_type.name for line_type in self.line_types]
        headers += ["Total expenses", "Savings", "Notes"]
        for category in self.categories:
            headers += [f"{category.name}: Як Є", f"{category.name}: Ціль/Межа"]
        headers += ["Нерозподілено", "Статус"]
        return headers

    def row(self, month: Month) -> list:
        lines_by_type: dict[int, Decimal] = {}
        for line in month.lines.all():
            lines_by_type[line.line_type_id] = lines_by_type.get(line.line_type_id, Decimal("0")) + line.amount
        allocations = {a.category_id: a for a in month.allocations.all()}

        row = [str(month), month.salary]
        row += [lines_by_type.get(line_type.pk) for line_type in self.line_types]
        row += [
            month.total_expenses_effective,
            "\n".join(format_savings_entry(entry) for entry in month.savings_entries.all()),
            month.notes,
        ]
        for category in self.categories:
            allocation = allocations.get(category.pk)
            row += [allocation.actual, allocation.limit_amount] if allocation else [None, None]
        row += [month.unallocated, month.get_status_display()]
        return row

    @property
    def rows(self) -> list[list]:
        return [self.row(month) for month in self.months]


def csv_response(filename: str, headers: list[str], rows: Iterable[list]) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # BOM на початку файлу, щоб Excel одразу розпізнав кирилицю.
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return response


def months_csv(queryset: QuerySet[Month] | None = None) -> HttpResponse:
    table = MonthTable(months_queryset(queryset))
    return csv_response("budget-months.csv", table.headers, table.rows)


def savings_csv(queryset: QuerySet[SavingsEntry]) -> HttpResponse:
    headers = ["Month", "Сума", "Валюта", "Призначення", "Примітка", "Оригінальний текст"]
    rows = (
        [str(e.month), e.amount, e.currency, e.destination.name if e.destination else "", e.note, e.raw_text]
        for e in queryset.select_related("month", "destination").order_by("month__period", "pk")
    )
    return csv_response("budget-savings.csv", headers, rows)
