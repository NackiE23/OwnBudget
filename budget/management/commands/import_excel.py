"""Імпорт історії з Excel-таблиці бюджету (етап 3 ТЗ)."""

from django.core.management.base import BaseCommand, CommandError

from budget.importer import Importer, WorkbookError, open_sheet


class Command(BaseCommand):
    help = (
        "Імпортує місяці з Excel-таблиці бюджету. Ключ запису місяць: повторний запуск оновлює "
        "місяці без дублів. Усі місяці, крім останнього, після імпорту закриваються."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", help="Файл .xlsx")
        parser.add_argument("--sheet", help="Назва аркуша (за замовчуванням активний)")
        parser.add_argument("--dry-run", action="store_true", help="Показати звіт, нічого не зберігаючи.")
        parser.add_argument(
            "--overwrite-closed",
            action="store_true",
            help="Перезаписати й закриті місяці (без прапорця вони пропускаються).",
        )

    def handle(self, *args, path, sheet=None, dry_run=False, overwrite_closed=False, **options):
        try:
            importer = Importer(open_sheet(path, sheet), overwrite_closed=overwrite_closed)
        except (OSError, KeyError, WorkbookError) as error:
            raise CommandError(str(error)) from error

        report = importer.run(dry_run=dry_run)
        for line in report.lines():
            self.stdout.write(line)
        if not dry_run:
            self.stdout.write(self.style.SUCCESS("Імпорт завершено."))
