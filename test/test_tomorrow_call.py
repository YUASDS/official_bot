"""一周目地窖案件走真实每日入口、文本/按钮回调、战斗及跨周目存档。"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "StoryTeller"))
nonebot.init()

from src.models.player import CreateInvestigator, Investigator, InvestigatorGenerator, ending_repo, investigator_repo
from src.models.stats import EventLog
from src.plugins import adventure as adv
from src.plugins import flow, gm_room
from src.services import event_service, tomorrow_call
from src.services.daily_service import _clear_adventure_done, _mark_adventure_done, _adventure_done_today
from src.utils.buttons import BUTTON_HANDLERS

BOT = SimpleNamespace(type="Test")


@pytest.fixture
def game(request, monkeypatch):
    uid = "phone_" + request.node.name
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    data.update(day=6, san=60)
    ending_repo.new_run(uid)
    investigator_repo.create_and_save(uid, "林", data)
    output = AsyncMock()
    button_send = AsyncMock()
    monkeypatch.setattr(adv, "_send_to_user", button_send)
    monkeypatch.setattr(adv.combat_cmd, "send", output)
    monkeypatch.setattr(adv.combat_cmd, "finish", output)
    monkeypatch.setattr(adv, "render_pic", AsyncMock(return_value=None))
    monkeypatch.setattr(adv, "run_sanity_and_madness", lambda *args: ("", "", False, 0, False, 0))
    yield uid, output, button_send
    adv.event_states.pop(uid, None)
    adv.battle_manager.remove_battle(uid)


def player(uid):
    return Investigator(investigator_repo.find_by_qq(uid))


def enter(uid, output):
    asyncio.run(adv._run_adventure(uid, BOT, output, output))


def command(uid, value):
    event = SimpleNamespace(get_user_id=lambda: uid)
    msg = SimpleNamespace(extract_plain_text=lambda: value)
    asyncio.run(adv.handle_combat(event, BOT, msg))


def click(uid, label, token):
    asyncio.run(BUTTON_HANDLERS["event"](uid, label, BOT, "", token))


def restart(uid):
    adv.event_states.pop(uid, None)
    adv.battle_manager.remove_battle(uid)


@pytest.mark.parametrize(("lead", "passed", "investigate", "ending", "outcome"), [
    ("追问门外是谁", True, "查许平的签名", "开门救值班员", "rescue"),
    ("追问门外是谁", True, "翻看修理单", "从窗外追踪", "track"),
    ("听清第三声", False, "描下窗框划痕", "拨回那一天", "call_back"),
    ("听清第三声", True, "用镜子看窗外", "开门救值班员", "rescue"),
])
def test_case_from_cellar_to_account_memory(game, monkeypatch, lead, passed, investigate, ending, outcome):
    uid, output, _ = game
    monkeypatch.setattr(event_service, "roll_check_core", lambda *args: (passed, "聆听", 10 if passed else 90, 20))
    enter(uid, output)
    ring = adv.event_states[uid]
    assert "接起电话" in str(output.await_args.args[0])
    click(uid, "接起电话", ring["token"])
    receiver = adv.event_states[uid]
    assert receiver["token"] != ring["token"]
    click(uid, "继续下楼", ring["token"])
    assert adv.event_states[uid] is receiver
    restart(uid)
    command(uid, lead)
    assert uid not in adv.event_states
    first_note = player(uid).get_flag(tomorrow_call.FLAG)["note"]
    assert ("许平" if lead == "追问门外是谁" else "玻璃") in first_note
    if lead == "听清第三声":
        assert ("窗扣" if passed else "分不清") in str(output.await_args.args[0])

    battle = adv.battle_manager.get_battle(uid)
    battle.hp_record["mon"] = 0
    assert battle._check_combat_over()
    asyncio.run(adv._cleanup_battle(uid, battle))
    assert player(uid).day == 7

    _clear_adventure_done(uid)
    enter(uid, output)
    assert first_note in str(output.await_args.args[0])
    click(uid, investigate, adv.event_states[uid]["token"])
    second_note = player(uid).get_flag(tomorrow_call.FLAG)["note"]
    assert player(uid).day == 8 and _adventure_done_today(uid)
    assert adv.battle_manager.get_battle(uid) is None
    _clear_adventure_done(uid)

    enter(uid, output)
    assert second_note in str(output.await_args.args[0])
    token = adv.event_states[uid]["token"]
    restart(uid)
    skill, gain = {"rescue": ("san", 3), "track": ("侦查", 3), "call_back": ("克苏鲁神话", 2)}[outcome]
    before = player(uid).get_skill(skill)
    click(uid, ending, token)
    assert player(uid).get_skill(skill) == before + gain
    click(uid, ending, token)
    assert player(uid).get_skill(skill) == before + gain
    assert player(uid).day == 9 and _adventure_done_today(uid)
    assert player(uid).get_flag(tomorrow_call.FLAG) == {"outcome": outcome}
    assert ending_repo.get_run_choices(uid)["tomorrow_call"] == outcome
    logs = EventLog.select().where(EventLog.qq == uid).order_by(EventLog.day)
    assert [(row.event_key, row.option) for row in logs] == [
        ("明天的来电", lead), ("明天的来电", investigate), ("明天的来电", ending),
    ]
    for run in (2, 3):
        creator = CreateInvestigator()
        assert creator.choose_investigator(1)
        successor = creator.create_investigator(uid, "新调查员")
        assert ending_repo.get_progress(uid).run_id == run
        assert successor.get_flag("past.tomorrow_call") == outcome
        assert successor.get_flag(tomorrow_call.FLAG) is None
        successor.day = 3
        assert tomorrow_call.due_node(successor) is None


def test_decline_resumes_original_paid_cellar(game):
    uid, output, _ = game
    inv = player(uid)
    investigator_repo.add_item_to_inventory(inv._model, "401")
    _mark_adventure_done(uid)
    enter(uid, output)
    ring = adv.event_states[uid]["token"]
    monster_id = adv.battle_manager.get_battle(uid).monster.id
    restart(uid)
    enter(uid, output)
    assert adv.event_states[uid]["token"] == ring
    assert adv.battle_manager.get_battle(uid).monster.id == monster_id
    command(uid, "继续下楼")
    assert adv.event_states[uid]["event"] == adv.data_loader.event_data["地窖"]
    token = adv.event_states[uid]["token"]
    restart(uid)
    click(uid, "绕开", token)
    assert not player(uid).get_equipments()[0].get("401")
    assert adv.battle_manager.get_battle(uid).monster.id == monster_id
    assert player(uid).get_flag(tomorrow_call.FLAG)["outcome"] == "declined"
    assert tomorrow_call.due_node(player(uid)) is None
    assert "tomorrow_call" not in ending_repo.get_run_choices(uid)


def test_first_run_window_and_fixed_day_priority(game):
    uid, _, _ = game
    inv = player(uid)
    for day, expected in [(2, None), (3, "ring"), (5, None), (7, "ring"), (8, None), (40, None)]:
        inv.day = day
        assert tomorrow_call.due_node(inv) == expected
    inv.day = 5
    inv.set_flag(tomorrow_call.FLAG, {"next": "door", "due": 5})
    inv.save()
    assert tomorrow_call.due_node(inv) is None
    inv.day = 6
    assert tomorrow_call.due_node(inv) == "door"
    inv.clear_flag(tomorrow_call.FLAG)
    inv.save()
    ending_repo.new_run(uid)
    assert tomorrow_call.due_node(inv) is None


def test_other_commands_leave_pending_investigation_intact(game, monkeypatch):
    uid, output, _ = game
    enter(uid, output)
    token = adv.event_states[uid]["token"]
    ending_repo.add_dream_fragment(uid, 1)
    asyncio.run(gm_room._use_dream_fragment(uid, BOT, output, output))
    assert ending_repo.get_dream_fragments(uid) == 1
    assert adv.event_states[uid]["token"] == token

    restart(uid)
    monkeypatch.setattr(flow, "is_master", lambda _: True)
    asyncio.run(flow._handle_explore(uid, "gm_room", BOT, output, output))
    assert uid not in flow.flow_states
    assert tomorrow_call.pending(player(uid))["token"] == token
