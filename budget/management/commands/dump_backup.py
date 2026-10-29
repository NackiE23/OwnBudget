"""Резервна копія всіх даних бюджету в JSON-файл (вимога B-13)."""

from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

BACKUP_MODELS = ["budget", "auth.user"]


class Command(BaseCommand):
    help = "Вивантажує всі дані бюджету (і користувачів) у JSON-файл."

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            nargs="?",
            help="Куди записати файл. За замовчуванням backups/budget-<дата>.json",
        )

    def handle(self, *args, path=None, **options):
        if path:
            target = Path(path)
        else:
            stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
            target = Path(settings.BASE_DIR) / "backups" / f"budget-{stamp}.json"
        target.parent.mkdir(parents=True, exist_ok=True)

        # Пишемо самі в UTF-8: dumpdata з output= на Windows бере кодування системи (cp1251).
        with target.open("w", encoding="utf-8") as stream:
            call_command("dumpdata", *BACKUP_MODELS, stdout=stream, indent=2, natural_foreign=True)
        self.stdout.write(self.style.SUCCESS(f"Копію збережено: {target}"))
