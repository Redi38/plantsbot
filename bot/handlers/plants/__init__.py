"""Просмотр конкретного списка: добавление/изменение/удаление растения.

(Общее меню групп и постраничный просмотр — bot/handlers/list_view.py;
удаление/переименование самой группы — bot/handlers/groups.py.)

Пакет разбит по сценариям, а не по типу кода:
  common.py      — общие хелперы (пагинация выбора растения, генерик "Отмена")
  add_flow.py    — сценарий добавления растения (FSM AddPlant)
  edit_delete.py — выбор растения из списка + сценарии изменения/удаления (FSM EditPlant)

Снаружи используется так же, как раньше использовался модуль plants.py —
регистрация в диспетчере не меняется: from bot.handlers import plants
"""

from aiogram import Router

router = Router(name="plants")

# add_flow/edit_delete регистрируют хендлеры на общий router как побочный
# эффект импорта — сами модули дальше не используются напрямую.
from . import add_flow, edit_delete  # noqa: E402,F401

__all__ = ["router"]
