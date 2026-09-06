from bot.services.ai_service.prompt import build_system_prompt, select_relevant_plants


# --- build_system_prompt --------------------------------------------------


def test_build_system_prompt_no_groups_no_plants():
    prompt = build_system_prompt(None, None)
    assert "нет ни одной группы" in prompt.lower()
    assert "пока нет ни одного растения" in prompt.lower()


def test_build_system_prompt_includes_group_names():
    prompt = build_system_prompt(["Суккуленты", "Кактусы"], None)
    assert "«Суккуленты»" in prompt
    assert "«Кактусы»" in prompt


def test_build_system_prompt_includes_plant_names():
    prompt = build_system_prompt(None, ["Алоэ", "Хавортия"])
    assert "«Алоэ»" in prompt
    assert "«Хавортия»" in prompt


def test_build_system_prompt_mentions_all_actions():
    prompt = build_system_prompt(["Суккуленты"], ["Алоэ"])
    for action in ["add", "delete", "delete_group", "create_group", "rename_group", "edit_plant", "list", "unknown"]:
        assert action in prompt


# --- select_relevant_plants ------------------------------------------------


def test_select_relevant_plants_returns_all_when_under_limit():
    plants = ["Алоэ", "Хавортия"]
    assert select_relevant_plants("что угодно", plants, limit=10) == plants


def test_select_relevant_plants_prioritizes_text_matches():
    plants = [f"Растение {i}" for i in range(10)] + ["Алоказия Полли"]
    result = select_relevant_plants("хочу полли добавить", plants, limit=3)
    assert "Алоказия Полли" in result
    assert len(result) == 3


def test_select_relevant_plants_fills_remainder_with_rest():
    plants = ["Алоказия Полли", "Растение А", "Растение Б", "Растение В"]
    result = select_relevant_plants("полли", plants, limit=3)
    assert len(result) == 3
    assert "Алоказия Полли" in result


def test_select_relevant_plants_no_match_returns_first_n():
    plants = ["Растение А", "Растение Б", "Растение В", "Растение Г"]
    result = select_relevant_plants("нет совпадений вообще", plants, limit=2)
    assert result == plants[:2]
