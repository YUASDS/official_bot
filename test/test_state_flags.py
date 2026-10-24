"""批次6：flags 状态持久化测试。

覆盖：
1. guest 当日守卫 `guest.met_day` 写入 / 重启模拟（新建 Investigator 实例标记仍在）
   → 同 game-day 不二次触发；完成标记 `guest.<world_id>.done`。
2. gm_room 当日守卫 `gm_room.met_day` 写入 / 重启模拟 → 同 game-day 不二次进入。
3. flow 完成标记 `flow.<flow_id>.done` 写入（flow_states 中途态保持内存，重启后放弃）。
4. boss 待挑战旗标 `boss.pending` 写入 / 重启模拟（boss_resolve_forced 从 flags 恢复）
   → 消费后 flags 清理（防重复消费）。
5. clear_flag 方法（player.py）删除键行为。

测试本地面板（test/ 不入库），不改 src/。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import asyncio
from unittest.mock import patch

import nonebot

nonebot.init()

from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    Investigator,
    InvestigatorGenerator,
    investigator_repo,
)
from src.services.data_loader import data_loader


def _make_inv(qq: str, name: str = "Flags") -> Investigator:
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    data["san"] = 60
    data["意志"] = 60
    inv_model = investigator_repo.create_and_save(qq, name, data)
    return Investigator(inv_model)


def _reload(qq: str) -> Investigator:
    """重启模拟：从 DB 重新加载为全新 Investigator 实例（内存 dict 已清空）。"""
    return Investigator(investigator_repo.find_by_qq(qq))


def _cleanup(qq: str) -> None:
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)


class ConsoleBot:
    type = "Console"

    async def send_msg(self, user_id=None, message=None):
        pass


# --- C1：guest 当日守卫 / 完成标记（薄壳迁移 flow_engine：完成标记 flow.<world>.done） ---
def test_guest_met_day_flag_written_and_survives_restart():
    """guest_enter 写入 `guest.met_day` + `flow.sword_magic.visited`；新建 Investigator
    实例（重启模拟）标记仍在，同 game-day 二次冒险不触发。"""
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    qq = "sf_guest_met"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    try:
        asyncio.run(guest.guest_enter(qq, inv, ConsoleBot(), send, "sword_magic"))
    except BaseException:
        pass  # guest_enter 正常出口为 FinishedException 中断

    # flags 已写入
    assert inv.get_flag("guest.met_day") == 15
    assert inv.get_flag("flow.sword_magic.visited") is True

    # 重启模拟：新 Investigator 实例读取 flags（内存 _guest_met_day / flow_states 已清）
    guest._guest_met_day.pop(qq, None)
    flow_states.pop(qq, None)
    reloaded = _reload(qq)
    assert reloaded.get_flag("guest.met_day") == 15
    # 同日二次冒险守卫：内存清空 + flags 兜底 → 不触发
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        assert guest.guest_should_trigger(reloaded) is None

    _cleanup(qq)


# 推到战斗节点的选项路径（薄壳驱动）
_GUEST_PATH = ["拾起一把断剑", "俯身行礼，献上敬重", "与他合奏"]


def _guest_drive_to_battle(qq: str, inv: Investigator, sent: list) -> None:
    """薄壳 guest_enter + 阶段推进至战斗节点（含首次进入自动检定，roll 固定成功）。"""
    from src.plugins.guest import _guest_handle_input, guest_enter

    async def send(msg):
        sent.append(str(msg))

    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        try:
            asyncio.run(guest_enter(qq, inv, ConsoleBot(), send, "sword_magic"))
        except BaseException:
            pass
        for choice in _GUEST_PATH:
            asyncio.run(_guest_handle_input(qq, choice, ConsoleBot(), send))


def _guest_finish(qq: str, sent: list, win: bool) -> None:
    """stub 战斗结果并驱动最后一轮 → 走胜利/战败收尾（纪念品 + done / 空手 + 无 done）。"""
    from src.plugins.guest import _guest_handle_input
    from src.services.flow_engine import flow_states

    async def send(msg):
        sent.append(str(msg))

    service = flow_states[qq]["battle"]

    def _act(action):
        if win:
            service.hp_record["mon"] = 0
        else:
            service.hp_record["inv"] = 0
            service.fled = True
        return ("你挥出最后一击。",)

    service.execute_action = _act  # type: ignore[method-assign]
    asyncio.run(_guest_handle_input(qq, "格斗", ConsoleBot(), send))


def test_guest_done_flag_written_on_complete():
    """完整通关（胜利路径）写入 `flow.sword_magic.done`；重启后仍可读。"""
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    qq = "sf_guest_done"
    inv = _make_inv(qq)
    inv.day = 15
    inv.set_skill("闪避", 80)
    inv.set_skill("聆听", 80)
    inv.save()
    sent: list[str] = []
    _guest_drive_to_battle(qq, inv, sent)
    _guest_finish(qq, sent, win=True)

    reloaded = _reload(qq)
    assert reloaded.get_flag("flow.sword_magic.done") == 1
    assert reloaded.get_flag("guest.done.sword_magic") is None  # 旧键不再写入
    assert qq not in flow_states
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


def test_guest_done_flag_not_written_on_defeat():
    """战败归途不写完成标记（flow.sword_magic.done 为空）。"""
    from src.plugins import guest
    from src.services.flow_engine import flow_states

    qq = "sf_guest_lose"
    inv = _make_inv(qq)
    inv.day = 15
    inv.set_skill("闪避", 80)
    inv.set_skill("聆听", 80)
    inv.save()
    sent: list[str] = []
    _guest_drive_to_battle(qq, inv, sent)
    _guest_finish(qq, sent, win=False)

    reloaded = _reload(qq)
    assert reloaded.get_flag("flow.sword_magic.done") is None
    assert qq not in flow_states
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


# --- C2：gm_room 当日守卫 ---
def test_gm_room_met_day_flag_written_and_survives_restart():
    """gm_room_enter 写入 `gm_room.met_day`；重启模拟后同 game-day 不二次进入。"""
    from src.plugins import gm_room

    qq = "sf_gm_met"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    try:
        asyncio.run(gm_room.gm_room_enter(qq, inv, ConsoleBot(), send))
    except BaseException:
        pass

    assert inv.get_flag("gm_room.met_day") == 15

    # 重启模拟：内存守卫清空 + flags 兜底 → 同日不触发
    gm_room._gm_met_day.pop(qq, None)
    from src.services.flow_engine import flow_states

    flow_states.pop(qq, None)
    reloaded = _reload(qq)
    with patch("src.plugins.gm_room.roll_dice", return_value=("100", 1)):
        assert gm_room.gm_room_should_trigger(reloaded) is False

    _cleanup(qq)


# --- C3：flow 完成标记（flow_states 中途态保持内存） ---
def test_flow_done_flag_written_on_finish():
    """flow 结束节点 → `flow.<flow_id>.done` = True；flow_states 中途态仍保持内存。"""
    import asyncio

    from src.services import flow_engine

    qq = "sf_flow_done"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    state = flow_engine.start_flow(qq, "lane_tale")
    assert state is not None
    assert qq in flow_engine.flow_states  # 中途态在内存
    asyncio.run(flow_engine.render_flow_node(qq, state, ConsoleBot(), send))
    # 推进到结束节点
    asyncio.run(flow_engine._flow_handle_choice(qq, state, "当作没看见，转身离开", ConsoleBot(), send))

    reloaded = _reload(qq)
    assert reloaded.get_flag("flow.lane_tale.done") == 1
    assert qq not in flow_engine.flow_states  # 结束后清理内存
    _cleanup(qq)


# --- C4：boss 待挑战旗标持久化 ---
def test_boss_pending_flag_written_and_restored_after_restart():
    """挑战按钮 → `boss.pending` 写入；重启模拟（内存清空）boss_resolve_forced 从 flags 恢复。"""
    import asyncio

    from src.services.boss_framework import boss_pending, boss_resolve_forced, handle_boss_button

    qq = "sf_boss_pending"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()

    async def fake_run(uid, bot, send, finish):
        pass

    with patch("src.plugins.adventure._run_adventure", fake_run):
        asyncio.run(handle_boss_button("jk", qq, "挑战", ConsoleBot(), ""))

    assert boss_pending.get(qq) == "jk"
    reloaded = _reload(qq)
    assert reloaded.get_flag("boss.pending") == "jk"

    # 重启模拟：内存清空 → 从 flags 恢复
    boss_pending.pop(qq, None)
    restored = boss_resolve_forced(qq, reloaded)
    assert restored == "jk"

    # 消费后 flags 清理（防重复消费）
    assert reloaded.get_flag("boss.pending") is None
    assert qq not in boss_pending
    _cleanup(qq)


def test_boss_pending_flag_cleared_on_day40_guard():
    """day40 守卫：flag 同样被清理（残留挑战旗标不覆盖守门人 36）。"""
    from src.services.boss_framework import boss_pending, boss_resolve_forced

    qq = "sf_boss_day40"
    inv = _make_inv(qq)
    inv.day = 40
    inv.save()
    inv.set_flag("boss.pending", "jk")
    inv.save()
    boss_pending[qq] = "jk"

    assert boss_resolve_forced(qq, inv) is None  # day40 归守门人
    reloaded = _reload(qq)
    assert reloaded.get_flag("boss.pending") is None  # flag 已清理
    assert qq not in boss_pending
    _cleanup(qq)


# --- C5：clear_flag 方法 ---
def test_clear_flag_method():
    qq = "sf_clear_flag"
    inv = _make_inv(qq)
    inv.set_flag("foo.bar", 1)
    inv.save()
    assert _reload(qq).get_flag("foo.bar") == 1
    inv.clear_flag("foo.bar")
    inv.save()
    assert _reload(qq).get_flag("foo.bar") is None
    # 删除不存在的键静默
    inv.clear_flag("no.such.key")
    _cleanup(qq)
