import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from budget import services
from budget.models import AllocationCategory, Month, MonthAllocation, MonthClosedError
from budget.tests.factories import add_line, category, make_month

D = Decimal
SEP = datetime.date(2025, 9, 1)
OCT = datetime.date(2025, 10, 1)


def add_withdrawal(month: Month, percent="20") -> MonthAllocation:
    allocation = MonthAllocation.objects.create(month=month, category=category("withdrawal"), percent=D(percent))
    month.recalculate()
    allocation.refresh_from_db()
    return allocation


class DistributionTests(TestCase):
    def test_limits_follow_tz_example(self):
        month = make_month(SEP)
        limits = {a.category.name: a.limit_amount for a in month.allocations.select_related("category")}
        self.assertEqual(limits["Загальні Накопичення"], D("15000.00"))
        self.assertEqual(limits["Хата"], D("12500.00"))
        self.assertEqual(month.allocated_total, D("50000.00"))
        self.assertEqual(month.unallocated, D("0.00"))
        self.assertEqual(month.percent_total, D("100"))
        self.assertEqual(month.percent_warning, "")

    def test_percent_warning_and_unallocated_when_not_100(self):
        month = make_month(SEP, percents={"Загальні Накопичення": "30", "Їжа": "20"})
        self.assertEqual(month.unallocated, D("25000.00"))
        self.assertIn("50", month.percent_warning)

    def test_amount_only_limits_skip_percent_check(self):
        month = Month.objects.create(period=SEP, salary=D("1000"))
        MonthAllocation.objects.create(month=month, category=category("savings_pool"), limit_amount=D("400"))
        self.assertIsNone(month.percent_total)
        self.assertEqual(month.percent_warning, "")
        self.assertEqual(month.unallocated, D("600.00"))

    def test_salary_change_recalculates_limits(self):
        month = make_month(SEP)
        month.salary = D("60000")
        month.save()
        month.recalculate()
        self.assertEqual(month.savings_pool_allocation().limit_amount, D("18000.00"))

    def test_period_is_normalized_to_first_day(self):
        month = Month.objects.create(period=datetime.date(2025, 9, 17))
        self.assertEqual(month.period, SEP)
        self.assertEqual(str(month), "09.25")


class WithdrawalTests(TestCase):
    def test_withdrawal_is_percent_of_previous_savings_limit(self):
        make_month(SEP)
        withdrawal = add_withdrawal(make_month(OCT))
        # Приклад із ТЗ: 20% від 15 000 попереднього місяця = 3 000.
        self.assertEqual(withdrawal.limit_amount, D("3000.00"))

    def test_withdrawal_uses_previous_actual_when_present(self):
        september = make_month(SEP)
        pool = september.savings_pool_allocation()
        pool.actual = D("12000")
        pool.save()
        withdrawal = add_withdrawal(make_month(OCT))
        self.assertEqual(withdrawal.limit_amount, D("2400.00"))

    def test_withdrawal_not_part_of_salary_distribution(self):
        make_month(SEP)
        october = make_month(OCT)
        add_withdrawal(october)
        self.assertEqual(october.unallocated, D("0.00"))
        self.assertEqual(october.percent_total, D("100"))

    def test_first_month_keeps_manual_withdrawal(self):
        month = make_month(SEP)
        allocation = MonthAllocation.objects.create(
            month=month, category=category("withdrawal"), percent=D("20"), limit_amount=D("777")
        )
        month.recalculate()
        allocation.refresh_from_db()
        self.assertEqual(allocation.limit_amount, D("777.00"))

    def test_saving_previous_month_updates_following_withdrawal(self):
        september = make_month(SEP)
        withdrawal = add_withdrawal(make_month(OCT))
        pool = september.savings_pool_allocation()
        pool.actual = D("10000")
        pool.save()
        services.recalculate_with_following(september)
        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.limit_amount, D("2000.00"))

    def test_only_one_withdrawal_category(self):
        with self.assertRaises(IntegrityError):
            AllocationCategory.objects.create(name="Друге зняття", kind=AllocationCategory.Kind.WITHDRAWAL)


class TotalExpensesTests(TestCase):
    def test_manual_total_is_kept_and_mismatch_reported(self):
        month = make_month(SEP)
        add_line(month, "Rent", "10000")
        add_line(month, "Taxes", "2000")
        add_line(month, "Additional incomes", "5000")
        month.total_expenses = D("12500")
        month.save()
        self.assertEqual(month.expense_lines_total, D("12000.00"))
        self.assertEqual(month.income_lines_total, D("5000.00"))
        self.assertEqual(month.total_expenses_effective, D("12500"))
        self.assertEqual(month.total_expenses_mismatch, D("500.00"))

    def test_empty_total_falls_back_to_lines(self):
        month = make_month(SEP)
        add_line(month, "Rent", "10000")
        self.assertEqual(month.total_expenses_effective, D("10000.00"))
        self.assertIsNone(month.total_expenses_mismatch)


class ClosingTests(TestCase):
    def test_closed_month_freezes_children(self):
        month = make_month(SEP)
        services.close_month(month)
        allocation = month.allocations.first()
        allocation.actual = D("1")
        with self.assertRaises(MonthClosedError):
            allocation.save()
        with self.assertRaises(MonthClosedError):
            add_line(month, "Rent", "1")

    def test_closed_month_is_not_recalculated(self):
        september = make_month(SEP)
        october = make_month(OCT)
        withdrawal = add_withdrawal(october)
        services.close_month(october)

        pool = september.savings_pool_allocation()
        pool.actual = D("1000")
        pool.save()
        services.recalculate_with_following(september)

        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.limit_amount, D("3000.00"))

    def test_closed_month_rejects_salary_change(self):
        month = make_month(SEP)
        services.close_month(month)
        month.salary = D("1")
        with self.assertRaises(ValidationError) as caught:
            month.full_clean()
        self.assertIn("salary", caught.exception.message_dict)

    def test_reopen_allows_edits(self):
        month = make_month(SEP)
        services.close_month(month)
        services.reopen_month(month)
        allocation = month.allocations.first()
        allocation.actual = D("5")
        allocation.save()


class CreateMonthTests(TestCase):
    def test_first_month_uses_reference_data(self):
        month = services.create_month(SEP, salary=D("40000"))
        names = list(month.allocations.values_list("category__name", flat=True))
        self.assertIn("Загальні Накопичення", names)
        self.assertIn("Знято з накопичень", names)
        self.assertEqual(month.lines.count(), 4)

    def test_next_month_copies_percents_and_line_types(self):
        september = make_month(SEP, salary="50000")
        add_line(september, "Rent", "10000")
        october = services.create_next_month()

        self.assertEqual(october.period, OCT)
        self.assertEqual(october.salary, D("50000"))
        copied = {a.category.name: (a.percent, a.limit_amount) for a in october.allocations.select_related("category")}
        self.assertEqual(copied["Хата"], (D("25.00"), D("12500.00")))
        lines = [(line.line_type.name, line.amount) for line in october.lines.select_related("line_type")]
        self.assertEqual(lines, [("Rent", D("0"))])

    def test_next_month_copies_fixed_amount_limits(self):
        september = Month.objects.create(period=SEP, salary=D("1000"))
        MonthAllocation.objects.create(month=september, category=category("savings_pool"), limit_amount=D("300"))
        october = services.create_next_month()
        self.assertEqual(october.savings_pool_allocation().limit_amount, D("300.00"))

    def test_next_period_rolls_over_year(self):
        Month.objects.create(period=datetime.date(2025, 12, 1))
        self.assertEqual(services.next_period(), datetime.date(2026, 1, 1))
