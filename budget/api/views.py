from django.db.models import Prefetch
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from budget import services
from budget.api import serializers
from budget.models import (
    AllocationCategory,
    LineType,
    Month,
    MonthAllocation,
    MonthLine,
    SavingsDestination,
    SavingsEntry,
)


class AllocationCategoryViewSet(viewsets.ModelViewSet):
    queryset = AllocationCategory.objects.all()
    serializer_class = serializers.AllocationCategorySerializer


class LineTypeViewSet(viewsets.ModelViewSet):
    queryset = LineType.objects.all()
    serializer_class = serializers.LineTypeSerializer


class SavingsDestinationViewSet(viewsets.ModelViewSet):
    queryset = SavingsDestination.objects.all()
    serializer_class = serializers.SavingsDestinationSerializer


class MonthViewSet(viewsets.ModelViewSet):
    serializer_class = serializers.MonthSerializer

    def get_queryset(self):
        queryset = Month.objects.prefetch_related(
            Prefetch("allocations", queryset=MonthAllocation.objects.select_related("category")),
            Prefetch("lines", queryset=MonthLine.objects.select_related("line_type")),
            Prefetch("savings_entries", queryset=SavingsEntry.objects.select_related("destination")),
        )
        year = self.request.query_params.get("year")
        if year and year.isdigit():
            queryset = queryset.filter(period__year=int(year))
        return queryset

    def _fresh(self, month: Month) -> Response:
        return Response(self.get_serializer(self.get_queryset().get(pk=month.pk)).data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        month = serializer.save()
        services.add_blank_rows(month)
        services.recalculate_with_following(month)
        response = self._fresh(month)
        response.status_code = status.HTTP_201_CREATED
        return response

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        serializer = self.get_serializer(self.get_object(), data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        month = serializer.save()
        services.recalculate_with_following(month)
        return self._fresh(month)

    def perform_destroy(self, instance: Month):
        if instance.is_closed:
            raise ValidationError({"status": "Закритий місяць не можна видалити: спершу відкрийте його."})
        following = instance.following()
        instance.delete()
        if following:
            following.recalculate()

    @action(detail=False, methods=["post"])
    def next(self, request):
        """Створює порожній місяць після останнього."""
        month = services.create_next_month()
        response = self._fresh(month)
        response.status_code = status.HTTP_201_CREATED
        return response

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        month = self.get_object()
        services.close_month(month)
        return self._fresh(month)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        month = self.get_object()
        services.reopen_month(month)
        return self._fresh(month)


class MonthChildViewSet(viewsets.ModelViewSet):
    """Дані місяця; фільтр ?month=<id>. Після кожної зміни місяць перераховується."""

    def get_queryset(self):
        queryset = super().get_queryset()
        month = self.request.query_params.get("month")
        if month and month.isdigit():
            queryset = queryset.filter(month_id=int(month))
        return queryset

    def perform_create(self, serializer):
        services.recalculate_with_following(serializer.save().month)

    def perform_update(self, serializer):
        services.recalculate_with_following(serializer.save().month)

    def perform_destroy(self, instance):
        if instance.month.is_closed:
            raise ValidationError({"month": f"Місяць {instance.month} закритий: спершу відкрийте його."})
        month = instance.month
        instance.delete()
        services.recalculate_with_following(month)


class MonthAllocationViewSet(MonthChildViewSet):
    queryset = MonthAllocation.objects.select_related("category", "month")
    serializer_class = serializers.MonthAllocationSerializer


class MonthLineViewSet(MonthChildViewSet):
    queryset = MonthLine.objects.select_related("line_type", "month")
    serializer_class = serializers.MonthLineSerializer


class SavingsEntryViewSet(MonthChildViewSet):
    queryset = SavingsEntry.objects.select_related("destination", "month")
    serializer_class = serializers.SavingsEntrySerializer
