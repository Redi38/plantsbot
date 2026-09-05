"""ИИ-агент: разбор свободного текста в намерение и доведение его до
конкретного действия (добавить/удалить растение, создать/удалить группу).

Пакет разбит по сценариям, а не по типу кода — вся логика одного сценария
целиком лежит в одном файле:
  states.py         — общие FSM-состояния (AIAdd, AIDelete)
  keyboards.py       — клавиатуры, общие для нескольких хендлеров
  common.py          — мелкие хелперы (reply() в Message/CallbackQuery)
  add_flow.py        — сценарий добавления растения (с подтверждением группы)
  delete_flow.py     — сценарий удаления растения (с подтверждением/выбором)
  group_actions.py   — создание группы и запуск удаления группы
  entrypoint.py       — точка входа: handle_free_text, парсинг intent и
                        диспетчеризация по action

Снаружи пакет используется так же, как раньше использовался модуль
ai_agent.py — регистрация в диспетчере не меняется:
    from bot.handlers.ai_agent import router
"""

from aiogram import Router

router = Router(name="ai_agent")

# entrypoint импортирует add_flow/delete_flow/group_actions, что и
# регистрирует все хендлеры пакета как побочный эффект импорта —
# самого entrypoint.handle_free_text для этого достаточно.
from . import entrypoint  # noqa: E402,F401

__all__ = ["router"]
