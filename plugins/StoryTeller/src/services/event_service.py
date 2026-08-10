"""奇遇事件核心逻辑：事件选择、选项解析（检定式/商品式）、效果应用、文案拼装。"""

import random
from typing import Optional

from database.db import add_gold

from ..models.player import Investigator
from .data_loader import data_loader
from .dice_roller import get_success_icon, roll_dice

# 奇遇随机出现概率
_EVENT_CHANCE = 0.4

# State for active random events (user_id -> event context)
event_states: dict[str, dict] = {}


def delta_value(v) -> int:
    """数值或骰子表达式 → 实际值。"""
    if isinstance(v, str):
        return roll_dice(v)[1]
    return v


def apply_event_effects(inv: Investigator, user_id: str, effects: dict) -> str:
    """应用奇遇事件效果并返回变更摘要（如「🧠 SAN +10 ｜ 💪 意志 +5」）。

    SAN 仅封底于 0（可超过意志上限）；HP 不超过最大生命值；乌帕不少于 0；
    数值效果支持骰子表达式（如 "2d6+4"）；摘要显示实际变化量。
    """
    from database.db import get_info

    from ..models.item import Equipment as _Equipment

    changes: list[str] = []
    if "san" in effects:
        delta = delta_value(effects["san"])
        cur = inv.get_skill("san", 0)
        actual = max(0, cur + delta) - cur
        inv.set_skill("san", cur + actual)
        if actual:
            changes.append(f"🧠 SAN {actual:+d}")
    if "hp" in effects:
        delta = delta_value(effects["hp"])
        max_hp = inv.get_max_hp()
        new_hp = min(max_hp, max(1, inv.hp + delta))
        actual = new_hp - inv.hp
        inv.hp = new_hp
        if actual:
            changes.append(f"❤️ HP {actual:+d}")
    if "金币" in effects:
        delta = delta_value(effects["金币"])
        cur = get_info(user_id).gold
        actual = max(0, cur + delta) - cur
        add_gold(user_id, actual)
        if actual:
            changes.append(f"🪙 金币 {actual:+d}")
    if "物品" in effects:
        item = _Equipment(effects["物品"])
        inv.add_item_to_inventory(effects["物品"], 1)
        changes.append(f"🎒 获得 {item.name}")
    if "技能" in effects:
        for sk_name, sk_delta in effects["技能"].items():
            sk_val = inv.get_skill(sk_name, 0) + sk_delta
            inv.set_skill(sk_name, max(0, sk_val))
            changes.append(f"💪 {sk_name} {sk_delta:+d}")
    inv.save()
    return " ｜ ".join(changes)


def event_buy_check(user_id: str, effects: dict) -> str:
    """商品选项（乌帕换物品）余额校验：不足返回提示文本，否则返回空串。"""
    price = -int(effects["金币"]) if effects.get("金币", 0) < 0 else 0
    if price > 0 and effects.get("物品"):
        from database.db import get_info

        if get_info(user_id).gold < price:
            return data_loader.get_text("adventure.event_no_gold")
    return ""


def resolve_check_option(
    inv: Investigator, option: dict
) -> tuple[dict, str, bool | None]:
    """检定式选项：掷 1d100 vs 指定技能。

    返回 (生效效果, 回复文本, 是否通过)；无检定时通过为 None。
    """
    check = option.get("检定")
    if not check:
        return option.get("效果", {}), option.get("回复", ""), None
    skill = check.get("技能", "意志")
    skill_val = inv.get_skill(skill, 0)
    _expr, roll = roll_dice("1d100")
    passed = roll <= skill_val
    effects = check.get("奖励", {}) if passed else check.get("失败", {})
    reply = option.get("成功回复") if passed else option.get("失败回复")
    if reply is None:
        reply = option.get("回复", "")
    t = data_loader.get_text
    icon = get_success_icon(1) if passed else get_success_icon(0)
    level = t("dice.success") if passed else t("dice.failure")
    check_desc = t(
        "adventure.check_desc",
        icon=icon,
        skill=skill,
        dice=roll,
        target=skill_val,
        level=level,
    )
    return effects, f"{reply}\n\n> {check_desc}", passed


def apply_event_choice(
    inv: Investigator, user_id: str, matched: dict
) -> tuple[str, bool]:
    """处理事件选项：乌帕校验 → 检定 → 效果 → 回复。返回 (消息, 是否跳过战斗)。"""
    effects, reply, passed = resolve_check_option(inv, matched)
    no_gold = event_buy_check(user_id, effects)
    if no_gold:
        return no_gold, False
    summary = apply_event_effects(inv, user_id, effects)
    reply = event_reply_with_effects(reply, summary)
    skip = bool(matched.get("跳过战斗")) and (passed is None or passed)
    return reply, skip


def event_condition_ok(event_data: dict, inv: Investigator) -> bool:
    """事件条件过滤：san_low（仅低 SAN 时进入随机池）。"""
    cond = event_data.get("条件") or {}
    return not (
        "san_low" in cond and inv.get_skill("san", 0) >= cond["san_low"]
    )


def pick_random_event(inv: Investigator) -> Optional[dict]:
    """选择今日事件：固定日期事件（触发.day）当天必触发；否则 40% 随机。

    随机池排除固定日期事件与不满足条件的事件。
    """
    for ev in data_loader.event_data.values():
        trigger = ev.get("触发") or {}
        if trigger.get("day") == inv.day:
            return ev
    if random.random() >= _EVENT_CHANCE or not data_loader.event_data:
        return None
    pool = [
        ev
        for ev in data_loader.event_data.values()
        if "触发" not in ev and event_condition_ok(ev, inv)
    ]
    return random.choice(pool) if pool else None


def event_option_label(option: dict, gold: int) -> str:
    """选项展示文本：商品选项显示价格，乌帕不足显示「乌帕不足」。"""
    label = option["输入"]
    effects = option.get("效果") or {}
    price = -int(effects["金币"]) if effects.get("金币", 0) < 0 else 0
    if price > 0 and effects.get("物品"):
        if gold >= price:
            return f"{label}（{price} 乌帕）"
        return f"{label}（{data_loader.get_text('adventure.event_no_gold')}）"
    return label


def event_reply_with_effects(event_reply: str, summary: str) -> str:
    """事件回复 + 效果摘要小节。"""
    t = data_loader.get_text
    if not summary:
        return event_reply
    return (
        f"{event_reply}\n\n" f"**{t('adventure.event_effect_title')}**\n" f"> {summary}"
    )


def event_option_rows(
    labels: list[str], options: list[dict]
) -> list[list[tuple[str, str]]]:
    """事件选项按钮行：每行 3 个，超出自动换行。"""
    return [
        [
            (labels[i], f"event:{opt['输入']}")
            for i, opt in enumerate(options[j : j + 3], j)
        ]
        for j in range(0, len(options), 3)
    ]
