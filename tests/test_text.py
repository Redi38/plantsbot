from bot.utils.text import split_long_text


def test_short_text_is_returned_as_single_chunk():
    text = "короткий текст"
    assert split_long_text(text) == [text]


def test_splits_only_on_line_boundaries():
    """Не должен разрывать строку посередине (например, HTML-тег
    <b>...</b>) — режет только по \\n."""
    lines = [f"строка {i}" for i in range(10)]
    text = "\n".join(lines)
    chunks = split_long_text(text, limit=20)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 20 or "\n" not in chunk  # одна длинная строка тоже не режется пополам
    # ни одна исходная строка не оказалась разрублена
    reassembled_lines = "\n".join(chunks).split("\n")
    assert reassembled_lines == lines


def test_reassembling_chunks_preserves_original_text():
    text = "\n".join(f"строка номер {i} с каким-то текстом" for i in range(50))
    chunks = split_long_text(text, limit=100)
    assert "\n".join(chunks) == text


def test_single_line_longer_than_limit_is_not_split():
    """Функция режет только по границам строк — если одна строка сама по
    себе длиннее limit, она остаётся целой строкой в своём чанке."""
    long_line = "а" * 200
    chunks = split_long_text(long_line, limit=50)
    assert chunks == [long_line]


def test_default_limit_keeps_telegram_sized_text_whole():
    text = "x" * 3000
    assert split_long_text(text) == [text]
