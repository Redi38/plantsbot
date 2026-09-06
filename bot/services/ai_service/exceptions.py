"""Исключения AI-сервиса."""


class AIServiceUnavailable(Exception):
    pass


class AIServiceTimeout(AIServiceUnavailable):
    """Отдельно от прочих ошибок — нет смысла повторять запрос при таймауте сети."""


class AIServiceRateLimited(AIServiceUnavailable):
    """429 от провайдера. _call_api уже пытается подождать и повторить сама
    (см. client.py) — наружу это исключение долетает только если провайдер
    отказал повторно и ждать ещё раз уже нет смысла в рамках одного
    пользовательского запроса."""
