"""
Дані, які надсилає застосунок OwnExpenses. Сервер лише приймає їх: записи
створюються й змінюються синхронізацією, в адмінці вони тільки для перегляду.
Бюджет (app budget) їх сам не змінює: огляд лише підказує з них суми, а
вписує їх людина.

Кожен запис має UUID, згенерований на телефоні: за ним синхронізація
впізнає запис при повторній відправці, тож дублів не буває.
"""

from decimal import Decimal

from django.db import models


class OperationType(models.TextChoices):
    EXPENSE = "expense", "Витрата"
    INCOME = "income", "Надходження"


class Device(models.Model):
    """Телефон із OwnExpenses. Входить токеном; у базі лежить лише його SHA-256."""

    name = models.CharField("назва", max_length=100)
    token_hash = models.CharField("хеш токена", max_length=64, unique=True, editable=False)
    token_hint = models.CharField(
        "початок токена", max_length=8, editable=False, help_text="Щоб упізнати токен, не зберігаючи його."
    )
    is_active = models.BooleanField("активний", default=True)
    created_at = models.DateTimeField("створено", auto_now_add=True)
    last_sync_at = models.DateTimeField("остання синхронізація", null=True, blank=True, editable=False)
    app_version = models.CharField("версія застосунку", max_length=32, blank=True, editable=False)

    class Meta:
        ordering = ["name"]
        verbose_name = "пристрій"
        verbose_name_plural = "пристрої"

    def __str__(self) -> str:
        return self.name


class Category(models.Model):
    """Категорія або підкатегорія з телефона. Колір та іконка серверу не потрібні."""

    uuid = models.UUIDField(unique=True, editable=False)
    device = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="categories", verbose_name="пристрій")
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="subcategories", verbose_name="батьківська"
    )
    name = models.CharField("назва", max_length=100)
    type = models.CharField("тип", max_length=10, choices=OperationType.choices)
    is_archived = models.BooleanField("в архіві", default=False)
    sort_order = models.IntegerField("порядок", default=0)
    deleted_at = models.DateTimeField("видалено", null=True, blank=True)
    synced_at = models.DateTimeField("синхронізовано", auto_now=True)

    class Meta:
        ordering = ["type", "sort_order", "name"]
        verbose_name = "категорія витрат"
        verbose_name_plural = "категорії витрат"

    def __str__(self) -> str:
        return f"{self.parent.name} › {self.name}" if self.parent_id else self.name


class OperationQuerySet(models.QuerySet):
    def alive(self) -> "OperationQuerySet":
        return self.filter(deleted_at__isnull=True)


class Operation(models.Model):
    """
    Витрата чи надходження. Сума додатна й у копійках, як на телефоні; знак
    задає тип. Видалена на телефоні операція лишається з deleted_at, щоб
    запізніла копія її не воскресила.
    """

    uuid = models.UUIDField(unique=True, editable=False)
    device = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="operations", verbose_name="пристрій")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="operations", verbose_name="категорія")
    type = models.CharField("тип", max_length=10, choices=OperationType.choices)
    amount_minor = models.BigIntegerField("сума, коп.")
    date = models.DateField("дата")
    description = models.CharField("опис", max_length=500, blank=True)
    client_created_at = models.DateTimeField("створено на телефоні")
    client_updated_at = models.DateTimeField(
        "змінено на телефоні", help_text="Час останньої зміни або видалення: новіша версія перемагає."
    )
    deleted_at = models.DateTimeField("видалено", null=True, blank=True)
    synced_at = models.DateTimeField("синхронізовано", auto_now=True)

    objects = OperationQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-client_created_at"]
        indexes = [models.Index(fields=["date"])]
        verbose_name = "операція"
        verbose_name_plural = "операції"

    def __str__(self) -> str:
        return f"{self.date:%d.%m.%Y} · {self.category} · {self.amount}"

    @property
    def amount(self) -> Decimal:
        return Decimal(self.amount_minor) / 100

    @property
    def signed_amount(self) -> Decimal:
        return self.amount if self.type == OperationType.INCOME else -self.amount


class SyncBatch(models.Model):
    """
    Одна прийнята відправка. batch_id генерує телефон: повтор тієї самої
    відправки (обірвалась відповідь) повертає збережений результат і нічого
    не змінює вдруге. Зберігаються лише лічильники, не вміст.
    """

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="batches", verbose_name="пристрій")
    batch_id = models.UUIDField("ID відправки")
    received_at = models.DateTimeField("отримано", auto_now_add=True)
    app_version = models.CharField("версія застосунку", max_length=32, blank=True)
    result = models.JSONField("результат", default=dict)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "синхронізація"
        verbose_name_plural = "журнал синхронізацій"
        constraints = [
            models.UniqueConstraint(fields=["device", "batch_id"], name="expenses_unique_device_batch"),
        ]

    def __str__(self) -> str:
        return f"{self.device} · {self.received_at:%d.%m.%Y %H:%M}"
