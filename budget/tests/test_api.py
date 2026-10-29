import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from budget import services
from budget.models import Month
from budget.tests.factories import make_month

D = Decimal
SEP = datetime.date(2025, 9, 1)


class ApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("owner", "owner@example.com", "a-long-test-password")
        self.client.force_login(self.user)

    def test_requires_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get("/api/budget/months/").status_code, 403)

    def test_month_detail_has_calculations(self):
        month = make_month(SEP)
        data = self.client.get(f"/api/budget/months/{month.pk}/").json()
        self.assertEqual(data["label"], "09.25")
        self.assertEqual(data["unallocated"], "0.00")
        self.assertEqual(data["percent_total"], "100.00")
        self.assertEqual(len(data["allocations"]), 6)

    def test_create_month_populates_and_next_action(self):
        response = self.client.post("/api/budget/months/", {"period": "2025-09-01", "salary": "40000"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.json()["allocations"]), 11)

        response = self.client.post("/api/budget/months/next/")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["label"], "10.25")

    def test_allocation_update_recalculates(self):
        month = make_month(SEP)
        allocation = month.savings_pool_allocation()
        response = self.client.patch(f"/api/budget/allocations/{allocation.pk}/", {"percent": "40"})
        self.assertEqual(response.status_code, 200)
        month_data = self.client.get(f"/api/budget/months/{month.pk}/").json()
        self.assertEqual(month_data["unallocated"], "-5000.00")
        self.assertIn("110", month_data["percent_warning"])

    def test_closed_month_is_read_only(self):
        month = make_month(SEP)
        self.assertEqual(self.client.post(f"/api/budget/months/{month.pk}/close/").json()["status"], "closed")

        allocation = month.allocations.first()
        response = self.client.patch(f"/api/budget/allocations/{allocation.pk}/", {"actual": "1"})
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(f"/api/budget/months/{month.pk}/", {"salary": "1"})
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(f"/api/budget/months/{month.pk}/", {"notes": "ок"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.delete(f"/api/budget/months/{month.pk}/").status_code, 400)

        services.reopen_month(Month.objects.get(pk=month.pk))
        response = self.client.patch(f"/api/budget/allocations/{allocation.pk}/", {"actual": "1"})
        self.assertEqual(response.status_code, 200)
