"""FSM-состояния диалогов зон полива."""

from aiogram.fsm.state import State, StatesGroup


class ZoneAdd(StatesGroup):
    name = State()
    interval = State()
    notify_time = State()


class ZoneEdit(StatesGroup):
    name = State()
    interval = State()
    notify_time = State()
