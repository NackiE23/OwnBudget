from rest_framework.routers import DefaultRouter

from budget.api import views

router = DefaultRouter()
router.register("months", views.MonthViewSet, basename="month")
router.register("allocations", views.MonthAllocationViewSet, basename="allocation")
router.register("lines", views.MonthLineViewSet, basename="line")
router.register("savings-entries", views.SavingsEntryViewSet, basename="savings-entry")
router.register("categories", views.AllocationCategoryViewSet, basename="category")
router.register("line-types", views.LineTypeViewSet, basename="line-type")
router.register("destinations", views.SavingsDestinationViewSet, basename="destination")

urlpatterns = router.urls
