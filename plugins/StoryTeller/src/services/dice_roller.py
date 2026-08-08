import random


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
    success_descriptions = {
        SuccessLevel.CRITICAL_FAILURE: "大失败",
        SuccessLevel.FAILURE: "失败",
        SuccessLevel.SUCCESS: "成功",
        SuccessLevel.HARD_SUCCESS: "困难成功",
        SuccessLevel.EXTREME_SUCCESS: "极难成功",
        SuccessLevel.CRITICAL_SUCCESS: "大成功",
    }
    return success_descriptions.get(rank, "未知")

class DiceRoll:
    """Base Dice Roll Class"""
    def __init__(self, skill: int) -> None:
        self.skill = skill
        self.dice = self._roll_d100()
        self.level = self._calculate_success_level(skill, self.dice)

    def _roll_d100(self) -> int:
        return random.randint(1, 100)

    def _calculate_success_level(self, skill: int, roll: int) -> int:
        if roll > 95:
            return SuccessLevel.CRITICAL_FAILURE
        if roll < 6:
            return SuccessLevel.CRITICAL_SUCCESS

        success_ratio = roll / skill
        if success_ratio > 1:
            return SuccessLevel.FAILURE
        if success_ratio > 0.5:
            return SuccessLevel.SUCCESS
        if success_ratio > 0.2:
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
