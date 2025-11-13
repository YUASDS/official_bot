import asyncio
from typing import Tuple, Optional, Literal, Dict, Any, List


from loguru import logger

from database.db import add_gold


from .Investigator import Investigator
from .Monster import Monster
from .Equipment import Equipment
from .GlobalData import data_manager, Separator
from .dice import (
    DiceRoll,
    ConfrontationRoll,
    PenaltyDiceRoll,
    SuccessLevel,
    get_success_description,
    roll_dice,
)


class CombatSystem:
    """
    重构后的战斗系统主类。
    - 完全依赖于 Investigator, Monster, Equipment 领域对象，实现了高内聚低耦合。
    - 状态管理由领域对象自身负责（如 HP 增减、物品损坏）。
    - 本类的唯一职责是协调战斗流程。
    """

    def __init__(self, investigator: Investigator, monster: Monster):
        self.investigator: Investigator = investigator
        self.monster: Monster = monster

        # 在战斗系统内部维护一个临时的HP记录，用于战斗过程中的判断
        self.hp_record: Dict[str, int] = {
            "inv": self.investigator.hp,
            "mon": self.monster.hp,
        }

        self.player_name: str = self.investigator.name
        self.available_actions: Dict[str, List[str]] = (
            self.investigator.get_available_actions()
        )
        self.current_turn: Literal["inv", "mon"] = "inv"
        self.current_action: str = "格斗"

        # 初始化远程武器状态
        self.gun: Optional[Equipment] = None
        self.bullet: int = 0
        self.max_bullet: int = 0
        self.succeded_skill = set()
        self._update_gun_status()

    def get_success_record_description(self, rank: int, skill_name: str = "") -> str:
        if rank > SuccessLevel.FAILURE and skill_name:
            self.succeded_skill.add(skill_name)
        return get_success_description(rank)

    def _update_gun_status(self) -> None:
        """从调查员对象更新枪械状态。"""
        gun_id = self.investigator.get_equipped_id("远程")
        if gun_id:
            self.gun = Equipment(gun_id)
            if self.gun.is_valid:
                # 假设装备数据中有 'bullet' 字段
                self.bullet = getattr(self.gun, "bullet", 0)
                self.max_bullet = getattr(self.gun, "bullet", 0)
            else:
                self.gun = None
                self.bullet = 0
                self.max_bullet = 0
        else:
            self.gun = None
            self.bullet = 0
            self.max_bullet = 0

    def start_turn(self) -> str:
        """决定先手并开始第一回合。"""
        confrontation = ConfrontationRoll(
            self.investigator.get_skill("敏捷"), self.monster.敏捷
        )
        player_starts = confrontation.get_result("先攻")
        self.current_turn = "inv" if player_starts else "mon"

        dex1_res = f"{self.player_name}进行敏捷鉴定: {confrontation.dice1}/{confrontation.skill1}【{self.get_success_record_description(confrontation.level1)}】"
        dex2_res = f"{self.monster.名字}进行敏捷鉴定: {confrontation.dice2}/{confrontation.skill2}【{self.get_success_record_description(confrontation.level2)}】"

        initial_prompt = self._get_next_turn_prompt()
        return f"{dex1_res}\n{dex2_res}\n{initial_prompt}"

    def fight_is_over(self) -> bool:
        """检查战斗是否结束。"""
        return self.hp_record["inv"] <= 0 or self.hp_record["mon"] <= 0

    def execute_action(self, action: str) -> Tuple:
        """根据当前回合执行行动分派。"""
        self.current_action = action
        self.investigator.update_equipment()
        handler = {
            "inv": self._execute_player_action,
            "mon": self._execute_monster_action,
        }.get(self.current_turn)

        if handler:
            return handler(action)
        return ("错误的战斗回合状态。",)

    # --- 玩家与怪物行动的具体实现 ---

    def _execute_player_action(self, action: str) -> Tuple:
        """执行玩家行动。"""
        action_handlers = {
            "格斗": self._melee_attack,
            "射击": lambda: self._ranged_attack(1),
            "二连射": lambda: self._ranged_attack(2),
            "三连射": lambda: self._ranged_attack(3),
            "换弹": self._reload_weapon,
        }
        handler = action_handlers.get(action)
        if handler:
            return handler()
        return (f"未知的玩家行动: {action}",)

    def _execute_monster_action(self, action: str) -> Tuple:
        """执行怪物回合的行动（由玩家选择防御方式）。"""
        if action in ("反击", "闪避"):
            return self._handle_defensive_action(action)
        return (f"未知的防御行动: {action}",)

    def _melee_attack(self) -> Tuple:
        """处理近战格斗行动。"""
        weapon_id = self.investigator.get_equipped_id(self.current_action)
        if not weapon_id:
            return ("你没有装备格斗武器！",)

        weapon = Equipment(weapon_id)
        if not weapon.is_valid:
            return (f"装备ID {weapon_id} 无效！",)

        monster_action = self.monster.get_action(self.current_turn)
        player_skill = self.investigator.get_skill(weapon.identify_skill, 25)
        confrontation = ConfrontationRoll(player_skill, monster_action["skill"])

        roll_desc = (
            f"{self.player_name}进行格斗: {confrontation.dice1}/{confrontation.skill1}【{self.get_success_record_description(confrontation.level1,weapon.identify_skill)}】\n"
            f"{self.monster.名字}进行反击: {confrontation.dice2}/{confrontation.skill2}【{self.get_success_record_description(confrontation.level2)}】"
        )

        if confrontation.level1 == SuccessLevel.CRITICAL_FAILURE:
            failure_desc = self._handle_player_critical_failure(weapon)
            # roll_desc += f"\n{failure_desc}"
            return (
                weapon.reply,
                monster_action["counterattack"],
                roll_desc,
                failure_desc,
                "",
                self._end_turn(),
            )

        if confrontation.get_result("反击"):
            return self._handle_player_melee_success(
                confrontation, weapon, monster_action, roll_desc
            )
        else:
            return self._handle_player_melee_failure(
                confrontation, weapon, monster_action, roll_desc
            )

    def _ranged_attack(self, shot_count: int) -> Tuple:
        """处理远程射击行动。"""
        if not self.gun or not self.gun.is_valid:
            return ("你没有装备远程武器。",)
        if self.bullet < shot_count:
            return (f"弹药不足！当前剩余 {self.bullet} 发。",)

        self.bullet -= shot_count
        weapon = self.gun
        player_skill = self.investigator.get_skill(weapon.identify_skill, 20)

        if shot_count > 1:
            return self._multiple_shot(shot_count, weapon, player_skill)
        else:
            return self._single_shot(weapon, player_skill)

    def _single_shot(self, weapon: Equipment, player_skill: int) -> Tuple:
        """处理单发射击。"""
        if not self.gun:
            return ("", "")
        roll = DiceRoll(player_skill)
        roll_description = f"{self.player_name}进行{weapon.identify_skill}鉴定,{roll.dice}/{roll.skill}【{self.get_success_record_description(roll.level,self.gun.identify_skill)}】"

        if roll.level > SuccessLevel.FAILURE:
            damage_expression = self._calculate_damage_expression(
                weapon.damage, roll.level, weapon.has_penetration
            )
            if roll.level > SuccessLevel.HARD_SUCCESS:
                reply_template = self._get_reply("射击大成功")
            else:
                reply_template = self._get_reply("射击成功")

            player_text = reply_template.replace(
                "$伤害", str(damage_expression[1])
            ).replace("$骰子", damage_expression[0])
            monster_text = self._apply_damage_to_monster(damage_expression[1])

            return (
                weapon.reply,
                roll_description,
                player_text,
                monster_text,
                self._end_turn(),
            )

        elif roll.level == SuccessLevel.CRITICAL_FAILURE:
            player_text = self._get_reply("射击大失败").replace("$装备", weapon.name)
            self.investigator.break_equipped_item("远程")
            self._update_gun_status()
            return (
                weapon.reply,
                roll_description,
                player_text,
                "",
                self._end_turn(),
            )
        else:
            player_text = self._get_reply("射击失败")
            return (
                weapon.reply,
                roll_description,
                player_text,
                "",
                self._end_turn(),
            )

    def _multiple_shot(
        self, shot_count: int, weapon: Equipment, player_skill: int
    ) -> Tuple:
        """处理多发射击。"""
        roll_descriptions = []
        total_damage = 0
        player_texts = []
        critical_failure = False

        for i in range(shot_count):
            roll = PenaltyDiceRoll(player_skill)
            roll_descriptions.append(
                f"{self.player_name}进行{weapon.identify_skill}鉴定,P={roll.dice}[惩罚骰:{roll.penalty_rolls}] "
                f"{roll.final_result}/{roll.skill}【{self.get_success_record_description(roll.level,weapon.identify_skill)}】"
            )

            if roll.level == SuccessLevel.CRITICAL_FAILURE:
                player_texts.append(
                    self._get_reply("射击大失败").replace("$装备", weapon.name)
                )
                self.investigator.break_equipped_item("远程")
                self._update_gun_status()
                critical_failure = True
                break
            elif roll.level > SuccessLevel.FAILURE:
                damage_expression = self._calculate_damage_expression(
                    weapon.damage, roll.level, weapon.has_penetration
                )
                player_texts.append(f"{damage_expression[0]}={damage_expression[1]}")
                total_damage += damage_expression[1]

        roll_description = "\n".join(roll_descriptions)
        player_text = "\n".join(player_texts)

        if not critical_failure and total_damage > 0:
            player_text += f"\n总伤害：{total_damage}"
            monster_text = self._apply_damage_to_monster(total_damage)
        else:
            monster_text = ""

        return (
            weapon.reply,
            roll_description,
            player_text,
            monster_text,
            self._end_turn(),
        )

    def _reload_weapon(self) -> Tuple:
        """处理换弹行动。"""
        if self.max_bullet > 0:
            self.bullet = self.max_bullet
            return ("换弹完成", self._end_turn())
        return ("你没有可换弹的武器。",)

    def _handle_defensive_action(self, player_action: str) -> Tuple:
        """处理玩家的防御性行动。"""
        monster_action = self.monster.get_action(self.current_turn)
        weapon: Optional[Equipment] = None
        used_skill = ""
        if player_action == "闪避":
            used_skill = "闪避"
            player_skill = self.investigator.get_skill("闪避", 25)
            action_reply = self._get_reply("闪避")
        else:  # 反击
            player_skill = self.investigator.get_skill("格斗", 25)
            weapon_id = self.investigator.get_equipped_id("格斗")
            if not weapon_id:
                return ("你没有装备武器来反击！",)
            weapon = Equipment(weapon_id)
            action_reply = weapon.reply
            used_skill = weapon.identify_skill

        confrontation = ConfrontationRoll(monster_action["skill"], player_skill)
        roll_desc = (
            f"{self.monster.名字}进行攻击: {confrontation.dice1}/{confrontation.skill1}【{self.get_success_record_description(confrontation.level1)}】\n"
            f"{self.player_name}进行{player_action}: {confrontation.dice2}/{confrontation.skill2}【{self.get_success_record_description(confrontation.level2,used_skill)}】"
        )

        if confrontation.level2 == SuccessLevel.CRITICAL_FAILURE and weapon:
            critical_text = self._handle_player_critical_failure(weapon)
            roll_desc += f"\n{critical_text}"

        monster_succeeds = confrontation.get_result(player_action)

        if monster_succeeds:
            monster_text, player_text = self._handle_monster_attack_success(
                monster_action, confrontation
            )
        elif player_action == "闪避":
            monster_text = ""
            player_text = self._get_reply("闪避成功")
        elif confrontation.level1 < 1 and confrontation.level2 < 1:
            monster_text = monster_action["attack_false"]
            player_text = ""
        else:  # 玩家反击成功
            player_text, monster_text = self._handle_player_counter_success(weapon)

        return (
            monster_action["attack"],
            action_reply,
            roll_desc,
            monster_text,
            player_text,
            self._end_turn(),
        )

    # --- 伤害与结果处理辅助函数 ---

    def _handle_player_melee_success(
        self,
        confrontation: ConfrontationRoll,
        weapon: Equipment,
        monster_action: Dict,
        roll_desc: str,
    ) -> Tuple:
        """处理玩家近战成功。"""
        damage_formula = self._get_player_damage_formula(weapon)
        expr, val = self._calculate_damage_expression(
            damage_formula, confrontation.level1, weapon.has_penetration
        )

        reply_key = (
            "格斗大成功"
            if confrontation.level1 > SuccessLevel.HARD_SUCCESS
            else "格斗成功"
        )
        player_text = (
            self._get_reply(reply_key)
            .replace("$装备", weapon.name)
            .replace("$伤害", str(val))
            .replace("$骰子", expr)
        )
        monster_text = self._apply_damage_to_monster(val)

        return (
            weapon.reply,
            monster_action["counterattack"],
            roll_desc,
            player_text,
            monster_text,
            self._end_turn(),
        )

    def _handle_player_melee_failure(
        self,
        confrontation: ConfrontationRoll,
        weapon: Equipment,
        monster_action: Dict,
        roll_desc: str,
    ) -> Tuple:
        """处理玩家近战失败。"""
        if confrontation.level1 < 1 and confrontation.level2 < 1:
            player_text = self._get_reply(f"{self.current_action}失败").replace(
                "$装备", weapon.name
            )
            monster_text = monster_action["counter_false"]
        else:
            monster_text, player_text = self._handle_monster_attack_success(
                monster_action, confrontation
            )
        return (
            weapon.reply,
            monster_action["counterattack"],
            roll_desc,
            player_text,
            monster_text,
            self._end_turn(),
        )

    def _handle_monster_attack_success(
        self, monster_action: Dict, confrontation: ConfrontationRoll
    ) -> Tuple[str, str]:
        """处理怪物攻击成功。"""
        armor = self.investigator.get_armor_value()
        expr, val = self._calculate_damage_expression(
            monster_action["damage"],
            confrontation.level1,
            monster_action.get("ex", 0),
            armor,
        )
        monster_text = (
            monster_action["attack_succ"]
            .replace("$伤害", str(val))
            .replace("$骰子", expr)
        )
        player_text = self._apply_damage_to_player(val)
        return monster_text, player_text

    def _handle_player_counter_success(
        self, weapon: Optional[Equipment]
    ) -> Tuple[str, str]:
        """处理玩家反击成功。"""
        if not weapon or not weapon.is_valid:
            return "反击失败", ""

        damage_formula = self._get_player_damage_formula(weapon)
        expr, val = self._calculate_damage_expression(damage_formula)
        logger.info(damage_formula)
        logger.info(expr)
        logger.info(val)

        player_text = (
            self._get_reply("反击成功")
            .replace("$装备", weapon.name)
            .replace("$伤害", str(val))
            .replace("$骰子", expr)
        )
        monster_text = self._apply_damage_to_monster(val)
        return player_text, monster_text

    def _handle_player_critical_failure(self, weapon: Equipment) -> str:
        """处理玩家大失败，特别是武器损坏。"""
        if weapon.name == "弹簧折刀":
            dice_result = roll_dice("1d4")
            damage_val = dice_result[1]
            self._apply_damage_to_player(damage_val)
            return (
                self._get_reply("大失败_初始")
                .replace("$骰子", "1d4")
                .replace("$伤害", dice_result[0])
            )
        else:
            self.investigator.break_equipped_item(self.current_action)
            return self._get_reply("反击大失败").replace("$装备", weapon.name)

    # --- 核心计算与状态变更 ---

    def _get_player_damage_formula(self, weapon: Equipment) -> str:
        """获取玩家包括DB在内的伤害公式字符串。"""
        damage = weapon.damage
        db = self.investigator.db
        if db and db != "0":
            damage = f"{damage}+{db}" if not db.startswith("-") else f"{damage}{db}"
        return damage

    def _apply_damage_to_player(self, damage: int) -> str:
        """对玩家造成伤害，并返回描述文本。"""
        if damage <= 0:
            return self._get_reply("低伤害")

        initial_hp = self.hp_record["inv"]
        armor = self.investigator.get_armor_value()
        actual_damage = max(0, damage - armor)
        self.hp_record["inv"] = max(0, self.hp_record["inv"] - actual_damage)

        if actual_damage > initial_hp / 2:
            return self._get_reply("高伤害")
        elif actual_damage < 2:
            return self._get_reply("低伤害")
        else:
            return self._get_reply("正常伤害")

    def _apply_damage_to_monster(self, damage: int) -> str:
        """对怪物造成伤害，并返回描述文本。"""
        if damage <= 0:
            return getattr(self.monster, "低伤害", "攻击无效。")

        initial_hp = self.hp_record["mon"]
        actual_damage = max(0, damage - getattr(self.monster, "armor", 0))
        self.hp_record["mon"] = max(0, self.hp_record["mon"] - actual_damage)

        if actual_damage > initial_hp / 2:
            return getattr(self.monster, "高伤害", "造成了重创！")
        elif actual_damage < 2:
            return getattr(self.monster, "低伤害", "攻击几乎无效。")
        else:
            return getattr(self.monster, "正常伤害", "对其造成了伤害。")

    def _calculate_damage_expression(
        self,
        damage: str,
        success_level: int = 1,
        has_penetration: bool = False,
        armor: int = 0,
    ) -> Tuple[str, int]:
        """计算伤害表达式"""
        is_critical = success_level > SuccessLevel.HARD_SUCCESS

        if is_critical and has_penetration:
            expr, val = self._double_damage(damage)
            damage = f"{damage}+{damage}"
        elif is_critical:
            expr, val = roll_dice(damage, use_max=True)
        else:
            expr, val = roll_dice(damage)

        # 应用护甲
        if armor > 0:
            val = max(1 if has_penetration else 0, val - armor)
            expr = f"({expr})-{armor}"
            damage = f"({damage})-{armor}"
        if (
            damage.isdigit()
            or int(damage.split("d")[0]) <= 1
            and "+" not in damage
            and "-" not in damage
        ):
            return damage, val
        return f"{damage}={expr}", val

    def _double_damage(self, damage: str) -> Tuple[str, int]:
        """双倍伤害计算"""
        max_expr, max_val = roll_dice(damage, use_max=True)
        rand_expr, rand_val = roll_dice(damage)
        return f"{max_expr}+{rand_expr}", max_val + rand_val

    # --- 回合结束与胜利/失败处理 ---

    def _end_turn(self) -> str:
        """结束回合，检查胜负，返回下一回合的提示。"""
        end_message = self._check_combat_over()
        if end_message:
            return end_message

        self.current_turn = "mon" if self.current_turn == "inv" else "inv"
        return self._get_next_turn_prompt()

    def _check_combat_over(self) -> Optional[str]:
        """检查战斗是否结束，如果结束则返回最终结果，并保存状态。"""
        if self.hp_record["inv"] <= 0:
            # self.investigator.hp = 1
            self.investigator.is_survive = False
            self.investigator.save()
            return f"{self.player_name}死亡..."
        if self.hp_record["mon"] <= 0:
            return self._handle_victory()
        return None

    def _handle_victory(self) -> str:
        """处理战斗胜利，更新并保存调查员状态。"""
        # 更新调查员HP
        # self.investigator.hp = self.hp_record["inv"]

        search_skill = self.investigator.get_skill("侦查", 25)
        search_roll = DiceRoll(search_skill)
        search_desc = f"{self.player_name}进行侦查: {search_roll.dice}/{search_roll.skill}【{self.get_success_record_description(search_roll.level,'侦查')}】"

        bonus_text = ""
        if search_roll.level > SuccessLevel.FAILURE:
            # 使用怪物的掉落生成方法
            gold, dropped_item, bonus_text = self.monster.generate_loot()
            add_gold(self.investigator.qq, gold)
            if dropped_item:
                # 假设 Investigator 类有 add_item_to_inventory 方法
                self.investigator.add_item_to_inventory(dropped_item.id, 1)
        else:
            bonus_text = self._get_reply("侦查失败")

        self.investigator.day += 1

        en_skill = "成长鉴定:\n"
        for skill_name in self.succeded_skill:
            skill = self.investigator.get_skill(skill_name, 25)
            dice = DiceRoll(skill)
            des = self.get_success_record_description(dice.level)
            en_skill += f"进行【{skill_name}】成长鉴定：{dice.dice}/{skill}【{des}】\n"
            if dice.level < 1:
                exp, res = roll_dice("1d10")
                en_skill += f"技能成长：1d10={res}\n"
                skill += res
                self.investigator.set_skill(skill_name, skill)
        self.investigator.save()
        return f"{getattr(self.monster, '结局', '怪物倒下了。')}{Separator}{search_desc}{Separator}{bonus_text}{Separator}{en_skill}"

    def _get_next_turn_prompt(self) -> str:
        """获取并格式化下一回合的行动提示。"""
        self.available_actions = self.investigator.get_available_actions()
        turn_owner = "你的" if self.current_turn == "inv" else "怪物"

        prompt = (
            f'--- {turn_owner}回合 ---\n当前HP: {self.hp_record["inv"]}\n请选择行动:\n'
        )
        if self.gun:
            prompt = f"当前子弹: {self.bullet}/{self.max_bullet}\n" + prompt

        actions = self.available_actions.get(self.current_turn, [])
        prompt += "".join([f"【/行动 {action}】\n" for action in actions])
        return prompt.strip()

    def _get_reply(self, key: str) -> str:
        """从数据管理器获取回复文本。"""
        reply_texts = data_manager.reply_data
        return reply_texts.get(key, f"[{key}]")


def start_combat(
    player_qq: str, player_name_override: Optional[str] = None
) -> CombatSystem:
    """
    工厂函数：创建并初始化一个战斗系统实例（完全重构版）。
    """
    logger.info(f"尝试为QQ {player_qq} 开始战斗...")

    # 1. 加载 Investigator 对象，不存在则自动创建
    investigator = Investigator.load(player_qq)
    if player_name_override:
        investigator.name = player_name_override

    # 2. 加载当天的随机怪物
    # 使用 Monster 的工厂方法
    monster = Monster.load_random_for_day(investigator.day)
    if not monster:
        raise RuntimeError(f"无法为第 {investigator.day} 天加载任何怪物！")

    # 3. 创建战斗系统实例
    combat_system = CombatSystem(investigator=investigator, monster=monster)
    logger.info(f"战斗开始: {investigator.name}({investigator.qq}) vs {monster.名字}")
    return combat_system


if __name__ == "__main__":
    # 测试代码
    try:
        test_qq = "12345_test"  # 使用一个测试QQ号
        test_name = "英勇的测试员"

        # 启动战斗
        combat = start_combat(test_qq, test_name)

        # 显示初始信息和第一回合提示
        initial_message = combat.start_turn()
        print(initial_message)

        while not combat.fight_is_over():
            print("-" * 20)
            # 根据当前回合决定可输入指令
            actions = combat.available_actions.get(combat.current_turn, [])
            command = input(f"输入指令({', '.join(actions)}): ")

            if not command or command not in actions:
                print("无效指令，请重新输入。")
                continue

            result_tuple = combat.execute_action(command)

            # 打印战斗过程信息
            for message in result_tuple:
                if message:
                    print(message)

            print(
                f"HP状态: 玩家 {combat.hp_record['inv']}, 怪物 {combat.hp_record['mon']}"
            )

        print("\n战斗结束！")

    except Exception as e:
        logger.exception("战斗系统测试时发生异常")
        print(f"战斗异常: {e}")
