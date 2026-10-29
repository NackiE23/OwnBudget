from rest_framework import serializers

from budget.models import (
    AllocationCategory,
    LineType,
    Month,
    MonthAllocation,
    MonthLine,
    SavingsDestination,
    SavingsEntry,
)


class AllocationCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AllocationCategory
        fields = ["id", "name", "kind", "default_percent", "sort_order", "is_active"]


class LineTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LineType
        fields = ["id", "name", "kind", "sort_order", "is_active"]


class SavingsDestinationSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavingsDestination
        fields = ["id", "name", "is_active"]


class MonthChildSerializer(serializers.ModelSerializer):
    """Не дає змінювати дані закритого місяця."""

    def validate(self, attrs):
        months = [attrs.get("month"), getattr(self.instance, "month", None)]
        for month in months:
            if month is not None and month.is_closed:
                raise serializers.ValidationError({"month": f"Місяць {month} закритий: спершу відкрийте його."})
        return attrs


class MonthAllocationSerializer(MonthChildSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_kind = serializers.CharField(source="category.kind", read_only=True)
    difference = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = MonthAllocation
        fields = [
            "id",
            "month",
            "category",
            "category_name",
            "category_kind",
            "percent",
            "limit_amount",
            "actual",
            "difference",
            "mark",
            "note",
        ]


class MonthLineSerializer(MonthChildSerializer):
    line_type_name = serializers.CharField(source="line_type.name", read_only=True)
    line_type_kind = serializers.CharField(source="line_type.kind", read_only=True)

    class Meta:
        model = MonthLine
        fields = ["id", "month", "line_type", "line_type_name", "line_type_kind", "amount", "note"]


class SavingsEntrySerializer(MonthChildSerializer):
    destination_name = serializers.CharField(source="destination.name", read_only=True, default=None)

    class Meta:
        model = SavingsEntry
        fields = ["id", "month", "amount", "currency", "destination", "destination_name", "raw_text", "note"]


def money_field():
    return serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, allow_null=True)


class MonthSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="__str__", read_only=True)
    allocated_total = money_field()
    unallocated = money_field()
    percent_total = serializers.DecimalField(max_digits=6, decimal_places=2, read_only=True, allow_null=True)
    percent_warning = serializers.CharField(read_only=True)
    withdrawal_amount = money_field()
    expense_lines_total = money_field()
    income_lines_total = money_field()
    total_expenses_effective = money_field()
    total_expenses_mismatch = money_field()
    allocations = MonthAllocationSerializer(many=True, read_only=True)
    lines = MonthLineSerializer(many=True, read_only=True)
    savings_entries = SavingsEntrySerializer(many=True, read_only=True)

    class Meta:
        model = Month
        fields = [
            "id",
            "period",
            "label",
            "salary",
            "salary_expression",
            "total_expenses",
            "notes",
            "status",
            "mark",
            "allocated_total",
            "unallocated",
            "percent_total",
            "percent_warning",
            "withdrawal_amount",
            "expense_lines_total",
            "income_lines_total",
            "total_expenses_effective",
            "total_expenses_mismatch",
            "allocations",
            "lines",
            "savings_entries",
        ]
        # Статус змінюється лише діями close / reopen.
        read_only_fields = ["status"]

    def validate(self, attrs):
        if self.instance is not None and self.instance.is_closed:
            frozen = {"period", "salary", "salary_expression", "total_expenses"} & attrs.keys()
            changed = [name for name in frozen if attrs[name] != getattr(self.instance, name)]
            if changed:
                raise serializers.ValidationError(
                    {name: "Місяць закритий: спершу відкрийте його." for name in changed}
                )
        return attrs
