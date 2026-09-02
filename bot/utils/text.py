"""Утилиты для форматирования сообщений бота."""

_SAFE_CHUNK_SIZE = 3800  # запас относительно лимита Telegram в 4096 символов


def split_long_text(text: str, limit: int = _SAFE_CHUNK_SIZE) -> list[str]:
    """Режет текст на части не длиннее limit, разрывая только по границам строк
    (\\n), чтобы не разрубить HTML-тег типа <b>...</b> посередине.
    Используется везде, где сообщение боту может стать длиннее лимита Telegram
    в 4096 символов (общий список растений, предпросмотр импорта и т.п.)."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current_lines: list[str] = []
    current_len = 0

    for line in text.split("\n"):
        line_len = len(line) + 1  # +1 за символ переноса строки при join
        if current_len + line_len > limit and current_lines:
            chunks.append("\n".join(current_lines))
            current_lines = [line]
            current_len = line_len
        else:
            current_lines.append(line)
            current_len += line_len

    if current_lines:
        chunks.append("\n".join(current_lines))

    return chunks
