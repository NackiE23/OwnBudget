from decimal import Decimal

from django.test import SimpleTestCase

from budget import calculations

D = Decimal


class CalculationsTests(SimpleTestCase):
    def test_share_matches_tz_example(self):
        self.assertEqual(calculations.share(D("50000"), D("30")), D("15000.00"))
        self.assertEqual(calculations.share(D("50000"), D("5")), D("2500.00"))

    def test_share_rounds_half_up_to_cents(self):
        self.assertEqual(calculations.share(D("333.33"), D("12.5")), D("41.67"))
        self.assertEqual(calculations.share(D("0.10"), D("5")), D("0.01"))

    def test_unallocated_ignores_empty_limits(self):
        self.assertEqual(calculations.unallocated(D("50000"), [D("15000"), None, D("10000")]), D("25000.00"))

    def test_unallocated_can_be_negative(self):
        self.assertEqual(calculations.unallocated(D("100"), [D("150")]), D("-50.00"))

    def test_percents_complete(self):
        self.assertTrue(calculations.percents_are_complete([D("30"), D("20"), D("50")]))
        self.assertFalse(calculations.percents_are_complete([D("30"), D("20")]))
