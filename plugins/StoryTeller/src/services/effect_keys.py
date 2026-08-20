"""标准效果键常量：event_service.apply_event_effects 统一收口，消费方过滤白名单共用。"""

from typing import Any

STANDARD_EFFECT_KEYS: frozenset[str] = frozenset(
    {"san", "hp", "金币", "物品", "技能"}
)


def standard_effects(effects: dict[str, Any]) -> dict[str, Any]:
    """过滤出标准效果键子集（白名单）。"""
    return {k: v for k, v in effects.items() if k in STANDARD_EFFECT_KEYS}