"""乱入遭遇系统（guest 迁移 flow_engine·批次2 薄壳版）测试。

覆盖：薄壳签名兼容（guest_should_trigger / guest_enter / _guest_button_handler）、
触发门与确定性每日骰、met_day 守卫、三世界数据红线、三世界端到端
（胜利→纪念品+done+day+1 / 战败空手归途 / 纪念品幂等 / 玩家文案注入 / 战斗图片卡片）、
adventure 互斥链挂载（触发即接管当日 / 未触发正常开战）。
内部状态机委托 flow_engine，断言基于 flow_states / flow_data 迁移内容。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import asyncio
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init()

from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    Investigator,
    InvestigatorGenerator,
    ending_repo,
    investigator_repo,
)
from src.services.battle import BattleService
from src.services.ending_engine import relic_ids

# 各世界推到战斗节点的选项路径（阶段 → 检定 → 战斗）
_PATHS = {
    "sword_magic": ["拾起一把断剑", "俯身行礼，献上敬重", "与他合奏"],
    "dnd_dungeon": ["点燃火把，大步踏入", "认真作答"],
    "anime_academy": ["抱着书包，冲向教学楼", "加入剑道部"],
    # anime_academy S2（多季世界 flow_id）：准备 → 循环 → 真相 → 场记小姐
    "flow.anime_academy.s2": [
        "接下舞台剧《勇者与龙》的主演",
        "追上可疑的场记小姐",
        "撕掉最终稿，抢过场记板",
    ],
}


def _make_inv(qq: str, name: str = "Guest") -> Investigator:
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


class ConsoleBot:
    type = "Console"

    async def send_msg(self, user_id=None, message=None):
        pass


def _drive(qq: str, choice: str, bot=None, token=None) -> list[str]:
    """经薄壳兼容入口 _guest_handle_input（= handle_flow_input）驱动，收集发送文本。"""
    from src.plugins.guest import _guest_handle_input

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(_guest_handle_input(qq, choice, bot or ConsoleBot(), send, token))
    return sent


def _enter(qq: str, inv: Investigator, world_id: str, bot=None) -> list[str]:
    """薄壳入口 guest_enter：占当日 + start_flow + 渲染首节点（FinishedException 中断）。

    注意：test_fullflow/test_new_storyteller 收集期会把 nonebot.exception mock 掉，
    FinishedException 类身份不可依赖，统一用 BaseException 捕获（对齐既有测试注释）。
    """
    from src.plugins.guest import guest_enter

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    try:
        asyncio.run(guest_enter(qq, inv, bot or ConsoleBot(), send, world_id))
    except BaseException:
        pass
    return sent


def _enter_battle(qq: str, inv: Investigator, world_id: str):
    """进入指定世界战斗节点（供胜利/战败/文案/图片断言复用）。"""
    from src.services.flow_engine import flow_states

    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        _enter(qq, inv, world_id)
        for choice in _PATHS[world_id]:
            _drive(qq, choice)
    assert flow_states[qq]["phase"] == "battle", f"{world_id} 未进入战斗"
    return flow_states[qq]


def _win_action(service):
    def _win(action):
        service.hp_record["mon"] = 0
        return ("你挥出最后一击。",)

    return _win


def _lose_action(service):
    def _lose(action):
        service.hp_record["inv"] = 0
        service.fled = True
        return ("你被击倒了。",)

    return _lose


# --- C1：触发门 / 确定性骰 / 互斥守卫 ---
def test_guest_unlocked_gate():
    from src.plugins.guest import guest_unlocked

    inv = _make_inv("guest_gate")
    inv.day = 4
    assert guest_unlocked(inv) is False  # day < 5 新手期不触发
    inv.day = 5
    assert guest_unlocked(inv) is True
    inv.day = 39
    assert guest_unlocked(inv) is True
    inv.day = 40
    assert guest_unlocked(inv) is False  # day40 门扉冻结不触发
    _cleanup("guest_gate")


def test_guest_daily_roll_deterministic():
    from src.plugins.guest import _guest_daily_roll

    inv = _make_inv("guest_roll")
    inv.day = 15
    assert _guest_daily_roll(inv) == _guest_daily_roll(inv)  # 稳定
    assert 1 <= _guest_daily_roll(inv) <= 100
    inv.day = 16
    assert 1 <= _guest_daily_roll(inv) <= 100
    _cleanup("guest_roll")


def test_guest_should_trigger_dice():
    from src.plugins import guest

    inv = _make_inv("guest_prob")
    inv.day = 15
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        wid = guest.guest_should_trigger(inv)
        assert wid is not None and wid in guest.guest_registry()
    with patch("src.plugins.guest._guest_daily_roll", return_value=99):
        assert guest.guest_should_trigger(inv) is None
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup("guest_prob")


def test_guest_should_trigger_guards():
    """当日已触发守卫 / 已处于 flow 中 → 不再触发。"""
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    inv = _make_inv("guest_guard")
    inv.day = 15
    guest._guest_met_day[inv.qq] = 15
    assert guest.guest_should_trigger(inv) is None
    guest._guest_met_day.pop(inv.qq, None)
    flow_states[inv.qq] = {"flow_id": "sword_magic", "node_id": "s1"}
    try:
        assert guest.guest_should_trigger(inv) is None
    finally:
        flow_states.pop(inv.qq, None)
    _cleanup("guest_guard")


def test_guest_should_trigger_not_on_day4_or_40():
    from unittest.mock import patch

    from src.plugins import guest

    inv = _make_inv("guest_day")
    for day in (4, 40):
        inv.day = day
        with patch("src.plugins.guest._guest_daily_roll", return_value=1):
            assert guest.guest_should_trigger(inv) is None, f"day{day} 不应触发"
    _cleanup("guest_day")


# --- C1.5：需求1 同局接力（每局一次 / 接力季 / 兜底 / 入口条件） ---
def test_guest_enter_sets_run_triggered():
    """触发成功后置 run_triggered 旗标（周目内每局一次守卫）。"""
    from src.plugins import guest

    qq = "guest_rt"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()
    _enter(qq, inv, "sword_magic")
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.get_flag("guest.run_triggered") == 1
    assert reloaded.get_flag("flow.sword_magic.visited") is True
    assert reloaded.get_flag("guest.met_day") == 15
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


def test_guest_should_trigger_once_per_run():
    """每局一次：run_triggered 置位后普通世界候选全部排除，返回 None。

    本场景 s1 未完成 → 接力候选同时为空（s2 入口条件不满足），双重断言。
    """
    from src.plugins import guest

    inv = _make_inv("guest_once")
    inv.day = 15
    inv.set_flag("guest.run_triggered", 1)
    inv.save()
    # roll=1 必中任一候选 value——仍返回 None 即证过滤生效（非骰子原因）
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        assert guest.guest_should_trigger(inv) is None
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup("guest_once")


def test_guest_should_trigger_once_fallback_done_flag():
    """兜底：无 run_triggered 但已有 flow.*.done 旗标（存量玩家）→ 普通异界同样不再触发。"""
    from src.plugins import guest

    inv = _make_inv("guest_fb")
    inv.day = 15
    inv.set_flag("flow.sword_magic.done", 1)
    inv.save()
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        assert guest.guest_should_trigger(inv) is None
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup("guest_fb")


def test_guest_relay_s2_after_s1_done():
    """同局接力：s1 done + run_triggered 置位后，骰 ≤ s2 value → 触发 anime_academy.s2。"""
    from src.plugins import guest

    inv = _make_inv("guest_relay")
    inv.day = 16
    inv.set_flag("flow.flow.anime_academy.s1.done", 1)
    inv.set_flag("guest.run_triggered", 1)
    inv.save()
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        assert guest.guest_should_trigger(inv) == "flow.anime_academy.s2"
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup("guest_relay")


def test_guest_relay_s2_via_fallback_legacy():
    """存量兼容：无 run_triggered 但 s1 已 done（旧版本玩家）→ 兜底识别后 s2 接力仍可触发。"""
    from src.plugins import guest

    inv = _make_inv("guest_relay_legacy")
    inv.day = 16
    inv.set_flag("flow.flow.anime_academy.s1.done", 1)
    inv.save()
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        assert guest.guest_should_trigger(inv) == "flow.anime_academy.s2"
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup("guest_relay_legacy")


def test_guest_relay_s2_only_once():
    """接力季本身遵守一个世界只触发一次：s2 已 visited → 接力候选推进到 s3。"""
    from src.plugins import guest

    inv = _make_inv("guest_relay_once")
    inv.day = 16
    inv.set_flag("flow.flow.anime_academy.s1.done", 1)
    inv.set_flag("guest.run_triggered", 1)
    inv.set_flag("flow.flow.anime_academy.s2.done", 1)
    inv.save()
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        assert guest.guest_should_trigger(inv) == "flow.anime_academy.s3"
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup("guest_relay_once")


def test_guest_relay_s1_not_done_entry_gate():
    """s1 未完成 → s2 入口条件不满足，_world_available 为 False（接力候选为空根因）。"""
    from src.plugins import guest
    from src.plugins.guest import _world_available, guest_registry

    inv = _make_inv("guest_relay_gate")
    inv.day = 16
    inv.save()
    s2 = guest_registry()["flow.anime_academy.s2"]
    assert s2.get("接力") is True
    assert _world_available(inv, s2, "flow.anime_academy.s2") is False
    # 触发流程层面：run_triggered 置位 + s1 未完成 → 无接力候选 → 返回 None
    inv.set_flag("guest.run_triggered", 1)
    inv.save()
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        assert guest.guest_should_trigger(inv) is None
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup("guest_relay_gate")


def test_guest_relay_s2_dice_threshold():
    """接力概率走季级「触发概率」：骰 > s2 value 时不触发 s2（推进到 s3 边界）。"""
    from src.plugins import guest

    inv = _make_inv("guest_relay_th")
    inv.day = 16
    inv.set_flag("flow.flow.anime_academy.s1.done", 1)
    inv.set_flag("flow.flow.anime_academy.s2.done", 1)  # S3 严格依赖 s2 完成（A3 修复）
    inv.set_flag("guest.run_triggered", 1)
    inv.save()
    with patch("src.plugins.guest._guest_daily_roll", return_value=11):
        # 11 > 10（s2 value）→ 不触发 s2；11 ≤ 15（s3 value）→ 推进到 s3
        assert guest.guest_should_trigger(inv) == "flow.anime_academy.s3"
    with patch("src.plugins.guest._guest_daily_roll", return_value=16):
        assert guest.guest_should_trigger(inv) is None  # 16 > 15（s3 value）
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup("guest_relay_th")


# 三世界（迁移目标 flow_id）：单季世界 flow_id = 世界 id；anime_academy 为多季世界
# （S1/S2 每季 flow.anime_academy.<季>，裸世界 id 别名 → 首季）
_ANIME_SEASONS = ("flow.anime_academy.s1", "flow.anime_academy.s2", "flow.anime_academy.s3")
_GUEST_FLOWS = ("sword_magic", "dnd_dungeon", "time_lab", *_ANIME_SEASONS)
_SOUVENIR_BY_FLOW = {
    "sword_magic": "310",
    "dnd_dungeon": "310",
    "time_lab": "312",  # 专属纪念品（311 已被 anime_academy s2 占用）
    "flow.anime_academy.s1": "310",
    "flow.anime_academy.s2": "311",  # S2 专属纪念品
    "flow.anime_academy.s3": "313",  # S3 专属纪念品
    "ext_guest": "310",  # test_flow_engine_ext 注册的扩展测试世界（同带 skip_daily）
}


# --- C2：迁移后数据红线 ---
def test_guest_registry_three_worlds():
    from src.plugins.guest import guest_registry

    reg = guest_registry()
    assert set(reg.keys()) >= set(_GUEST_FLOWS)
    for fid in _GUEST_FLOWS:
        f = reg[fid]
        assert f.get("节点"), f"{fid} 缺少节点"
        assert f.get("收尾", {}).get("skip_daily") is True, f"{fid} 缺 guest 收尾标记"
        assert f["节点"]["end"]["类型"] == "结束"
        assert f["节点"]["end_lose"]["完成标记"] is False, f"{fid} 战败应不设 done"
        assert f["节点"]["souvenir"]["类型"] == "纪念品"


def test_guest_data_no_relic_rewards():
    """红线：迁移后 flow 效果物品不得含 400~508 信物（E09 稀缺性保护）。"""
    from src.plugins.guest import guest_registry

    relics = set(relic_ids())
    for fid, flow in guest_registry().items():
        for nid, node in flow["节点"].items():
            effects = (node.get("效果") or {}) if node.get("类型") == "奖励" else {}
            item = effects.get("物品")
            if item:
                assert str(item) not in relics, f"{fid}/{nid} 奖励信物 {item}"
            for opt in node.get("选项") or []:
                item = (opt.get("效果") or {}).get("物品")
                if item:
                    assert str(item) not in relics, f"{fid}/{nid} 选项掉落信物 {item}"
            battle = node.get("战斗") or {}
            item = (battle.get("胜利") or {}).get("效果", {}).get("物品")
            if item:
                assert str(item) not in relics, f"{fid}/{nid} 战斗掉落信物 {item}"


def test_guest_battle_monsters_exist():
    """迁移后战斗节点怪物 id 必须在 monster_data 中存在。"""
    from src.plugins.guest import guest_registry
    from src.services.data_loader import data_loader

    for fid, flow in guest_registry().items():
        for nid, node in flow["节点"].items():
            battle = node.get("战斗") or {}
            if battle:
                mid = str(battle.get("怪物", ""))
                assert mid in data_loader.monster_data, f"{fid}/{nid} 怪物 {mid} 不存在"


def test_guest_stage_references_valid():
    """节点「下一步」引用合法：阶段存在 / souvenir/end/end_lose 齐备。

    仅验证真实世界季（排除测试注册的 ext_guest 简化 flow）。
    """
    from src.plugins.guest import guest_registry

    for fid, flow in guest_registry().items():
        if fid.startswith("ext_"):
            continue  # 测试专用简化 flow（无标准四节点）
        nodes = flow["节点"]
        assert flow["入口"] in nodes
        assert set(nodes) >= {"enter", "souvenir", "end", "end_lose"}
        for nid, node in nodes.items():
            nxt = node.get("下一步")
            if nxt and nxt != "结束":
                assert nxt in nodes, f"{fid}/{nid} 下一步 {nxt} 不存在"
            for opt in node.get("选项") or []:
                nxt = opt.get("下一步", "结束")
                if nxt != "结束":
                    assert nxt in nodes, f"{fid}/{nid} 选项下一步 {nxt} 不存在"
            battle = node.get("战斗") or {}
            for br in ("胜利", "战败"):
                nxt = battle.get(br, {}).get("下一步")
                if nxt:
                    assert nxt in nodes, f"{fid}/{nid} 战斗.{br} 下一步 {nxt} 不存在"
            if node.get("类型") == "纪念品":
                # 节点物品缺省回退 flow.纪念品（每季专属）；测试 flow（ext_*）跳过
                if fid.startswith("ext_"):
                    continue
                assert (node.get("物品") or flow.get("纪念品")) == _SOUVENIR_BY_FLOW[fid]


# --- C3：三世界端到端（胜利 → 纪念品 + done + day+1） ---
def test_guest_full_flow_win_souvenir_and_day_advance():
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    qq = "guest_full"
    inv = _make_inv(qq)
    inv.day = 15
    inv.is_adventure = True
    inv.set_skill("闪避", 80)
    inv.set_skill("聆听", 80)
    inv.save()
    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        out = _enter(qq, inv, "sword_magic")
    text = "\n".join(out)
    assert "空间裂缝" in text  # guest.enter_title
    assert "王都" in text  # 世界入场
    assert "坠入王都" in text  # s1 标题（flow.标题 · node.标题）
    assert "你的抉择" in text
    assert flow_states[qq]["node_id"] == "s1"

    _drive(qq, "拾起一把断剑")  # 临时 格斗+10
    assert flow_states[qq]["node_id"] == "s2"
    assert flow_states[qq]["buff"]["格斗"] == 10
    _drive(qq, "俯身行礼，献上敬重")
    assert flow_states[qq]["node_id"] == "s3"
    _drive(qq, "与他合奏")
    state = flow_states[qq]
    assert state["phase"] == "battle"
    assert state["battle"].is_gm_room is True  # 隔离：战败不登记 E07 / 胜利不走完整结算

    service = state["battle"]
    service.execute_action = _win_action(service)
    out = _drive(qq, "格斗")
    text = "\n".join(out)
    assert "幽影在你的剑下坍缩" in text  # 胜利回复
    assert "异界纪念品" in text  # 纪念品发放行（guest.souvenir_gain）
    assert "王都记着你的名字" in text  # 纪念品节点文案
    assert qq not in flow_states  # 已收尾清理

    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.day == 16  # 归途 day+1
    assert reloaded.is_adventure is False
    assert reloaded.get_flag("flow.sword_magic.done") == 1  # 胜利设完成标记
    equipments, _ = reloaded.get_equipments()
    assert equipments.get("310", 0) == 1
    assert reloaded.get_flag("guest.met_day") == 15  # 当日守卫持久化
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


def test_guest_full_flow_battle_defeat_no_souvenir():
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    qq = "guest_lose"
    inv = _make_inv(qq)
    inv.day = 20
    inv.set_skill("闪避", 80)
    inv.set_skill("聆听", 80)
    inv.save()
    state = _enter_battle(qq, inv, "sword_magic")
    service = state["battle"]
    service.execute_action = _lose_action(service)
    out = _drive(qq, "闪避")
    text = "\n".join(out)
    assert "故事没有结束，只是换了主角" in text  # 战败回复
    assert "异界纪念品" not in text  # 空手，无纪念品
    assert qq not in flow_states

    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.day == 21
    assert not reloaded.get_flag("flow.sword_magic.done")  # 战败不设 done
    equipments, _ = reloaded.get_equipments()
    assert equipments.get("310", 0) == 0
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


def test_guest_souvenir_idempotent_when_held():
    """已持 310 重通关：不重发、不重复文案（幂等，存量玩家兼容）。"""
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    qq = "guest_sou"
    inv = _make_inv(qq)
    inv.day = 15
    inv.set_skill("闪避", 80)
    inv.set_skill("聆听", 80)
    inv.add_item_to_inventory("310", 1)
    inv.save()
    state = _enter_battle(qq, inv, "sword_magic")
    service = state["battle"]
    service.execute_action = _win_action(service)
    out = _drive(qq, "格斗")
    text = "\n".join(out)
    assert "异界纪念品" not in text  # 已持有：不追加发放行
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    equipments, _ = reloaded.get_equipments()
    assert equipments.get("310", 0) == 1  # 数量不变
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


def test_guest_dnd_dungeon_win():
    """dnd_dungeon 世界：2 阶段 + 战斗(46) 胜利 → 奖励 505 + 纪念品 + done。"""
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    qq = "guest_dnd"
    inv = _make_inv(qq)
    inv.day = 15
    inv.set_skill("智力", 80)
    inv.save()
    state = _enter_battle(qq, inv, "dnd_dungeon")
    service = state["battle"]
    assert service.monster.id == "46"  # 凝视者
    service.execute_action = _win_action(service)
    _drive(qq, "格斗")
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    equipments, _ = reloaded.get_equipments()
    assert equipments.get("310", 0) == 1  # 纪念品
    assert equipments.get("505", 0) == 1  # 胜利奖励消耗品
    assert reloaded.get_flag("flow.dnd_dungeon.done") == 1
    assert qq not in flow_states
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


def test_guest_anime_academy_win_and_player_texts():
    """anime_academy 世界：胜利 + flow.玩家文案 17 键注入战斗。"""
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    qq = "guest_anime"
    inv = _make_inv(qq)
    inv.day = 15
    inv.set_skill("聆听", 80)
    inv.save()
    state = _enter_battle(qq, inv, "anime_academy")
    service = state["battle"]
    assert service.monster.id == "47"  # 学生会会长
    assert service._guest_texts.get("格斗成功") == (
        "你一个标准的「主角式突进」轰在会长身上，$骰子=$伤害，造成$伤害点伤害——连背景的樱花都被你的气势震落了八瓣。"
    )
    assert len(service._guest_texts) == 17  # flow 级玩家文案 17 键全量注入
    service.execute_action = _win_action(service)
    _drive(qq, "格斗")
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    # 多季世界：S1 规范键 = flow.anime_academy.s1 → done 旗标 = flow.flow.anime_academy.s1.done
    assert reloaded.get_flag("flow.flow.anime_academy.s1.done") == 1
    assert qq not in flow_states
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


def test_guest_anime_academy_s2_win():
    """anime_academy S2：需 S1 done 解锁；双连战（49→50）胜利 → 纪念品 311 + done。"""
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    qq = "guest_anime_s2"
    inv = _make_inv(qq)
    inv.day = 16
    inv.set_flag("flow.flow.anime_academy.s1.done", 1)  # S1 完成解锁 S2
    inv.save()
    # 未完成 S1：S2 不可进入（入口条件门）
    fresh = _make_inv("guest_anime_s2_locked")
    fresh.day = 16
    fresh.save()
    from src.services.flow_engine import start_flow

    assert start_flow(fresh.qq, "flow.anime_academy.s2") is None
    _cleanup("guest_anime_s2_locked")

    state = _enter_battle(qq, inv, "flow.anime_academy.s2")
    service = state["battle"]
    assert service.monster.id == "49"  # 场记小姐（中 Boss）
    service.execute_action = _win_action(service)
    _drive(qq, "格斗")
    state = flow_states[qq]
    assert state["node_id"] == "b2"  # 胜利 → 第二场连战
    service = state["battle"]
    assert service.monster.id == "50"  # 大结局委员会（最终 Boss）
    service.execute_action = _win_action(service)
    _drive(qq, "格斗")
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.get_flag("flow.flow.anime_academy.s2.done") == 1
    equipments, _ = reloaded.get_equipments()
    assert equipments.get("311", 0) == 1  # S2 专属纪念品
    assert qq not in flow_states
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


def test_guest_anime_academy_s2_battle_defeat_no_souvenir():
    """anime_academy S2 战败（场记小姐）：空手归途、不设 done。"""
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    qq = "guest_anime_s2_lose"
    inv = _make_inv(qq)
    inv.day = 17
    inv.set_flag("flow.flow.anime_academy.s1.done", 1)
    inv.save()
    state = _enter_battle(qq, inv, "flow.anime_academy.s2")
    service = state["battle"]
    service.execute_action = _lose_action(service)
    out = _drive(qq, "格斗")
    text = "\n".join(out)
    assert "场记小姐" in text or "路人" in text  # 战败回复
    assert "异界纪念品" not in text  # 空手，无纪念品
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert not reloaded.get_flag("flow.flow.anime_academy.s2.done")  # 战败不设 done
    equipments, _ = reloaded.get_equipments()
    assert equipments.get("311", 0) == 0
    assert qq not in flow_states
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


def test_guest_battle_image_card_flow():
    """战斗图片卡片（battle_round_html）：胜利路径图片成功仍走纪念品/收尾。"""
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    qq = "guest_img"
    inv = _make_inv(qq)
    inv.day = 15
    inv.set_skill("闪避", 80)
    inv.set_skill("聆听", 80)
    inv.save()
    state = _enter_battle(qq, inv, "sword_magic")
    service = state["battle"]
    service.execute_action = _win_action(service)
    with patch("src.services.flow_engine.render_pic", return_value=b"img"), patch(
        "src.services.flow_engine.send_pic", new=AsyncMock(return_value=True)
    ):
        out = _drive(qq, "格斗")
    text = "\n".join(out)
    assert "你挥出最后一击。" not in text  # 图片成功：md 战报不发
    assert "幽影在你的剑下坍缩" in text  # 胜利分支仍走
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.get_flag("flow.sword_magic.done") == 1
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


# --- C4：命令兜底 / 按钮回调（薄壳兼容委托 flow_engine） ---
def test_guest_command_flow():
    """命令兜底：/行动 <选项输入> 经 _guest_handle_input 委托完成阶段选择。"""
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    qq = "guest_cmd"
    inv = _make_inv(qq)
    inv.day = 15
    inv.set_skill("闪避", 80)
    inv.save()
    san_before = inv.get_skill("san", 0)
    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        _enter(qq, inv, "sword_magic")
        out = _drive(qq, "走向钟楼")  # san -1
    assert "震颤" in "\n".join(out)
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.get_skill("san", 0) == san_before - 1
    assert flow_states[qq]["node_id"] == "s2"
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


def test_guest_invalid_choice():
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    qq = "guest_bad"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()
    _enter(qq, inv, "sword_magic")
    out = _drive(qq, "这不是选项")
    assert "没有这样的回应" in "\n".join(out)
    assert flow_states[qq]["node_id"] == "s1"  # 未推进
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


def test_guest_button_handler_compat():
    """薄壳兼容按钮回调：payload flow_stage:node|option → 应用效果；旧节点被守卫忽略。"""
    from src.plugins.guest import _guest_button_handler
    from src.services.flow_engine import flow_states

    qq = "guest_btn"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()
    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        _enter(qq, inv, "sword_magic")
        asyncio.run(_guest_button_handler(qq, "s1|拾起一把断剑", ConsoleBot(), ""))
    assert flow_states[qq]["buff"]["格斗"] == 10
    assert flow_states[qq]["node_id"] == "s2"
    baseline = Investigator(investigator_repo.find_by_qq(qq))
    # 旧节点 payload（s1 已过期）→ 忽略：状态与数值不变
    asyncio.run(_guest_button_handler(qq, "s1|走向钟楼", ConsoleBot(), ""))
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.get_skill("san", 0) == baseline.get_skill("san", 0)
    assert flow_states[qq]["node_id"] == "s2"
    from src.plugins import guest

    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


# --- C5：adventure 互斥链挂载 ---
class FakeBot:
    type = "Console"

    async def send_msg(self, user_id=None, message=None):
        pass


def test_adventure_guest_trigger_sends_stage():
    """乱入命中 → 薄壳 guest_enter 接管当日冒险（不进正常战斗），flow 状态建立。"""
    from src.plugins.adventure import _run_adventure
    from src.services.flow_engine import flow_states
    from src.utils.active_battles import battle_manager
    from util.DaylyRecord import add_data, get_data, write_json

    qq = "guest_adv_on"
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

    with patch("src.plugins.adventure.guest_should_trigger", return_value="sword_magic"), patch(
        "src.plugins.adventure.hidden_should_trigger", return_value=None
    ):
        try:
            asyncio.run(_run_adventure(qq, FakeBot(), send, finish))
        except BaseException:
            pass

    text = "\n".join(sent)
    assert "空间裂缝" in text
    assert "王都" in text  # 世界入场
    assert "你的抉择" in text  # 第一幕选项
    assert qq in flow_states
    assert flow_states[qq]["node_id"] == "s1"
    assert flow_states[qq]["phase"] == "stage"
    assert battle_manager.get_battle(qq) is None  # 触发即替代当日冒险
    assert get_data(qq, "adventure_done") is True
    from src.plugins import guest

    guest._guest_met_day.pop(qq, None)
    flow_states.pop(qq, None)
    battle_manager.remove_battle(qq)
    add_data(qq, "adventure_done", False)
    write_json()
    _cleanup(qq)


def test_adventure_guest_not_triggered_normal_battle():
    """乱入未触发 → 正常开战（adventure.py 互斥链零改动冒烟）。"""
    from src.plugins.adventure import _run_adventure
    from src.utils.active_battles import battle_manager

    qq = "guest_adv_off"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()

    async def send(msg):
        pass

    async def finish(msg):
        raise SystemExit

    with patch("src.plugins.adventure.guest_should_trigger", return_value=None), patch(
        "src.plugins.adventure.npc_should_trigger", return_value=None
    ), patch("src.plugins.adventure.gm_room_should_trigger", return_value=False), patch(
        "src.plugins.adventure.pick_random_event", return_value=None
    ), patch(
        "src.plugins.adventure.run_sanity_and_madness",
        return_value=("", "", False, 0, False, 0),
    ), patch(
        "src.plugins.adventure.hidden_should_trigger", return_value=None
    ):
        try:
            asyncio.run(_run_adventure(qq, FakeBot(), send, finish))
        except SystemExit:
            pass

    battle = battle_manager.get_battle(qq)
    assert battle is not None, "乱入未触发时应正常开战"
    battle_manager.remove_battle(qq)
    _cleanup(qq)
