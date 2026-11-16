import hashlib
import secrets

from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication, exceptions, permissions

from expenses.models import Device

TOKEN_BYTES = 32


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(device: Device) -> str:
    """Новий токен пристрою. Повертає його один раз: у базі лишається тільки хеш."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    device.token_hash = hash_token(token)
    device.token_hint = token[:6]
    return token


class DeviceTokenAuthentication(authentication.BaseAuthentication):
    """Заголовок «Authorization: Bearer <токен>». Користувач Django тут не потрібен: request.auth це Device."""

    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode(errors="replace").split()
        if not header or header[0] != self.keyword:
            return None
        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Очікується «Authorization: Bearer <токен>».")
        device = Device.objects.filter(token_hash=hash_token(header[1]), is_active=True).first()
        if device is None:
            raise exceptions.AuthenticationFailed("Невідомий або вимкнений токен пристрою.")
        return AnonymousUser(), device

    def authenticate_header(self, request) -> str:
        return self.keyword


class IsDevice(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        return isinstance(request.auth, Device)
