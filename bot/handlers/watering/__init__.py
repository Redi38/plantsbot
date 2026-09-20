"""Зоны полива: меню «💧 Полив», создание/просмотр/удаление зоны, смена
интервала и кнопки под напоминанием («Полил» / «Отложить»).

Сами напоминания шлёт фоновая задача (bot/services/watering_reminders.py);
здесь только реакция на нажатия. Кнопки под напоминанием (wzdone/wzlate)
намеренно отдельные от кнопок карточки зоны (wzwater): напоминание — это
одноразовое сообщение, после ответа его текст заменяется итогом без
клавиатуры, а карточка после действия перерисовывается сама.

Роутер регистрируется в диспетчере ДО ai_agent — тот ловит любой
свободный текст вне FSM.

Пакет разбит по сценариям, а не по типу кода:
  states.py    — FSM-состояния (ZoneAdd, ZoneEdit)
  common.py    — общие представления меню/карточки и хелперы диалога
  menu.py      — открыть список зон / карточку зоны, вернуться в меню
  add_flow.py  — сценарий создания зоны (FSM ZoneAdd)
  edit_flow.py — переименование, смена интервала, смена времени напоминания (FSM ZoneEdit)
  actions.py   — полить сейчас / удалить зону, кнопки под напоминанием

Снаружи используется так же, как раньше использовался модуль watering.py
— регистрация в диспетчере не меняется:
    from bot.handlers import watering
    dp.include_router(watering.router)
ask_interval/ask_notify_time остаются доступны как watering.ask_interval /
watering.ask_notify_time для bot/handlers/ai_agent/zone_flow.py."""

from aiogram import Router

router = Router(name="watering")

# Подмодули регистрируют хендлеры на общий router как побочный эффект
# импорта — сами модули дальше не используются напрямую, кроме
# ask_interval/ask_notify_time, реэкспортируемых для ai_agent.
from . import actions, edit_flow, menu  # noqa: E402,F401
from .add_flow import ask_interval, ask_notify_time  # noqa: E402

__all__ = ["router", "ask_interval", "ask_notify_time"]
