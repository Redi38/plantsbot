"""FSM-состояния диалога добавления препарата."""

from aiogram.fsm.state import State, StatesGroup


class MedAdd(StatesGroup):
    name = State()
    kind = State()
    substance = State()
    expires = State()
    comment = State()
