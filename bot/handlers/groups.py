from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import crud
from bot.db.database import get_session
from bot.handlers.list_view import group_menu_text_and_kb, send_group_page
from bot.keyboards.inline import confirm_delete_keyboard, groups_keyboard
from bot.keyboards.reply import MENU_BUTTONS
from bot.services import group_service, plant_service
from bot.utils.chat import pop_tracked, render, safe_delete_message, safe_edit_text, track_callback

router = Router(name="groups")


class RenameGroup(StatesGroup):
    new_name = State()


class DeleteGroup(StatesGroup):
    new_group_name = State()


@router.callback_query(F.data.startswith("lgrename:"))
async def lgrename_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Переименование прямо из просмотра конкретной группы — группа уже
    известна из токена, поэтому сразу спрашиваем новое название."""
    token = callback.data.split(":", 1)[1]
    await state.clear()
    await state.update_data(group_id=int(token))
    await state.set_state(RenameGroup.new_name)
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад", callback_data=f"lg:{token}", style="primary")
    await callback.message.edit_text("✏️ Новое название группы?", reply_markup=builder.as_markup())
    await track_callback(callback, state)


@router.message(StateFilter(RenameGroup.new_name))
async def rename_apply(message: Message, state: FSMContext, user_id: int) -> None:
    data = await state.get_data()
    tracked_id = await pop_tracked(state)

    async with get_session() as session:
        group = await crud.get_group(session, data["group_id"], user_id)
        if group is None:
            await state.clear()
            await message.answer("⚠️ Группа не найдена, возможно уже удалена.")
            return

        old_name = group.name
        new_name = message.text.strip()
        await group_service.rename(session, group, new_name)
        group_id = group.id

    await state.clear()
    await send_group_page(
        message, user_id, str(group_id), edit_message_id=tracked_id, notice=f"✅ Готово: «{old_name}» → «{new_name}»"
    )


# ---------- Удаление группы ----------

@router.callback_query(F.data.startswith("lggdel:"))
async def lggdel_menu(callback: CallbackQuery) -> None:
    """Спрашивает, как поступить с растениями внутри удаляемой группы."""
    gid = callback.data.split(":", 1)[1]
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Удалить, растения переместить", callback_data=f"lggdelmove:{gid}", style="primary")
    builder.button(text="🗑 Удалить вместе с растениями", callback_data=f"lggdelwith:{gid}", style="danger")
    builder.button(text="⬅️ Назад", callback_data=f"lg:{gid}", style="primary")
    builder.adjust(1)
    await callback.message.edit_text(
        "🗑 Удалить группу — как поступить с растениями внутри неё?", reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("lggdelwith:"))
async def lggdel_with_confirm_ask(callback: CallbackQuery) -> None:
    gid = callback.data.split(":", 1)[1]
    await callback.answer()
    kb = confirm_delete_keyboard(
        f"lggdelwithc:{gid}",
        f"lggdel:{gid}",
        confirm_label="🗑 Да, удалить всё",
        cancel_label="⬅️ Назад",
        cancel_style="primary",
    )
    await callback.message.edit_text(
        "⚠️ Группа и все растения внутри неё будут удалены безвозвратно. Точно?",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("lggdelwithc:"))
async def lggdel_with_apply(callback: CallbackQuery, user_id: int) -> None:
    gid = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        group = await crud.get_group(session, gid, user_id)
        if group is None:
            await callback.answer("Уже удалено", show_alert=True)
            return
        name = group.name
        await group_service.remove_with_plants(session, group)

    await callback.answer("Удалено")
    text, kb = await group_menu_text_and_kb(user_id)
    await safe_edit_text(callback.message, f"✅ Группа «{name}» удалена вместе с растениями.\n\n{text}", reply_markup=kb)


@router.callback_query(F.data.startswith("lggdelmove:"))
async def lggdel_move_pick(callback: CallbackQuery, user_id: int) -> None:
    """Куда перенести растения перед удалением группы — список остальных
    групп пользователя, "без группы" или новая группа."""
    gid = callback.data.split(":", 1)[1]
    await callback.answer()
    async with get_session() as session:
        groups = [g for g in await crud.list_groups(session, user_id) if g.id != int(gid)]
        ungrouped_label = await plant_service.get_ungrouped_label(session, user_id)
    await callback.message.edit_text(
        "📁 Куда перенести растения из этой группы?",
        reply_markup=groups_keyboard(
            groups,
            prefix=f"lggmoveto:{gid}",
            none_label=ungrouped_label,
            back_data=f"lggdel:{gid}",
        ),
    )


@router.callback_query(F.data.startswith("lggmoveto:"))
async def lggdel_move_apply(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    _, gid, value = callback.data.split(":", 2)
    await callback.answer()

    if value == "new":
        await state.clear()
        await state.update_data(group_id=int(gid))
        await state.set_state(DeleteGroup.new_group_name)
        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ Назад", callback_data=f"lggdelmove:{gid}", style="primary")
        await callback.message.edit_text("🆕 Название новой группы для растений?", reply_markup=builder.as_markup())
        await track_callback(callback, state)
        return

    target_group_id = None if value == "none" else int(value)
    await _finalize_delete_move(callback.message, user_id, int(gid), target_group_id, edit=True)


@router.message(StateFilter(DeleteGroup.new_group_name), ~F.text.in_(MENU_BUTTONS))
async def lggdel_move_new_group(message: Message, state: FSMContext, user_id: int) -> None:
    data = await state.get_data()
    gid = data["group_id"]
    new_name = message.text.strip()

    async with get_session() as session:
        target_group, _ = await crud.get_or_create_group(session, user_id, new_name)
        if target_group.id == gid:
            await session.rollback()
            await render(message, state, "⚠️ Так называется удаляемая группа, придумай другое название.")
            return
        await session.commit()
        target_id = target_group.id

    tracked_id = await pop_tracked(state)
    await state.clear()
    if tracked_id:
        await safe_delete_message(message.bot, message.chat.id, tracked_id)
    await _finalize_delete_move(message, user_id, gid, target_id, edit=False)


async def _finalize_delete_move(
    message: Message, user_id: int, gid: int, target_group_id: int | None, *, edit: bool
) -> None:
    async with get_session() as session:
        group = await crud.get_group(session, gid, user_id)
        if group is None:
            text = "⚠️ Группа не найдена, возможно уже удалена."
            if edit:
                await safe_edit_text(message, text)
            else:
                await message.answer(text)
            return

        name = group.name
        if target_group_id is None:
            await group_service.remove(session, group)
        else:
            await group_service.remove_move_plants(session, group, target_group_id)

    text, kb = await group_menu_text_and_kb(user_id)
    notice = f"✅ Группа «{name}» удалена, растения перенесены.\n\n{text}"
    if edit:
        await safe_edit_text(message, notice, reply_markup=kb)
    else:
        await message.answer(notice, reply_markup=kb)
