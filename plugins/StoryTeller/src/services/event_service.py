"""奇遇事件核心逻辑：事件选择、选项解析（检定式/商品式）、效果应用、文案拼装。"""

import random
from typing import Optional

from database.db import add_gold

from ..models.player import Investigator
from .data_loader import data_loader
from .dice_roller import get_success_icon, roll_dice

# 奇遇随机出现概率
_EVENT_CHANCE = 0.4

# 随机事件去重（D5）：近 _RECENT_RECORD_DAYS 天已触发事件不重复（user_id -> 事件 key 队列）
_RECENT_RECORD_DAYS = 3
_recent_events: dict[str, list[str]] = {}

# State for active random events (user_id -> event context)
event_states: dict[str, dict] = {}


def delta_value(v) -> int:
    """数值或骰子表达式 → 实际值。"""
    if isinstance(v, str):
        return roll_dice(v)[1]
    return v


def _option_condition_ok(
    option: dict, inv: Optional[Investigator], progress=None
) -> bool:
    """选项「条件」字段求值（3.3）：无条件或条件满足返回 True；无 inv 时不拦截。"""
    cond = option.get("条件")
    if not cond or inv is None:
        return True
    from ..services.ending_engine import eval_option_condition

    return eval_option_condition(cond, inv, progress)


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
        # 信物获得登记 items_first（跨周目图鉴累计）
        from ..services.ending_engine import register_relic_obtained

        register_relic_obtained(inv, effects["物品"])
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
    """处理事件选项：条件硬门 → 乌帕校验 → 检定 → 效果 → 回复。返回 (消息, 是否跳过战斗)。"""
    from ..models.player import ending_repo

    # 执行层硬门（3.3）：命令直输绕过按钮时再次求值，不满足直接拦截
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    if not _option_condition_ok(matched, inv, progress):
        return (
            data_loader.get_text(
                "adventure.event_locked", default="条件未满足"
            )
            + "，无法选择此选项。",
            False,
        )
    effects, reply, passed = resolve_check_option(inv, matched)
    no_gold = event_buy_check(user_id, effects)
    if no_gold:
        return no_gold, False
    summary = apply_event_effects(inv, user_id, effects)
    reply = event_reply_with_effects(reply, summary)
    skip = bool(matched.get("跳过战斗")) and (passed is None or passed)
    return reply, skip


def event_condition_ok(
    event_data: dict, inv: Investigator, progress=None
) -> bool:
    """事件条件过滤：san_low + 全选项条件未满足时事件整体出随机池（3.3）。"""
    cond = event_data.get("条件") or {}
    if "san_low" in cond and inv.get_skill("san", 0) >= cond["san_low"]:
        return False
    options = event_data.get("选项") or []
    if options and all(not _option_condition_ok(o, inv, progress) for o in options):
        return False
    return True


def pick_random_event(inv: Investigator) -> Optional[dict]:
    """选择今日事件：固定日期事件（触发.day）当天必触发；否则 40% 随机。

    随机池排除固定日期事件、不满足条件的事件，以及近 3 天已触发过的事件（去重，
    防重复刷取/降低重复感，见 D5）。候选池全部被去重时回退允许重复，避免当日无事件。
    """
    from ..models.player import ending_repo

    for ev in data_loader.event_data.values():
        trigger = ev.get("触发") or {}
        if trigger.get("day") == inv.day:
            return ev
    if random.random() >= _EVENT_CHANCE or not data_loader.event_data:
        return None
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    recent = _recent_events.get(inv.qq) or []
    pool = [
        key
        for key, ev in data_loader.event_data.items()
        if "触发" not in ev
        and event_condition_ok(ev, inv, progress)
        and key not in recent
    ]
    if not pool:
        # 去重后候选池为空：回退全部可触发事件（随机池过小，允许当日重复）
        pool = [
            key
            for key, ev in data_loader.event_data.items()
            if "触发" not in ev and event_condition_ok(ev, inv, progress)
        ]
    if not pool:
        return None
    key = random.choice(pool)
    # 记录本次触发，并裁剪至最近 N 天（队列长度上限 _RECENT_RECORD_DAYS）
    queue = _recent_events.setdefault(inv.qq, [])
    queue.append(key)
    del queue[:-_RECENT_RECORD_DAYS]
    return data_loader.event_data[key]


def event_option_locked(
    option: dict, inv: Optional[Investigator], progress=None
) -> bool:
    """选项是否条件未满足（展示层置灰标记）。"""
    return not _option_condition_ok(option, inv, progress)


def event_option_label(
    option: dict,
    gold: int,
    inv: Optional[Investigator] = None,
    progress=None,
) -> str:
    """选项展示文本：商品选项显示价格；条件未满足追加「（条件未满足）」。"""
    label = option["输入"]
    effects = option.get("效果") or {}
    price = -int(effects["金币"]) if effects.get("金币", 0) < 0 else 0
    if price > 0 and effects.get("物品"):
        if gold >= price:
            return f"{label}（{price} 乌帕）"
        return f"{label}（{data_loader.get_text('adventure.event_no_gold')}）"
    if event_option_locked(option, inv, progress):
        locked = data_loader.get_text("adventure.event_locked", default="条件未满足")
        return f"{label}（{locked}）"
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
    labels: list[str],
    options: list[dict],
    locks: Optional[list[bool]] = None,
) -> list[list[tuple[str, str]]]:
    """事件选项按钮行：条件未满足的选项锁定（禁点回调 event_locked），每行 3 个。"""
    locks = locks or []
    return [
        [
            (
                (labels[i], f"event_locked:{opt['输入']}")
                if i < len(locks) and locks[i]
                else (labels[i], f"event:{opt['输入']}")
            )
            for i, opt in enumerate(options[j : j + 3], j)
        ]
        for j in range(0, len(options), 3)
    ]
