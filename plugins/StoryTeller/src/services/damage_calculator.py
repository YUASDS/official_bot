from __future__ import annotations

from .dice_roller import SuccessLevel, roll_dice


def calculate_damage(
    damage: str,
    success_level: int = SuccessLevel.SUCCESS,
    is_extreme_double: bool = False,
    armor: int = 0,
) -> tuple[str, int]:
    is_critical = success_level > SuccessLevel.HARD_SUCCESS

    if is_critical and is_extreme_double:
        expr, val = _double_damage(damage)
    elif is_critical:
        expr, val = roll_dice(damage, use_max=True)
    else:
        expr, val = roll_dice(damage)

    # 装甲正常减伤（不参与 ex 交互；无「保底 1 伤」穿透逻辑）
    if armor > 0:
        val = max(0, val - armor)

    if "d" in damage:
        return f"{damage}={expr}", val
    return f"{expr}", val


def _double_damage(damage: str) -> tuple[str, int]:
    max_expr, max_val = roll_dice(damage, use_max=True)
    rand_expr, rand_val = roll_dice(damage)
    return f"{max_expr}+{rand_expr}", max_val + rand_val
