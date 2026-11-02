"""统一流程引擎（flow_engine）测试。

覆盖：注册表与引用解析、节点流转（文本→选项→检定→奖励→结束）、选项分支/条件门/
效果应用/临时 buff、检定成功失败、战斗节点（胜利/战败 + is_gm_room 隔离）、
状态清理、未知 flow 回退、命令入口与按钮守卫。
只测新增行为，不修改 src/ 既有系统。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import asyncio

import pytest
import ujson

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
from src.models.monster import Monster
from src.services.battle import BattleService


def _make_inv(qq: str, name: str = "Flow") -> Investigator:
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


@pytest.fixture(autouse=True)
def _allow_gm_explore(monkeypatch):
    """/探索 已改为 GM 专用；测试用 stub 放行，同时覆盖命令通道行为。"""
    from src.plugins import flow

    monkeypatch.setattr(flow, "is_master", lambda _uid: True)


class ConsoleBot:
    type = "Console"

    async def send_msg(self, user_id=None, message=None):
        pass


def _drive(qq: str, choice: str, bot=None, token=None) -> list[str]:
    """驱动 flow_engine.handle_flow_input，收集发送文本。"""
    from src.services.flow_engine import handle_flow_input

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(handle_flow_input(qq, choice, bot or ConsoleBot(), send, token))
    return sent


def _explore(qq: str, ref: str, bot=None) -> list[str]:
    """模拟 /探索：解析引用 → start_flow → 渲染首节点。"""
    from src.services.flow_engine import render_flow_node, resolve_flow_ref, start_flow

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    fid = resolve_flow_ref(ref)
    state = start_flow(qq, fid) if fid else None
    if state is not None:
        asyncio.run(render_flow_node(qq, state, bot or ConsoleBot(), send))
    return sent


# --- C1：注册表 / 引用解析 / 数据完整性 ---
def test_flow_registry_has_lane_tale():
    from src.services.flow_engine import flow_registry

    reg = flow_registry()
    assert "lane_tale" in reg
    f = reg["lane_tale"]
    assert f["入口"] == "n1"
    for nid in ("n1", "n2", "n3", "n4", "end"):
        assert nid in f["节点"], f"缺少节点 {nid}"


def test_flow_node_text_keys_present():
    """每个节点默认文案键 flow.<fid>.<nid> 在 text_data 中存在（end 用结束收尾文案）。"""
    from src.services.flow_engine import _node_text, flow_registry

    for fid, f in flow_registry().items():
        for nid in f.get("节点") or {}:
            assert _node_text(fid, nid) != "", f"{fid}/{nid} 缺文案"


def test_resolve_flow_ref_by_id_and_name():
    from src.services.flow_engine import resolve_flow_ref

    assert resolve_flow_ref("lane_tale") == "lane_tale"
    assert resolve_flow_ref("巷口异闻") == "lane_tale"  # 中文名字可触发
    assert resolve_flow_ref("不存在的探索") is None
    assert resolve_flow_ref("") is None
    assert resolve_flow_ref("   ") is None


# --- C2：节点流转 / 状态机 ---
def test_start_flow_unknown_returns_none():
    from src.services.flow_engine import flow_states, start_flow

    qq = "flow_unknown"
    _make_inv(qq)
    assert start_flow(qq, "no_such_flow") is None
    assert qq not in flow_states
    _cleanup(qq)


def test_explore_renders_entry_text_and_options():
    from src.services.flow_engine import flow_states

    qq = "flow_entry"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    out = _explore(qq, "lane_tale")
    text = "\n".join(out)
    assert "入夜时分" in text  # n1 文案
    assert "你的抉择" in text  # 选项标题（选项按钮在键盘段，文本不含按钮文字）
    assert qq in flow_states
    assert flow_states[qq]["node_id"] == "n1"
    assert flow_states[qq]["phase"] == "stage"
    _cleanup(qq)


def test_text_node_choice_advances():
    from src.services.flow_engine import flow_states

    qq = "flow_adv"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    _explore(qq, "lane_tale")
    _drive(qq, "跟上那抹身影")
    assert flow_states[qq]["node_id"] == "n2"
    _cleanup(qq)


def test_choice_leave_ends_flow():
    from src.services.flow_engine import flow_states

    qq = "flow_leave"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    _explore(qq, "lane_tale")
    out = _drive(qq, "当作没看见，转身离开")
    text = "\n".join(out)
    assert "探索结束" in text
    assert "你走出巷口" in text  # end 节点文案
    assert qq not in flow_states  # 状态已清理


def test_invalid_choice_stays_on_node():
    from src.services.flow_engine import flow_states

    qq = "flow_bad"
    _make_inv(qq)
    _explore(qq, "lane_tale")
    out = _drive(qq, "这不是选项")
    assert "没有这样的回应" in "\n".join(out)
    assert flow_states[qq]["node_id"] == "n1"
    _cleanup(qq)


def test_old_button_payload_ignored():
    """旧按钮（node_id 已过期）被守卫忽略：状态与数值不变。"""
    from src.services.flow_engine import flow_states

    qq = "flow_oldbtn"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    _explore(qq, "lane_tale")
    _drive(qq, "跟上那抹身影")  # n1 -> n2
    baseline = flow_states[qq]["node_id"]
    out = _drive(qq, "n1|跟上那抹身影")  # 旧节点 payload
    assert flow_states[qq]["node_id"] == baseline  # 未被回退
    _cleanup(qq)


def test_start_flow_resets_previous_state():
    """重复启动同一 flow 清理旧状态（防叠加）。"""
    from src.services.flow_engine import flow_states

    qq = "flow_restart"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    _explore(qq, "lane_tale")
    _drive(qq, "跟上那抹身影")
    assert flow_states[qq]["node_id"] == "n2"
    _explore(qq, "lane_tale")  # 重新启动 → 回到入口，状态重置
    assert flow_states[qq]["node_id"] == "n1"
    _cleanup(qq)


# --- C3：选项分支 / 条件门 / 效果应用 / 检定 ---
def test_option_check_success_applies_effects():
    """「迎上去盘问」侦查检定成功：金币 +15，随后 n3 意志成功 hp+3、n4 奖励金币+20 san+2。"""
    from unittest.mock import patch

    from database.db import get_info

    from src.services.flow_engine import flow_states

    qq = "flow_ok"
    inv = _make_inv(qq)
    inv.day = 10
    inv.set_skill("侦查", 80)
    inv.set_skill("意志", 90)
    inv.hp = 5  # 压到上限以下，便于断言 hp+3
    inv.save()
    hp_before = inv.hp
    san_before = inv.get_skill("san", 0)
    gold_before = get_info(qq).gold
    _explore(qq, "lane_tale")
    _drive(qq, "跟上那抹身影")
    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        out = _drive(qq, "迎上去盘问")
    text = "\n".join(out)
    assert "侦查 检定" in text
    assert "你赢了" not in text
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert get_info(qq).gold == gold_before + 15 + 20  # 选项检定 + 奖励节点
    assert reloaded.hp == hp_before + 3  # n3 意志成功 hp+3
    assert reloaded.get_skill("san", 0) == san_before + 2  # n4 奖励 san+2
    assert qq not in flow_states  # 自动推进到结束并清理
    _cleanup(qq)


def test_option_check_failure_applies_effects():
    """「迎上去盘问」侦查检定失败：hp-2 san-1；n3 意志失败 san-4；n4 奖励 san+2。"""
    from unittest.mock import patch

    from src.services.flow_engine import flow_states

    qq = "flow_fail"
    inv = _make_inv(qq)
    inv.day = 10
    inv.set_skill("侦查", 30)
    inv.set_skill("意志", 30)
    inv.save()
    hp_before = inv.hp
    san_before = inv.get_skill("san", 0)
    _explore(qq, "lane_tale")
    _drive(qq, "跟上那抹身影")
    with patch("src.services.flow_engine.roll_dice", return_value=("70", 70)):
        out = _drive(qq, "迎上去盘问")
    text = "\n".join(out)
    assert "侦查 检定" in text
    assert "寒意" in text  # n3 失败回复
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.hp == hp_before - 2  # 选项检定失败 hp-2
    assert reloaded.get_skill("san", 0) == san_before - 1 - 4 + 2  # -1(选项) -4(n3) +2(n4)
    _cleanup(qq)


def test_conditioned_option_locked_without_item():
    """无斧头（116）时「悄悄摸走」选项条件未满足：选中被拦截，节点不推进。"""
    from src.services.flow_engine import flow_states

    qq = "flow_lock"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    _explore(qq, "lane_tale")
    _drive(qq, "跟上那抹身影")
    out = _drive(qq, "悄悄摸走它怀里的东西")
    text = "\n".join(out)
    assert "条件未满足" in text
    assert flow_states[qq]["node_id"] == "n2"  # 未推进
    _cleanup(qq)


def test_conditioned_option_available_with_item():
    """持有斧头（116）时「悄悄摸走」可选：金币+10 san-2、临时 闪避+10，推进到 n3。"""
    from unittest.mock import patch

    from database.db import get_info

    from src.services.flow_engine import flow_states

    qq = "flow_cond"
    inv = _make_inv(qq)
    inv.day = 10
    inv.set_skill("意志", 90)
    inv.save()
    inv.add_item_to_inventory("116", 1)
    gold_before = get_info(qq).gold
    san_before = inv.get_skill("san", 0)
    _explore(qq, "lane_tale")
    _drive(qq, "跟上那抹身影")
    with patch("src.services.flow_engine.roll_dice", return_value=("5", 5)):
        out = _drive(qq, "悄悄摸走它怀里的东西")
    text = "\n".join(out)
    assert "顺走" in text  # 选项回复文案
    assert get_info(qq).gold == gold_before + 10 + 20  # 选项 +10，奖励节点 +20
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.get_skill("san", 0) == san_before - 2 + 2  # 选项 -2，n3 成功(意志90)+n4 奖励 +2
    assert qq not in flow_states  # n3 → n4 → end 自动推进清理
    _cleanup(qq)


# --- C4：检定节点 / 奖励节点自动推进 ---
def test_reward_node_applies_effects_then_end():
    from unittest.mock import patch

    from database.db import get_info

    from src.services.flow_engine import flow_states

    qq = "flow_reward"
    inv = _make_inv(qq)
    inv.day = 10
    inv.set_skill("意志", 90)
    inv.save()
    gold_before = get_info(qq).gold
    san_before = inv.get_skill("san", 0)
    _explore(qq, "lane_tale")
    _drive(qq, "跟上那抹身影")
    # 直接驱动到 n3 前的分支：选「悄悄摸走」（无条件门用物品绕过锁定）
    inv.add_item_to_inventory("116", 1)
    with patch("src.services.flow_engine.roll_dice", return_value=("5", 5)):
        _drive(qq, "悄悄摸走它怀里的东西")
    # 已自动推进到结束：奖励节点（金币+20 san+2）已应用
    assert qq not in flow_states
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert get_info(qq).gold == gold_before + 10 + 20  # 选项 +10，奖励节点 +20
    assert reloaded.get_skill("san", 0) == san_before - 2 + 2
    _cleanup(qq)


# --- C5：战斗节点（BattleService + is_gm_room 隔离） ---
def test_battle_node_victory():
    """战斗节点：is_gm_room 隔离、临时 buff 注入环境、胜利发放奖励 + 战利品并推进到结束。"""
    from database.db import get_info

    from src.services.flow_engine import flow_states, register_flow

    register_flow(
        "test_battle_flow",
        {
            "id": "test_battle_flow",
            "名字": "测试战斗",
            "入口": "n1",
            "节点": {
                "n1": {
                    "类型": "文本",
                    "选项": [
                        {"输入": "拔剑", "临时": {"格斗": 10}, "下一步": "battle"}
                    ],
                },
                "battle": {
                    "类型": "战斗",
                    "战斗": {
                        "怪物": "45",
                        "开场": "测试开场白",
                        "胜利": {
                            "效果": {"金币": 30, "hp": 5},
                            "回复": "你赢了。",
                            "下一步": "end",
                        },
                        "战败": {"回复": "你输了。", "下一步": "end"},
                    },
                },
                "end": {"类型": "结束"},
            },
        },
    )

    qq = "flow_bwin"
    inv = _make_inv(qq)
    inv.day = 10
    inv.hp = 5  # 压到上限以下，便于断言胜利 hp+5
    inv.save()
    _explore(qq, "test_battle_flow")
    _drive(qq, "拔剑")
    state = flow_states[qq]
    assert state["phase"] == "battle"
    service = state["battle"]
    assert service is not None
    assert service.is_gm_room is True  # 隔离：战败不登记 E07 / 胜利不走完整结算（战利品仍发）
    assert flow_states[qq]["buff"]["格斗"] == 10  # 选项「临时」buff 累积
    assert service.environment.get("玩家", {}).get("格斗") == 10  # buff 注入战斗环境
    gold_before = get_info(qq).gold
    hp_before = inv.hp

    def _win(action):
        service.hp_record["mon"] = 0
        return ("你挥出最后一击。",)

    service.execute_action = _win  # type: ignore[method-assign]
    out = _drive(qq, "格斗")
    text = "\n".join(out)
    assert "你赢了。" in text
    # 节点胜利效果（+30 金币 +5 HP）与战利品共存：怪物 45 hp15 → 档2 乌帕 10~20
    assert gold_before + 40 <= get_info(qq).gold <= gold_before + 50
    assert "乌帕" in text  # 战利品行追加在胜利回复尾部
    assert Investigator(investigator_repo.find_by_qq(qq)).hp == hp_before + 5
    assert qq not in flow_states  # 胜利推进到 end 并清理
    _cleanup(qq)


def test_battle_node_defeat():
    """战斗节点战败：战败回复 + 状态清理（不落 is_survive，隔离语义）。"""
    from src.services.flow_engine import flow_states, register_flow

    register_flow(
        "test_battle_lose",
        {
            "id": "test_battle_lose",
            "名字": "测试战败",
            "入口": "n1",
            "节点": {
                "n1": {
                    "类型": "文本",
                    "选项": [{"输入": "应战", "下一步": "battle"}],
                },
                "battle": {
                    "类型": "战斗",
                    "战斗": {
                        "怪物": "45",
                        "开场": "测试开场白",
                        "胜利": {"回复": "你赢了。", "下一步": "end"},
                        "战败": {"回复": "你输了。", "下一步": "end"},
                    },
                },
                "end": {"类型": "结束"},
            },
        },
    )

    qq = "flow_blose"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    _explore(qq, "test_battle_lose")
    _drive(qq, "应战")
    service = flow_states[qq]["battle"]
    assert service.is_gm_room is True

    def _lose(action):
        service.hp_record["inv"] = 0
        service.fled = True
        return ("你被击倒了。",)

    service.execute_action = _lose  # type: ignore[method-assign]
    out = _drive(qq, "闪避")
    text = "\n".join(out)
    assert "你输了。" in text
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.is_survive is True  # 隔离红线：战败不落 is_survive
    assert qq not in flow_states
    _cleanup(qq)


def test_battle_no_action_when_no_battle():
    from src.services.flow_engine import flow_states

    qq = "flow_nobattle"
    _make_inv(qq)
    _explore(qq, "lane_tale")
    flow_states[qq]["phase"] = "battle"  # 伪造战斗阶段但无 battle 对象
    flow_states[qq]["battle"] = None
    out = _drive(qq, "格斗")
    assert "没有正在进行中的探索" in "\n".join(out)
    _cleanup(qq)


# --- C6：输入分发 / 命令入口 / 状态清理 ---
def test_no_active_when_no_state():
    qq = "flow_noact"
    _make_inv(qq)
    out = _drive(qq, "跟上那抹身影")
    assert "没有正在进行中的探索" in "\n".join(out)
    _cleanup(qq)


def test_command_handle_explore_smoke():
    """/探索 巷口异闻（中文名）入口：发入场标题 + 首节点，进入状态机。"""
    from src.plugins.flow import _handle_explore

    from src.services.flow_engine import flow_states

    qq = "flow_cmd"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    sent: list[str] = []
    finished: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    async def finish(msg):
        finished.append(str(msg))

    asyncio.run(_handle_explore(qq, "巷口异闻", ConsoleBot(), send, finish))
    text = "\n".join(sent + finished)
    assert "奇闻探索" in text
    assert "入夜时分" in text
    assert qq in flow_states
    _cleanup(qq)


def test_command_unknown_flow():
    """未知 flow：提示未知 + 不建状态。"""
    from src.plugins.flow import _handle_explore

    from src.services.flow_engine import flow_states

    qq = "flow_cmd_unk"
    _make_inv(qq)
    finished: list[str] = []

    async def send(msg):
        pass

    async def finish(msg):
        finished.append(str(msg))

    asyncio.run(_handle_explore(qq, "不存在的探索", ConsoleBot(), send, finish))
    assert "没有找到这个探索" in "\n".join(finished)
    assert qq not in flow_states
    _cleanup(qq)


def test_command_explore_while_active_rejected():
    """进行中再启动探索被拒绝。"""
    from src.plugins.flow import _handle_explore

    from src.services.flow_engine import flow_states

    qq = "flow_cmd_dup"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    sent: list[str] = []
    finished: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    async def finish(msg):
        finished.append(str(msg))

    asyncio.run(_handle_explore(qq, "巷口异闻", ConsoleBot(), send, finish))
    asyncio.run(_handle_explore(qq, "巷口异闻", ConsoleBot(), send, finish))
    assert "你正在探索中" in "\n".join(sent + finished)
    _cleanup(qq)


def test_command_explore_denied_for_non_master(monkeypatch):
    """/探索 仅 GM 可用；普通玩家必须拿到权限拒绝而不是进入 flow。"""
    from src.plugins import flow
    from src.services.flow_engine import flow_states

    monkeypatch.setattr(flow, "is_master", lambda _uid: False)
    qq = "flow_denied"
    sent: list[str] = []
    finished: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    async def finish(msg):
        finished.append(str(msg))

    asyncio.run(flow._handle_explore(qq, "巷口异闻", ConsoleBot(), send, finish))
    text = "\n".join(sent + finished)
    assert "没有管理员权限" in text
    assert qq not in flow_states


def test_bad_next_reference_finishes_safely():
    """选项「下一步」引用不存在的节点 → 安全收尾（不崩溃）。"""
    from src.services.flow_engine import flow_states, register_flow

    register_flow(
        "test_bad_next",
        {
            "id": "test_bad_next",
            "名字": "坏引用",
            "入口": "n1",
            "节点": {
                "n1": {
                    "类型": "文本",
                    "选项": [{"输入": "走", "下一步": "nope"}],
                }
            },
        },
    )

    qq = "flow_badnext"
    _make_inv(qq)
    _explore(qq, "test_bad_next")
    _drive(qq, "走")
    assert qq not in flow_states
    _cleanup(qq)


def test_node_condition_locked_ends():
    """节点「条件」未满足：无法进入，安全收尾。"""
    from src.services.flow_engine import flow_states, register_flow

    register_flow(
        "test_node_cond",
        {
            "id": "test_node_cond",
            "名字": "条件节点",
            "入口": "n1",
            "节点": {
                "n1": {"类型": "文本", "条件": {"物品": "999"}},
            },
        },
    )

    qq = "flow_nodecond"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    out = _explore(qq, "test_node_cond")
    assert "条件未满足" in "\n".join(out)
    assert qq not in flow_states
    _cleanup(qq)
