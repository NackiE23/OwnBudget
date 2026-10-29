import datetime
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

D = Decimal


class SplitLpottMigrationTests(TransactionTestCase):
    before = [("budget", "0005_amount_expressions")]
    after = [("budget", "0006_split_lpott")]

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_open_month_is_split_and_history_kept(self):
        apps = self.migrate(self.before)
        Month = apps.get_model("budget", "Month")
        MonthAllocation = apps.get_model("budget", "MonthAllocation")
        AllocationCategory = apps.get_model("budget", "AllocationCategory")
        lpott = AllocationCategory.objects.get(name="ЛПОТТ")
        closed = Month.objects.create(period=datetime.date(2026, 9, 1), salary=D("80000"), status="closed")
        current = Month.objects.create(period=datetime.date(2026, 10, 1), salary=D("88360"))
        MonthAllocation.objects.create(month=closed, category=lpott, percent=D("10"), limit_amount=D("8000"), actual=D("20463"))
        MonthAllocation.objects.create(month=current, category=lpott, percent=D("10"), limit_amount=D("8836"))

        apps = self.migrate(self.after)
        MonthAllocation = apps.get_model("budget", "MonthAllocation")
        AllocationCategory = apps.get_model("budget", "AllocationCategory")

        self.assertFalse(AllocationCategory.objects.get(name="ЛПОТТ").is_active)
        self.assertEqual(
            list(MonthAllocation.objects.filter(month__period=datetime.date(2026, 9, 1)).values_list("category__name", "actual")),
            [("ЛПОТТ", D("20463.00"))],
        )
        split = {
            a.category.name: (a.percent, a.limit_amount)
            for a in MonthAllocation.objects.filter(month__period=datetime.date(2026, 10, 1)).select_related("category")
        }
        self.assertEqual(
            split,
            {name: (D("2.00"), D("1767.20")) for name in ["Ліки", "Побут", "Одяг", "Тварини", "Транспорт"]},
        )
