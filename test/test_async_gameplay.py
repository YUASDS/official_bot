"""异步游戏入口回归：重复结算、流程互斥、发送失败恢复和 SAN 终局。

使用真实服务、临时数据库及明确的异步等待点；仅替换随机攻击和外部发送/渲染。
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
import pytest
import ujson

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))
nonebot.init()

from database.db import User, get_info
from src.models.monster import Monster
from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    Investigator,
    InvestigatorGenerator,
    ending_repo,
    investigator_repo,
)
from src.plugins import adventure as adv
from src.plugins import flow as flow_plugin
from src.services import flow_engine as fe
from src.services.battle import BattleService
from src.services.battle import settlement
from src.services.daily_service import _adventure_done_today, _clear_adventure_done
from src.services.resurrect import do_resurrect
from src.utils.active_battles import battle_manager


class FakeBot:
    type = "Test"


BOT = FakeBot()


@pytest.fixture
def make_inv(request):
    users = []

    def create(day=5):
        uid = f"async_{request.node.name}_{len(users)}"
        users.append(uid)
        data = InvestigatorGenerator.generate_investigator_data(1)[0]
        data.update({"san": 60, "意志": 60})
        model = investigator_repo.create_and_save(uid, "Async test", data)
        inv = Investigator(model)
        inv.day = day
        inv.save()
        ending_repo.ensure_progress(uid, day)
        return inv

    yield create
    for uid in users:
        battle_manager.remove_battle(uid)
        fe.flow_states.pop(uid, None)
        adv.door_states.pop(uid, None)
        _clear_adventure_done(uid)
        investigator_repo.delete_by_qq(uid)
        EndingProgressModel.delete().where(EndingProgressModel.qq == uid).execute()
        EndingCollectionModel.delete().where(EndingCollectionModel.qq == uid).execute()
        User.delete().where(User.user_id == uid).execute()


async def discard(*args, **kwargs):
    pass


def lethal_battle(inv, monkeypatch, *, isolated=False):
    battle = BattleService(inv, Monster("1"))
    battle.is_gm_room = isolated
    battle.roll_initiative()
    battle.current_turn = "inv"

    def strike():
        battle.hp_record["mon"] = 0
        return ("致命一击", battle._end_turn())

    monkeypatch.setattr(battle, "_melee_attack", strike)
    monkeypatch.setattr(battle.monster, "generate_loot", lambda *a, **k: (10, None, "10 乌帕"))
    monkeypatch.setattr(
        settlement, "DiceRoll",
        lambda skill: SimpleNamespace(level=1, dice=1, skill=skill),
    )
    return battle


def test_battle_terminal_settlement_is_idempotent(make_inv, monkeypatch):
    inv = make_inv(day=1)
    battle = lethal_battle(inv, monkeypatch)
    token = battle.get_turn_token()
    first = battle.execute_action("格斗")
    second = battle.execute_action("格斗")
    assert second == (first[-1],)
    assert battle._check_combat_over() == first[-1]
    assert battle.get_turn_token() != token
    assert get_info(inv.qq).gold == 10
    assert investigator_repo.find_by_qq(inv.qq).day == 2


@pytest.mark.parametrize("second_channel", ["button", "command"])
def test_repeated_victory_during_send_only_rewards_once(make_inv, monkeypatch, second_channel):
    inv = make_inv(day=1)
    battle = lethal_battle(inv, monkeypatch)
    battle_manager.add_battle(inv.qq, battle)
    token = battle.get_turn_token()
    monkeypatch.setattr(adv, "_send_new_battle_endings", AsyncMock())
    monkeypatch.setattr(adv, "_present_door_choice", AsyncMock())

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def sending(*args, **kwargs):
            entered.set()
            await release.wait()

        monkeypatch.setattr(adv, "send_combat_result", sending)
        task = asyncio.create_task(adv.handle_combat_action(inv.qq, "格斗", BOT, token=token))
        await entered.wait()
        assert battle_manager.get_battle(inv.qq) is None
        if second_channel == "button":
            await adv.handle_combat_action(inv.qq, "格斗", BOT, token=token)
        else:
            message = SimpleNamespace(extract_plain_text=lambda: "格斗")
            await adv.handle_combat(SimpleNamespace(get_user_id=lambda: inv.qq), BOT, message)
        release.set()
        await task

    asyncio.run(run())
    assert get_info(inv.qq).gold == 10
    assert investigator_repo.find_by_qq(inv.qq).day == 2
    assert _adventure_done_today(inv.qq)


def test_normal_battle_send_failure_still_cleans_up(make_inv, monkeypatch):
    inv = make_inv(day=1)
    battle = lethal_battle(inv, monkeypatch)
    battle_manager.add_battle(inv.qq, battle)
    monkeypatch.setattr(adv, "send_combat_result", AsyncMock(side_effect=RuntimeError("send failed")))
    monkeypatch.setattr(adv, "_send_new_battle_endings", AsyncMock())
    monkeypatch.setattr(adv, "_present_door_choice", AsyncMock())
    with pytest.raises(RuntimeError, match="send failed"):
        asyncio.run(adv.handle_combat_action(inv.qq, "格斗", BOT))
    assert battle_manager.get_battle(inv.qq) is None
    assert _adventure_done_today(inv.qq)
    assert get_info(inv.qq).gold == 10
    assert investigator_repo.find_by_qq(inv.qq).day == 2


def academy_choice(inv):
    fid = fe.resolve_flow_ref("anime_academy")
    state = fe.start_flow(inv.qq, fid)
    assert state is not None
    state["node_id"] = "s1"
    option = next(o for o in fe.get_flow(fid)["节点"]["s1"]["选项"] if o.get("效果", {}).get("金币") == 10)
    return state, option["输入"]


@pytest.mark.parametrize("button", [True, False])
def test_flow_choice_concurrency_does_not_duplicate_gold(make_inv, button):
    inv = make_inv()
    other = make_inv()
    state, option = academy_choice(inv)
    _, other_option = academy_choice(other)
    choice = f"s1|{option}" if button else option

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def sending(msg):
            entered.set()
            await release.wait()

        task = asyncio.create_task(fe.handle_flow_input(inv.qq, choice, BOT, sending))
        await entered.wait()
        await fe.handle_flow_input(inv.qq, choice, BOT, sending)
        # 不同用户不互相阻塞。
        await fe.handle_flow_input(other.qq, other_option, BOT, discard)
        release.set()
        await task

    asyncio.run(run())
    assert get_info(inv.qq).gold == 10
    assert get_info(other.qq).gold == 10
    assert state["node_id"] == "s2"
    asyncio.run(fe.handle_flow_input(inv.qq, f"s1|{option}", BOT, discard))
    assert get_info(inv.qq).gold == 10


def test_flow_choice_send_failure_consumes_option_and_releases_guard(make_inv):
    inv = make_inv()
    state, option = academy_choice(inv)
    send = AsyncMock(side_effect=RuntimeError("send failed"))
    with pytest.raises(RuntimeError, match="send failed"):
        asyncio.run(fe.handle_flow_input(inv.qq, f"s1|{option}", BOT, send))
    assert state["node_id"] == "s2"
    assert get_info(inv.qq).gold == 10
    # 异常后接受下一次操作，不因互斥标记残留而永久锁住。
    collector = AsyncMock()
    asyncio.run(fe.handle_flow_input(inv.qq, "", BOT, collector))
    assert collector.await_count == 1
    assert state["node_id"] == "s2"
    collector.reset_mock()
    asyncio.run(fe.handle_flow_input(inv.qq, f"s1|{option}", BOT, collector))
    assert collector.await_count == 1
    assert get_info(inv.qq).gold == 10


@pytest.mark.parametrize("phase", ["stage", "battle"])
def test_active_flow_blocks_adventure_before_scroll_or_hp_change(make_inv, monkeypatch, phase):
    inv = make_inv()
    inv.hp = 1
    inv.save()
    investigator_repo.add_item_to_inventory(inv._model, "401", 1)
    state, _ = academy_choice(inv)
    if phase == "battle":
        state["node_id"] = "b1"
        monkeypatch.setattr(fe, "send_card_or_md", AsyncMock(return_value=False))
        asyncio.run(fe._flow_start_battle(inv.qq, state, BOT, discard))
    adv._mark_adventure_done(inv.qq)
    finish = AsyncMock()
    asyncio.run(adv._run_adventure(inv.qq, BOT, discard, finish))
    assert finish.await_count == 1
    assert "你正在探索中" in str(finish.call_args.args[0])
    assert battle_manager.get_battle(inv.qq) is None
    assert fe.flow_states[inv.qq] is state
    saved = Investigator(investigator_repo.find_by_qq(inv.qq))
    assert saved.hp == 1
    assert saved.get_equipments()[0]["401"] == 1


def test_gm_explore_does_not_replace_active_battle(make_inv, monkeypatch):
    inv = make_inv()
    battle = BattleService(inv, Monster("1"))
    battle_manager.add_battle(inv.qq, battle)
    monkeypatch.setattr(flow_plugin, "is_master", lambda uid: True)
    finish = AsyncMock()
    asyncio.run(flow_plugin._handle_explore(inv.qq, "lane_tale", BOT, discard, finish))
    assert finish.await_count == 1
    assert battle_manager.get_battle(inv.qq) is battle
    assert inv.qq not in fe.flow_states


def transport_flow(inv, monkeypatch, target="choice"):
    fid = "async_transport"
    config = {
        "入口": "fight", "收尾": {"skip_daily": True},
        "节点": {
            "fight": {"类型": "战斗", "战斗": {
                "胜利": {"回复": "胜利", "效果": {"金币": 10}, "下一步": target},
                "战败": {"回复": "战败", "下一步": target},
            }},
            "choice": {"类型": "选项", "文案": "继续旅程", "选项": [
                {"输入": "继续", "回复": "归途", "下一步": "end"},
            ]},
            "end": {"类型": "结束", "文案": "旅程结束"},
        },
    }
    monkeypatch.setitem(fe._CODE_FLOWS, fid, config)
    monkeypatch.setattr(fe, "render_pic", AsyncMock(return_value=None))
    monkeypatch.setattr(fe, "_flow_battle_loot", lambda *args: "")
    state = fe.start_flow(inv.qq, fid)
    battle = lethal_battle(inv, monkeypatch, isolated=True)
    state.update(phase="battle", battle=battle)
    return state, battle


@pytest.mark.parametrize("target", ["choice", "end"])
@pytest.mark.parametrize("failed_message", [1, 2, 3])
def test_flow_battle_transport_failure_never_blocks_settlement(make_inv, monkeypatch, target, failed_message):
    inv = make_inv()
    state, battle = transport_flow(inv, monkeypatch, target)
    count = 0

    async def sending(msg):
        nonlocal count
        count += 1
        if count == failed_message:
            raise RuntimeError("send failed")

    with pytest.raises(RuntimeError, match="send failed"):
        asyncio.run(fe.handle_flow_input(inv.qq, "格斗", BOT, sending))
    assert get_info(inv.qq).gold == 10
    assert state["battle"] is None
    assert state["phase"] == "stage"
    if target == "choice":
        assert state["node_id"] == "choice"
        asyncio.run(fe.handle_flow_input(inv.qq, "choice|继续", BOT, discard))
    assert inv.qq not in fe.flow_states
    assert investigator_repo.find_by_qq(inv.qq).day == 6
    # 重复收尾不能发奖或再推进一天。
    asyncio.run(fe._flow_battle_end(inv.qq, state, BOT, discard))
    asyncio.run(fe._finish_flow(inv.qq, state, BOT, discard))
    assert get_info(inv.qq).gold == 10
    assert investigator_repo.find_by_qq(inv.qq).day == 6


def test_ended_flow_battle_can_recover_on_next_input(make_inv, monkeypatch):
    inv = make_inv()
    state, battle = transport_flow(inv, monkeypatch, "end")
    battle.hp_record["mon"] = 0
    asyncio.run(fe.handle_flow_input(inv.qq, "格斗", BOT, discard))
    assert inv.qq not in fe.flow_states
    assert get_info(inv.qq).gold == 10
    assert investigator_repo.find_by_qq(inv.qq).day == 6


def test_flow_defeat_send_failure_exits_without_killing_character(make_inv, monkeypatch):
    inv = make_inv()
    state, battle = transport_flow(inv, monkeypatch, "end")
    battle.hp_record["inv"] = 0
    with pytest.raises(RuntimeError, match="send failed"):
        asyncio.run(fe.handle_flow_input(
            inv.qq, "格斗", BOT, AsyncMock(side_effect=RuntimeError("send failed"))
        ))
    assert inv.qq not in fe.flow_states
    assert state["battle"] is None
    saved = investigator_repo.find_by_qq(inv.qq)
    assert saved.issurvive and saved.day == 6
    assert get_info(inv.qq).gold == 0


def test_cancelled_final_report_still_finishes_and_unlocks_user(make_inv, monkeypatch):
    inv = make_inv()
    transport_flow(inv, monkeypatch, "end")

    async def run():
        entered = asyncio.Event()

        async def sending(msg):
            if not entered.is_set():
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(fe.handle_flow_input(inv.qq, "格斗", BOT, sending))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        collector = AsyncMock()
        await fe.handle_flow_input(inv.qq, "格斗", BOT, collector)
        assert collector.await_count == 1

    asyncio.run(run())
    assert inv.qq not in fe.flow_states
    assert get_info(inv.qq).gold == 10
    assert investigator_repo.find_by_qq(inv.qq).day == 6


@pytest.mark.parametrize("isolated", [False, True])
def test_spell_exhausting_san_respects_battle_isolation(make_inv, monkeypatch, isolated):
    inv = make_inv()
    inv.set_skill("san", 1)
    inv.add_spell("s003")
    inv.save()
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    battle = BattleService(inv, Monster("1"))
    battle.is_gm_room = isolated
    battle.current_turn = "inv"
    result = battle.execute_action("施法s003")
    progress = ending_repo.get_progress(inv.qq)
    assert inv.get_skill("san") == 0
    if isolated:
        assert inv.is_survive
        assert not battle.fight_is_over()
        assert not progress.ended
        assert not progress.san_zero_hit
        assert battle.temp_hp > 0
    else:
        assert not inv.is_survive
        assert battle.fight_is_over()
        assert progress.ended and progress.san_zero_hit
        saved = investigator_repo.find_by_qq(inv.qq)
        assert saved.san == 0 and not saved.issurvive
        assert battle.temp_hp == 0  # 永久疯狂发生后不再应用施法效果
        records = ujson.loads(ending_repo.get_collection(inv.qq).endings)
        assert sum(row["id"] == "E06" for row in records) == 1
        mp = battle.mp
        assert battle.execute_action("施法s003") == (result[-1],)
        assert battle.mp == mp
        do_resurrect(inv.qq)
        assert not investigator_repo.find_by_qq(inv.qq).issurvive
        assert inv.get_equipments()[0]["501"] == 1
        from src.services import combat_messaging as messaging

        monkeypatch.setattr(messaging, "render_pic", AsyncMock(return_value=None))
        collector = AsyncMock()
        asyncio.run(messaging.send_combat_result(battle, BOT, result, collector))
        text = "\n".join(str(call.args[0]) for call in collector.call_args_list)
        assert "永久疯狂" in text
        assert "/创建调查员" in text
        assert "/复活" not in text
