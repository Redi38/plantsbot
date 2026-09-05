from aiogram.fsm.state import State, StatesGroup


class AIAdd(StatesGroup):
    confirm_duplicate = State()  # "уже есть в списке — добавить ещё раз?"
    confirm_group = State()      # "добавить «X» в группу «Y»?"
    pick_group = State()         # список всех групп на выбор
    new_group_name = State()     # ввод названия новой группы


class AIDelete(StatesGroup):
    pick_plant = State()      # несколько совпадений по имени — какое удалить
    confirm_delete = State()  # "точно удалить «X»?"


class AIEdit(StatesGroup):
    pick_plant = State()  # несколько совпадений по имени — какое изменить
