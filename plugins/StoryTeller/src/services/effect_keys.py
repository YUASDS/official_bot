"""标准效果键常量：event_service.apply_event_effects 统一收口，消费方过滤白名单共用。"""

from typing import Any

STANDARD_EFFECT_KEYS: frozenset[str] = frozenset(
    {"san", "hp", "金币", "物品", "技能", "随机属性", "梦之碎片", "助力"}
)

RANDOM_ATTR_POOL: list[str] = ["力量", "体质", "敏捷", "体型", "外貌"]


def standard_effects(effects: dict[str, Any]) -> dict[str, Any]:
    """过滤出标准效果键子集（白名单）。"""
    return {k: v for k, v in effects.items() if k in STANDARD_EFFECT_KEYS}


def gold_price(effects: dict[str, Any]) -> int:
    """负金币效果视为购买价（返回正值）；非负返回 0。"""
    return -int(effects["金币"]) if effects.get("金币", 0) < 0 else 0