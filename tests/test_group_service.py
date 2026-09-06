from bot.db import crud
from bot.services import group_service, plant_service


async def test_rename(session, user_id):
    group, _ = await crud.get_or_create_group(session, user_id, "Старое имя")
    await session.commit()

    await group_service.rename(session, group, "Новое имя")

    reloaded = await crud.get_group(session, group.id, user_id)
    assert reloaded.name == "Новое имя"


async def test_remove_unlinks_plants_instead_of_deleting_them(session, user_id):
    group, _ = await crud.get_or_create_group(session, user_id, "Суккуленты")
    plant = await plant_service.add_plant(session, user_id, name="Алоэ", group_id=group.id)
    await session.commit()

    fresh_group = await crud.get_group(session, group.id, user_id)
    await group_service.remove(session, fresh_group)

    assert await crud.get_group(session, group.id, user_id) is None
    reloaded_plant = await crud.get_plant(session, plant.id, user_id)
    assert reloaded_plant is not None
    assert reloaded_plant.group_id is None


async def test_remove_with_plants_deletes_plants_too(session, user_id):
    group, _ = await crud.get_or_create_group(session, user_id, "Суккуленты")
    plant = await plant_service.add_plant(session, user_id, name="Алоэ", group_id=group.id)
    await session.commit()

    fresh_group = await crud.get_group(session, group.id, user_id)
    await group_service.remove_with_plants(session, fresh_group)

    assert await crud.get_group(session, group.id, user_id) is None
    assert await crud.get_plant(session, plant.id, user_id) is None


async def test_remove_move_plants_transfers_to_target_group(session, user_id):
    source, _ = await crud.get_or_create_group(session, user_id, "Старая группа")
    target, _ = await crud.get_or_create_group(session, user_id, "Новая группа")
    plant = await plant_service.add_plant(session, user_id, name="Алоэ", group_id=source.id)
    await session.commit()

    fresh_source = await crud.get_group(session, source.id, user_id)
    await group_service.remove_move_plants(session, fresh_source, target.id)

    assert await crud.get_group(session, source.id, user_id) is None
    reloaded_plant = await crud.get_plant(session, plant.id, user_id)
    assert reloaded_plant.group_id == target.id


async def test_get_all_returns_only_this_users_groups(session, user_id):
    await crud.get_or_create_group(session, user_id, "Суккуленты")
    other_user = await crud.get_or_create_user(session, telegram_id=999, username=None, full_name=None)
    await crud.get_or_create_group(session, other_user.id, "Чужая группа")
    await session.commit()

    groups = await group_service.get_all(session, user_id)
    assert [g.name for g in groups] == ["Суккуленты"]
