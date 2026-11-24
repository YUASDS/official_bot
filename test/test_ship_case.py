"""从正式入口进入失船案；案件与战斗重载、真实胜利结算、三条终局。"""

import asyncio
import random
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import nonebot
import pytest
import ujson

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "StoryTeller"))
nonebot.init()

from src.models.monster import Monster
from src.models.player import Investigator, ending_repo, investigator_repo
from src.models.stats import RunStats
from src.plugins import adventure, mainline
from src.services import ship_case as ship
from src.services.daily_service import _clear_adventure_done
from src.services.ship_battle import ShipBattle
from src.utils.active_battles import battle_manager


def character(qq, day=1):
    inv = Investigator.load(qq)
    inv.day = day
    inv.set_flag(ship.ACTIVE)
    inv.set_skill("san", 95)
    inv.set_skill("意志", 95)
    inv.save()
    return inv


def test_qualification_counts_completions_per_run():
    qq = "ship_qualification"
    ending_repo.add_ending(qq, "E07", run=1)
    ending_repo.add_ending(qq, "E06", run=2)
    ending_repo.add_ending(qq, "E05", run=3)
    assert not ship.unlocked(qq)
    ending_repo.add_ending(qq, "E03", run=4, campaign="door")
    assert not ship.unlocked(qq)
    ending_repo.add_ending(qq, "E03", run=5, campaign="door")
    assert ship.unlocked(qq)
    assert (
        len(
            [
                r
                for r in ujson.loads(ending_repo.get_collection(qq).endings)
                if r["id"] == "E03"
            ]
        )
        == 1
    )

    legacy = "ship_legacy"
    ending_repo.add_ending(legacy, "E03", run=1)
    RunStats.create(qq=legacy, run_id=2, ending_id="E05", door_choice="")
    assert not ship.unlocked(legacy)
    RunStats.create(qq=legacy, run_id=3, ending_id="E05", door_choice="E05")
    assert ship.unlocked(legacy)
    routed = "ship_two_mainlines"
    ending_repo.add_ending(routed, "E03", run=2, campaign="mainline")
    ending_repo.add_ending(routed, "E12", run=3, campaign="mainline")
    assert not ship.unlocked(routed)
    ending_repo.add_ending(routed, "E03", run=4, campaign="door")
    assert ship.unlocked(routed)
    inv = Investigator.load("ship_unqualified")
    assert not mainline.apply_selector_choice(inv, "ship")


def test_puzzles_persist_and_reward_once():
    inv = character("ship_riddle", 10)
    state = ship.open_day(inv)
    restored = ship.open_day(Investigator.load(inv.qq))
    assert restored == state
    pool = ship._riddles(10)
    name = pool[state["answer"]][3][0]
    assert ship.respond(inv, name, state["token"])[0] == "done"
    assert ship.respond(inv, name, state["token"])[0] == "stale"
    assert inv.get_equipments()[0]["505"] == 1
    inv.day = 26
    inv.save()
    state = ship.open_day(inv)
    assert ship.respond(inv, "一张邮票", state["token"])[0] == "fight"
    assert ship.pending(inv)["monster"] == "82"


def test_interview_failure_and_correct_threshold():
    for total, values in [(3, [1, 1, 1]), (4, [2, 1, 1])]:
        inv = character(f"ship_score_{total}", 21)
        state = ship.open_day(inv)
        for step, points in enumerate(values):
            answers = ship.data_loader.ship_case["questions"][step][1]
            label = next(label for label, score in answers if score == points)
            token = state["token"]
            kind, _ = ship.respond(inv, label, token)
            if step < 2:
                inv = Investigator.load(inv.qq)
                state = ship.open_day(inv)
                assert kind == "prompt" and state["token"] != token
                assert ship.respond(inv, label, token)[0] == "stale"
        assert kind == ("fight" if total < 4 else "done")


@pytest.mark.parametrize(
    ("mid", "sequence", "expected"),
    [
        ("81", ["近身挑扣", "近身挑扣", "远投铁坠", "远投铁坠"], 0),
        ("81", ["远投铁坠", "近身挑扣", "近身挑扣", "远投铁坠"], 2),
        ("84", ["垂手听名", "垂手听名"], 0),
    ],
)
def test_mechanic_survives_restart(mid, sequence, expected):
    inv = character("ship_round_" + mid + str(expected), 8)
    inv.set_flag(ship.PENDING, {"day": 8, "phase": "fight", "monster": mid})
    inv.save()
    fight = ShipBattle(inv, Monster(mid))
    for i, action in enumerate(sequence):
        fight.current_turn = "inv"
        assert action in fight.get_available_actions_for_turn()
        fight.execute_action(action)
        if i == 0:
            fight.checkpoint()
            inv = Investigator.load(inv.qq)
            restored = ShipBattle(inv, Monster(mid))
            restored.restore(ship.pending(inv)["battle"])
            assert restored.get_turn_token() == fight.get_turn_token()
            assert restored.hp_record == fight.hp_record
            fight = restored
    assert fight.monster.armor == expected
    assert Monster(mid).armor == 4


@pytest.fixture
def seeded_combat():
    original = random.getstate()
    random.seed(7135)
    yield
    random.setstate(original)


@pytest.mark.parametrize(("first", "ending"), [(0, "E14"), (1, "E15"), (1, "E16")])
def test_forty_adventures_from_selector(monkeypatch, seeded_combat, first, ending):
    qq = f"ship_full_{ending}"
    ending_repo.add_ending(qq, "E03", run=1, campaign="door")
    ending_repo.add_ending(qq, "E12", run=2)
    coll = ending_repo.get_collection(qq)
    coll.total_runs = 2
    coll.save()
    ending_repo.new_run(qq)
    inv = Investigator.load(qq)
    # 一条路线用前期取得的装备和正常上限的格斗技能实打，另两条专测路径/结算。
    if ending == "E14":
        for item in ("110", "303"):
            inv.add_item_to_inventory(item)
            assert investigator_repo.equip_item(qq, item)[0]
        inv.add_item_to_inventory("501", 2)
        inv.update_equipment()
        for skill in ("力量", "体质", "体型", "敏捷", "意志"):
            inv.set_skill(skill, 80)
        inv.set_skill("格斗", 75)
        inv.set_skill("闪避", 65)
    inv.set_skill("san", 99)
    inv.save()
    send = AsyncMock()
    monkeypatch.setattr(
        adventure, "_try_send_ending_card", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(adventure, "send_combat_result", AsyncMock())
    monkeypatch.setattr(adventure, "render_pic", AsyncMock(return_value=None))
    asyncio.run(adventure._run_adventure(qq, None, send, send))
    assert "接听未结的呼救" in str(send.call_args.args[0])
    asyncio.run(adventure._choose_mainline(qq, "ship", None, send, send))
    for day in range(1, 41):
        inv = Investigator.load(qq)
        assert inv.day == day
        if day > 1:
            _clear_adventure_done(qq)
            asyncio.run(adventure._run_adventure(qq, None, send, send))
        state = ship.pending(inv) or ship.pending(Investigator.load(qq))
        assert state and state["day"] == day
        while state and state["phase"] != "fight":
            inv = Investigator.load(qq)
            phase = state["phase"]
            title, body, labels = ship.view(inv, state)
            assert str(day) + "/40" in title
            idx = first if day == 1 else 0
            if day == 2:
                assert ("司机" if first == 0 else "咳嗽") in body
            if day == 30:
                idx = first
            if phase == "radio":
                idx = {"E14": 0, "E15": 1, "E16": 2}[ending]
            if phase == "interview":
                idx = state["order"].index(0)
            if phase == "riddle":
                idx = state["order"].index(state["answer"])
            asyncio.run(
                adventure._ship_input(inv, labels[idx], state["token"], None, send)
            )
            state = ship.pending(Investigator.load(qq))
        if state and state["phase"] == "fight":
            fight = battle_manager.get_battle(qq)
            assert isinstance(fight, ShipBattle)
            if ending == "E14":
                for _ in range(100):
                    if fight.current_turn == "mon":
                        action = (
                            "闪避"
                            if fight.monster.id == "84" and fight.player_round < 2
                            else "反击"
                        )
                    elif fight.hp_record[
                        "inv"
                    ] <= 7 and fight.investigator.get_equipments()[0].get("505", 0):
                        action = "使用505"
                    elif fight.monster.id == "81" and fight.player_round < 4:
                        action = "近身挑扣" if fight.player_round < 2 else "远投铁坠"
                    elif fight.monster.id == "84" and fight.player_round < 2:
                        action = "垂手听名"
                    else:
                        action = "格斗"
                    result = fight.execute_action(action)
                    asyncio.run(
                        adventure._send_action_result(qq, fight, {}, result, None, send)
                    )
                    if not fight.investigator.is_survive:
                        assert (
                            Investigator.load(qq).get_equipments()[0].get("501", 0) > 0
                        ), (day, result)
                        adventure.do_resurrect(qq)
                        _clear_adventure_done(qq)
                        asyncio.run(adventure._run_adventure(qq, None, send, send))
                        fight = battle_manager.get_battle(qq)
                    elif fight.fight_is_over():
                        break
                assert fight.hp_record["inv"] > 0 and fight.hp_record["mon"] == 0, (
                    day,
                    result,
                )
            else:
                fight.hp_record["mon"] = 0
                result = fight._check_combat_over()
                asyncio.run(
                    adventure._send_action_result(qq, fight, {}, (result,), None, send)
                )
            assert not battle_manager.get_battle(qq)
    assert ending_repo.get_progress(qq).ended
    assert Investigator.load(qq).day == 40
    assert (
        RunStats.get((RunStats.qq == qq) & (RunStats.run_id == 3)).ending_id == ending
    )
    assert not ending_repo.get_progress(qq).boss36_defeated


def test_allocation_of_the_last_nine_seconds():
    inv = character("ship_closed_line", 40)
    inv.set_flag(ship.JOURNAL, {"37": {"choice": 1}, "39": {"choice": 1}})
    inv.save()
    state = ship.open_day(inv)
    assert ship.view(inv, state)[2] == ["亲自接替旧播报", "保留旧呼号"]
    assert ship.respond(inv, "让旧台静默", state["token"])[0] == "prompt"
    assert ship.pending(inv)["phase"] == "radio"
    ship.respond(inv, "亲自接替旧播报")
    ship.respond(inv, "由我留下值夜")
    assert ship.pending(inv)["ending"] == "E15"
    ship.respond(inv, "重接线路")
    assert ship.view(inv, ship.pending(inv))[2] == ["亲自接替旧播报", "保留旧呼号"]


def test_open_question_restores_without_admission_or_duplicate_click(monkeypatch):
    qq = "ship_button_resume"
    inv = character(qq, 10)
    state = ship.open_day(inv)
    _clear_adventure_done(qq)
    adventure._mark_adventure_done(qq)
    inv.add_item_to_inventory("401")
    send = AsyncMock()
    monkeypatch.setattr(adventure, "_send_to_user", send)
    asyncio.run(adventure._run_adventure(qq, None, send, send))
    inv = Investigator.load(qq)
    assert inv.get_equipments()[0]["401"] == 1
    assert ship.pending(inv) == state
    number = str(state["order"].index(state["answer"]) + 1)
    for _ in range(2):
        asyncio.run(
            adventure.handle_ship_button(qq, number, None, token=state["token"])
        )
    inv = Investigator.load(qq)
    assert inv.day == 11 and inv.get_equipments()[0]["505"] == 1
    asyncio.run(adventure._run_adventure(qq, None, send, send))
    inv = Investigator.load(qq)
    assert not inv.get_equipments()[0].get("401")
    assert ship.pending(inv)["day"] == 11


def test_legacy_ending_collection_gets_named_campaign_clears(tmp_path):
    """旧库缺 campaign_clears：ALTER 必须建正确列名，不能建出名为 TEXT 的垃圾列。"""
    import os
    import sqlite3
    import subprocess

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ending_collection (id INTEGER PRIMARY KEY, qq TEXT, "
        "total_runs INTEGER, total_days INTEGER, endings TEXT, ng_plus INTEGER, "
        "collection TEXT, dream_fragments INTEGER, last_run_choices TEXT)"
    )
    conn.execute(
        "INSERT INTO ending_collection (qq, total_runs) VALUES ('legacy', 2)"
    )
    conn.commit()
    conn.close()
    plugin_root = Path(__file__).resolve().parents[1] / "plugins" / "StoryTeller"
    script = (
        "import sys; sys.path.insert(0, "
        f"{str(plugin_root)!r}); import src.models.player as p; "
        "assert p.ending_repo.get_collection('legacy').campaign_clears == '{}'"
    )
    env = dict(os.environ, STORYTELLER_DB_PATH=str(path))
    for _ in range(2):  # 二次启动验证迁移幂等
        subprocess.run([sys.executable, "-c", script], env=env, check=True)

    conn = sqlite3.connect(path)
    try:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(ending_collection)")]
        assert "campaign_clears" in columns
        assert "TEXT" not in columns and "INTEGER" not in columns
        row = conn.execute(
            "SELECT total_runs, campaign_clears FROM ending_collection WHERE qq='legacy'"
        ).fetchone()
        assert row == (2, "{}")
    finally:
        conn.close()


def test_restarted_battle_button_resumes_exact_round(monkeypatch):
    qq = "ship_rejoin_combat"
    inv = character(qq, 8)
    state = ship.open_day(inv)
    ship.respond(inv, "1", state["token"])
    send = AsyncMock()
    monkeypatch.setattr(adventure, "send_combat_result", send)
    monkeypatch.setattr(adventure, "_send_to_user", send)
    asyncio.run(adventure._show_ship_day(inv, ship.pending(inv), None, send))
    fight = battle_manager.get_battle(qq)
    fight.current_turn = "inv"
    fight.checkpoint()
    token = fight.get_turn_token()
    battle_manager.remove_battle(qq)
    asyncio.run(adventure.handle_combat_action(qq, "近身挑扣", None, token=token))
    fight = battle_manager.get_battle(qq)
    assert fight.breaks == 1 and fight.player_round == 1
    asyncio.run(adventure.handle_combat_action(qq, "近身挑扣", None, token=token))
    assert fight.breaks == 1 and fight.player_round == 1
    battle_manager.remove_battle(qq)
