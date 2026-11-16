"""
OwnExpenses в адмінці: операції, журнал синхронізацій і пристрої.
Операції й журнал лише для перегляду: їх пише синхронізація з телефона.
"""

from decimal import Decimal

from django.contrib import admin, messages
from django.db.models import Count, Q, Sum
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import RangeDateFilter
from unfold.decorators import action, display

from expenses.api.authentication import issue_token
from expenses.formatting import money
from expenses.models import Category, Device, Operation, OperationType, SyncBatch


def minor_to_money(value: int | None) -> Decimal:
    return Decimal(value or 0) / 100


def totals(queryset) -> dict[str, Decimal | int]:
    sums = queryset.aggregate(
        expenses=Sum("amount_minor", filter=Q(type=OperationType.EXPENSE)),
        incomes=Sum("amount_minor", filter=Q(type=OperationType.INCOME)),
        count=Count("pk"),
    )
    expenses, incomes = minor_to_money(sums["expenses"]), minor_to_money(sums["incomes"])
    return {"expenses": expenses, "incomes": incomes, "balance": incomes - expenses, "count": sums["count"]}


class ReadOnlyAdmin(ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TopCategoryFilter(admin.SimpleListFilter):
    """Категорія верхнього рівня разом з її підкатегоріями."""

    title = "категорія"
    parameter_name = "category"

    def lookups(self, request, model_admin):
        categories = Category.objects.filter(parent__isnull=True, deleted_at__isnull=True)
        return [(str(c.pk), f"{c.name} ({c.get_type_display().lower()})") for c in categories]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        return queryset.filter(Q(category_id=self.value()) | Q(category__parent_id=self.value()))


@admin.register(Operation)
class OperationAdmin(ReadOnlyAdmin):
    list_display = ["date_display", "category_display", "description", "amount_display", "type"]
    list_display_links = None
    list_filter = [("date", RangeDateFilter), "type", TopCategoryFilter]
    list_filter_submit = True
    search_fields = ["description", "category__name", "category__parent__name"]
    list_per_page = 100
    list_before_template = "admin/expenses/operation/stats.html"

    def get_queryset(self, request):
        # Видалені на телефоні операції лишаються в базі як tombstone, але не показуються.
        return super().get_queryset(request).alive().select_related("category__parent")

    @display(description="дата", ordering="date")
    def date_display(self, obj: Operation) -> str:
        return obj.date.strftime("%d.%m.%Y")

    @display(description="категорія", ordering="category__name")
    def category_display(self, obj: Operation) -> str:
        return str(obj.category)

    @display(description="сума", ordering="amount_minor")
    def amount_display(self, obj: Operation) -> str:
        sign, color = ("+", "text-green-700 dark:text-green-500") if obj.type == OperationType.INCOME else ("−", "")
        return format_html('<span class="whitespace-nowrap {}">{}{}</span>', color, sign, money(obj.amount))

    def changelist_view(self, request: HttpRequest, extra_context=None):
        response = super().changelist_view(request, {"title": "Операції OwnExpenses", **(extra_context or {})})
        context = getattr(response, "context_data", None)
        if not context or "cl" not in context:
            return response
        today = timezone.localdate()
        alive = Operation.objects.alive()
        context["stats"] = {
            "filtered": totals(context["cl"].queryset),
            "is_filtered": context["cl"].has_active_filters or bool(context["cl"].query),
            "month": totals(alive.filter(date__year=today.year, date__month=today.month)),
            "all": totals(alive),
            "month_label": today.strftime("%m.%Y"),
        }
        context["last_sync"] = SyncBatch.objects.select_related("device").first()
        context["devices"] = Device.objects.filter(is_active=True)
        return response


@admin.register(SyncBatch)
class SyncBatchAdmin(ReadOnlyAdmin):
    list_display = ["received_at", "device", "app_version", "operations_display", "categories_display"]
    list_display_links = None
    list_filter = ["device"]

    def changelist_view(self, request: HttpRequest, extra_context=None):
        return super().changelist_view(request, {"title": "Журнал синхронізацій", **(extra_context or {})})

    @staticmethod
    def _counts(counts: dict) -> str:
        labels = [("created", "нових"), ("updated", "змінено"), ("deleted", "видалено"), ("stale", "застарілих")]
        parts = [f"{counts[key]} {label}" for key, label in labels if counts.get(key)]
        return ", ".join(parts) or "без змін"

    @display(description="операції")
    def operations_display(self, obj: SyncBatch) -> str:
        return self._counts(obj.result.get("operations", {}))

    @display(description="категорії")
    def categories_display(self, obj: SyncBatch) -> str:
        return self._counts(obj.result.get("categories", {}))


@admin.register(Device)
class DeviceAdmin(ModelAdmin):
    list_display = ["name", "token_hint_display", "is_active", "last_sync_at", "app_version"]
    fields = ["name", "is_active", "token_hint_display", "created_at", "last_sync_at", "app_version"]
    readonly_fields = ["token_hint_display", "created_at", "last_sync_at", "app_version"]
    actions_detail = ["rotate_token"]

    @display(description="токен")
    def token_hint_display(self, obj: Device) -> str:
        return f"{obj.token_hint}…" if obj.token_hint else "—"

    def has_delete_permission(self, request, obj=None):
        # Операції пристрою захищені PROTECT: пристрій вимикають, а не видаляють.
        return obj is None or not (obj.operations.exists() or obj.categories.exists())

    def save_model(self, request, obj: Device, form, change):
        token = None if change else issue_token(obj)
        super().save_model(request, obj, form, change)
        if token:
            self._show_token(request, obj, token)

    @staticmethod
    def _show_token(request: HttpRequest, device: Device, token: str) -> None:
        messages.warning(
            request,
            format_html(
                "Токен для «{}»: <code class='select-all'>{}</code> Збережіть його в OwnExpenses: більше він не показується.",
                device,
                token,
            ),
        )

    @action(description="Новий токен", icon="key", url_path="rotate-token")
    def rotate_token(self, request: HttpRequest, object_id):
        device = Device.objects.get(pk=object_id)
        token = issue_token(device)
        device.save(update_fields=["token_hash", "token_hint"])
        self._show_token(request, device, token)
        messages.info(request, "Старий токен більше не працює.")
        return HttpResponseRedirect(reverse("admin:expenses_device_change", args=[device.pk]))
