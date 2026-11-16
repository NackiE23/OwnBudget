from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from expenses import sync
from expenses.api.authentication import DeviceTokenAuthentication, IsDevice
from expenses.api.serializers import SyncRequestSerializer


class SyncView(APIView):
    """
    GET: перевірка з'єднання й токена.
    POST: прийом відправки з телефона (лише телефон → сервер).
    """

    authentication_classes = [DeviceTokenAuthentication]
    permission_classes = [IsDevice]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "expenses-sync"

    def get(self, request):
        device = request.auth
        return Response(
            {
                "device": device.name,
                "last_sync_at": device.last_sync_at.isoformat() if device.last_sync_at else None,
                "server_time": timezone.now().isoformat(),
            }
        )

    def post(self, request):
        serializer = SyncRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = sync.apply_batch(request.auth, serializer.validated_data)
        except sync.SyncConflict as error:
            return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)
        return Response({**result, "server_time": timezone.now().isoformat()})
