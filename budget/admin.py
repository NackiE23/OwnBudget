from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from budget import exports, services
from budget.models import (
    AllocationCategory,
    LineType,
    Month,
    MonthAllocation,
    MonthLine,
    SavingsDestination,
    SavingsEntry,
)


def fmt(value: Decimal | None) -> str:
    """Сума з пробілом між тисячами, як в Excel: 12 500.00."""
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", " ")


# --- Доступ ---------------------------------------------------------------

admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


# --- Довідники --------------------------------------------------------------


@admin.register(AllocationCategory)
class AllocationCategoryAdmin(ModelAdmin):
    list_display = ["name", "kind", "default_percent", "sort_order", "is_active"]
    list_editable = ["default_percent", "sort_order", "is_active"]
    list_filter = ["kind", "is_active"]


@admin.register(LineType)
class LineTypeAdmin(ModelAdmin):
    list_display = ["name", "kind", "sort_order", "is_active"]
    list_editable = ["sort_order", "is_active"]
    list_filter = ["kind", "is_active"]


@admin.register(SavingsDestination)
class SavingsDestinationAdmin(ModelAdmin):
    list_display = ["name", "is_active"]
    list_editable = ["is_active"]
    search_fields = ["name"]


# --- Місяць -----------------------------------------------------------------


class ClosedMonthInlineMixin:
    """Інлайни закритого місяця лише для читання."""

    def _editable(self, obj: Month | None) -> bool:
        return obj is None or not obj.is_closed

    def has_add_permission(self, request, obj=None):
        return self._editable(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._editable(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._editable(obj) and super().has_delete_permission(request, obj)


class MonthAllocationInline(ClosedMonthInlineMixin, TabularInline):
    model = MonthAllocation
    extra = 0
    fields = ["category", "percent", "limit_amount", "actual", "difference_display", "note"]
    readonly_fields = ["difference_display"]
    tab = True

    @display(description="Залишок")
    def difference_display(self, obj: MonthAllocation) -> str:
        return fmt(obj.difference)


class MonthLineInline(ClosedMonthInlineMixin, TabularInline):
    model = MonthLine
    extra = 0
    fields = ["line_type", "amount", "note"]
    tab = True


class SavingsEntryInline(ClosedMonthInlineMixin, TabularInline):
    model = SavingsEntry
    extra = 0
    fields = ["amount", "currency", "destination", "note", "raw_text"]
    tab = True


class YearListFilter(admin.SimpleListFilter):
    title = "рік"
    parameter_name = "year"

    def lookups(self, request, model_admin):
        years = Month.objects.dates("period", "year", order="DESC")
        return [(str(year.year), str(year.year)) for year in years]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(period__year=self.value())
        return queryset


STATUS_LABELS = {"Відкритий": "info", "Закритий": "success"}
MARK_LABELS = {"Золотий": "warning", "Зелений": "success", "Червоний": "danger"}


@admin.register(Month)
class MonthAdmin(ModelAdmin):
    inlines = [MonthAllocationInline, MonthLineInline, SavingsEntryInline]
    list_display = [
        "period_display",
        "salary_display",
        "total_expenses_display",
        "unallocated_display",
        "status_display",
        "mark_display",
    ]
    list_display_links = ["period_display"]
    list_filter = [YearListFilter, "status", "mark"]
    actions = ["close_selected", "reopen_selected", "export_selected_csv"]
    actions_list = ["create_next_month", "open_overview", "export_all_csv"]
    actions_detail = ["close_month", "reopen_month"]
    summary_fields = [
        "status",
        "allocated_display",
        "unallocated_display",
        "percent_total_display",
        "withdrawal_display",
        "expense_lines_display",
        "total_expenses_mismatch_display",
        "income_lines_display",
    ]
    fieldsets = [
        ("Місяць", {"fields": ["period", "salary", "salary_expression", "total_expenses", "mark", "notes"]}),
        ("Підсумки", {"fields": summary_fields}),
    ]
    warn_unsaved_form = True

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .prefetch_related(
                Prefetch("allocations", queryset=MonthAllocation.objects.select_related("category")),
                Prefetch("lines", queryset=MonthLine.objects.select_related("line_type")),
            )
        )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.summary_fields)
        if obj is not None and obj.is_closed:
            readonly += ["period", "salary", "salary_expression", "total_expenses"]
        return readonly

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            # Підсумки для нового місяця з'являться після збереження.
            return self.fieldsets[:1]
        return self.fieldsets

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_closed:
            return False
        return super().has_delete_permission(request, obj)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial.setdefault("period", services.next_period())
        latest = Month.objects.order_by("-period").first()
        if latest:
            initial.setdefault("salary", latest.salary)
        return initial

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        month = form.instance
        if not change:
            services.populate_from_previous(month)
        services.recalculate_with_following(month)
        if month.percent_warning:
            messages.warning(request, f"{month}: {month.percent_warning} Нерозподілено {fmt(month.unallocated)}.")
        mismatch = month.total_expenses_mismatch
        if mismatch:
            messages.info(
                request,
                f"{month}: Total expenses відрізняється від суми рядків витрат на {fmt(mismatch)}.",
            )

    # --- колонки й підсумки ----------------------------------------------

    @display(description="Місяць", ordering="period")
    def period_display(self, obj: Month) -> str:
        return str(obj)

    @display(description="Salary", ordering="salary")
    def salary_display(self, obj: Month) -> str:
        return fmt(obj.salary)

    @display(description="Total expenses")
    def total_expenses_display(self, obj: Month) -> str:
        return fmt(obj.total_expenses_effective)

    @display(description="Нерозподілено")
    def unallocated_display(self, obj: Month) -> str:
        return fmt(obj.unallocated)

    @display(description="Статус", ordering="status", label=STATUS_LABELS)
    def status_display(self, obj: Month) -> str:
        return obj.get_status_display()

    @display(description="Позначка", ordering="mark", label=MARK_LABELS)
    def mark_display(self, obj: Month) -> str:
        return obj.get_mark_display() if obj.mark else ""

    @display(description="Виділено")
    def allocated_display(self, obj: Month) -> str:
        return fmt(obj.allocated_total)

    @display(description="Сума відсотків")
    def percent_total_display(self, obj: Month) -> str:
        total = obj.percent_total
        if total is None:
            return "ліміти задані сумами"
        return f"{total}%" + (" ⚠ має бути 100%" if obj.percent_warning else "")

    @display(description="Знято з накопичень")
    def withdrawal_display(self, obj: Month) -> str:
        allocation = obj.withdrawal_allocation()
        if allocation is None:
            return "—"
        previous = obj.previous()
        base = obj.withdrawal_base()
        if allocation.percent is not None and base is not None:
            return f"{fmt(allocation.limit_amount)} ({allocation.percent}% від {fmt(base)} за {previous})"
        return fmt(allocation.limit_amount)

    @display(description="Сума рядків витрат")
    def expense_lines_display(self, obj: Month) -> str:
        return fmt(obj.expense_lines_total)

    @display(description="Розбіжність Total expenses")
    def total_expenses_mismatch_display(self, obj: Month) -> str:
        return fmt(obj.total_expenses_mismatch) if obj.total_expenses_mismatch else "немає"

    @display(description="Сума додаткових доходів")
    def income_lines_display(self, obj: Month) -> str:
        return fmt(obj.income_lines_total)

    # --- дії ----------------------------------------------------------------

    def _change_url(self, month: Month) -> str:
        return reverse("admin:budget_month_change", args=[month.pk])

    @action(description="Створити наступний місяць", icon="add_circle", url_path="create-next")
    def create_next_month(self, request: HttpRequest):
        month = services.create_next_month()
        messages.success(request, f"Створено місяць {month}. Розподіл скопійовано з попереднього.")
        return HttpResponseRedirect(self._change_url(month))

    @action(description="Огляд", icon="table_chart", url_path="go-overview")
    def open_overview(self, request: HttpRequest):
        return HttpResponseRedirect(reverse("admin:budget_month_overview"))

    @action(description="Експорт у CSV", icon="download", url_path="export-csv")
    def export_all_csv(self, request: HttpRequest):
        return exports.months_csv()

    def has_close_permission(self, request: HttpRequest, object_id=None) -> bool:
        if not self.has_change_permission(request):
            return False
        if object_id is None:
            return True
        return Month.objects.filter(pk=object_id, status=Month.Status.OPEN).exists()

    def has_reopen_permission(self, request: HttpRequest, object_id=None) -> bool:
        if not self.has_change_permission(request):
            return False
        if object_id is None:
            return True
        return Month.objects.filter(pk=object_id, status=Month.Status.CLOSED).exists()

    @action(description="Закрити місяць", icon="lock", url_path="close", permissions=["close"])
    def close_month(self, request: HttpRequest, object_id):
        month = Month.objects.get(pk=object_id)
        services.close_month(month)
        messages.success(request, f"Місяць {month} закрито, суми зафіксовано.")
        return HttpResponseRedirect(self._change_url(month))

    @action(description="Відкрити місяць", icon="lock_open", url_path="reopen", permissions=["reopen"])
    def reopen_month(self, request: HttpRequest, object_id):
        month = Month.objects.get(pk=object_id)
        services.reopen_month(month)
        messages.success(request, f"Місяць {month} знову відкритий.")
        return HttpResponseRedirect(self._change_url(month))

    @admin.action(description="Закрити вибрані місяці")
    def close_selected(self, request, queryset):
        for month in queryset.order_by("period"):
            services.close_month(month)
        messages.success(request, f"Закрито місяців: {queryset.count()}.")

    @admin.action(description="Відкрити вибрані місяці")
    def reopen_selected(self, request, queryset):
        for month in queryset.order_by("period"):
            services.reopen_month(month)
        messages.success(request, f"Відкрито місяців: {queryset.count()}.")

    @admin.action(description="Експорт вибраних у CSV")
    def export_selected_csv(self, request, queryset):
        return exports.months_csv(queryset)

    # --- огляд (B-10) -----------------------------------------------------

    def get_urls(self):
        urls = [
            path(
                "overview/",
                self.admin_site.admin_view(self.overview_view),
                name="budget_month_overview",
            ),
        ]
        return urls + super().get_urls()

    def overview_view(self, request: HttpRequest):
        queryset = Month.objects.all()
        year = request.GET.get("year", "")
        if year.isdigit():
            queryset = queryset.filter(period__year=int(year))
        table = exports.MonthTable(exports.months_queryset(queryset))
        rows = [[fmt(v) if isinstance(v, Decimal) else (v or "") for v in row] for row in table.rows]
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Огляд місяців",
            "years": [d.year for d in Month.objects.dates("period", "year", order="DESC")],
            "selected_year": year,
            "table": {"headers": table.headers, "rows": rows},
        }
        return TemplateResponse(request, "admin/budget/month/overview.html", context)


# --- Записи накопичень ------------------------------------------------------


class SavingsEntryForm(forms.ModelForm):
    class Meta:
        model = SavingsEntry
        fields = "__all__"

    def clean_month(self):
        month = self.cleaned_data["month"]
        if month.is_closed:
            raise forms.ValidationError("Місяць закритий: спершу відкрийте його.")
        return month


@admin.register(SavingsEntry)
class SavingsEntryAdmin(ModelAdmin):
    form = SavingsEntryForm
    list_display = ["month", "amount", "currency", "destination", "note", "raw_text"]
    list_filter = ["currency", "destination", ("month__period", admin.DateFieldListFilter)]
    list_select_related = ["month", "destination"]
    search_fields = ["raw_text", "note", "destination__name"]
    actions = ["export_selected_csv"]
    actions_list = ["export_all_csv"]

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.month.is_closed:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.month.is_closed:
            return False
        return super().has_delete_permission(request, obj)

    @action(description="Експорт у CSV", icon="download", url_path="export-csv")
    def export_all_csv(self, request: HttpRequest):
        return exports.savings_csv(SavingsEntry.objects.all())

    @admin.action(description="Експорт вибраних у CSV")
    def export_selected_csv(self, request, queryset):
        return exports.savings_csv(queryset)
