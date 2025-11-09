import random
from typing import Union, Tuple, Dict, Any, Optional, List
from loguru import logger

from .dice import *
from .Investigator import investigator_service, Investigator
from .Monster import Monster, get_mon
from .Equipment import EquipmentService, LootService, Equipment
from .GlobalData import data_manager, Separator


class CombatSystem:
    """战斗系统主类"""

    def __init__(
        self,
        player_id: str,
        player_info: Dict[str, Any],
        monster_id: str,
        available_actions: Dict[str, Any],
        hp_record: Dict[str, Dict[str, int]],
        player_name: str,
    ):
        self.player_id = str(player_id)
        self.player_info = player_info
        self.monster = Monster(monster_id)
        self.available_actions = available_actions
        self.hp_record = hp_record
        self.player_name = player_name
        self.current_action = None
        self.gun = investigator_service.get_equipped_id(player_id, "远程")
        if self.gun:
            gun = Equipment(self.gun)
            self.bullet = gun.bullet
            self.max_bullet = self.bullet

    def first_turn(self) -> str:
        turn, replys, action_dict = first(
            inv=investigator_service.get_investigator(self.player_id),
            mon=self.monster,
            qq=self.player_id,
            name=self.player_name,
        )
        self.current_turn = turn
        return replys

    def fight_is_over(self) -> bool:
        """检查战斗是否结束"""
        player_hp = self.hp_record[self.player_id]["inv"]
        monster_hp = self.hp_record[self.player_id]["mon"]
        return player_hp <= 0 or monster_hp <= 0

    def execute_action(self, action: str) -> Tuple:
        """执行行动"""
        self.current_action = action

        if self.current_turn == "inv":
            return self._execute_player_action(action)
        else:
            return self._execute_monster_action(action)

    def _execute_player_action(self, action: str) -> Tuple:
        """执行玩家行动"""
        action_handlers = {
            "格斗": self._melee_attack,
            "射击": lambda: self._ranged_attack(1),
            "二连射": lambda: self._ranged_attack(2),
            "三连射": lambda: self._ranged_attack(3),
            "换弹": lambda: self._change_bomb,
        }

        handler = action_handlers.get(action)
        if handler:
            return handler()
        else:
            logger.warning(f"未知的玩家行动: {action}")
            return ()

    def _execute_monster_action(self, action: str) -> Tuple:
        """执行怪物回合行动"""
        action_handlers = {"反击": self._counter_attack, "闪避": self._dodge}

        handler = action_handlers.get(action)
        if handler:
            return handler()
        else:
            logger.warning(f"未知的怪物行动: {action}")
            return ()

    def _melee_attack(self) -> Tuple:
        """近战攻击"""
        # 获取装备信息
        equipped_item = investigator_service.get_equipped_id(
            self.player_id, action=self.current_action  # type: ignore
        )
        if not equipped_item:
            logger.exception(f"玩家 {self.player_id} 没有装备格斗武器")
            return ()

        action_reply = EquipmentService.get_equipment_reply(equipped_item)
        monster_action = self.monster.get_action(self.current_turn)

        # 从玩家信息中获取技能值
        player_skill = self.player_info.get(self.current_action, 25)  # type: ignore
        monster_skill = monster_action["skill"]

        confrontation = ConfrontationRoll(player_skill, monster_skill)
        player_wins = confrontation.get_result("反击")

        # 构建鉴定描述
        player_roll_desc = f"{self.player_name}进行格斗鉴定,{confrontation.dice1}/{confrontation.skill1}【{get_success_description(confrontation.level1)}】"
        # TO DO: 根据怪物的行动优化
        monster_roll_desc = f"{self.monster.名字}进行反击鉴定,{confrontation.dice2}/{confrontation.skill2}【{get_success_description(confrontation.level2)}】"
        roll_description = f"{player_roll_desc}\n{monster_roll_desc}"

        equipment_name = EquipmentService.get_equipment_name(equipped_item)

        if confrontation.level1 == SuccessLevel.CRITICAL_FAILURE:
            des = self._handle_critical_failure(equipment_name)
            roll_description += f"\n{des}"
        if player_wins:
            return self._handle_successful_attack(
                confrontation,
                equipped_item,
                action_reply,
                monster_action["counterattack"],
                roll_description,
            )
        else:
            return self._handle_failed_attack(
                confrontation,
                equipment_name,
                action_reply,
                monster_action,
                roll_description,
            )

    def _handle_critical_failure(
        self,
        equipment: str,
    ) -> str:
        """处理大失败情况"""
        if equipment != "弹簧折刀":
            player_text = self._get_reply_text("反击大失败").replace("$装备", equipment)
        else:
            damage = roll_dice("1d4")
            self._apply_damage_to_player(damage[1])
            player_text = (
                self._get_reply_text("大失败_初始")
                .replace("$骰子", "1d4")
                .replace("$伤害", damage[0])
            )

        investigator_service.break_equipped_item(self.player_id, self.current_action)  # type: ignore
        self.available_actions = investigator_service.get_available_actions(
            self.player_id
        )
        return player_text

    def _handle_successful_attack(
        self,
        confrontation: ConfrontationRoll,
        equipped_item: str,
        action_reply: str,
        monster_reply: str,
        roll_description: str,
    ) -> Tuple:
        """处理成功攻击"""
        damage = EquipmentService.get_equipment_damage(equipped_item)
        has_penetration = EquipmentService.has_penetration_effect(equipped_item)
        equipment_name = EquipmentService.get_equipment_name(equipped_item)
        db: str = self.player_info.get("db", "0")
        if db != "0":
            if db.startswith("-"):
                damage += db
            else:
                damage += f"+{db}"

        damage_expression = self._calculate_damage_expression(
            damage, confrontation.level1, has_penetration
        )

        if confrontation.level1 > SuccessLevel.HARD_SUCCESS:
            reply_template = self._get_reply_text("格斗大成功")
        else:
            reply_template = self._get_reply_text("格斗成功")

        player_text = (
            reply_template.replace("$装备", equipment_name)
            .replace("$伤害", str(damage_expression[1]))
            .replace("$骰子", damage_expression[0])
        )

        monster_text = self._apply_damage_to_monster(damage_expression[1])

        return (
            action_reply,
            monster_reply,
            roll_description,
            player_text,
            monster_text,
            self._end_turn(),
        )

    def _handle_failed_attack(
        self,
        confrontation: ConfrontationRoll,
        equipment: str,
        action_reply: str,
        monster_action: Dict,
        roll_description: str,
    ) -> Tuple:
        """处理失败攻击"""
        if confrontation.level1 < 1 and confrontation.level2 < 1:
            action = self.current_action
            player_text = self._get_reply_text(f"{action}失败").replace(
                "$装备", equipment
            )
            monster_text = monster_action["counter_false"]
        else:
            monster_damage = monster_action["damage"]
            armor = int(investigator_service.get_armor(self.player_id))

            damage_expression = self._calculate_damage_expression(
                monster_damage, armor=armor
            )

            monster_text = (
                monster_action["counter_succ"]
                .replace("$伤害", str(damage_expression[1]))
                .replace("$骰子", damage_expression[0])
            )

            player_text = self._apply_damage_to_player(damage_expression[1])

        return (
            action_reply,
            monster_action["counterattack"],
            roll_description,
            player_text,
            monster_text,
            self._end_turn(),
        )

    def _ranged_attack(self, shot_count: int) -> Tuple:
        """远程攻击"""
        if self.bullet < shot_count:
            return (f"当前剩余弹药{self.bullet}", "无法发射")
        else:
            self.bullet -= shot_count
        if shot_count > 1:
            return self._multiple_shot(shot_count)
        else:
            return self._single_shot()

    def _change_bomb(self) -> Tuple:
        self.bullet = self.max_bullet
        return ("换弹完成", self._end_turn())

    def _single_shot(self) -> Tuple:
        """单发射击"""
        equipped_item = investigator_service.get_equipped_id(
            self.player_id, action=self.current_action  # type: ignore
        )
        if not equipped_item:
            logger.exception(f"玩家 {self.player_id} 没有装备远程武器")
            return ()

        skill_name = EquipmentService.get_identify_skill(equipped_item)
        player_skill = self.player_info.get(skill_name, 20)
        damage = EquipmentService.get_equipment_damage(equipped_item)
        equipment_name = EquipmentService.get_equipment_name(equipped_item)

        roll = DiceRoll(player_skill)
        roll_description = f"{self.player_name}进行{skill_name}鉴定,{roll.dice}/{roll.skill}【{get_success_description(roll.level)}】"

        if roll.level > SuccessLevel.FAILURE:
            damage_expression = self._calculate_damage_expression(damage, roll.level, 1)
            if roll.level > SuccessLevel.HARD_SUCCESS:
                reply_template = self._get_reply_text("射击大成功")
            else:
                reply_template = self._get_reply_text("射击成功")

            player_text = reply_template.replace(
                "$伤害", str(damage_expression[1])
            ).replace("$骰子", damage_expression[0])
            monster_text = self._apply_damage_to_monster(damage_expression[1])

        elif roll.level == SuccessLevel.CRITICAL_FAILURE:
            player_text = self._get_reply_text("射击大失败").replace(
                "$装备", equipment_name
            )
            monster_text = None
            investigator_service.break_equipped_item(
                self.player_id, self.current_action
            )
            self.gun = 0
            self.available_actions = investigator_service.get_available_actions(
                self.player_id
            )
            # damage_eq(self.player_id, self.current_action)
        else:
            player_text = self._get_reply_text("射击失败")
            monster_text = None

        return (
            EquipmentService.get_equipment_reply(equipped_item),
            roll_description,
            player_text,
            monster_text,
            self._end_turn(),
        )

    def _multiple_shot(self, shot_count: int) -> Tuple:
        """多发射击"""
        equipped_item = investigator_service.get_equipped_id(
            self.player_id, action=self.current_action  # type: ignore
        )
        if not equipped_item:
            logger.exception(f"玩家 {self.player_id} 没有装备远程武器")
            return ()

        skill_name = EquipmentService.get_identify_skill(equipped_item)
        player_skill = self.player_info.get(skill_name, 20)
        damage = EquipmentService.get_equipment_damage(equipped_item)
        equipment_name = EquipmentService.get_equipment_name(equipped_item)

        roll_descriptions = []
        total_damage = 0
        player_text = ""

        for _ in range(shot_count):
            roll = PenaltyDiceRoll(player_skill)
            roll_descriptions.append(
                f"{self.player_name}进行{skill_name}鉴定,P={roll.dice}[惩罚骰:{roll.penalty_rolls}] "
                f"{roll.final_result}/{roll.skill}【{get_success_description(roll.level)}】"
            )

            if roll.level == SuccessLevel.CRITICAL_FAILURE:
                player_text += f'{self._get_reply_text("射击大失败")}\n'.replace(
                    "$装备", equipment_name
                )
                # damage_eq(self.player_id, self.current_action)
                investigator_service.break_equipped_item(
                    self.player_id, self.current_action
                )
                self.gun = 0
                self.available_actions = investigator_service.get_available_actions(
                    self.player_id
                )
                break
            elif roll.level > SuccessLevel.FAILURE:
                damage_expression = self._calculate_damage_expression(
                    damage, roll.level, 1
                )
                player_text += f"{damage_expression[0]}={damage_expression[1]}\n"
                total_damage += damage_expression[1]

        roll_description = "\n".join(roll_descriptions)
        player_text += f"总伤害：{total_damage}"
        monster_text = self._apply_damage_to_monster(total_damage)

        return (
            EquipmentService.get_equipment_reply(equipped_item),
            roll_description,
            player_text,
            monster_text,
            self._end_turn(),
        )

    def _counter_attack(self) -> Tuple:
        """反击行动"""
        return self._handle_defensive_action("反击")

    def _dodge(self) -> Tuple:
        """闪避行动"""
        return self._handle_defensive_action("闪避")

    def _handle_defensive_action(self, action: str) -> Tuple:
        """处理防御性行动"""
        if action == "闪避":
            action_reply = self._get_reply_text("闪避")
        else:
            equipped_item = investigator_service.get_equipped_id(
                self.player_id, action=action
            )
            if not equipped_item:
                logger.exception(f"玩家 {self.player_id} 没有装备 {action} 武器")
                return ()
            action_reply = EquipmentService.get_equipment_reply(equipped_item)

        monster_action = self.monster.get_action(self.current_turn)

        monster_skill = monster_action["skill"]
        player_skill = self.player_info.get(action, "")
        if not player_skill:
            player_skill = self.player_info.get("格斗", 25)
        confrontation = ConfrontationRoll(monster_skill, player_skill)
        monseter_succeeds = confrontation.get_result(action)
        monster_roll_desc = f"{self.monster.名字}进行鉴定,{confrontation.dice1}/{confrontation.skill1}【{get_success_description(confrontation.level1)}】"
        player_roll_desc = f"{self.player_name}进行{action}鉴定,{confrontation.dice2}/{confrontation.skill2}【{get_success_description(confrontation.level2)}】"
        roll_description = f"{monster_roll_desc}\n{player_roll_desc}"
        monster_text = None

        if confrontation.level2 == SuccessLevel.CRITICAL_FAILURE and action != "闪避":
            equipment_name = EquipmentService.get_equipment_name(equipped_item)
            des = self._handle_critical_failure(equipment_name)
            roll_description += f"\n{des}"
        # 怪物造成伤害
        if monseter_succeeds:
            monster_text, player_text = self._handle_monster_success(
                monster_action, confrontation
            )
        # 闪避成功
        elif action == "闪避":
            player_text = self._get_reply_text("闪避成功")
            monster_text = None
        # 双方攻击失败
        elif confrontation.level1 < 1 and confrontation.level2 < 1:
            monster_text = monster_action["attack_false"]
            player_text = None
        else:
            # 对怪物造成伤害
            player_text, monster_text = self._handle_successful_counter(monster_action)

        return (
            monster_action["attack"],
            action_reply,
            roll_description,
            monster_text,
            player_text,
            self._end_turn(),
        )

    def _handle_successful_counter(self, monster_action: Dict) -> Tuple:
        """处理成功反击"""
        equipped_item = investigator_service.get_equipped_id(
            self.player_id, action=self.current_action
        )
        if not equipped_item:
            return "反击失败", ""

        damage = EquipmentService.get_equipment_damage(equipped_item)
        name = EquipmentService.get_equipment_name(equipped_item)
        db = self.player_info.get("db", "0")
        if db != "0":
            if db.startswith("-"):
                damage += db
            else:
                damage += f"+{db}"
        # print(damage)
        damage_expression = self._calculate_damage_expression(damage)
        player_text = (
            self._get_reply_text("反击成功")
            .replace("$装备", name)
            .replace("$伤害", str(damage_expression[1]))
            .replace("$骰子", damage_expression[0])
        )

        monster_text = self._apply_damage_to_monster(damage_expression[1])
        return player_text, monster_text

    def _handle_monster_success(
        self, monster_action: Dict, confrontation: ConfrontationRoll
    ) -> Tuple:
        """处理怪物成功"""
        monster_damage = monster_action["damage"]
        armor = int(investigator_service.get_armor(self.player_id))
        damage_expression = self._calculate_damage_expression(
            monster_damage, confrontation.level1, monster_action.get("ex", 0), armor
        )

        monster_text = (
            monster_action["attack_succ"]
            .replace("$伤害", str(damage_expression[1]))
            .replace("$骰子", damage_expression[0])
        )

        player_text = (
            self._get_reply_text("闪避失败") if self.current_action == "闪避" else ""
        )
        player_text += self._apply_damage_to_player(damage_expression[1])

        return monster_text, player_text

    def _calculate_damage_expression(
        self, damage: str, success_level: int = 0, extra_damage: int = 0, armor: int = 0
    ) -> Tuple[str, int]:
        """
        计算伤害表达式

        Args:
            damage: 基础伤害
            success_level: 成功等级
            extra_damage: 额外伤害
            armor: 护甲值

        Returns:
            (伤害表达式, 实际伤害)
        """
        # 计算基础伤害
        if success_level > SuccessLevel.HARD_SUCCESS and extra_damage:
            base_expression = self._double_damage(damage)
        elif success_level > SuccessLevel.HARD_SUCCESS:
            base_expression = roll_dice(damage, use_max=True)
        else:
            base_expression = (damage, roll_dice(damage)[1])

        expression, total_damage = base_expression

        # 应用护甲
        if armor:
            total_damage = max(0, total_damage - armor)
            expression = f"{expression}-{armor}"

        return expression, total_damage

    def _double_damage(self, damage: str) -> Tuple[str, int]:
        """双倍伤害计算"""
        max_damage = roll_dice(damage, use_max=True)
        random_damage = roll_dice(damage)

        total_damage = max_damage[1] + random_damage[1]
        expression = f"{max_damage[0]}+{damage}={max_damage[0]}+{random_damage[0]}"

        return expression, total_damage

    def _apply_damage_to_monster(self, damage: int) -> str:
        """对怪物造成伤害"""
        actual_damage = self.monster.damage_to_mon(damage)
        current_monster_hp = self.hp_record[self.player_id]["mon"]
        self.hp_record[self.player_id]["mon"] -= actual_damage

        if current_monster_hp / 2 < actual_damage:
            return self.monster.高伤害
        elif actual_damage < 2:
            return self.monster.低伤害
        else:
            return self.monster.正常伤害

    def _apply_damage_to_player(self, damage: int) -> str:
        """对玩家造成伤害"""
        current_player_hp = self.hp_record[self.player_id]["inv"]
        self.hp_record[self.player_id]["inv"] -= damage

        if current_player_hp / 2 < damage:
            return self._get_reply_text("高伤害")
        elif damage < 2:
            return self._get_reply_text("低伤害")
        else:
            return self._get_reply_text("正常伤害")

    def _end_turn(self):
        """结束当前回合"""
        player_hp = self.hp_record[self.player_id]["inv"]
        monster_hp = self.hp_record[self.player_id]["mon"]

        if player_hp > 0 and monster_hp > 0:
            self.current_turn = "mon" if self.current_turn == "inv" else "inv"
            if self.gun:
                bullet_num = f"当前子弹剩余：{self.bullet}\n"
                return bullet_num + self._get_turn_message(player_hp)
            else:
                return self._get_turn_message(player_hp)
        elif player_hp <= 0:
            investigator_service.mark_as_deceased(self.player_id)
            return f"{self.player_name}死亡"
        else:
            return self._handle_victory()

    def _get_turn_message(self, player_hp: int) -> str:
        """获取回合开始消息"""
        if self.current_turn == "mon":
            reply = f'{"怪物的回合".center(10, "-")}\n当前剩余HP：{player_hp}\n请选择行动：\n'
            for action in self.available_actions["mon"]:
                reply += f"【/行动 {action}】\n"
        else:
            reply = (
                f'{"你的回合".center(10, "-")}\n当前剩余HP：{player_hp}\n请选择行动：\n'
            )
            for action in self.available_actions["inv"]:
                reply += f"【/行动 {action}】\n"

        return reply.strip()

    def _handle_victory(self) -> str:
        """处理胜利情况"""
        search_skill = self.player_info.get("侦查", 25)
        search_roll = DiceRoll(search_skill)
        search_desc = f"{self.player_name}进行侦查鉴定,{search_roll.dice}/{search_roll.skill}【{get_success_description(search_roll.level)}】"

        if search_roll.level > SuccessLevel.FAILURE:

            gold, item, bonus = LootService.get_loot_reward(self.monster.data.get("id"))
            if item:
                item_id = item.get(
                    "id",
                )
                investigator_service.add_item_to_inventory(self.player_id, item_id)

        else:
            bonus = self._get_reply_text("侦查失败")
        investigator_service.update_investigator(
            self.player_id, {"day": self.player_info.get("day", 1) + 1}
        )
        return f"{self.monster.结局}{Separator}{search_desc}{Separator}{bonus}"

    def _get_reply_text(self, key: str) -> str:
        """获取回复文本"""
        # 这里可以从数据库或配置文件中获取，暂时返回简单文本
        reply_texts = data_manager.reply_data
        return reply_texts.get(key, f"[{key}]")


def first(inv: Investigator, mon: Monster, qq, name):
    confrontation = ConfrontationRoll(inv.敏捷, mon.敏捷)
    turn = "inv" if confrontation.get_result("先攻") else "mon"
    dex1_res = f"{name}进行敏捷鉴定：\n{confrontation.dice1}/{confrontation.skill1} 【{get_success_description(confrontation.level1)}】\n"
    dex2_res = f"{mon.名字}进行敏捷鉴定：\n{confrontation.dice2}/{confrontation.skill2} 【{get_success_description(confrontation.level2)}】\n"
    action_dict = investigator_service.get_available_actions(qq)
    if turn == "inv":
        replys = (
            f"{dex1_res}{dex2_res}调查员回合".center(10, "-") + "\n" + "请选择行动：\n"
        )
        for i in action_dict["inv"]:
            replys += f"【/行动 {i}】\n"
    else:
        replys = (
            f"{dex1_res}{dex2_res}怪物回合".center(10, "-") + "\n" + "请选择行动：\n"
        )
        for i in action_dict["mon"]:
            replys += f"【/行动 {i}】\n"
    return turn, replys, action_dict


def start_combat(
    player_id: Union[str, int], player_name: str = "调查员"
) -> CombatSystem:
    """
    开始战斗

    Args:
        player_id: 玩家ID
        player_name: 玩家名称

    Returns:
        战斗系统实例
    """
    # 获取玩家信息
    player_id = str(player_id)
    player_info = investigator_service.get_investigator_dict(player_id)
    if not player_info:
        logger.exception(f"无法找到玩家 {player_id} 的信息")
        raise ValueError(f"玩家 {player_id} 不存在")

    # 获取行动字典
    action_dict = investigator_service.get_available_actions(player_id)

    # 获取怪物信息（基于玩家当前天数）
    day = player_info.get("day", 1)
    monster_info = get_mon(str(day))

    # 初始化HP记录
    hp_record = {
        player_id: {"inv": player_info.get("hp", 10), "mon": monster_info.get("hp", 20)}
    }

    # 创建战斗系统
    combat_system = CombatSystem(
        player_id=player_id,
        player_info=player_info,
        monster_id=monster_info["id"],
        available_actions=action_dict,
        hp_record=hp_record,
        player_name=player_name,
    )

    logger.info(
        f"开始战斗: 玩家 {player_name}({player_id}) vs {monster_info.get('名字', '怪物')}"
    )
    return combat_system


if __name__ == "__main__":
    # 测试代码
    try:
        combat = start_combat("1787569211", "测试玩家")
        result = combat.first_turn()
        print(result)
        while (
            combat.hp_record[combat.player_id]["inv"] > 0
            and combat.hp_record[combat.player_id]["mon"] > 0
        ):
            command = input("输入指令：")
            result = combat.execute_action(command)

            if not result:
                print("无效指令或无法执行")
                continue

            for message in result:
                if message:
                    print(message)

            print(f"HP状态: {combat.hp_record}")

    except Exception as e:
        logger.exception("战斗系统异常")
        print(f"战斗异常: {e}")
