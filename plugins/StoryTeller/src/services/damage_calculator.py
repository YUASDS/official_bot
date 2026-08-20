from __future__ import annotations

from .dice_roller import SuccessLevel, roll_dice


def calculate_damage(
    damage: str,
    success_level: int = SuccessLevel.SUCCESS,
    is_extreme_double: bool = False,
    armor: int = 0,
    dmg_type: str = "physical",
    min_roll: bool = False,
) -> tuple[str, int]:
    if min_roll and is_extreme_double:
        # 骰子压制：仅贯穿武器（ex=1 → is_extreme_double）的骰子部分强制取最小值
        # （4d6→4、3d6+1→4），固定 +N 不受影响；即使暴击/极难成功也取最小。
        # 普通武器（无 ex）在 dice_suppress 下不被压制，走下方正常判定（暴击/困难/普攻）。
        expr, val = _roll_min(damage)
    else:
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


def _roll_min(damage: str) -> tuple[str, int]:
    """最小骰子压制结算：骰子项每骰取 1（4d6→1+1+1+1=4），固定 +N/-N 不受影响。

    返回 (表达式明细, 总值)，格式与 roll_dice 一致。
    """

    def _min_part(part: str) -> tuple[int, str]:
        part = part.strip()
        if "d" in part:
            if part.startswith("d"):
                count, sides = 1, int(part[1:])
            else:
                count_str, sides_str = part.split("d")
                count = int(count_str) if count_str else 1
                sides = int(sides_str)
            del sides
            rolls = ["1"] * count
            return count, "+".join(rolls)
        return int(part), part

    expression = damage.lower().replace(" ", "")
    if "d" not in expression:
        return expression, int(expression)

    parts = []
    current = ""
    for ch in expression:
        if ch in "+-" and current:
            parts.append(current)
            parts.append(ch)
            current = ""
        else:
            current += ch
    if current:
        parts.append(current)

    if len(parts) == 1:
        val, detail = _min_part(parts[0])
        return detail, val

    total = 0
    details = []
    op = "+"
    for part in parts:
        if part in "+-":
            op = part
        else:
            val, detail = _min_part(part)
            if op == "+":
                total += val
                details.append(f"+{detail}")
            else:
                total -= val
                details.append(f"-{detail}")

    if details and details[0].startswith("+"):
        details[0] = details[0][1:]

    return "".join(details), total
