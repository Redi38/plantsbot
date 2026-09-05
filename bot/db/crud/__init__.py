"""Доступ к БД (CRUD), разбитый по сущностям, а не по одному файлу:

  users.py    — User: get_or_create_user, ungrouped_label, list/get/clear
  groups.py   — Group: создание/поиск/переименование/удаление/перенос растений
  plants.py   — Plant: создание/поиск/обновление/удаление
  ai_logs.py  — AiLog: запись и чтение логов ИИ-агента

Снаружи используется так же, как раньше использовался модуль crud.py —
весь плоский API собран здесь же одним re-export'ом:
    from bot.db import crud
    await crud.get_group(session, group_id, user_id)
"""

from .ai_logs import create_ai_log, list_ai_logs_all, list_ai_logs_for_user
from .groups import (
    create_group,
    delete_group,
    delete_group_with_plants,
    find_groups_fuzzy,
    get_group,
    get_group_by_name,
    get_or_create_group,
    list_groups,
    move_group_plants,
    rename_group,
)
from .plants import (
    create_plant,
    delete_plant,
    find_plant_by_name,
    find_plant_by_name_any_group,
    get_full_tree,
    get_plant,
    update_plant,
)
from .users import clear_user_plants, get_or_create_user, get_user, list_users, set_ungrouped_label

__all__ = [
    "create_ai_log",
    "list_ai_logs_all",
    "list_ai_logs_for_user",
    "create_group",
    "delete_group",
    "delete_group_with_plants",
    "find_groups_fuzzy",
    "get_group",
    "get_group_by_name",
    "get_or_create_group",
    "list_groups",
    "move_group_plants",
    "rename_group",
    "create_plant",
    "delete_plant",
    "find_plant_by_name",
    "find_plant_by_name_any_group",
    "get_full_tree",
    "get_plant",
    "update_plant",
    "clear_user_plants",
    "get_or_create_user",
    "get_user",
    "list_users",
    "set_ungrouped_label",
]
