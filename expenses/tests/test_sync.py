import datetime
import uuid
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from budget.models import Month
from expenses.api.authentication import issue_token
from expenses.models import Category, Device, Operation, SyncBatch

FOOD, CAFE, SALARY = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
MS = 1_763_000_000_000  # якийсь момент у листопаді 2025, мілісекунди


def categories(*extra):
    return [
        # Підкатегорія навмисно раніше за батьківську.
        {"id": str(CAFE), "parent_id": str(FOOD), "name": "Кафе", "type": "expense", "sort_order": 1},
        {"id": str(FOOD), "parent_id": None, "name": "Їжа", "type": "expense", "sort_order": 0},
        {"id": str(SALARY), "name": "Зарплата", "type": "income"},
        *extra,
    ]


def operation(key, amount=12550, updated=MS, category=CAFE, **extra):
    return {
        "id": str(key),
        "type": "expense",
        "amount_minor": amount,
        "category_id": str(category),
        "date": "2025-11-14",
        "description": "кава",
        "created_at": MS,
        "updated_at": updated,
        **extra,
    }


class SyncTests(TestCase):
    def setUp(self):
        self.device = Device(name="Pixel")
        self.token = issue_token(self.device)
        self.device.save()
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.url = reverse("expenses-sync")

    def send(self, operations=(), deleted=(), cats=None, batch_id=None):
        payload = {
            "batch_id": str(batch_id or uuid.uuid4()),
            "app_version": "1.4.0",
            "categories": categories() if cats is None else cats,
            "operations": list(operations),
            "deleted_operations": list(deleted),
        }
        return self.client.post(self.url, payload, format="json")

    def test_token_is_required(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.post(self.url, {}, format="json").status_code, 401)
        anonymous.credentials(HTTP_AUTHORIZATION="Bearer wrong")
        self.assertEqual(anonymous.get(self.url).status_code, 401)
        self.device.is_active = False
        self.device.save()
        self.assertEqual(self.client.get(self.url).status_code, 401)
        # Хеш, а не сам токен.
        self.assertNotIn(self.token, self.device.token_hash)

    def test_ping(self):
        response = self.client.get(self.url)
        self.assertEqual((response.status_code, response.json()["device"]), (200, "Pixel"))

    def test_first_sync_and_retry(self):
        coffee = uuid.uuid4()
        batch = uuid.uuid4()
        response = self.send([operation(coffee)], batch_id=batch)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertFalse(body["duplicate"])
        self.assertEqual(body["categories"]["created"], 3)
        self.assertEqual(body["operations"]["created"], 1)

        saved = Operation.objects.get(uuid=coffee)
        self.assertEqual((saved.amount, saved.category.parent.name, saved.date), (Decimal("125.5"), "Їжа", datetime.date(2025, 11, 14)))
        self.device.refresh_from_db()
        self.assertEqual((self.device.app_version, self.device.last_sync_at is not None), ("1.4.0", True))

        # Відповідь загубилася, телефон шле те саме ще раз: нічого не дублюється.
        again = self.send([operation(coffee)], batch_id=batch).json()
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["operations"]["created"], 1)
        self.assertEqual((Operation.objects.count(), Category.objects.count(), SyncBatch.objects.count()), (1, 3, 1))

    def test_newer_version_wins(self):
        coffee = uuid.uuid4()
        self.send([operation(coffee)])
        counts = self.send([operation(coffee)]).json()["operations"]
        self.assertEqual(counts["unchanged"], 1)

        counts = self.send([operation(coffee, amount=9000, updated=MS + 1000)]).json()["operations"]
        self.assertEqual(counts["updated"], 1)
        # Запізніла стара копія не перезаписує свіжу.
        counts = self.send([operation(coffee, amount=12550, updated=MS)]).json()["operations"]
        self.assertEqual(counts["stale"], 1)
        self.assertEqual(Operation.objects.get(uuid=coffee).amount_minor, 9000)

    def test_deletion_tombstone(self):
        coffee = uuid.uuid4()
        self.send([operation(coffee, updated=MS + 5000)])
        # Видалення, старше за останню зміну (зміна після undo), не застосовується.
        self.assertEqual(self.send(deleted=[{"id": str(coffee), "deleted_at": MS}]).json()["operations"]["stale"], 1)

        counts = self.send(deleted=[{"id": str(coffee), "deleted_at": MS + 6000}]).json()["operations"]
        self.assertEqual(counts["deleted"], 1)
        self.assertFalse(Operation.objects.alive().exists())
        # Стара копія не воскрешає видалену операцію, а новіша (undo) відновлює.
        self.assertEqual(self.send([operation(coffee, updated=MS + 5000)]).json()["operations"]["stale"], 1)
        self.assertEqual(self.send([operation(coffee, updated=MS + 7000)]).json()["operations"]["updated"], 1)
        self.assertTrue(Operation.objects.alive().filter(uuid=coffee).exists())
        # Видалення невідомої операції нічого не ламає.
        self.assertEqual(self.send(deleted=[{"id": str(uuid.uuid4()), "deleted_at": MS}]).status_code, 200)

    def test_category_snapshot(self):
        self.send()
        counts = self.send(cats=categories()[1:]).json()["categories"]
        self.assertEqual((counts["deleted"], counts["unchanged"]), (1, 2))
        self.assertIsNotNone(Category.objects.get(uuid=CAFE).deleted_at)

        renamed = categories()
        renamed[0] = {**renamed[0], "name": "Кав'ярні", "parent_id": None}
        counts = self.send(cats=renamed).json()["categories"]
        self.assertEqual(counts["updated"], 1)
        cafe = Category.objects.get(uuid=CAFE)
        self.assertEqual((cafe.name, cafe.parent, cafe.deleted_at), ("Кав'ярні", None, None))

    def test_invalid_batches_change_nothing(self):
        cases = {
            "unknown category": [operation(uuid.uuid4(), category=uuid.uuid4())],
            "zero amount": [operation(uuid.uuid4(), amount=0)],
            "duplicate ids": [operation(FOOD), operation(FOOD)],
        }
        for name, operations in cases.items():
            with self.subTest(name):
                self.assertEqual(self.send(operations).status_code, 400)
        deep = categories({"id": str(uuid.uuid4()), "parent_id": str(CAFE), "name": "Глибше", "type": "expense"})
        self.assertEqual(self.send(cats=deep).status_code, 400)
        self.assertEqual((Category.objects.count(), SyncBatch.objects.count()), (0, 0))

    def test_uuid_of_another_device_is_rejected(self):
        coffee = uuid.uuid4()
        self.send([operation(coffee)])
        other = Device(name="Old phone")
        token = issue_token(other)
        other.save()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        payload = {"batch_id": str(uuid.uuid4()), "categories": categories(), "operations": [operation(coffee)]}
        self.assertEqual(client.post(self.url, payload, format="json").status_code, 409)

    def test_budget_is_not_touched(self):
        self.send([operation(uuid.uuid4())])
        self.assertFalse(Month.objects.exists())


class ExpensesAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("owner", "owner@example.com", "a-long-test-password"))

    def test_pages_and_stats(self):
        device = Device(name="Pixel")
        token = issue_token(device)
        device.save()
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        payload = {
            "batch_id": str(uuid.uuid4()),
            "categories": categories(),
            "operations": [
                operation(uuid.uuid4(), amount=10000),
                operation(uuid.uuid4(), amount=500000, category=SALARY, type="income"),
            ],
        }
        api.post(reverse("expenses-sync"), payload, format="json")

        response = self.client.get(reverse("admin:expenses_operation_changelist"))
        self.assertContains(response, "Остання синхронізація")
        self.assertEqual(response.context["stats"]["all"]["balance"], Decimal("4900"))
        self.assertContains(response, "4 900.00")
        filtered = self.client.get(reverse("admin:expenses_operation_changelist") + "?type__exact=income")
        self.assertEqual(filtered.context["stats"]["filtered"]["count"], 1)
        for name in ["expenses_syncbatch_changelist", "expenses_device_changelist"]:
            self.assertEqual(self.client.get(reverse(f"admin:{name}")).status_code, 200)

    def test_new_device_shows_token_once(self):
        response = self.client.post(reverse("admin:expenses_device_add"), {"name": "Pixel", "is_active": "on"}, follow=True)
        device = Device.objects.get()
        self.assertContains(response, "більше він не показується")
        self.assertEqual(len(device.token_hash), 64)
