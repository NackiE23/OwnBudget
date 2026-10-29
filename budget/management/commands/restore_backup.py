"""Відновлення даних бюджету з файлу, створеного dump_backup (вимога B-13)."""

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from budget.models import (
    AllocationCategory,
    LineType,
    Month,
    MonthAllocation,
    MonthLine,
    SavingsDestination,
    SavingsEntry,
)

# Порядок видалення: спершу залежні записи, потім довідники.
BUDGET_MODELS = [MonthAllocation, MonthLine, SavingsEntry, Month, AllocationCategory, LineType, SavingsDestination]


class Command(BaseCommand):
    help = "Відновлює дані бюджету з JSON-файлу. Поточні дані бюджету замінюються вмістом файлу."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Файл, створений командою dump_backup")
        parser.add_argument(
            "--keep-existing",
            action="store_true",
            help="Не видаляти поточні дані бюджету, а лише оновити й додати записи з файлу.",
        )
        parser.add_argument("--yes", action="store_true", help="Не питати підтвердження.")

    def handle(self, *args, path, keep_existing=False, yes=False, **options):
        if not keep_existing and not yes:
            answer = input("Поточні дані бюджету буде замінено вмістом файлу. Продовжити? [y/N] ")
            if answer.strip().lower() not in {"y", "yes", "т", "так"}:
                raise CommandError("Скасовано.")

        with transaction.atomic():
            if not keep_existing:
                # Масове видалення не викликає Model.delete(), тож закриті місяці його не блокують.
                for model in BUDGET_MODELS:
                    model.objects.all().delete()
            call_command("loaddata", path, verbosity=0)

        counts = ", ".join(f"{model._meta.verbose_name_plural}: {model.objects.count()}" for model in BUDGET_MODELS)
        self.stdout.write(self.style.SUCCESS(f"Відновлено з {path}. {counts}"))
