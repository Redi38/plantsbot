import pytest

from bot.db import crud
from bot.services import import_service

# --- parse_csv ---------------------------------------------------------


def test_parse_csv_basic():
    raw = "group,name,comment\nСуккуленты,Алоэ,пересадила\nСуккуленты,Хавортия,\n"
    rows = import_service.parse_csv(raw)
    assert len(rows) == 2
    assert rows[0] == import_service.ImportRow(group_name="Суккуленты", plant_name="Алоэ", comment="пересадила")
    assert rows[1] == import_service.ImportRow(group_name="Суккуленты", plant_name="Хавортия", comment=None)


def test_parse_csv_ungrouped_rows_have_none_group():
    raw = "group,name,comment\n,Алоэ,\n"
    rows = import_service.parse_csv(raw)
    assert rows[0].group_name is None


def test_parse_csv_skips_rows_without_name():
    raw = "group,name,comment\nСуккуленты,,\nСуккуленты,Алоэ,\n"
    rows = import_service.parse_csv(raw)
    assert len(rows) == 1
    assert rows[0].plant_name == "Алоэ"


def test_parse_csv_missing_name_column_raises():
    with pytest.raises(import_service.ImportParseError, match="name"):
        import_service.parse_csv("group,comment\nСуккуленты,привет\n")


def test_parse_csv_no_data_rows_raises():
    with pytest.raises(import_service.ImportParseError):
        import_service.parse_csv("group,name,comment\n")


# --- parse_markdown ------------------------------------------------------


def test_parse_markdown_grouped():
    raw = "Алоказии:\n- Алоказия Полли: пересадила в марте\n- Алоказия Одора\n\nСуккуленты:\n- Хавортия\n"
    rows = import_service.parse_markdown(raw)
    assert rows == [
        import_service.ImportRow("Алоказии", "Алоказия Полли", "пересадила в марте"),
        import_service.ImportRow("Алоказии", "Алоказия Одора", None),
        import_service.ImportRow("Суккуленты", "Хавортия", None),
    ]


def test_parse_markdown_supports_asterisk_bullets():
    raw = "Суккуленты:\n* Алоэ\n"
    rows = import_service.parse_markdown(raw)
    assert rows == [import_service.ImportRow("Суккуленты", "Алоэ", None)]


def test_parse_markdown_lines_before_any_heading_are_ungrouped():
    raw = "- Алоэ\nСуккуленты:\n- Хавортия\n"
    rows = import_service.parse_markdown(raw)
    assert rows[0].group_name is None
    assert rows[0].plant_name == "Алоэ"
    assert rows[1].group_name == "Суккуленты"


def test_parse_markdown_no_rows_raises():
    with pytest.raises(import_service.ImportParseError):
        import_service.parse_markdown("   \n\n   ")


def test_parse_markdown_plain_line_without_heading_is_treated_as_ungrouped_plant():
    """Задокументированное поведение: до первого заголовка вида 'Название:'
    любая непустая строка без '-'/'*' — это растение без группы, а не
    структурная ошибка."""
    rows = import_service.parse_markdown("Просто Алоэ")
    assert rows == [import_service.ImportRow(None, "Просто Алоэ", None)]


# --- build_preview / commit_import (нужна БД) ----------------------------


async def test_build_preview_flags_new_vs_existing_group_case_insensitively(session, user_id):
    await crud.get_or_create_group(session, user_id, "Суккуленты")
    await session.commit()

    rows = [
        import_service.ImportRow("суккуленты", "Алоэ", None),  # матчится по регистру
        import_service.ImportRow("Кактусы", "Опунция", None),  # новая группа
    ]
    preview = await import_service.build_preview(session, user_id, rows)

    by_name = {pg.name: pg for pg in preview}
    assert by_name["суккуленты"].is_new is False
    assert by_name["суккуленты"].matched_existing_name == "Суккуленты"
    assert by_name["Кактусы"].is_new is True


async def test_build_preview_groups_ungrouped_rows_together(session, user_id):
    rows = [
        import_service.ImportRow(None, "Алоэ", None),
        import_service.ImportRow(None, "Хавортия", None),
    ]
    preview = await import_service.build_preview(session, user_id, rows)
    assert len(preview) == 1
    assert preview[0].name == "Без группы"
    assert len(preview[0].plants) == 2


async def test_commit_import_creates_plants_and_groups(session, user_id):
    rows = [import_service.ImportRow("Суккуленты", "Алоэ", "комментарий")]
    preview = await import_service.build_preview(session, user_id, rows)
    count, skipped = await import_service.commit_import(session, user_id, preview)

    assert (count, skipped) == (1, 0)
    groups, _ = await crud.get_full_tree(session, user_id)
    assert len(groups) == 1
    assert groups[0].name == "Суккуленты"
    assert groups[0].plants[0].comment == "комментарий"


async def test_commit_import_skips_duplicates_within_same_batch(session, user_id):
    rows = [
        import_service.ImportRow("Суккуленты", "Алоэ", None),
        import_service.ImportRow("Суккуленты", "алоэ", None),  # дубль по регистру
    ]
    preview = await import_service.build_preview(session, user_id, rows)
    count, skipped = await import_service.commit_import(session, user_id, preview)
    assert (count, skipped) == (1, 1)


async def test_commit_import_skips_plants_already_in_db(session, user_id):
    await import_service.commit_import(
        session, user_id, await import_service.build_preview(session, user_id, [import_service.ImportRow(None, "Алоэ", None)])
    )
    count, skipped = await import_service.commit_import(
        session, user_id, await import_service.build_preview(session, user_id, [import_service.ImportRow(None, "Алоэ", None)])
    )
    assert (count, skipped) == (0, 1)


# --- render_preview_text (чистая функция форматирования) -----------------


def test_render_preview_text_mentions_counts_and_names():
    preview = [
        import_service.PreviewGroup("Суккуленты", is_new=True, matched_existing_name=None, plants=[
            import_service.ImportRow("Суккуленты", "Алоэ", None)
        ])
    ]
    text = import_service.render_preview_text(preview)
    assert "Суккуленты" in text
    assert "новая группа" in text
    assert "Алоэ" in text
    assert "Всего растений: 1" in text
