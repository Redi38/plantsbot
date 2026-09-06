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
import time

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

ADMIN_USER = os.environ["ADMIN_USER"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
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


# ---------- Rate-limit попыток входа ----------
#
# Панель обычно живёт за SSH-туннелем/закрытым HTTPS (см. README), но если
# её когда-нибудь откроют наружу, ничего не мешает подбирать пароль перебором —
# verify_credentials() защищает только от timing-атак, а не от частоты попыток.
# In-memory счётчик по IP: после 5 неудачных попыток подряд — экспоненциально
# растущая блокировка (32с, 64с, 128с, ... до потолка в 1 час). Успешный вход
# сбрасывает счётчик. Хранится в памяти процесса — этого достаточно для одного
# инстанса админки; если её когда-нибудь будут запускать в нескольких
# репликах, стоит перенести в общее хранилище (Redis и т.п.).

LOGIN_RATE_LIMIT_THRESHOLD = 5  # попыток без задержки, прежде чем включится блокировка
LOGIN_RATE_LIMIT_BASE_SECONDS = 32
LOGIN_RATE_LIMIT_MAX_SECONDS = 60 * 60

_login_attempts: dict[str, list[float]] = {}
# ip -> (кол-во неудачных попыток подряд после threshold, время окончания блокировки)
_login_lockouts: dict[str, tuple[int, float]] = {}


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def login_lockout_remaining(ip: str) -> float:
    """Сколько секунд ещё ждать перед следующей попыткой для этого IP.
    0, если блокировки нет или она уже истекла."""
    entry = _login_lockouts.get(ip)
    if not entry:
        return 0.0
    _, locked_until = entry
    remaining = locked_until - time.monotonic()
    return remaining if remaining > 0 else 0.0


def register_failed_login(ip: str) -> None:
    attempts = _login_attempts.setdefault(ip, [])
    attempts.append(time.monotonic())
    # Считаем только попытки, которые уже привели к блокировке (после
    # threshold), возводя задержку в степень — 32с, 64с, 128с, ...
    strikes_over_threshold = len(attempts) - LOGIN_RATE_LIMIT_THRESHOLD
    if strikes_over_threshold > 0:
        delay = min(
            LOGIN_RATE_LIMIT_BASE_SECONDS * (2 ** (strikes_over_threshold - 1)),
            LOGIN_RATE_LIMIT_MAX_SECONDS,
        )
        count, _ = _login_lockouts.get(ip, (0, 0.0))
        _login_lockouts[ip] = (count + 1, time.monotonic() + delay)


def reset_login_attempts(ip: str) -> None:
    _login_attempts.pop(ip, None)
    _login_lockouts.pop(ip, None)


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
    except (BadSignature, SignatureExpired) as e:
        raise AuthRequired() from e
    return username
