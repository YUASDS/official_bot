from loguru import logger
import random

from .data_loader import data_loader

# 骰子判定阈值缺省值（display_data.json `dice` 段缺失/非法时回退，与现状硬编码一致）。
_DICE_DEFAULT_THRESHOLDS = {
    "fumble_above": 95,
    "crit_below": 6,
    "hard_ratio": 0.5,
    "extreme_ratio": 0.2,
}


def _dice_thresholds() -> dict:
    """骰子判定阈值（display_data.json `dice` 段）；缺失/类型非法回退现状默认值。

    函数内读取：dice_roller 是底层模块，避免反向依赖 battle_cards（battle_cards 依赖
    battle → dice_roller 会成环）；display_data 由 data_loader 统一加载，dice_roller
    本就依赖 data_loader，零新增依赖。
    """
    cfg = getattr(data_loader, "display_data", {}) or {}
    cfg = cfg.get("dice") if isinstance(cfg, dict) else None
    if not isinstance(cfg, dict):
        return dict(_DICE_DEFAULT_THRESHOLDS)
    try:
        return {
            "fumble_above": int(
                cfg.get("fumble_above", _DICE_DEFAULT_THRESHOLDS["fumble_above"])
            ),
            "crit_below": int(
                cfg.get("crit_below", _DICE_DEFAULT_THRESHOLDS["crit_below"])
            ),
            "hard_ratio": float(
                cfg.get("hard_ratio", _DICE_DEFAULT_THRESHOLDS["hard_ratio"])
            ),
            "extreme_ratio": float(
                cfg.get("extreme_ratio", _DICE_DEFAULT_THRESHOLDS["extreme_ratio"])
            ),
        }
    except (TypeError, ValueError) as e:
        logger.warning(f"静默异常[TypeError/ValueError] in _dice_thresholds: {e}")
        return dict(_DICE_DEFAULT_THRESHOLDS)


class SuccessLevel:
    """Success Level Constants"""
    CRITICAL_FAILURE = -1
    FAILURE = 0
    SUCCESS = 1
    HARD_SUCCESS = 2
    EXTREME_SUCCESS = 3
    CRITICAL_SUCCESS = 4

def get_success_description(rank: int) -> str:
    """Get success level description"""
    t = data_loader.get_text
    success_descriptions = {
        SuccessLevel.CRITICAL_FAILURE: t("dice.critical_failure"),
        SuccessLevel.FAILURE: t("dice.failure"),
        SuccessLevel.SUCCESS: t("dice.success"),
        SuccessLevel.HARD_SUCCESS: t("dice.hard_success"),
        SuccessLevel.EXTREME_SUCCESS: t("dice.extreme_success"),
        SuccessLevel.CRITICAL_SUCCESS: t("dice.critical_success"),
    }
    return success_descriptions.get(rank, t("dice.unknown"))

def get_success_icon(rank: int) -> str:
    """Get success level icon (report.icon_*)."""
    t = data_loader.get_text
    icons = {
        SuccessLevel.CRITICAL_FAILURE: t("report.icon_critical_failure"),
        SuccessLevel.FAILURE: t("report.icon_failure"),
        SuccessLevel.SUCCESS: t("report.icon_success"),
        SuccessLevel.HARD_SUCCESS: t("report.icon_hard_success"),
        SuccessLevel.EXTREME_SUCCESS: t("report.icon_extreme_success"),
        SuccessLevel.CRITICAL_SUCCESS: t("report.icon_critical_success"),
    }
    return icons.get(rank, t("report.icon_failure"))

class DiceRoll:
    """Base Dice Roll Class"""
    def __init__(self, skill: int) -> None:
        self.skill = skill
        self.dice = self._roll_d100()
        self.level = self._calculate_success_level(skill, self.dice)

    def _roll_d100(self) -> int:
        return random.randint(1, 100)

    def _calculate_success_level(self, skill: int, roll: int) -> int:
        th = _dice_thresholds()
        if roll > th["fumble_above"]:
            return SuccessLevel.CRITICAL_FAILURE
        if roll < th["crit_below"]:
            return SuccessLevel.CRITICAL_SUCCESS
        if skill <= 0:
            return SuccessLevel.FAILURE

        success_ratio = roll / skill
        if success_ratio > 1:
            return SuccessLevel.FAILURE
        if success_ratio > th["hard_ratio"]:
            return SuccessLevel.SUCCESS
        if success_ratio > th["extreme_ratio"]:
            return SuccessLevel.HARD_SUCCESS
        return SuccessLevel.EXTREME_SUCCESS

class BonusDiceRoll(DiceRoll):
    """Bonus Dice Roll"""
    def __init__(self, skill: int, bonus_dice: int) -> None:
        self.bonus_dice_count = bonus_dice
        super().__init__(skill)
        self._apply_bonus_dice()
        self.level = self._calculate_success_level(skill, self.final_result)

    def _apply_bonus_dice(self):
        self.bonus_rolls = []
        self.final_result = self.dice
        for _ in range(self.bonus_dice_count):
            bonus_val = random.randint(0, 9)
            self.bonus_rolls.append(bonus_val)
            bonus_result = self.dice
            if self.dice // 10 > bonus_val:
                bonus_result = self.dice % 10 + bonus_val * 10
                if bonus_result == 0:
                    bonus_result = 100
            self.final_result = min(bonus_result, self.final_result)

class PenaltyDiceRoll(DiceRoll):
    """Penalty Dice Roll"""
    def __init__(self, skill: int, penalty_dice: int = 1) -> None:
        self.penalty_dice_count = penalty_dice
        super().__init__(skill)
        self._apply_penalty_dice()
        self.level = self._calculate_success_level(skill, self.final_result)

    def _apply_penalty_dice(self):
        self.penalty_rolls = []
        self.final_result = self.dice

        for _ in range(self.penalty_dice_count):
            penalty_val = random.randint(0, 9)
            self.penalty_rolls.append(penalty_val)
            penalty_result = self.dice
            if self.dice // 10 < penalty_val:
                penalty_result = self.dice % 10 + penalty_val * 10
            self.final_result = max(penalty_result, self.final_result)
            if self.final_result == 0:
                self.final_result = 100

class ConfrontationRoll(DiceRoll):
    """Opposed Roll"""
    def __init__(self, skill1: int, skill2: int) -> None:
        self.skill1 = skill1
        self.skill2 = skill2
        super().__init__(skill1)
        self.dice1 = self.dice
        self.level1 = self.level

        # Reroll for the second skill (conceptually, distinct roll)
        self.dice2 = self._roll_d100()
        self.level2 = self._calculate_success_level(skill2, self.dice2)

    def get_result(self, action_type: str = "") -> bool:
        if action_type == "闪避":
            return self.level1 > self.level2
        if action_type == "反击":
            if self.level1 <= SuccessLevel.FAILURE:
                return False
            return self.level1 >= self.level2
        if self.level1 != self.level2:
            return self.level1 > self.level2
        return self.skill1 >= self.skill2

def calculate_damage_bonus(size: int, strength: int) -> str:
    """
    Calculate Extra Damage Bonus (DB) based on Size (SIZ) and Strength (STR).
    """
    total = size + strength
    if total < 65:
        return "-2"
    if total < 85:
        return "-1"
    if total < 125:
        return "0"
    if total < 165:
        return "1d4"
    if total < 205:
        return "1d6"

    additional_dice = (total - 205) // 80 + 2
    return f"{additional_dice}d6"

def roll_dice(dice_expression: str, use_max: bool = False) -> tuple[str, int]:
    """
    Roll dice based on expression like '1d10', '2d6+3', '1d100', '1d8+2d6+3'.
    Returns (expression_result_str, total_value).
    """

    def _roll_single(part: str) -> tuple[int, str]:
        part = part.strip()
        if "d" in part:
            if part.startswith("d"):
                count, sides = 1, int(part[1:])
            else:
                count_str, sides_str = part.split("d")
                count = int(count_str) if count_str else 1
                sides = int(sides_str)
            if use_max:
                rolls = [sides] * count
                return count * sides, "+".join(map(str, rolls))
            rolls = [random.randint(1, sides) for _ in range(count)]
            return sum(rolls), "+".join(map(str, rolls))
        return int(part), part

    expression = dice_expression.lower().replace(" ", "")

    if expression.replace("+", "").replace("-", "").isdigit():
        return expression, int(expression)

    # Parse compound expressions: split on +/-
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
        val, detail = _roll_single(parts[0])
        return detail, val

    total = 0
    details = []
    op = "+"
    for part in parts:
        if part in "+-":
            op = part
        else:
            val, detail = _roll_single(part)
            if op == "+":
                total += val
                details.append(f"+{detail}")
            else:
                total -= val
                details.append(f"-{detail}")

    if details and details[0].startswith("+"):
        details[0] = details[0][1:]

    return "".join(details), total
