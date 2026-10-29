import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from budget import services
from budget.models import Month, SavingsDestination, SavingsEntry
from budget.tests.factories import add_line, make_month

D = Decimal
SEP = datetime.date(2025, 9, 1)


class AdminTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("owner", "owner@example.com", "a-long-test-password")
        self.client.force_login(self.user)

    def test_pages_render(self):
        month = make_month(SEP)
        add_line(month, "Rent", "10000")
        SavingsEntry.objects.create(month=month, amount=D("113"), currency="USD")
        urls = [
            reverse("admin:index"),
            reverse("admin:budget_month_changelist"),
            reverse("admin:budget_month_changelist") + "?year=2025",
            reverse("admin:budget_month_add"),
            reverse("admin:budget_month_change", args=[month.pk]),
            reverse("admin:budget_month_overview"),
            reverse("admin:budget_savingsentry_changelist"),
            reverse("admin:budget_allocationcategory_changelist"),
            reverse("admin:budget_linetype_changelist"),
            reverse("admin:budget_savingsdestination_changelist"),
            reverse("admin:auth_user_changelist"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_change_form_shows_summary(self):
        month = make_month(SEP)
        response = self.client.get(reverse("admin:budget_month_change", args=[month.pk]))
        self.assertContains(response, "Нерозподілено")
        self.assertContains(response, "100.00%")

    def test_add_month_populates_from_reference_data(self):
        response = self.client.post(
            reverse("admin:budget_month_add"),
            {
                "period": "2025-09-01",
                "salary": "40000",
                "salary_expression": "",
                "total_expenses": "",
                "mark": "",
                "notes": "",
                "allocations-TOTAL_FORMS": "0",
                "allocations-INITIAL_FORMS": "0",
                "lines-TOTAL_FORMS": "0",
                "lines-INITIAL_FORMS": "0",
                "savings_entries-TOTAL_FORMS": "0",
                "savings_entries-INITIAL_FORMS": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        month = Month.objects.get(period=SEP)
        self.assertEqual(month.allocations.count(), 7)
        self.assertEqual(month.lines.count(), 4)

    def test_create_next_month_action(self):
        make_month(SEP)
        response = self.client.get(reverse("admin:budget_month_create_next_month"))
        october = Month.objects.get(period=datetime.date(2025, 10, 1))
        self.assertRedirects(response, reverse("admin:budget_month_change", args=[october.pk]))

    def test_close_and_reopen_actions(self):
        month = make_month(SEP)
        self.client.get(reverse("admin:budget_month_close_month", args=[month.pk]))
        month.refresh_from_db()
        self.assertTrue(month.is_closed)

        response = self.client.get(reverse("admin:budget_month_change", args=[month.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="salary"')

        self.client.get(reverse("admin:budget_month_reopen_month", args=[month.pk]))
        month.refresh_from_db()
        self.assertFalse(month.is_closed)

    def test_close_action_refused_for_closed_month(self):
        month = make_month(SEP)
        services.close_month(month)
        response = self.client.get(reverse("admin:budget_month_close_month", args=[month.pk]))
        self.assertEqual(response.status_code, 403)

    def test_overview_shows_newest_month_first(self):
        make_month(SEP)
        make_month(datetime.date(2025, 10, 1))
        content = self.client.get(reverse("admin:budget_month_overview")).content.decode("utf-8")
        self.assertLess(content.index("10.25"), content.index("09.25"))

    def test_export_csv(self):
        month = make_month(SEP)
        SavingsEntry.objects.create(
            month=month, amount=D("113"), currency="USD", destination=SavingsDestination.objects.get(name="моно")
        )
        response = self.client.get(reverse("admin:budget_month_export_all_csv"))
        content = response.content.decode("utf-8")
        self.assertTrue(content.startswith("﻿Month,Salary"))
        self.assertEqual(content.count("﻿"), 1)
        self.assertIn("09.25,50000.00", content)
        self.assertIn("113.00 USD моно", content)

        response = self.client.get(reverse("admin:budget_savingsentry_export_all_csv"))
        self.assertIn("09.25,113.00,USD,моно", response.content.decode("utf-8"))

    def test_savings_entry_cannot_be_added_to_closed_month(self):
        month = make_month(SEP)
        services.close_month(month)
        response = self.client.post(
            reverse("admin:budget_savingsentry_add"),
            {"month": month.pk, "amount": "1", "currency": "UAH", "raw_text": "", "note": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SavingsEntry.objects.exists())
