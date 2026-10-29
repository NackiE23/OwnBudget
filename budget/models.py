from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from budget import calculations

MONEY = {"max_digits": 14, "decimal_places": 2}
PERCENT = {
    "max_digits": 5,
    "decimal_places": 2,
    "validators": [MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
}


class MonthClosedError(ValidationError):
    """Спроба змінити дані закритого місяця."""


class Currency(models.TextChoices):
    UAH = "UAH", "UAH"
    USD = "USD", "USD"
    EUR = "EUR", "EUR"
    PLN = "PLN", "PLN"
    USDT = "USDT", "USDT"


class Month(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Відкритий"
        CLOSED = "closed", "Закритий"

    class Mark(models.TextChoices):
        GOLD = "gold", "Золотий"
        GREEN = "green", "Зелений"
        RED = "red", "Червоний"

    period = models.DateField("місяць", unique=True, help_text="Зберігається як перший день місяця.")
    salary = models.DecimalField("Salary", **MONEY, default=Decimal("0"))
    salary_expression = models.CharField(
        "формула Salary",
        max_length=255,
        blank=True,
        help_text="Вираз, яким Salary було набрано в Excel, якщо він був (наприклад =1200*41.5).",
    )
    total_expenses = models.DecimalField(
        "Total expenses",
        **MONEY,
        null=True,
        blank=True,
        help_text="Вводиться вручну, як у таблиці. Порожнє значення означає суму рядків витрат.",
    )
    notes = models.TextField("нотатки", blank=True)
    status = models.CharField("статус", max_length=10, choices=Status.choices, default=Status.OPEN)
    mark = models.CharField("позначка", max_length=10, choices=Mark.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period"]
        verbose_name = "місяць"
        verbose_name_plural = "місяці"

    def __str__(self) -> str:
        return self.period.strftime("%m.%y") if self.period else "новий місяць"

    def save(self, *args, **kwargs):
        if self.period:
            self.period = self.period.replace(day=1)
        super().save(*args, **kwargs)

    def clean(self):
        if self.period:
            self.period = self.period.replace(day=1)
        if self.pk and self.is_closed:
            stored = Month.objects.filter(pk=self.pk).values("salary", "status").first()
            if stored and stored["status"] == self.Status.CLOSED and stored["salary"] != self.salary:
                raise MonthClosedError({"salary": "Місяць закритий: спершу відкрийте його, щоб змінити Salary."})

    @property
    def is_closed(self) -> bool:
        return self.status == self.Status.CLOSED

    def ensure_open(self) -> None:
        if self.is_closed:
            raise MonthClosedError(f"Місяць {self} закритий: спершу відкрийте його.")

    # --- сусідні місяці -------------------------------------------------

    def previous(self) -> "Month | None":
        return Month.objects.filter(period__lt=self.period).order_by("-period").first()

    def following(self) -> "Month | None":
        return Month.objects.filter(period__gt=self.period).order_by("period").first()

    # --- розподіл ---------------------------------------------------------

    def _allocations(self) -> list["MonthAllocation"]:
        # Використовує prefetch, якщо він є (список місяців в адмінці, API).
        return list(self.allocations.all())

    def distribution_allocations(self) -> list["MonthAllocation"]:
        """Категорії, між якими ділиться Salary (усе, крім «Знято з накопичень»)."""
        return [a for a in self._allocations() if a.category.kind != AllocationCategory.Kind.WITHDRAWAL]

    def withdrawal_allocation(self) -> "MonthAllocation | None":
        return next(
            (a for a in self._allocations() if a.category.kind == AllocationCategory.Kind.WITHDRAWAL),
            None,
        )

    def savings_pool_allocation(self) -> "MonthAllocation | None":
        return next(
            (a for a in self._allocations() if a.category.kind == AllocationCategory.Kind.SAVINGS_POOL),
            None,
        )

    def savings_pool_value(self) -> Decimal | None:
        """
        Скільки пішло в «Загальні Накопичення» цього місяця: фактична сума,
        а якщо її ще не внесли, то ліміт.
        """
        pool = self.savings_pool_allocation()
        if pool is None:
            return None
        return pool.actual if pool.actual is not None else pool.limit_amount

    def withdrawal_base(self) -> Decimal | None:
        """База для «Знято з накопичень»: «Загальні Накопичення» попереднього місяця."""
        previous = self.previous()
        return previous.savings_pool_value() if previous else None

    @property
    def allocated_total(self) -> Decimal:
        return calculations.money(
            sum((a.limit_amount for a in self.distribution_allocations() if a.limit_amount is not None), Decimal("0"))
        )

    @property
    def unallocated(self) -> Decimal:
        return calculations.unallocated(self.salary, (a.limit_amount for a in self.distribution_allocations()))

    @property
    def percent_total(self) -> Decimal | None:
        """Сума відсотків, якщо всі категорії розподілу задані відсотками; інакше None."""
        allocations = self.distribution_allocations()
        if not allocations or any(a.percent is None for a in allocations):
            return None
        return calculations.percents_total(a.percent for a in allocations)

    @property
    def percent_warning(self) -> str:
        total = self.percent_total
        if total is None or total == calculations.HUNDRED:
            return ""
        return f"Сума відсотків {total}% замість 100%."

    @property
    def withdrawal_amount(self) -> Decimal | None:
        allocation = self.withdrawal_allocation()
        return allocation.limit_amount if allocation else None

    def recalculate(self) -> None:
        """
        Перераховує ліміти категорій із відсотків. Закриті місяці не чіпає:
        їхні суми заморожені.
        """
        if self.is_closed:
            return
        base = None
        base_loaded = False
        for allocation in self.allocations.select_related("category"):
            if allocation.percent is None:
                continue
            if allocation.category.kind == AllocationCategory.Kind.WITHDRAWAL:
                if not base_loaded:
                    base, base_loaded = self.withdrawal_base(), True
                if base is None:
                    # Перший місяць історії: базу взяти ніде, сума лишається ручною.
                    continue
                new_limit = calculations.share(base, allocation.percent)
            else:
                new_limit = calculations.share(self.salary, allocation.percent)
            if new_limit != allocation.limit_amount:
                allocation.limit_amount = new_limit
                allocation.save(update_fields=["limit_amount"])

    # --- рядки доходів і витрат ------------------------------------------

    def _lines_total(self, kind: str) -> Decimal:
        return calculations.money(
            sum((line.amount for line in self.lines.all() if line.line_type.kind == kind), Decimal("0"))
        )

    @property
    def expense_lines_total(self) -> Decimal:
        return self._lines_total(LineType.Kind.EXPENSE)

    @property
    def income_lines_total(self) -> Decimal:
        return self._lines_total(LineType.Kind.INCOME)

    @property
    def total_expenses_effective(self) -> Decimal:
        """Total expenses, як його бачить користувач: ручне значення або сума рядків."""
        return self.total_expenses if self.total_expenses is not None else self.expense_lines_total

    @property
    def total_expenses_mismatch(self) -> Decimal | None:
        """Різниця між ручним Total expenses і сумою рядків витрат, якщо вона є."""
        if self.total_expenses is None:
            return None
        lines = [line for line in self.lines.all() if line.line_type.kind == LineType.Kind.EXPENSE]
        if not lines:
            return None
        difference = calculations.money(self.total_expenses - self.expense_lines_total)
        return difference or None


class AllocationCategory(models.Model):
    class Kind(models.TextChoices):
        REGULAR = "regular", "Звичайна"
        SAVINGS_POOL = "savings_pool", "Загальні накопичення"
        WITHDRAWAL = "withdrawal", "Знято з накопичень"

    name = models.CharField("назва", max_length=100, unique=True)
    kind = models.CharField("тип", max_length=20, choices=Kind.choices, default=Kind.REGULAR)
    default_percent = models.DecimalField(
        "відсоток за замовчуванням",
        **PERCENT,
        null=True,
        blank=True,
        help_text="Для «Знято з накопичень» це відсоток від «Загальні Накопичення» попереднього місяця.",
    )
    sort_order = models.PositiveSmallIntegerField("порядок", default=0)
    is_active = models.BooleanField("активна", default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "категорія розподілу"
        verbose_name_plural = "категорії розподілу"
        constraints = [
            models.UniqueConstraint(
                fields=["kind"],
                condition=models.Q(kind__in=["savings_pool", "withdrawal"]),
                name="budget_single_special_category_kind",
                violation_error_message="Категорія такого типу може бути лише одна.",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class MonthChildMixin(models.Model):
    """Дані, що належать місяцю: їх не можна змінювати, поки місяць закритий."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.month.ensure_open()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.month.ensure_open()
        return super().delete(*args, **kwargs)


class MonthAllocation(MonthChildMixin):
    month = models.ForeignKey(Month, on_delete=models.CASCADE, related_name="allocations", verbose_name="місяць")
    category = models.ForeignKey(
        AllocationCategory, on_delete=models.PROTECT, related_name="allocations", verbose_name="категорія"
    )
    percent = models.DecimalField(
        "%",
        **PERCENT,
        null=True,
        blank=True,
        help_text="Якщо задано, ліміт рахується автоматично.",
    )
    limit_amount = models.DecimalField(
        "ліміт",
        **MONEY,
        null=True,
        blank=True,
        help_text="Ціль/Межа. Рахується з відсотка або вводиться вручну.",
    )
    actual = models.DecimalField("як є", **MONEY, null=True, blank=True)
    note = models.CharField("примітка", max_length=255, blank=True)

    class Meta:
        ordering = ["category__sort_order", "category__name"]
        verbose_name = "розподіл"
        verbose_name_plural = "розподіл за категоріями"
        constraints = [
            models.UniqueConstraint(fields=["month", "category"], name="budget_unique_month_category"),
        ]

    def __str__(self) -> str:
        return f"{self.month} · {self.category}"

    @property
    def difference(self) -> Decimal | None:
        """Ліміт мінус фактична сума: додатне значення означає, що лишилось."""
        if self.limit_amount is None or self.actual is None:
            return None
        return calculations.money(self.limit_amount - self.actual)


class LineType(models.Model):
    class Kind(models.TextChoices):
        INCOME = "income", "Дохід"
        EXPENSE = "expense", "Витрата"

    name = models.CharField("назва", max_length=100, unique=True)
    kind = models.CharField("тип", max_length=10, choices=Kind.choices)
    sort_order = models.PositiveSmallIntegerField("порядок", default=0)
    is_active = models.BooleanField("активний", default=True)

    class Meta:
        ordering = ["kind", "sort_order", "name"]
        verbose_name = "тип рядка"
        verbose_name_plural = "типи рядків"

    def __str__(self) -> str:
        return self.name


class MonthLine(MonthChildMixin):
    month = models.ForeignKey(Month, on_delete=models.CASCADE, related_name="lines", verbose_name="місяць")
    line_type = models.ForeignKey(LineType, on_delete=models.PROTECT, related_name="lines", verbose_name="тип")
    amount = models.DecimalField("сума", **MONEY, default=Decimal("0"))
    note = models.CharField("примітка", max_length=255, blank=True)

    class Meta:
        ordering = ["line_type__kind", "line_type__sort_order", "line_type__name", "pk"]
        verbose_name = "рядок доходу чи витрати"
        verbose_name_plural = "доходи й витрати"

    def __str__(self) -> str:
        return f"{self.month} · {self.line_type}: {self.amount}"


class SavingsDestination(models.Model):
    name = models.CharField("назва", max_length=100, unique=True)
    is_active = models.BooleanField("активне", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "призначення накопичень"
        verbose_name_plural = "призначення накопичень"

    def __str__(self) -> str:
        return self.name


class SavingsEntry(MonthChildMixin):
    month = models.ForeignKey(Month, on_delete=models.CASCADE, related_name="savings_entries", verbose_name="місяць")
    amount = models.DecimalField(
        "сума",
        **MONEY,
        null=True,
        blank=True,
        help_text="Порожня, якщо рядок з Excel не вдалося розібрати: тоді дивіться оригінальний текст.",
    )
    currency = models.CharField("валюта", max_length=4, choices=Currency.choices, default=Currency.UAH)
    destination = models.ForeignKey(
        SavingsDestination,
        on_delete=models.PROTECT,
        related_name="entries",
        null=True,
        blank=True,
        verbose_name="призначення",
    )
    raw_text = models.CharField("оригінальний текст", max_length=500, blank=True)
    note = models.CharField("примітка", max_length=255, blank=True)

    class Meta:
        ordering = ["-month__period", "pk"]
        verbose_name = "запис накопичень"
        verbose_name_plural = "записи накопичень"

    def __str__(self) -> str:
        amount = f"{self.amount} {self.currency}" if self.amount is not None else self.raw_text
        return f"{self.month} · {amount}"
