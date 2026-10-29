import datetime
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from budget import services
from budget.models import Month, SavingsEntry
from budget.tests.factories import make_month

D = Decimal
SEP = datetime.date(2025, 9, 1)


class BackupTests(TestCase):
    def test_dump_and_restore_round_trip(self):
        month = make_month(SEP)
        SavingsEntry.objects.create(month=month, amount=D("113"), currency="USD", raw_text="$113 моно")
        services.close_month(month)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup.json"
            call_command("dump_backup", str(path), stdout=StringIO())

            # Зміни після копії мають зникнути після відновлення.
            make_month(datetime.date(2025, 10, 1))
            call_command("restore_backup", str(path), "--yes", stdout=StringIO())

        self.assertEqual(list(Month.objects.values_list("period", flat=True)), [SEP])
        restored = Month.objects.get()
        self.assertTrue(restored.is_closed)
        self.assertEqual(restored.allocations.count(), 6)
        self.assertEqual(restored.savings_entries.get().raw_text, "$113 моно")
        self.assertEqual(restored.unallocated, D("0.00"))
