"""
Прийом даних з OwnExpenses (лише телефон → сервер).

Протокол описано в README («Синхронізація з OwnExpenses»). Коротко:
- Відправка має batch_id: повтор тієї самої відправки нічого не змінює вдруге
  й отримує збережений результат.
- Категорії приходять повним списком щоразу (їх небагато): чого в списку
  немає, те видалено на телефоні.
- Операції приходять лише змінені з минулої відправки, видалені окремим
  списком (tombstone). Запис упізнається за UUID; з двох версій перемагає та,
  що новіша за часом зміни на телефоні, тож запізніла копія не перезапише
  свіжу й не воскресить видалену.
"""

import datetime
import uuid
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils import timezone

from expenses.models import Category, Device, Operation, SyncBatch

# Журнал синхронізацій тримаємо лише за останній час: лічильники старших відправок нікому не потрібні.
BATCH_RETENTION = datetime.timedelta(days=90)


class SyncConflict(ValueError):
    """Дані не можна прийняти: наприклад, UUID уже належить іншому пристрою."""


@dataclass
class Counts:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    stale: int = 0  # прийшла старша версія, ніж уже є на сервері

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class SyncResult:
    categories: Counts = field(default_factory=Counts)
    operations: Counts = field(default_factory=Counts)

    def as_dict(self) -> dict:
        return {"categories": self.categories.as_dict(), "operations": self.operations.as_dict()}


def from_millis(value: int) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(value / 1000, tz=datetime.timezone.utc)


def apply_batch(device: Device, data: dict) -> dict:
    """
    Застосовує перевірену відправку (див. SyncRequestSerializer). Усе або
    нічого: помилка скасовує всю відправку. Повертає лічильники й прапорець
    duplicate для повтору вже прийнятої відправки.
    """
    try:
        with transaction.atomic():
            previous = SyncBatch.objects.filter(device=device, batch_id=data["batch_id"]).first()
            if previous:
                return {"batch_id": str(previous.batch_id), "duplicate": True, **previous.result}

            result = SyncResult()
            categories = _sync_categories(device, data["categories"], result.categories)
            _sync_operations(device, data["operations"], categories, result.operations)
            _delete_operations(device, data["deleted_operations"], result.operations)

            now = timezone.now()
            SyncBatch.objects.create(
                device=device, batch_id=data["batch_id"], app_version=data["app_version"], result=result.as_dict()
            )
            device.last_sync_at = now
            device.app_version = data["app_version"]
            device.save(update_fields=["last_sync_at", "app_version"])
            SyncBatch.objects.filter(device=device, received_at__lt=now - BATCH_RETENTION).delete()
    except IntegrityError:
        # Та сама відправка паралельно: перша вже записала batch, віддаємо її результат.
        previous = SyncBatch.objects.filter(device=device, batch_id=data["batch_id"]).first()
        if previous is None:
            raise
        return {"batch_id": str(previous.batch_id), "duplicate": True, **previous.result}
    return {"batch_id": str(data["batch_id"]), "duplicate": False, **result.as_dict()}


def _foreign(model, uuids: set[uuid.UUID], device: Device) -> None:
    taken = model.objects.filter(uuid__in=uuids).exclude(device=device).values_list("uuid", flat=True)
    if taken:
        raise SyncConflict(f"UUID уже належить іншому пристрою: {', '.join(sorted(map(str, taken)))}.")


def _sync_categories(device: Device, items: list[dict], counts: Counts) -> dict[uuid.UUID, Category]:
    """Повний список категорій телефона. Повертає категорії за UUID для операцій."""
    incoming = {item["id"]: item for item in items}
    _foreign(Category, set(incoming), device)
    existing = {category.uuid: category for category in Category.objects.filter(device=device)}
    now = timezone.now()
    untouched = set()  # категорії без змін у першому проході: зміна батька зробить їх оновленими

    for key, item in incoming.items():
        values = {
            "name": item["name"],
            "type": item["type"],
            "is_archived": item["archived"],
            "sort_order": item["sort_order"],
            "deleted_at": None,
        }
        category = existing.get(key)
        if category is None:
            existing[key] = Category.objects.create(uuid=key, device=device, **values)
            counts.created += 1
            continue
        changed = [name for name, value in values.items() if getattr(category, name) != value]
        for name in changed:
            setattr(category, name, values[name])
        if changed:
            category.save(update_fields=[*changed, "synced_at"])
            counts.updated += 1
        else:
            untouched.add(key)

    # Батьків ставимо другим проходом: батьківська категорія може йти в списку пізніше.
    for key, item in incoming.items():
        category = existing[key]
        parent = existing[item["parent_id"]] if item["parent_id"] else None
        if category.parent_id != (parent.pk if parent else None):
            category.parent = parent
            category.save(update_fields=["parent", "synced_at"])
            if key in untouched:
                untouched.discard(key)
                counts.updated += 1
    counts.unchanged += len(untouched)

    for key, category in existing.items():
        if key not in incoming and category.deleted_at is None:
            category.deleted_at = now
            category.save(update_fields=["deleted_at", "synced_at"])
            counts.deleted += 1
    return {key: existing[key] for key in incoming}


def _sync_operations(
    device: Device, items: list[dict], categories: dict[uuid.UUID, Category], counts: Counts
) -> None:
    incoming = {item["id"]: item for item in items}
    _foreign(Operation, set(incoming), device)
    existing = {operation.uuid: operation for operation in Operation.objects.filter(device=device, uuid__in=incoming)}

    new, changed_operations, changed_fields = [], [], set()
    for key, item in incoming.items():
        updated_at = from_millis(item["updated_at"])
        values = {
            "category": categories[item["category_id"]],
            "type": item["type"],
            "amount_minor": item["amount_minor"],
            "date": item["date"],
            "description": item["description"] or "",
            "client_created_at": from_millis(item["created_at"]),
            "client_updated_at": updated_at,
            "deleted_at": None,
        }
        operation = existing.get(key)
        if operation is None:
            new.append(Operation(uuid=key, device=device, **values))
            counts.created += 1
            continue
        if updated_at < operation.client_updated_at:
            counts.stale += 1
            continue
        changed = [name for name, value in values.items() if getattr(operation, name) != value]
        if not changed:
            counts.unchanged += 1
            continue
        for name in changed:
            setattr(operation, name, values[name])
        changed_operations.append(operation)
        changed_fields.update(changed)
        counts.updated += 1

    Operation.objects.bulk_create(new)
    if changed_operations:
        # bulk_update не чіпає auto_now: synced_at ставимо самі.
        now = timezone.now()
        for operation in changed_operations:
            operation.synced_at = now
        Operation.objects.bulk_update(changed_operations, [*changed_fields, "synced_at"])


def _delete_operations(device: Device, items: list[dict], counts: Counts) -> None:
    incoming = {item["id"]: from_millis(item["deleted_at"]) for item in items}
    _foreign(Operation, set(incoming), device)
    for operation in Operation.objects.filter(device=device, uuid__in=incoming):
        deleted_at = incoming[operation.uuid]
        if operation.deleted_at is not None:
            counts.unchanged += 1
        elif deleted_at < operation.client_updated_at:
            # Операцію змінили вже після цього видалення (наприклад, undo).
            counts.stale += 1
        else:
            operation.deleted_at = deleted_at
            operation.client_updated_at = deleted_at
            operation.save(update_fields=["deleted_at", "client_updated_at", "synced_at"])
            counts.deleted += 1
    # Видалення операції, якої сервер ще не бачив, нічого не змінює: її просто не буде.
