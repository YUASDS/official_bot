"""NPC / 伙伴系统：配置驱动的对话树 + 好感度 + 伙伴助战。

每日冒险开局（qiren 彩蛋之后）对已解锁 NPC 掷配置骰触发对话：
- 按钮（kind "npc"，payload = "npc_id|选项输入"）或 `/行动 <选项输入>` 命令完成选择；
- 选项效果走 `apply_event_effects`（san/hp/金币/物品/技能）+ 好感度（存 flags `npc.<id>.好感度`）
  + 可选 `flag` 键直接置位（伙伴解锁）；
- 好感度递增到「好感度上限」且 NPC 配置 `助战` 时自动置 `助战.解锁` 旗标 → 成为伙伴，
  战斗中由 `inject_companion` 注入 `BattleService.companion`（泛化 506 骨哨助战）。

qiren 一期保持独立，本框架平行新增，不触碰 38 号隐藏挑战。
"""

from __future__ import annotations

import copy
from typing import Any, Optional

from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.exception import FinishedException

from ..models.player import Investigator, investigator_repo
from ..services.data_loader import data_loader
from ..services.dice_roller import roll_dice
from ..services.ending_engine import eval_option_condition
from ..services.event_service import apply_event_effects
from ..utils.buttons import _send_to_user, register_button_handler
from ..utils.md_format import build_keyboard, md_message, need_create_message
from ..utils.state_registry import register_state_store

# 未决对话状态：user_id -> {"npc_id": ..., "node_id": ...}（类比 event_states）
npc_states: dict[str, dict] = {}
register_state_store(npc_states)
# 每日互斥守卫：user_id -> 已触发 NPC 的当日 day（一天最多一次 NPC 彩蛋）
_npc_met_day: dict[str, int] = {}


def npc_registry() -> dict:
    """NPC 配置注册表（npc_data.json）。"""
    return data_loader.npc_data or {}


def npc_affinity(inv: Investigator, npc_id: str) -> int:
    """当前好感度（flags 存储，缺省 0）。"""
    value = inv.get_flag(f"npc.{npc_id}.好感度")
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def npc_available(inv: Investigator, npc: dict) -> bool:
    """NPC 解锁条件求值（复用事件条件求值器，支持 日/物品/SAN/克苏鲁神话 等）。"""
    cond = npc.get("解锁")
    if not cond:
        return True
    return eval_option_condition(cond, inv)


def npc_should_trigger(inv: Investigator) -> Optional[str]:
    """每日开局概率触发：对已解锁 NPC 按配置顺序掷骰，命中返回第一个 NPC id（无则 None）。

    当天已触发过（_npc_met_day 守卫）或 day40 冻结不再触发（解锁条件 max 已排除）。
    """
    if _npc_met_day.get(inv.qq) == inv.day:
        return None
    for npc_id, npc in npc_registry().items():
        if not npc_available(inv, npc):
            continue
        prob = npc.get("概率") or {}
        dice = prob.get("骰", "d20")
        trigger_val = int(prob.get("值", 1))
        _expr, val = roll_dice(dice)
        if val == trigger_val:
            return npc_id
    return None


def npc_current_node(npc: dict, affinity: int) -> dict:
    """当前对话节点：触发.好感度_min 满足的最高节点（好感度推进关系线）。"""
    nodes = npc.get("节点") or []
    if not nodes:
        return {}
    chosen = nodes[0]
    for node in nodes:
        trig = node.get("触发") or {}
        try:
            need = int(trig.get("好感度_min", 0))
        except (TypeError, ValueError):
            need = 0
        if need <= affinity:
            chosen = node
    return chosen


def _option_rows(npc: dict, node: dict) -> list[list[tuple[str, str]]]:
    """对话选项按钮行：每行 3 个（对齐事件选项），payload = "npc:<npc_id>|<选项输入>"。"""
    options = node.get("选项") or []
    return [
        [(o["输入"], f"npc:{npc['id']}|{o['输入']}") for o in options[i : i + 3]]
        for i in range(0, len(options), 3)
    ]


async def npc_send_dialogue(
    user_id: str, bot: Bot, send, npc_id: str
) -> None:
    """发送 NPC 出场 + 当前节点对话 + 选项按钮。"""
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        await send(need_create_message(bot, mention=user_id))
        return
    inv = Investigator(inv_model)
    npc = npc_registry().get(npc_id)
    if npc is None:
        return
    node = npc_current_node(npc, npc_affinity(inv, npc_id))
    kb = build_keyboard(_option_rows(npc, node))
    npc_states[user_id] = {"npc_id": npc_id, "node_id": node.get("id")}
    _npc_met_day[user_id] = inv.day
    msg = md_message(
        f"\n{npc.get('标题', '')}\n\n{npc.get('出场', '')}\n\n{node.get('对话', '')}",
        bot,
        mention=user_id,
    )
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


def _gold_check(user_id: str, effects: dict) -> bool:
    """选项金币为负（购买）时校验乌帕是否充足；不足返回 False。"""
    price = -int(effects["金币"]) if effects.get("金币", 0) < 0 else 0
    if price <= 0:
        return True
    from database.db import get_info

    return get_info(user_id).gold >= price


def apply_npc_choice(inv: Investigator, user_id: str, npc: dict, node: dict, option: dict) -> str:
    """应用对话选项：标准效果（san/hp/金币/物品/技能）+ 好感度 + 伙伴解锁。返回回复文本。"""
    t = data_loader.get_text
    effects = option.get("效果") or {}
    npc_id = npc["id"]
    max_aff = int(npc.get("好感度上限", 5))
    before = npc_affinity(inv, npc_id)

    standard = {k: v for k, v in effects.items() if k in ("san", "hp", "金币", "物品", "技能")}
    summary = apply_event_effects(inv, user_id, standard) if standard else ""

    delta = int(effects.get("好感度", 0) or 0)
    after = max(0, min(max_aff, before + delta))
    if after != before:
        inv.set_flag(f"npc.{npc_id}.好感度", after)

    ally_unlocked = False
    ally_name = ""
    if before < max_aff and after >= max_aff:
        # 好感度递增到上限：NPC 配置 `助战` 且 `解锁` 键 → 自动结为伙伴
        zz = npc.get("助战") or {}
        unlock_key = zz.get("解锁")
        if unlock_key and not inv.get_flag(unlock_key):
            inv.set_flag(unlock_key, True)
            ally_unlocked = True
            ally_name = zz.get("名字") or npc.get("名字", "")

    flag_key = effects.get("flag")
    if flag_key:
        inv.set_flag(flag_key, True)

    inv.save()

    reply = option.get("回复", "")
    summary_lines = []
    if summary:
        summary_lines.append(f"**{t('adventure.event_effect_title')}**\n> {summary}")
    if delta:
        summary_lines.append(
            f"> {t('npc.affinity_change', name=npc.get('名字', ''), delta=delta, cur=after, max=max_aff)}"
        )
    if ally_unlocked:
        summary_lines.append(
            t(
                "npc.ally_unlocked",
                name=npc.get("名字", ""),
                ally=ally_name,
            )
        )
    if summary_lines:
        reply = f"{reply}\n\n" + "\n".join(summary_lines)
    return reply


async def _handle_npc_choice(user_id: str, npc_id: str, option_input: str, bot: Bot, send) -> None:
    """按钮 / 命令共用的选择处理。"""
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        await send(need_create_message(bot, mention=user_id))
        return
    inv = Investigator(inv_model)
    npc = npc_registry().get(npc_id)
    if npc is None:
        await send(
            md_message(
                f"\n{data_loader.get_text('npc.unknown_npc')}",
                bot,
                mention=user_id,
            )
        )
        return
    node = npc_current_node(npc, npc_affinity(inv, npc_id))
    option = next(
        (o for o in node.get("选项") or [] if o.get("输入") == option_input),
        None,
    )
    if option is None:
        await send(
            md_message(
                f"\n{data_loader.get_text('npc.invalid_choice')}",
                bot,
                mention=user_id,
            )
        )
        return
    if not _gold_check(user_id, option.get("效果") or {}):
        await send(
            md_message(
                f"\n{data_loader.get_text('adventure.event_no_gold')}",
                bot,
                mention=user_id,
            )
        )
        return
    reply = apply_npc_choice(inv, user_id, npc, node, option)
    await send(md_message(f"\n{reply}", bot, mention=user_id))


async def handle_npc_button(
    user_id: str,
    payload: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """NPC 对话选项按钮回调：payload = "npc_id|选项输入"。

    以 npc_states 为已决守卫：对话发出时挂起状态，首次点击消费并应用，重复点击忽略
    （对齐事件按钮的 event_states 语义，防连点造成好感度/效果重复结算）。
    """
    async def _send(msg) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)

    state = npc_states.get(user_id)
    if not state or not payload or "|" not in payload:
        return  # 状态缺失（已处理/已过期）→ 忽略旧按钮
    npc_id, option_input = payload.split("|", 1)
    if state.get("npc_id") != npc_id:
        npc_states.pop(user_id, None)
        return
    npc_states.pop(user_id, None)
    await _handle_npc_choice(user_id, npc_id, option_input, bot, _send)


async def npc_handle_command(user_id: str, npc_id: str, choice: str, bot: Bot, send) -> None:
    """命令兜底：`/行动 <选项输入>` 完成 NPC 对话选择（对齐事件命令通道）。"""
    if not choice:
        await send(
            md_message(
                f"\n{data_loader.get_text('adventure.need_action')}",
                bot,
                mention=user_id,
            )
        )
        return
    await _handle_npc_choice(user_id, npc_id, choice, bot, send)


def npc_status(inv: Investigator) -> str:
    """`/伙伴` 状态文本：每个 NPC 的好感度 / 结伴状态。"""
    t = data_loader.get_text
    if not npc_registry():
        return t("npc.none")
    lines = [t("npc.status_title")]
    for npc_id, npc in npc_registry().items():
        name = npc.get("名字", npc_id)
        aff = npc_affinity(inv, npc_id)
        max_aff = int(npc.get("好感度上限", 5))
        zz = npc.get("助战") or {}
        unlock_key = zz.get("解锁")
        if unlock_key and inv.get_flag(unlock_key):
            status = t("npc.companion")
        elif aff <= 0:
            status = t("npc.unknown")
        else:
            status = t("npc.affinity_line", cur=aff, max=max_aff)
        lines.append(t("npc.status_row", name=name, status=status))
    return "\n".join(lines)


def inject_companion(service: Any, inv: Investigator) -> None:
    """伙伴助战注入：好感度满解锁的 NPC 伙伴 → BattleService.companion（副本，防状态污染）。

    多伙伴同时解锁时取注册表顺序第一个（单伙伴语义，对齐 506 骨哨单实例）。
    """
    if getattr(service, "companion", None) is not None:
        return
    for _npc_id, npc in npc_registry().items():
        zz = npc.get("助战") or {}
        unlock_key = zz.get("解锁")
        if unlock_key and inv.get_flag(unlock_key):
            service.companion = copy.deepcopy(zz)
            return


npc_cmd = on_command(
    "伙伴", aliases={"npc_status", "好感度"}, priority=10, block=True
)


@npc_cmd.handle()
async def handle_npc_cmd(event: Event, bot: Bot) -> None:
    """查看 NPC 好感度与结伴状态。"""
    user_id = event.get_user_id()
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        await npc_cmd.finish(need_create_message(bot, mention=user_id))
    inv = Investigator(inv_model)
    await npc_cmd.finish(md_message(f"\n{npc_status(inv)}", bot, mention=user_id))


register_button_handler("npc", handle_npc_button)
