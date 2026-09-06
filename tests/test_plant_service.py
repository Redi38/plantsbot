import pytest

from bot.db import crud
from bot.services import plant_service


async def test_add_plant_without_group(session, user_id):
    plant = await plant_service.add_plant(session, user_id, name="Алоэ")
    assert plant.name == "Алоэ"
    assert plant.group_id is None


async def test_add_plant_creates_group_by_name(session, user_id):
    plant = await plant_service.add_plant(session, user_id, name="Алоэ", group_name="Суккуленты")
    assert plant.group_id is not None
    group = await crud.get_group(session, plant.group_id, user_id)
    assert group.name == "Суккуленты"


async def test_add_plant_reuses_existing_group_by_name(session, user_id):
    group, _ = await crud.get_or_create_group(session, user_id, "Суккуленты")
    await session.commit()

    plant = await plant_service.add_plant(session, user_id, name="Алоэ", group_name="Суккуленты")
    assert plant.group_id == group.id


async def test_add_plant_group_id_takes_priority_over_group_name(session, user_id):
    group, _ = await crud.get_or_create_group(session, user_id, "Настоящая группа")
    await session.commit()

    plant = await plant_service.add_plant(
        session, user_id, name="Алоэ", group_id=group.id, group_name="Другое имя, должно игнорироваться"
    )
    assert plant.group_id == group.id


async def test_add_plant_raises_on_duplicate_in_same_group(session, user_id):
    await plant_service.add_plant(session, user_id, name="Алоэ")
    with pytest.raises(plant_service.DuplicatePlantError) as exc_info:
        await plant_service.add_plant(session, user_id, name="алоэ")  # регистр не важен
    assert exc_info.value.existing.name == "Алоэ"


async def test_add_plant_duplicate_check_is_scoped_to_group(session, user_id):
    """Тот же самый по имени, но в другой группе — не дубль."""
    group, _ = await crud.get_or_create_group(session, user_id, "Суккуленты")
    await session.commit()

    await plant_service.add_plant(session, user_id, name="Алоэ")
    plant = await plant_service.add_plant(session, user_id, name="Алоэ", group_id=group.id)
    assert plant.group_id == group.id


async def test_add_plant_force_skips_duplicate_check(session, user_id):
    await plant_service.add_plant(session, user_id, name="Алоэ")
    plant = await plant_service.add_plant(session, user_id, name="Алоэ", force=True)
    assert plant.name == "Алоэ"


async def test_get_ungrouped_label_default(session, user_id):
    assert await plant_service.get_ungrouped_label(session, user_id) == "Без группы"


async def test_get_ungrouped_label_custom(session, user_id):
    user = await crud.get_user(session, user_id)
    await crud.set_ungrouped_label(session, user, "Прочее")
    await session.commit()
    assert await plant_service.get_ungrouped_label(session, user_id) == "Прочее"


async def test_render_pages_empty_list(session, user_id):
    pages = await plant_service.render_pages(session, user_id)
    assert len(pages) == 1
    assert "Пока нет ни одного растения" in pages[0]


async def test_render_pages_includes_group_and_ungrouped(session, user_id):
    await plant_service.add_plant(session, user_id, name="Алоэ", group_name="Суккуленты")
    await plant_service.add_plant(session, user_id, name="Хавортия")

    pages = await plant_service.render_pages(session, user_id)
    full_text = "\n".join(pages)
    assert "Суккуленты" in full_text
    assert "Алоэ" in full_text
    assert "Без группы" in full_text
    assert "Хавортия" in full_text


async def test_find_plants_by_term(session, user_id):
    await plant_service.add_plant(session, user_id, name="Алоказия Полли")
    await plant_service.add_plant(session, user_id, name="Алоказия Одора")
    await plant_service.add_plant(session, user_id, name="Хавортия")

    matches = await plant_service.find_plants_by_term(session, user_id, "алоказия")
    assert {p.name for p in matches} == {"Алоказия Полли", "Алоказия Одора"}
