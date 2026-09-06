"""FSM-состояния и отображение шагов диалога добавления растения."""

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import crud
from bot.db.database import get_session
from bot.keyboards.inline import groups_keyboard
from bot.services import plant_service
from bot.utils.chat import render, track_callback

from ..common import cancel_keyboard


class AddPlant(StatesGroup):
    name = State()
    group = State()
    new_group_name = State()
    comment = State()


async def show_step(event: Message | CallbackQuery, state: FSMContext, text: str, reply_markup) -> None:
    """Показывает следующий шаг диалога добавления — редактирует
    сообщение, если вызвано нажатием инлайн-кнопки, или использует
    render(), если вызвано сообщением пользователя (чтобы не плодить
    лишние сообщения в чате)."""
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=reply_markup)
        await track_callback(event, state)
    else:
        await render(event, state, text, reply_markup=reply_markup)


async def ask_comment(event: Message | CallbackQuery, state: FSMContext, *, prefix: str = "") -> None:
    await state.set_state(AddPlant.comment)
    await show_step(
        event, state, f"{prefix}💬 Комментарий есть? Напиши текстом или пришли /skip", cancel_keyboard().as_markup()
    )


async def warn_duplicate(event: Message | CallbackQuery, state: FSMContext, existing_name: str) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Всё равно добавить", callback_data="addforce", style="primary")
    builder.button(text="❌ Отмена", callback_data="addcancel", style="danger")
    builder.adjust(1)
    await show_step(
        event, state, f"⚠️ «{existing_name}» уже есть в списке. Добавить ещё один экземпляр?", builder.as_markup()
    )


async def show_group_choice(event: Message | CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Показывает выбор группы либо, если групп ещё нет, сразу
    определяет растение в "без группы" и переходит к комментарию.
    Повтор по имени уже проверен раньше (сразу после ввода названия —
    см. handlers.add_name/add_force), поэтому здесь его заново не ищем."""
    async with get_session() as session:
        groups = await crud.list_groups(session, user_id)
        ungrouped_label = await plant_service.get_ungrouped_label(session, user_id)

    if groups:
        await state.set_state(AddPlant.group)
        await show_step(
            event,
            state,
            "📁 Выбери группу:",
            groups_keyboard(groups, prefix="addgroup", none_label=ungrouped_label, cancel_data="addcancel"),
        )
        return

    await state.update_data(group_id=None)
    await ask_comment(event, state, prefix=f"📁 Групп пока нет — добавлю в «{ungrouped_label}». ")
