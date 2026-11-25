import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.models.monster import Monster
from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.services.battle import BattleService
from src.services.battle_cards import end_card_html
from src.services.combat_messaging import send_end_buttons
from src.services.event_service import event_option_rows


class FakeBot:
    type = "Console"


def _capture(battle: BattleService, bot: FakeBot) -> str:
    captured: list[str] = []

    async def send(msg) -> None:
        captured.append(str(msg))

    import asyncio

    asyncio.run(send_end_buttons(battle, bot, send))
    return "\n".join(captured)


def test_death_end_card_uses_player_death_ending():
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_end_card_dead", "DeadHero", data
    )
    inv = Investigator(inv_model)
    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq("test_end_card_dead")
        return
    bs = BattleService(inv, m)
    bs.hp_record["inv"] = 0  # 强制玩家死亡

    assert bs.get_end_card_data()["victory"] is False

    html = end_card_html(bs)
    monster_ending = getattr(m, "结局", "")
    assert monster_ending not in html  # 不再显示怪物结局
    assert "你倒在了庄园的阴影里" in html  # 显示玩家死亡结局
    investigator_repo.delete_by_qq("test_end_card_dead")


def test_victory_end_card_still_uses_monster_ending():
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_end_card_win", "WinHero", data
    )
    inv = Investigator(inv_model)
    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq("test_end_card_win")
        return
    bs = BattleService(inv, m)
    bs.hp_record["mon"] = 0  # 强制怪物死亡

    html = end_card_html(bs)
    assert getattr(m, "结局", "") in html
    investigator_repo.delete_by_qq("test_end_card_win")


def test_end_buttons_victory_has_scroll_button():
    investigator_repo.delete_by_qq("test_end_btn_win")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_end_btn_win", "BtnWin", data
    )
    inv = Investigator(inv_model)
    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq("test_end_btn_win")
        return
    bs = BattleService(inv, m)
    bs.hp_record["mon"] = 0
    text = _capture(bs, FakeBot())
    assert "调查员信息" in text
    assert "购买冒险卷" in text
    assert "%2F%E8%B4%AD%E4%B9%B0%20401" in text  # /购买 401 (urlencoded)
    investigator_repo.delete_by_qq("test_end_btn_win")


def test_end_buttons_fled_has_scroll_button():
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_end_btn_fled", "BtnFled", data
    )
    inv = Investigator(inv_model)
    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq("test_end_btn_fled")
        return
    bs = BattleService(inv, m)
    bs.fled = True
    text = _capture(bs, FakeBot())
    assert "调查员信息" in text
    assert "购买冒险卷" in text
    investigator_repo.delete_by_qq("test_end_btn_fled")


def test_event_option_rows_chunk_by_three():
    options = [{"输入": f"o{i}"} for i in range(7)]
    labels = [f"L{i}" for i in range(7)]

    rows = event_option_rows(labels, options)
    assert [len(r) for r in rows] == [3, 3, 1]  # 自适应：3/3/1
    assert rows[0][0] == ("L0", "event:o0")
    assert rows[1][2] == ("L5", "event:o5")
    assert rows[2][0] == ("L6", "event:o6")

    single = event_option_rows(["A"], [{"输入": "x"}])
    assert [len(r) for r in single] == [1]

    six = event_option_rows([f"L{i}" for i in range(6)], [{"输入": f"o{i}"} for i in range(6)])
    assert [len(r) for r in six] == [3, 3]


def test_end_buttons_death_keeps_resurrect_create():
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_end_btn_dead", "BtnDead", data
    )
    inv = Investigator(inv_model)
    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq("test_end_btn_dead")
        return
    bs = BattleService(inv, m)
    bs.hp_record["inv"] = 0
    text = _capture(bs, FakeBot())
    assert "复活" in text
    assert "创建调查员" in text
    assert "购买冒险卷" not in text
    investigator_repo.delete_by_qq("test_end_btn_dead")
