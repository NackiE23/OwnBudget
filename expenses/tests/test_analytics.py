import datetime
import json
import uuid
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from budget.models import Month
from budget.tests.factories import make_month
from expenses.analytics import WITHOUT_SUBCATEGORY, category_breakdown, monthly_expenses
from expenses.models import Category, Device, Operation, OperationType

D = Decimal
NOV = datetime.date(2025, 11, 1)
DEC = datetime.date(2025, 12, 1)


class AnalyticsFixture(TestCase):
    def setUp(self):
        self.device = Device.objects.create(name="Pixel", token_hash="x" * 64)
        self.food = self.category("Їжа")
        self.cafe = self.category("Кафе", parent=self.food)
        self.shop = self.category("Магазин", parent=self.food)
        self.transport = self.category("Транспорт")
        self.salary = self.category("Зарплата", type=OperationType.INCOME)

    def category(self, name, parent=None, type=OperationType.EXPENSE):
        return Category.objects.create(uuid=uuid.uuid4(), device=self.device, parent=parent, name=name, type=type)

    def operation(self, category, amount_minor, date, deleted=False):
        now = timezone.now()
        return Operation.objects.create(
            uuid=uuid.uuid4(),
            device=self.device,
            category=category,
            type=category.type,
            amount_minor=amount_minor,
            date=date,
            client_created_at=now,
            client_updated_at=now,
            deleted_at=now if deleted else None,
        )


class BreakdownTests(AnalyticsFixture):
    def test_subcategories_roll_up(self):
        day = datetime.date(2025, 11, 5)
        self.operation(self.cafe, 12550, day)
        self.operation(self.cafe, 4450, day)
        self.operation(self.shop, 30000, day)
        self.operation(self.food, 5000, day)  # прямо на батьківську
        self.operation(self.transport, 80000, day)
        self.operation(self.transport, 99999, day, deleted=True)
        self.operation(self.salary, 5000000, day)

        breakdown = category_breakdown(Operation.objects.alive())
        self.assertEqual(breakdown.total, D("1320"))
        self.assertEqual([(g.name, g.amount, g.count) for g in breakdown.groups], [("Транспорт", D("800"), 1), ("Їжа", D("520"), 4)])
        transport, food = breakdown.groups
        self.assertEqual(transport.children, [])
        self.assertEqual(transport.share, D("60.6"))
        self.assertEqual(
            [(c.name, c.amount) for c in food.children],
            [("Магазин", D("300")), ("Кафе", D("170")), (WITHOUT_SUBCATEGORY, D("50"))],
        )
        self.assertEqual(food.children[-1].key, f"{self.food.pk}-own")

        incomes = category_breakdown(Operation.objects.alive(), OperationType.INCOME)
        self.assertEqual([(g.name, g.amount, g.share) for g in incomes.groups], [("Зарплата", D("50000"), D("100.0"))])

    def test_monthly_expenses(self):
        self.operation(self.cafe, 10000, datetime.date(2025, 11, 1))
        self.operation(self.cafe, 20000, datetime.date(2025, 11, 30))
        self.operation(self.transport, 5000, datetime.date(2025, 12, 31))
        self.operation(self.transport, 7000, datetime.date(2026, 1, 1))
        self.operation(self.transport, 9000, datetime.date(2025, 10, 31))

        result = monthly_expenses([DEC, NOV])
        self.assertEqual(set(result), {NOV, DEC})
        self.assertEqual(result[NOV].total, D("300"))
        # Підкатегорія без інших у групі не розбивається на «без підкатегорії».
        self.assertEqual([(c.name, c.amount) for c in result[NOV].groups[0].children], [("Кафе", D("300"))])
        self.assertEqual(result[DEC].total, D("50"))
        self.assertEqual(monthly_expenses([]), {})
        self.assertEqual(monthly_expenses([datetime.date(2024, 1, 1)]), {})


class OverviewSuggestionTests(AnalyticsFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_superuser("owner", "owner@example.com", "a-long-test-password"))

    def test_open_month_gets_suggestions(self):
        open_month = make_month(NOV)
        closed = make_month(DEC)
        closed.status = Month.Status.CLOSED
        closed.save()
        self.operation(self.cafe, 12550, datetime.date(2025, 11, 14))
        self.operation(self.transport, 5000, datetime.date(2025, 12, 2))

        response = self.client.get(reverse("admin:budget_month_overview") + "?year=2025")
        data = response.context["expense_suggestions"]
        # Закритий місяць не редагується, тож і підказок для нього немає.
        self.assertEqual(set(data), {str(open_month.pk)})
        self.assertEqual(data[str(open_month.pk)]["total"], "125.5")
        self.assertEqual(data[str(open_month.pk)]["groups"][0]["name"], "Їжа")
        script = response.content.decode().split('id="expense-suggestions" type="application/json">')[1].split("</script>")[0]
        self.assertEqual(json.loads(script), data)
        self.assertContains(response, f'data-suggest="{open_month.pk}" data-suggest-field="total_expenses"')
        self.assertNotContains(response, f'data-suggest="{closed.pk}"')

    def test_no_suggestions_without_operations(self):
        make_month(NOV)
        response = self.client.get(reverse("admin:budget_month_overview") + "?year=2025")
        self.assertEqual(response.context["expense_suggestions"], {})
        self.assertNotContains(response, 'id="suggest-dialog"')
        self.assertNotContains(response, "data-suggest=")


class OperationsPageTests(AnalyticsFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_superuser("owner", "owner@example.com", "a-long-test-password"))

    def test_category_cards(self):
        today = timezone.localdate()
        self.operation(self.cafe, 12550, today)
        self.operation(self.salary, 500000, today)
        self.operation(self.transport, 70000, datetime.date(2020, 1, 1))
        url = reverse("admin:expenses_operation_changelist")

        response = self.client.get(url)
        titles = [title for title, _ in response.context["breakdowns"]]
        self.assertEqual(titles, ["Витрати", "Надходження"])
        self.assertContains(response, "Витрати за категоріями")
        self.assertContains(response, "Кафе")
        self.assertNotContains(response, "Транспорт</span>")  # не цей місяць

        # З фільтром картки рахують відфільтроване, а не поточний місяць.
        filtered = self.client.get(url + "?type__exact=expense")
        breakdowns = dict(filtered.context["breakdowns"])
        self.assertEqual(list(breakdowns), ["Витрати"])
        self.assertEqual(breakdowns["Витрати"].total, D("825.50"))
        self.assertContains(filtered, "за фільтром")
