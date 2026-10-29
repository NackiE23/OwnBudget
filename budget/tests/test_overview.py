import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from budget import calculations, services
from budget.models import LineType, SavingsDestination, SavingsEntry
from budget.overview import Overview, OverviewError, actual_field, limit_field, line_field, save_month
from budget.expressions import ExpressionError, parse_amount
from budget.parsing import entry_text, parse_savings_line
from budget.tests.factories import add_line, by_name, make_month

D = Decimal
SEP = datetime.date(2025, 9, 1)
OCT = datetime.date(2025, 10, 1)


class MarkTests(SimpleTestCase):
    def mark(self, actual, limit, more_is_better=False):
        return calculations.allocation_mark(
            D(actual) if actual is not None else None, D(limit) if limit is not None else None, more_is_better=more_is_better
        )

    def test_within_tolerance_is_green(self):
        for actual in ["9000", "10000", "11000"]:
            self.assertEqual(self.mark(actual, "10000"), "green", actual)
            self.assertEqual(self.mark(actual, "10000", more_is_better=True), "green", actual)

    def test_spending_less_than_limit_is_gold(self):
        self.assertEqual(self.mark("8999.99", "10000"), "gold")
        self.assertEqual(self.mark("11000.01", "10000"), "red")

    def test_saving_more_than_target_is_gold(self):
        self.assertEqual(self.mark("11000.01", "10000", more_is_better=True), "gold")
        self.assertEqual(self.mark("8999.99", "10000", more_is_better=True), "red")

    def test_no_mark_without_both_values(self):
        self.assertEqual(self.mark(None, "10000"), "")
        self.assertEqual(self.mark("10000", None), "")


class ParsingTests(TestCase):
    def test_entry_text_round_trips(self):
        month = make_month(SEP)
        crypto = SavingsDestination.objects.get(name="Crypto")
        entries = [
            SavingsEntry(month=month, amount=D("113.00"), currency="USD", destination=SavingsDestination.objects.get(name="моно")),
            SavingsEntry(month=month, amount=D("110.50"), currency="EUR"),
            SavingsEntry(month=month, amount=D("200"), currency="PLN"),
            SavingsEntry(month=month, amount=D("2000"), currency="UAH", destination=crypto, note="на USDT"),
            SavingsEntry(month=month, amount=None, raw_text="4000 (-1500)"),
        ]
        texts = [entry_text(entry) for entry in entries]
        self.assertEqual(texts, ["$113 моно", "€110.5", "200zl", "2000 Crypto на USDT", "4000 (-1500)"])
        # Оригінальний рядок лишається, якщо він дає той самий запис.
        bonds = SavingsDestination.objects.get(name="облігації")
        original = SavingsEntry(month=month, amount=D("6000"), currency="UAH", destination=bonds, raw_text="2000+4000 облігації")
        self.assertEqual(entry_text(original), "2000+4000 облігації")
        two = SavingsEntry(month=month, amount=D("8000"), currency="UAH", raw_text="8000 + $200")
        self.assertEqual(entry_text(two), "8000")
        parsed = [parse_savings_line(text)[0] for text in texts[:3]]
        self.assertEqual([(p.amount, p.currency) for p in parsed], [(D("113"), "USD"), (D("110.5"), "EUR"), (D("200"), "PLN")])

    def test_parse_amount(self):
        self.assertEqual(parse_amount(""), (None, ""))
        self.assertEqual(parse_amount("1 200,50"), (D("1200.50"), ""))
        self.assertEqual(parse_amount("=54815+33545"), (D("88360"), "=54815+33545"))
        self.assertEqual(parse_amount("100 * 1.5"), (D("150.0"), "=100*1.5"))

    def test_expression_rejects_code(self):
        for text in ["__import__('os')", "abc", "1+"]:
            with self.assertRaises(ExpressionError, msg=text):
                parse_amount(text)


class OverviewTableTests(TestCase):
    def test_grouped_headers(self):
        table = Overview([make_month(SEP)])
        groups = [(h.label, h.colspan) for h in table.header_top if h.colspan == 2]
        self.assertEqual(groups[0], ("Загальні Накопичення", 2))
        self.assertEqual(table.header_bottom[:4], ["Як Є", "Ціль", "Як Є", "Межа"])

    def test_cells_carry_marks_and_editable_fields(self):
        month = make_month(SEP)
        pool = month.savings_pool_allocation()
        pool.actual = D("20000")  # ціль 15 000: накопичили більше
        pool.save()

        row = Overview([month]).row(month)
        cells = {cell.field: cell for cell in row.cells if cell.field}
        pool_cell = cells[actual_field(pool.category_id)]
        self.assertEqual((pool_cell.display, pool_cell.mark), ("20 000.00", "gold"))
        # Ліміт з відсотка не редагується, лише показується.
        self.assertNotIn(limit_field(pool.category_id), cells)
        self.assertTrue(row.editable)

        services.close_month(month)
        self.assertFalse(Overview([month]).row(month).editable)


class SaveMonthTests(TestCase):
    def setUp(self):
        self.september = make_month(SEP)
        self.month = make_month(OCT)
        self.rent = add_line(self.month, "Rent", "15000")
        self.food = by_name("Їжа")

    def test_saves_fields_and_recalculates(self):
        save_month(
            self.month,
            {
                "salary": "=30000+30000",
                "total_expenses": "",
                "notes": "-2000 лампа  ",
                line_field(self.rent.line_type_id): "15500",
                line_field(LineType.objects.get(name="Taxes").pk): "1200",
                actual_field(self.food.pk): "9 000",
            },
        )
        self.month.refresh_from_db()
        self.assertEqual((self.month.salary, self.month.salary_expression), (D("60000.00"), "=30000+30000"))
        self.assertIsNone(self.month.total_expenses)
        self.assertEqual(self.month.notes, "-2000 лампа")
        lines = {line.line_type.name: line.amount for line in self.month.lines.select_related("line_type")}
        self.assertEqual(lines, {"Rent": D("15500.00"), "Taxes": D("1200.00")})
        food = self.month.allocations.get(category=self.food)
        # Ліміт перераховано з нового Salary: 20% від 60 000.
        self.assertEqual((food.actual, food.limit_amount, food.mark), (D("9000.00"), D("12000.00"), "gold"))

    def test_percent_limits_are_not_editable(self):
        save_month(self.month, {limit_field(self.food.pk): "1"})
        self.assertEqual(self.month.allocations.get(category=self.food).limit_amount, D("10000.00"))

    def test_manual_limit_for_category_without_percent(self):
        vacation = by_name("На відпустку")
        self.month.allocations.filter(category=vacation).update(percent=None)
        save_month(self.month, {limit_field(vacation.pk): "7000"})
        self.assertEqual(self.month.allocations.get(category=vacation).limit_amount, D("7000.00"))

    def test_savings_text(self):
        save_month(self.month, {"savings": "$100 моно\n2000 Crypto на USDT\n500 нова картка"})
        entries = [
            (e.amount, e.currency, e.destination.name, e.note)
            for e in self.month.savings_entries.select_related("destination").order_by("pk")
        ]
        self.assertEqual(
            entries,
            [
                (D("100.00"), "USD", "моно", ""),
                (D("2000.00"), "UAH", "Crypto", "на USDT"),
                (D("500.00"), "UAH", "нова картка", ""),
            ],
        )
        ids = list(self.month.savings_entries.values_list("pk", flat=True))
        # Незмінений текст не перезаписує записи.
        save_month(self.month, {"savings": "$100 моно\r\n2000 Crypto на USDT\r\n500 нова картка\r\n"})
        self.assertEqual(list(self.month.savings_entries.values_list("pk", flat=True)), ids)

    def test_error_saves_nothing(self):
        with self.assertRaises(OverviewError) as caught:
            save_month(self.month, {"salary": "1000", "total_expenses": "abc"})
        self.assertIn("Total expenses", caught.exception.messages[0])
        self.month.refresh_from_db()
        self.assertEqual(self.month.salary, D("50000.00"))

    def test_closed_month_is_rejected(self):
        services.close_month(self.month)
        with self.assertRaises(Exception):
            save_month(self.month, {"salary": "1"})


class OverviewViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_superuser("owner", "owner@example.com", "a-long-test-password")
        self.client.force_login(user)
        self.closed = make_month(SEP)
        services.close_month(self.closed)
        self.open = make_month(OCT)

    def test_page_shows_inputs_only_for_open_month(self):
        response = self.client.get(reverse("admin:budget_month_overview"))
        self.assertContains(response, 'id="month-form-%d"' % self.open.pk)
        self.assertNotContains(response, 'id="month-form-%d"' % self.closed.pk)
        self.assertContains(response, reverse("admin:budget_month_change", args=[self.closed.pk]))
        self.assertContains(response, 'colspan="2"')

    def test_save_redirects_back_with_year(self):
        url = reverse("admin:budget_month_overview_save", args=[self.open.pk])
        response = self.client.post(url, {"salary": "60000", "year": "2025"})
        self.assertRedirects(
            response, reverse("admin:budget_month_overview") + f"?year=2025#month-{self.open.pk}", fetch_redirect_response=False
        )
        self.open.refresh_from_db()
        self.assertEqual(self.open.salary, D("60000.00"))

    def test_closed_month_is_not_saved(self):
        url = reverse("admin:budget_month_overview_save", args=[self.closed.pk])
        response = self.client.post(url, {"salary": "1"}, follow=True)
        self.assertContains(response, "закритий")
        self.closed.refresh_from_db()
        self.assertEqual(self.closed.salary, D("50000.00"))
