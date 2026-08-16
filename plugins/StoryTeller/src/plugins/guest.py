"""乱入遭遇系统薄壳（guest 迁移 flow_engine·批次2）。

对齐 `.qa/plans/guest-migration-design.md` §4 收编范式（同 jk/qiren 收编）：

- **保留（触发调度侧）**：`guest_should_trigger` / `guest_enter` 公开签名不变
  （adventure.py 每日互斥链 `kind=="guest"` 分支零改动）——触发门（day_min/day_max）、
  确定性每日骰（(qq, 周目, day) 播种 d100 ≤ value）、met_day 内存+flags 双守卫、
  三世界解锁过滤（flow.入口条件 = 原 guest 世界「解锁」）。
- **委托（状态机侧）**：三世界内容已迁入 flow_data.json（flow_id = world_id），
  阶段/检定/战斗/纪念品/归途全部由 flow_engine 接管，guest 自身状态机全部删除。
- **按钮/命令**：按钮 kind 切换为 flow_stage/flow_battle；`/行动` 兜底由 flow_action_cmd
  接管（flow.py），本薄壳不再注册 guest 专属命令/按钮。
- **兼容导出**：`guest_active`（= flow_states）、`_guest_handle_input`（= handle_flow_input）、
  `guest_registry`（= guest 模式 flow 注册表）供既有调用方/测试读取。
"""

from __future__ import annotations

import random
from typing import Optional

from nonebot.adapters import Bot
from nonebot.exception import FinishedException

from ..models.player import Investigator, ending_repo
from ..services.daily_service import _mark_adventure_done
from ..services.data_loader import data_loader
from ..services.flow_engine import (
    flow_states,
    handle_flow_input,
    render_flow_node,
    resolve_flow_ref,
    start_flow,
)
from ..utils.buttons import _send_to_user
from ..utils.md_format import md_message

# --- 兼容导出（薄壳）：活跃流程态 = flow_engine 状态机 ---
guest_active = flow_states
_guest_met_day: dict[str, int] = {}


def _guest_trigger() -> dict:
    return data_loader.get_trigger("guest")


def guest_registry() -> dict:
    """guest 世界注册表（= flow_data.json 中开启 guest 模式收尾的 flow；flow_id = world_id）。"""
    from ..services.flow_engine import flow_registry

    return {
        fid: f
        for fid, f in flow_registry().items()
        if (f.get("收尾") or {}).get("skip_daily")
    }


def guest_unlocked(inv: Investigator) -> bool:
    """触发条件门：day_min ≤ day ≤ day_max（缺省 5/39，读 triggers 配置）。"""
    t = _guest_trigger()
    day_min = int(t.get("day_min", 5))
    day_max = int(t.get("day_max", 39))
    return day_min <= inv.day <= day_max


def _guest_daily_roll(inv: Investigator) -> int:
    """确定性每日骰：(qq, 周目, day) 播种返回 1~100（防连点重掷/测试回归）。"""
    collection = ending_repo.get_collection(inv.qq)
    run = int(collection.total_runs) if collection else 0
    rng = random.Random(f"guest|{inv.qq}|{run}|{inv.day}")
    return rng.randint(1, 100)


def _guest_met_flag(inv: Investigator) -> Optional[int]:
    """读当日守卫持久化镜像：flags `guest.met_day`（重启后仍生效）。"""
    try:
        return int(inv.get_flag("guest.met_day"))
    except (TypeError, ValueError):
        return None


def _world_available(inv: Investigator, flow: dict, flow_id: str) -> bool:
    """世界解锁条件求值（复用事件条件求值器；flow.入口条件 = 原 guest 世界「解锁」）。

    已触发过（进入过或通关过）的世界永久排除——战败后也不再相遇（用户需求）。
    """
    if inv.get_flag(f"flow.{flow_id}.visited") or inv.get_flag(f"flow.{flow_id}.done"):
        return False
    from ..services.ending_engine import eval_option_condition

    cond = flow.get("入口条件")
    if not cond:
        return True
    return eval_option_condition(cond, inv)


def guest_should_trigger(inv: Investigator) -> Optional[str]:
    """触发：解锁门 + 每日骰 ≤ value（缺省 3）+ met_day 守卫 → 随机一个已解锁世界 id。"""
    if not guest_unlocked(inv):
        return None
    if _guest_met_day.get(inv.qq) == inv.day or _guest_met_flag(inv) == inv.day:
        return None
    if flow_states.get(inv.qq):
        return None
    roll = _guest_daily_roll(inv)
    # 候选世界：已解锁 + 未触发过（visited/done 排除）
    worlds = [
        fid
        for fid, f in guest_registry().items()
        if _world_available(inv, f, fid)
    ]
    if not worlds:
        return None
    # 季级触发概率优先（季文件「触发概率」字段），缺省回退全局 guest value
    for fid in worlds:
        flow = guest_registry()[fid]
        prob = (flow.get("触发概率") or {}).get("value")
        value = int(prob) if prob is not None else int(_guest_trigger().get("value", 3))
        if roll <= value:
            return fid
    return None


async def guest_enter(user_id: str, inv: Investigator, bot: Bot, send, world_id: str) -> None:
    """进入乱入小剧场（签名不变）：占当日 → met_day 守卫 → start_flow + 渲染首节点。

    触发即替代当日冒险；归途 day+1 由 flow 收尾.skip_daily 处理。抛 FinishedException 中断今日流程。
    """
    if flow_states.get(user_id):
        await send(
            md_message(f"\n{data_loader.get_text('guest.no_active')}", bot, mention=user_id)
        )
        raise FinishedException()
    _mark_adventure_done(user_id)
    _guest_met_day[user_id] = inv.day
    inv.set_flag("guest.met_day", inv.day)  # 当日守卫持久化：重启后同 game-day 不二次触发
    flow_id = resolve_flow_ref(world_id)
    if flow_id is None:
        return
    state = start_flow(user_id, flow_id)
    if state is None:
        return
    inv.set_flag(f"flow.{flow_id}.visited", True)
    inv.save()
    await send(md_message(f"\n**{data_loader.get_text('guest.enter_title')}**", bot, mention=user_id))
    await render_flow_node(user_id, state, bot, send)
    raise FinishedException()


# --- 兼容导出（薄壳）：统一输入分发委托 flow_engine ---
_guest_handle_input = handle_flow_input

async def _guest_button_handler(
    user_id: str,
    payload: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """兼容：旧 guest 按钮回调委托 flow_engine 分发（生产由 flow_stage/flow_battle 接管）。"""

    async def _send(msg) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)

    try:
        await handle_flow_input(user_id, payload, bot, _send, token)
    except FinishedException:
        pass
