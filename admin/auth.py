"""
Аутентификация админки через обычную HTML-форму + подписанную cookie-сессию,
вместо браузерного попапа HTTP Basic Auth (тот рисуется поверх страницы самим
браузером, а не сайтом, и его не стилизовать/не убрать со страницы).

Логин/пароль сравниваются как раньше через ADMIN_USER/ADMIN_PASSWORD,
но после успешного входа выдаётся подписанный токен в httponly-cookie,
который проверяется на каждом запросе через require_auth().
"""

import os
import secrets

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

ADMIN_USER = os.environ["ADMIN_USER"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
# используется только для подписи cookie-сессии, не для хранения паролей —
# при желании можно зафиксировать через ADMIN_SECRET_KEY в .env, иначе
# генерируется при каждом запуске контейнера (тогда все сессии сбрасываются
# при рестарте — не критично для личной админки на 1-2 человека)
SECRET_KEY = os.getenv("ADMIN_SECRET_KEY") or secrets.token_hex(32)

SESSION_COOKIE = "admin_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 дней

_serializer = URLSafeTimedSerializer(SECRET_KEY)


class AuthRequired(Exception):
    """Кидается, когда валидной сессии нет — main.py ловит это
    и редиректит на /login, вместо того чтобы отдавать голый 401."""


def verify_credentials(username: str, password: str) -> bool:
    correct_user = secrets.compare_digest(username, ADMIN_USER)
    correct_password = secrets.compare_digest(password, ADMIN_PASSWORD)
    return correct_user and correct_password


def create_session_token(username: str) -> str:
    return _serializer.dumps(username)


def require_auth(request: Request) -> str:
    """Dependency для защищённых роутов. Поднимает AuthRequired,
    если cookie отсутствует, повреждена или истекла (>7 дней)."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise AuthRequired()
    try:
        username: str = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise AuthRequired()
    return username
