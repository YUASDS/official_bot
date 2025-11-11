from typing import Union, Tuple, Dict, Any, Optional
import random


class SuccessLevel:
    """成功等级常量"""

    CRITICAL_FAILURE = -1
    FAILURE = 0
    SUCCESS = 1
    HARD_SUCCESS = 2
    EXTREME_SUCCESS = 3
    CRITICAL_SUCCESS = 4


class DiceRoll:
    """基础骰子类"""

    def __init__(self, skill: int):
        self.skill = skill
        self.dice = self._roll_d100()
        self.level = self._calculate_success_level(skill, self.dice)

    def _roll_d100(self) -> int:
        """掷d100骰子"""
        return random.randint(1, 100)

    def _calculate_success_level(self, skill: int, roll: int) -> int:
        """
        计算成功等级

        Args:
            skill: 技能值
            roll: 骰子出目

        Returns:
            成功等级
        """
        if roll > 95:
            return SuccessLevel.CRITICAL_FAILURE
        elif roll < 6:
            return SuccessLevel.CRITICAL_SUCCESS

        success_ratio = roll / skill
        if success_ratio > 1:
            return SuccessLevel.FAILURE
        elif success_ratio > 0.5:
            return SuccessLevel.SUCCESS
        elif success_ratio > 0.2:
            return SuccessLevel.HARD_SUCCESS
        else:
            return SuccessLevel.EXTREME_SUCCESS


class ConfrontationRoll(DiceRoll):
    """对抗骰子类"""

    def __init__(self, skill1: int, skill2: int):
        self.skill1 = skill1
        self.skill2 = skill2
        super().__init__(skill1)
        self.dice1 = self.dice
        self.level1 = self.level

        # 为第二个技能重新掷骰
        self.dice = self._roll_d100()
        self.level2 = self._calculate_success_level(skill2, self.dice)
        self.dice2 = self.dice

    def get_result(self, action_type: str = "") -> Optional[bool]:
        """获取对抗结果"""
        return self._check_confrontation(self.level1, self.level2, action_type)

    def _check_confrontation(
        self, level1: int, level2: int, action_type: str
    ) -> Optional[bool]:
        """
        检查对抗结果

        Args:
            level1: 第一个成功等级
            level2: 第二个成功等级
            action_type: 行动类型

        Returns:
            对抗结果
        """
        if action_type == "闪避":
            return level1 > level2
        elif action_type == "反击":
            if level1 <= SuccessLevel.FAILURE:
                return False
            return level1 >= level2
        elif level1 != level2:
            return level1 > level2
        else:
            # 等级相同时，技能高者胜
            return self.skill1 >= self.skill2


class BonusDiceRoll(DiceRoll):
    """奖励骰类"""

    def __init__(self, skill: int, bonus_dice: int):
        self.bonus_dice_count = bonus_dice
        super().__init__(skill)
        self._apply_bonus_dice()
        self.level = self._calculate_success_level(skill, self.final_result)

    def _apply_bonus_dice(self):
        """应用奖励骰"""
        self.bonus_rolls = []
        self.final_result = self.dice

        for _ in range(self.bonus_dice_count):
            bonus_roll, bonus_value = self._roll_bonus_dice()
            self.bonus_rolls.append(bonus_value)
            self.final_result = min(bonus_roll, self.final_result)

    def _roll_bonus_dice(self) -> Tuple[int, int]:
        """掷单个奖励骰"""
        bonus_value = random.randint(0, 9)
        result = self.dice

        if result // 10 > bonus_value:
            result = result % 10 + bonus_value * 10

        return result, bonus_value


class PenaltyDiceRoll(DiceRoll):
    """惩罚骰类"""

    def __init__(self, skill: int, penalty_dice: int = 1):
        self.penalty_dice_count = penalty_dice
        super().__init__(skill)
        self._apply_penalty_dice()
        self.level = self._calculate_success_level(skill, self.final_result)

    def _apply_penalty_dice(self):
        """应用惩罚骰"""
        self.penalty_rolls = []
        self.final_result = self.dice

        for _ in range(self.penalty_dice_count):
            penalty_roll, penalty_value = self._roll_penalty_dice()
            self.penalty_rolls.append(penalty_value)
            self.final_result = max(penalty_roll, self.final_result)

    def _roll_penalty_dice(self) -> Tuple[int, int]:
        """掷单个惩罚骰"""
        penalty_value = random.randint(0, 9)
        result = self.dice

        if result // 10 < penalty_value:
            result = result % 10 + penalty_value * 10

        return result, penalty_value


def get_success_description(rank: int) -> str:
    """获取成功等级描述"""
    success_descriptions = {
        SuccessLevel.CRITICAL_FAILURE: "大失败",
        SuccessLevel.FAILURE: "失败",
        SuccessLevel.SUCCESS: "成功",
        SuccessLevel.HARD_SUCCESS: "困难成功",
        SuccessLevel.EXTREME_SUCCESS: "极难成功",
        SuccessLevel.CRITICAL_SUCCESS: "大成功",
    }
    return success_descriptions.get(rank, "未知")


def calculate_damage_bonus(size: int, strength: int) -> str:
    """
    根据体型和体质计算伤害加值

    Args:
        size: 体型值
        strength: 体质值

    Returns:
        伤害加值字符串
    """
    total = size + strength

    # 使用字典映射范围判断，提高可读性
    bonus_ranges = [(65, "-2"), (85, "-1"), (125, "0"), (165, "1d4"), (205, "1d6")]

    for threshold, bonus in bonus_ranges:
        if total < threshold:
            return bonus

    # 超过205的情况
    additional_dice = (total - 205) // 80 + 2
    return f"{additional_dice}d6"


def roll_dice(
    dice_expression: str, use_max: Union[bool, int] = False
) -> Tuple[str, int]:
    """
    解析并执行掷骰表达式

    Args:
        dice_expression: 掷骰表达式 (如 "3d6+1-1")
        use_max: 是否使用最大骰值，True时取满值

    Returns:
        tuple: (详细掷骰过程字符串, 总结果)
    """

    def roll_single_dice(part: str) -> Tuple[int, str]:
        """处理单个骰子表达式或数字"""
        part = part.strip()

        # 处理骰子表达式 (如 "3d6", "d4", "2d4")
        if "d" in part:
            # 处理省略了前面数字的情况 (如 "d4" 应该等于 "1d4")
            if part.startswith("d"):
                count = 1
                sides = int(part[1:])
            else:
                count, sides = map(int, part.split("d"))

            if use_max:
                rolls = [sides] * count
                detail = " + ".join(map(str, rolls))
                return count * sides, detail
            else:
                rolls = [random.randint(1, sides) for _ in range(count)]
                detail = " + ".join(map(str, rolls))
                return sum(rolls), detail.replace(" ", "")
        else:
            # 处理纯数字
            return int(part), part

    # 纯数字情况
    if dice_expression.replace("+", "").replace("-", "").replace(" ", "").isdigit():
        return dice_expression, int(dice_expression)

    # 解析复合表达式 - 使用正则表达式或手动解析来正确处理加减号
    # 这里使用简单的解析方法
    expression = dice_expression.replace(" ", "")  # 移除空格
    parts = []
    current_part = ""

    # 手动解析表达式，正确处理正负号
    for char in expression:
        if char in "+-" and current_part:
            parts.append(current_part)
            parts.append(char)
            current_part = ""
        else:
            current_part += char
    if current_part:
        parts.append(current_part)

    # 如果没有操作符，直接返回
    if len(parts) == 1:
        result, detail = roll_single_dice(parts[0])
        return detail.replace(" ", ""), result

    # 计算结果
    total = 0
    details = []
    current_operator = "+"

    for part in parts:
        if part in "+-":
            current_operator = part
        else:
            result, detail = roll_single_dice(part)
            if current_operator == "+":
                total += result
                details.append(f"+ {detail}")
            else:
                total -= result
                details.append(f"- {detail}")

    # 处理第一个元素的符号显示
    if details and details[0].startswith("+ "):
        details[0] = details[0][2:]  # 移除第一个元素的"+ "

    detail_str = " ".join(details)

    return detail_str.replace(" ", ""), total


# 测试代码
if __name__ == "__main__":
    # 测试伤害加值计算
    test_cases = [(60, 50), (70, 60), (100, 80), (150, 100), (200, 150)]
    for size, str_val in test_cases:
        db = calculate_damage_bonus(size, str_val)
        print(f"Size: {size}, Strength: {str_val} -> DB: {db}")

    # 测试掷骰

    dice_tests = ["3d6", "2d4+1", "1d8+2d6+3", "10"]
    for test in dice_tests:
        # res = test + "+1d4"
        # print(res)
        detail, total = roll_dice(test)
        print(f"{test} -> {detail} = {total}")

    # 测试最大骰值
    detail, total = roll_dice("3d6+1", use_max=True)
    print(f"3d6+1 (max) -> {detail} = {total}")
