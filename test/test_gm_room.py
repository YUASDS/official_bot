"""GM 房间彩蛋 / 梦之碎片 补测。

覆盖：触发条件门与概率、账号级存储（跨周目保留）、战斗强化注入（全技能+30/
伤害翻倍/+25临时生命）、门扉重掷（撤销判定→重新判定）、/使用梦之碎片 流程、
adventure 钩子（余韵注入 + 碎片入账）。只测新增行为，不修改 src/。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import ujson

import nonebot

nonebot.init()

from src.models.monster import Monster
from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    Investigator,
    InvestigatorGenerator,
    ending_repo,
    investigator_repo,
)
from src.services.battle import BattleService
from src.services.ending_engine import judge_door_choice, reroll_door_choice


def _make_inv(qq: str, name: str = "GmRoom") -> Investigator:
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    data["san"] = 60
    data["意志"] = 60
    inv_model = investigator_repo.create_and_save(qq, name, data)
    return Investigator(inv_model)


def _cleanup(qq: str) -> None:
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)


def _ending_ids(qq: str) -> list[str]:
    coll = ending_repo.get_collection(qq)
    if not coll:
        return []
    try:
        return [r.get("id") for r in ujson.loads(coll.endings or "[]")]
    except (ValueError, TypeError):
        return []


# --- C1：触发条件门与概率 ---
def test_gm_room_unlocked_gate():
    from src.plugins.gm_room import gm_room_unlocked

    inv = _make_inv("test_gm_gate")
    inv.day = 9
    assert gm_room_unlocked(inv) is False  # day < 10 新手期不触发
    inv.day = 15
    assert gm_room_unlocked(inv) is True
    inv.day = 40
    assert gm_room_unlocked(inv) is False  # day40 门扉冻结不触发
    _cleanup("test_gm_gate")


def test_gm_room_should_trigger_probability():
    from unittest.mock import patch

    from src.plugins.gm_room import gm_room_should_trigger

    inv = _make_inv("test_gm_prob")
    inv.day = 15
    with patch("src.plugins.gm_room.roll_dice", return_value=("100", 1)):
        assert gm_room_should_trigger(inv) is True
    with patch("src.plugins.gm_room.roll_dice", return_value=("100", 7)):
        assert gm_room_should_trigger(inv) is False
    _cleanup("test_gm_prob")


def test_gm_room_should_trigger_not_on_day9_or_40():
    from unittest.mock import patch

    from src.plugins.gm_room import gm_room_should_trigger

    inv = _make_inv("test_gm_day")
    for day in (9, 40):
        inv.day = day
        with patch("src.plugins.gm_room.roll_dice", return_value=("100", 1)):
            assert gm_room_should_trigger(inv) is False, f"day{day} 不应触发"
    _cleanup("test_gm_day")


# --- C2：账号级存储（跨周目/重建保留、一次性）---
def test_dream_fragment_storage_lifecycle():
    qq = "test_gm_storage"
    assert ending_repo.get_dream_fragments(qq) == 0  # 无行返回 0
    ending_repo.add_dream_fragment(qq, 1)
    assert ending_repo.get_dream_fragments(qq) == 1
    assert ending_repo.remove_dream_fragment(qq) is True
    assert ending_repo.get_dream_fragments(qq) == 0
    assert ending_repo.remove_dream_fragment(qq) is False  # 空时不可消耗
    _cleanup(qq)


def test_dream_fragment_preserved_across_runs():
    qq = "test_gm_runs"
    ending_repo.add_dream_fragment(qq, 2)
    coll = ending_repo.new_run(qq)
    assert coll.total_runs == 1
    assert ending_repo.get_dream_fragments(qq) == 2  # 跨周目保留
    _cleanup(qq)


def test_dream_fragment_survives_character_rebuild():
    qq = "test_gm_rebuild"
    inv = _make_inv(qq)
    ending_repo.add_dream_fragment(qq, 1)
    # 重建调查员（死亡重开）：背包清空，但账号级碎片保留
    investigator_repo.delete_by_qq(qq)
    assert investigator_repo.find_by_qq(qq) is None
    assert ending_repo.get_dream_fragments(qq) == 1
    _cleanup(qq)


def test_ending_collection_has_dream_fragments_column():
    """schema 回归：表 B 存在 dream_fragments 列（_migrate 容错）。"""
    coll = ending_repo.ensure_collection("test_gm_schema")
    assert coll.dream_fragments == 0
    _cleanup("test_gm_schema")


# --- C3：战斗强化注入（用途 A）---
def test_battle_dream_buff_skill_and_temp():
    qq = "test_gm_buff_skill"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster.load_random_for_day(1))
    base = bs._get_player_modified_skill("格斗", 25)
    bs.apply_dream_buff()
    assert bs.dream_buff is True
    assert bs.temp_hp == 25
    assert bs._get_player_modified_skill("格斗", 25) == base + 30  # 全技能 +30
    assert bs._get_player_modified_skill("闪避", 25) >= 25  # 修正恒非负
    # 临时生命抵扣伤害（25 临时护盾先抵伤害）
    bs.hp_record["inv"] = 13
    bs._apply_damage_to_player(30, armor_absorbed=True)
    assert bs.hp_record["inv"] == 13 - 5  # 30 - 25 临时 = 5
    assert bs.temp_hp == 0
    _cleanup(qq)


def test_battle_dream_buff_damage_doubled():
    qq = "test_gm_buff_dmg"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster.load_random_for_day(1))
    bs.hp_record["mon"] = 9999
    bs.monster.armor = 0
    bs.monster._data["装甲"] = 0
    bs._apply_damage_to_monster(5)
    assert bs.hp_record["mon"] == 9999 - 5  # 未激活：正常伤害
    bs.apply_dream_buff()
    bs.hp_record["mon"] = 9999
    bs._apply_damage_to_monster(5)
    assert bs.hp_record["mon"] == 9999 - 10  # 激活：伤害翻倍
    _cleanup(qq)


def test_battle_dream_buff_no_state_leak():
    """buff 仅战斗内临时状态：不修改角色存档技能。"""
    qq = "test_gm_buff_leak"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster.load_random_for_day(1))
    skill_before = inv.get_skill("格斗", 25)
    bs.apply_dream_buff()
    assert inv.get_skill("格斗", 25) == skill_before  # 角色存档技能未变
    assert bs.temp_hp == 25  # 临时生命只挂在战斗实例
    _cleanup(qq)


# --- C4：门扉重掷（用途 B）---
def _settle_door_e08(qq: str, inv: Investigator) -> None:
    inv.day = 40
    inv.save()
    progress = ending_repo.ensure_progress(qq, 40)
    progress.dead_once = True
    progress.save()
    r = judge_door_choice(inv, "O_observe")
    assert r.get("ending") == "E08"


def test_door_reroll_undo_and_rejudge():
    qq = "test_gm_reroll"
    inv = _make_inv(qq)
    _settle_door_e08(qq, inv)
    progress = ending_repo.get_progress(qq)
    assert progress.ended is True
    assert progress.door_choice == "E08"
    assert _ending_ids(qq) == ["E08"]

    text = reroll_door_choice(inv)
    assert text is not None
    progress = ending_repo.get_progress(qq)
    assert progress.ended is False  # 判定撤销
    assert progress.door_choice == ""
    assert "E08" not in _ending_ids(qq)  # 已登记结局移除

    # 重掷后再判：门扉 C（san>=30）→ E03
    inv2 = Investigator(investigator_repo.find_by_qq(qq))
    r2 = judge_door_choice(inv2, "C")
    assert r2.get("ending") == "E03"
    assert _ending_ids(qq) == ["E03"]
    _cleanup(qq)


def test_door_reroll_not_rerollable():
    qq = "test_gm_reroll_guard"
    inv = _make_inv(qq)
    # 未判定 → 不可重掷
    assert reroll_door_choice(inv) is None
    # 非门扉终局（door_choice 为空，如 E06/E10）→ 不可重掷
    progress = ending_repo.ensure_progress(qq, inv.day)
    progress.ended = True
    progress.door_choice = ""
    progress.save()
    assert reroll_door_choice(inv) is None
    _cleanup(qq)


# --- C5：/使用梦之碎片 流程 ---
class FakeBot:
    type = "Console"

    async def send_msg(self, user_id=None, message=None):
        pass


async def _run_use(qq: str):
    from src.plugins.gm_room import _use_dream_fragment

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    async def finish(msg):
        sent.append("FIN:" + str(msg))
        raise SystemExit

    try:
        await _use_dream_fragment(qq, FakeBot(), send, finish)
    except SystemExit:
        pass
    return sent


def test_use_fragment_no_fragment():
    import asyncio

    qq = "test_gm_use_none"
    _make_inv(qq)
    sent = asyncio.run(_run_use(qq))
    assert "空空如也" in sent[0]
    _cleanup(qq)


def test_use_fragment_in_battle():
    import asyncio

    from src.plugins.gm_room import _use_dream_fragment
    from src.utils.active_battles import battle_manager

    qq = "test_gm_use_battle"
    inv = _make_inv(qq)
    ending_repo.add_dream_fragment(qq, 1)
    bs = BattleService(inv, Monster.load_random_for_day(1))
    battle_manager.add_battle(qq, bs)

    asyncio.run(_run_use(qq))
    # 状态断言（不依赖图片/md 渲染模式）
    assert bs.dream_buff is True
    assert bs.temp_hp == 25
    assert ending_repo.get_dream_fragments(qq) == 0  # 一次性消耗
    assert bs.get_turn_token() == 1  # 消耗回合（与 505/506 一致）
    battle_manager.remove_battle(qq)
    _cleanup(qq)


def test_use_fragment_door_reroll():
    import asyncio

    from src.plugins.gm_room import _use_dream_fragment
    from src.services.ending_engine import render_door_choice

    qq = "test_gm_use_door"
    inv = _make_inv(qq)
    _settle_door_e08(qq, inv)
    ending_repo.add_dream_fragment(qq, 1)

    sent = asyncio.run(_run_use(qq))
    text = "\n".join(sent)
    assert "门扉重掷" in text
    assert "门扉在你身后重新合拢" in text
    progress = ending_repo.get_progress(qq)
    assert progress.ended is False  # 判定已撤销
    assert ending_repo.get_dream_fragments(qq) == 0
    # 重掷后门扉选项仍可渲染（含 重赴门前/驻足旁观 战败分支）
    render = render_door_choice(inv)
    keys = [c["key"] for c in render["choices"]]
    assert "R_refight" in keys
    _cleanup(qq)


def test_use_fragment_not_usable_outside():
    import asyncio

    qq = "test_gm_use_nowhere"
    _make_inv(qq)
    ending_repo.add_dream_fragment(qq, 1)
    sent = asyncio.run(_run_use(qq))
    assert "只能在战斗中使用" in sent[0]
    assert ending_repo.get_dream_fragments(qq) == 1  # 未消耗
    _cleanup(qq)


# --- C6：adventure 钩子（V2：触发 → 状态入 flow_states + 替代当日冒险）---
def test_adventure_gm_room_trigger_starts_room_v2():
    import asyncio
    from unittest.mock import patch

    from src.plugins.adventure import _run_adventure
    from src.services.flow_engine import flow_states
    from src.utils.active_battles import battle_manager
    from util.DaylyRecord import add_data, get_data, write_json

    qq = "test_gm_adv_v2"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()
    add_data(qq, "adventure_done", False)
    write_json()

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    async def finish(msg):
        sent.append("FIN:" + str(msg))
        raise SystemExit

    with patch("src.plugins.adventure.gm_room_should_trigger", return_value=True), patch(
        "src.plugins.adventure.pick_random_event", return_value=None
    ), patch("src.plugins.adventure.npc_should_trigger", return_value=None), patch(
        "src.plugins.adventure.hidden_should_trigger", return_value=None
    ):
        try:
            asyncio.run(_run_adventure(qq, FakeBot(), send, finish))
        except BaseException:
            # V2 触发会以中断当日冒险流程的方式退出（FinishedException）。
            # 注意：test_fullflow 收集期会把 nonebot.exception mock 掉，
            # 此处用 BaseException 保证在干净/污染两种测试环境下均健壮。
            pass

    text = "\n".join(sent)
    assert "空间扭曲" in text  # V2 叙事（第一轮对话）
    assert qq in flow_states  # 状态已入 flow 节点机
    assert flow_states[qq]["flow_id"] == "gm_room"
    assert flow_states[qq]["phase"] == "stage"  # 停在首个选项节点（第一轮对话）
    assert ending_repo.get_dream_fragments(qq) == 0  # 碎片改为胜利出口发放，触发不发
    assert battle_manager.get_battle(qq) is None  # 触发即替代当日冒险，不进正常战斗
    assert get_data(qq, "adventure_done") is True  # 当日冒险已被房间接管
    flow_states.pop(qq, None)
    add_data(qq, "adventure_done", False)
    write_json()
    _cleanup(qq)


def test_adventure_gm_room_no_trigger_by_default():
    import asyncio
    from unittest.mock import patch

    from src.plugins.adventure import _run_adventure
    from src.utils.active_battles import battle_manager

    qq = "test_gm_adv_off"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()

    async def send(msg):
        pass

    async def finish(msg):
        raise SystemExit

    with patch("src.plugins.adventure.gm_room_should_trigger", return_value=False), patch(
        "src.plugins.adventure.pick_random_event", return_value=None
    ), patch(
        "src.plugins.adventure.run_sanity_and_madness",
        return_value=("", "", False, 0, False, 0),
    ), patch("src.plugins.adventure.npc_should_trigger", return_value=None), patch(
        "src.plugins.adventure.hidden_should_trigger", return_value=None
    ):
        try:
            asyncio.run(_run_adventure(qq, FakeBot(), send, finish))
        except SystemExit:
            pass

    assert ending_repo.get_dream_fragments(qq) == 0  # 未触发不发放
    battle = battle_manager.get_battle(qq)
    assert battle is not None
    assert battle.dream_buff is False  # 无余韵
    battle_manager.remove_battle(qq)
    _cleanup(qq)


def test_qiren_forced_day_skips_gm_room():
    """启挑战强制路径不触发 GM 房间（当日只保留一个特殊事件）。"""
    import asyncio
    from unittest.mock import patch

    from src.plugins.adventure import _run_adventure
    from src.plugins.qiren import qiren_pending
    from src.utils.active_battles import battle_manager

    qq = "test_gm_qiren"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    qiren_pending[qq] = True

    async def send(msg):
        pass

    async def finish(msg):
        raise SystemExit

    with patch("src.plugins.adventure.gm_room_should_trigger", return_value=True), patch(
        "src.plugins.adventure.pick_random_event", return_value=None
    ):
        try:
            asyncio.run(_run_adventure(qq, FakeBot(), send, finish))
        except SystemExit:
            pass

    battle = battle_manager.get_battle(qq)
    assert battle is not None
    assert battle.monster.id == "38"  # 启强制路径未被覆盖
    assert battle.dream_buff is False  # GM 房间未参与
    assert ending_repo.get_dream_fragments(qq) == 0
    qiren_pending.pop(qq, None)
    battle_manager.remove_battle(qq)
    _cleanup(qq)


# --- C7：flow 节点机四分支走查（gm_room.json + flow_engine）---
from src.services.flow_engine import (  # noqa: E402
    _flow_battle_end,
    flow_states,
    handle_flow_input,
    render_flow_node,
    start_flow,
)


async def _flow_enter(qq: str, bot=None) -> list[str]:
    """启动 gm_room flow 并渲染到首个选项节点，收集发送文本。"""
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    state = start_flow(qq, "gm_room")
    await render_flow_node(qq, state, bot or FakeBot(), send)
    return sent


async def _flow_drive(qq: str, choice: str, bot=None) -> list[str]:
    """驱动 handle_flow_input（阶段选项 / 战斗行动），收集发送文本。"""
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    await handle_flow_input(qq, choice, bot or FakeBot(), send)
    return sent


async def _flow_resolve(qq: str, win: bool, bot=None) -> list[str]:
    """强制结束当前战斗并按 胜利/战败 分支推进（不跑真实回合），收集发送文本。"""
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    state = flow_states[qq]
    assert state["phase"] == "battle"
    service = state["battle"]
    if win:
        service.hp_record["mon"] = 0
    else:
        service.hp_record["inv"] = 0
    await _flow_battle_end(qq, state, bot or FakeBot(), send)
    return sent


async def _drive_to_battle(qq: str) -> list[str]:
    """两轮对话走到守卫战（第一轮战前），返回累计文本。"""
    sent: list[str] = []
    sent += await _flow_enter(qq)
    sent += await _flow_drive(qq, "「你是谁？」")
    sent += await _flow_drive(qq, "「来吧。」")
    assert flow_states[qq]["phase"] == "battle"
    return sent


def test_gm_flow_dlg_to_battle():
    import asyncio
    from unittest.mock import patch

    qq = "test_gm_flow_dlg"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()

    with patch("src.services.flow_engine.random.choice", return_value="42"):
        sent = asyncio.run(_drive_to_battle(qq))

    state = flow_states[qq]
    assert state["phase"] == "battle"
    assert state["guard_id"] == "42"
    assert state["battle"] is not None
    assert state["battle"].is_gm_room is True
    assert "管理员" in "\n".join(sent)
    flow_states.pop(qq, None)
    _cleanup(qq)


def test_gm_flow_flee_disabled():
    import asyncio

    qq = "test_gm_flow_flee"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()

    asyncio.run(_drive_to_battle(qq))
    state = flow_states[qq]
    token = state["battle"].get_turn_token()
    sent = asyncio.run(_flow_drive(qq, "逃跑"))
    assert "退路" in "\n".join(sent)  # 战斗无出口（flow.no_exit）
    assert state["battle"].get_turn_token() == token  # 未消耗回合
    flow_states.pop(qq, None)
    _cleanup(qq)


def test_gm_flow_win1_grants_armor_attr_fragment():
    import asyncio

    from src.models.item import Equipment

    qq = "test_gm_flow_win1"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()

    asyncio.run(_drive_to_battle(qq))
    sent = asyncio.run(_flow_resolve(qq, win=True))
    text = "\n".join(sent)
    assert "没想到你这么厉害啊" in text  # GM 无奈台词（win1 收尾）
    inv2 = Investigator(investigator_repo.find_by_qq(qq))
    eq, _ = inv2.get_equipments()
    assert eq.get("306", 0) == 1  # 护甲 4 防具
    assert Equipment("306").armor_point == 4
    assert ending_repo.get_dream_fragments(qq) == 1  # 梦之碎片
    assert inv2.day == 16  # 收尾 skip_daily → day+1
    assert qq not in flow_states  # 状态已清
    _cleanup(qq)


def test_gm_flow_defeat_revive_round2_boost():
    import asyncio

    qq = "test_gm_flow_rev"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()

    asyncio.run(_drive_to_battle(qq))
    guard_id = flow_states[qq]["guard_id"]
    sent = asyncio.run(_flow_resolve(qq, win=False))
    state = flow_states[qq]
    assert state["phase"] == "battle"  # gm_revive → 二轮再战
    assert state["guard_id"] == guard_id  # @上次：仍打第一轮那只守卫
    service = state["battle"]
    assert service.is_gm_room is True
    base = service._get_player_modified_skill("格斗", 25)
    assert base == inv.get_skill("格斗", 25) + 75  # 助力：环境技能修正 +75
    assert inv.get_skill("格斗", 25) == 25  # 存档技能未变（临时）
    assert "GM 发力" in "\n".join(sent)
    flow_states.pop(qq, None)
    _cleanup(qq)


def test_gm_flow_second_win_choice_flow():
    import asyncio

    qq = "test_gm_flow_choice"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()

    asyncio.run(_drive_to_battle(qq))
    asyncio.run(_flow_resolve(qq, win=False))  # 一轮败 → 复活
    asyncio.run(_flow_resolve(qq, win=True))  # 二轮胜 → 武器轮
    assert flow_states[qq]["node_id"] == "choice_weapon"

    # 选武器（管理员的长柄匙）
    asyncio.run(_flow_drive(qq, "武器「近战·管理员的长柄匙」"))
    eq, _ = Investigator(investigator_repo.find_by_qq(qq)).get_equipments()
    assert eq.get("307", 0) == 1
    assert flow_states[qq]["node_id"] == "choice_item"

    # 选道具（骰子袋 → 随机属性 +10）
    before = dict(Investigator(investigator_repo.find_by_qq(qq)).get_full_attributes_dict())
    asyncio.run(_flow_drive(qq, "道具「管理员·骰子袋」"))
    eq, _ = Investigator(investigator_repo.find_by_qq(qq)).get_equipments()
    assert eq.get("308", 0) == 1
    after = dict(Investigator(investigator_repo.find_by_qq(qq)).get_full_attributes_dict())
    changed = [k for k in before if after.get(k, 0) != before[k]]
    assert changed, "骰子袋应带来一项属性 +10"
    assert flow_states[qq]["node_id"] == "choice_stat"

    # 属性 +50（对齐 P1b：stat 轮 +50，不再 →120）；骰子袋可能已随机加到力量，
    # base 需从库重读（inv 为旧快照，防 flaky）
    base_stat = Investigator(investigator_repo.find_by_qq(qq)).get_skill("力量", 0)
    asyncio.run(_flow_drive(qq, "力量 +50"))
    inv2 = Investigator(investigator_repo.find_by_qq(qq))
    assert inv2.get_skill("力量", 0) == base_stat + 50
    eq, _ = inv2.get_equipments()
    assert eq.get("309", 0) == 1  # 管理员的徽记（纪念）
    assert qq not in flow_states  # 三轮完成 → 正常收尾清状态
    assert inv2.day == 16
    _cleanup(qq)


def test_gm_flow_all_declined_special_cg():
    import asyncio

    qq = "test_gm_flow_refuse"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()

    asyncio.run(_drive_to_battle(qq))
    asyncio.run(_flow_resolve(qq, win=False))  # 一轮败 → 复活
    asyncio.run(_flow_resolve(qq, win=True))  # 二轮胜 → 武器轮

    sent: list[str] = []
    sent += asyncio.run(_flow_drive(qq, "「我不想要」"))  # 拒武器
    sent += asyncio.run(_flow_drive(qq, "「我不想要」"))  # 拒道具
    sent += asyncio.run(_flow_drive(qq, "「我不想要」"))  # 拒属性 → 三拒特殊 CG
    text = "\n".join(sent)
    assert "真拿你没办法" in text  # GM 无奈
    assert ending_repo.get_dream_fragments(qq) == 1  # 特殊 CG：梦之碎片
    assert qq not in flow_states
    inv2 = Investigator(investigator_repo.find_by_qq(qq))
    assert inv2.day == 16
    _cleanup(qq)


def test_gm_flow_lose2_empty_handed():
    import asyncio

    qq = "test_gm_flow_lose2"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()

    asyncio.run(_drive_to_battle(qq))
    asyncio.run(_flow_resolve(qq, win=False))  # 一轮败 → 复活
    sent = asyncio.run(_flow_resolve(qq, win=False))  # 二轮败 → 空手退出
    text = "\n".join(sent)
    assert "空手而归" in text or "缘分到此为止" in text
    inv2 = Investigator(investigator_repo.find_by_qq(qq))
    eq, _ = inv2.get_equipments()
    assert set(eq) <= {"101"}  # 空手：除默认弹簧折刀外无任何奖励
    assert ending_repo.get_dream_fragments(qq) == 0  # 不给碎片
    assert inv2.day == 16  # 房间结束 day+1
    assert qq not in flow_states
    _cleanup(qq)


# --- C8：隔离性（E01~E10 / 信物 / 每日池）---
def test_gm_room_engine_short_circuit_defeat_no_e07():
    qq = "test_gm_iso_e07"
    inv = _make_inv(qq)
    inv.day = 20
    inv.save()
    service = BattleService(inv, Monster("42"))
    service.is_gm_room = True
    service.hp_record["inv"] = 0
    service.hp_record["mon"] = 40
    msg = service._check_combat_over()
    assert service.gm_room_defeated is True
    assert inv.is_survive is True  # 战败不落死亡
    assert "E07" not in _ending_ids(qq)  # 不登记 E07
    assert "守卫" in msg
    _cleanup(qq)


def test_gm_room_engine_short_circuit_victory_no_settlement():
    qq = "test_gm_iso_vic"
    inv = _make_inv(qq)
    inv.day = 20
    inv.save()
    day_before = inv.day
    service = BattleService(inv, Monster("42"))
    service.is_gm_room = True
    service.hp_record["inv"] = 8
    service.hp_record["mon"] = 0
    service.succeded_skill.add("格斗")
    msg = service._check_combat_over()
    assert service.gm_room_victory is True
    assert inv.day == day_before  # 胜利不推进 day
    eq, _ = inv.get_equipments()
    assert set(eq) <= {"101"}  # 不掉落（守卫奖励为空 + 结算短路）
    assert inv.get_skill("格斗", 25) == 25  # 无成长污染
    assert "E01" not in _ending_ids(qq)
    _cleanup(qq)


def test_gm_room_attr_pools_and_reward_isolation():
    from src.services.data_loader import data_loader
    from src.services.ending_engine import relic_ids
    from src.services.flow_engine import get_flow

    flow = get_flow("gm_room")
    nodes = flow["节点"]
    # 属性轮：力量/体质/敏捷/智力 +50（白名单，禁 意志 / 克苏鲁神话）
    stat_attrs = {
        next(iter(o["效果"]["技能"]))
        for o in nodes["choice_stat"]["选项"]
        if "技能" in o.get("效果", {})
    }
    assert stat_attrs == {"力量", "体质", "敏捷", "智力"}
    assert "意志" not in stat_attrs
    assert "克苏鲁神话" not in stat_attrs
    # 奖励物品 id 从 JSON 节点效果 + 选项效果全量收集
    reward_ids: set[str] = set()
    def _add(eff: dict) -> None:
        if isinstance(eff, dict) and "物品" in eff:
            reward_ids.add(str(eff["物品"]))

    for node in nodes.values():
        _add(node.get("效果") or {})
        for opt in node.get("选项") or []:
            _add(opt.get("效果") or {})
        battle = node.get("战斗") or {}
        for br in ("胜利", "战败"):
            _add((battle.get(br) or {}).get("效果") or {})
    assert reward_ids == {"306", "307", "308", "309", "9", "15", "703", "702", "605"}
    assert not (reward_ids & set(relic_ids()))  # 不含任何 400~508 信物
    for mid in ("40", "41", "42"):
        m = data_loader.monster_data[mid]
        assert m["理智值丧失"] == "0/0"  # 房间不掉理智 → SAN 永不归零（E06 不触发）
        assert m["奖励"] == {}  # 不走掉落
        assert "高危" in m


def test_gm_guards_not_in_daily_pool():
    from src.models.monster import monster_repo

    pools = monster_repo._checkpoint_data
    for pid in ("40", "41", "42"):
        for _day, ids in pools.items():
            assert pid not in ids, f"守卫 {pid} 不应出现在每日池（{_day}）"
