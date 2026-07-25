from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Optional

from database.db import add_gold

from ..models.item import Equipment
from .damage_calculator import calculate_damage as calc_dmg
from .data_loader import Separator, data_loader
from .dice_roller import (
    ConfrontationRoll,
    DiceRoll,
    PenaltyDiceRoll,
    SuccessLevel,
    get_success_description,
    roll_dice,
)

if TYPE_CHECKING:
    from ..models.monster import Monster
    from ..models.player import Investigator


class BattleService:
    def __init__(self, investigator: Investigator, monster: Monster) -> None:
        self.investigator = investigator
        self.monster = monster
        self.player_name = investigator.name
        self.hp_record = {"inv": investigator.hp, "mon": monster.hp}
        self.current_turn: Literal["inv", "mon"] = "inv"
        self.current_action = "格斗"

        self.gun: Optional[Equipment] = None
        self.bullet = 0
        self.max_bullet = 0
        self.succeded_skill = set()
        self._update_gun_status()

        self.is_madness = False
        self.madness_duration = 0

        self.environment: dict[str, dict] = {}

    def set_environment(self, env_data: dict) -> None:
        self.environment = env_data

    def set_madness(self, is_madness: bool, duration: int = 5) -> None:
        self.is_madness = is_madness
        self.madness_duration = duration

    def _get_player_modified_skill(self, skill_name: str, default: int = 0) -> int:
        base = self.investigator.get_skill(skill_name, default)
        player_mods = self.environment.get("玩家", {})
        if skill_name in player_mods:
            base += player_mods[skill_name]
        return max(0, base)

    def _get_monster_modified(self, attr: str, default: int = 0) -> int:
        base = getattr(self.monster, attr, default)
        monster_mods = self.environment.get("怪物", {})
        if attr in monster_mods:
            base += monster_mods[attr]
        return base

    def _update_gun_status(self):
        gun_id = self.investigator.get_equipped_id("远程")
        if gun_id:
            self.gun = Equipment(gun_id)
            if self.gun.is_valid:
                self.bullet = self.gun.bullet
                self.max_bullet = self.gun.max_bullet
            else:
                self.gun = None
        else:
            self.gun = None

    def get_success_record_description(self, rank: int, skill_name: str = "") -> str:
        if rank > SuccessLevel.FAILURE and skill_name:
            self.succeded_skill.add(skill_name)
        return get_success_description(rank)

    def _get_reply(self, key: str) -> str:
        return data_loader.reply_data.get(key, f"[{key}]")

    def start_turn(self) -> str:
        player_dex = self._get_player_modified_skill("敏捷")
        monster_dex = self._get_monster_modified("dex", 50)
        confrontation = ConfrontationRoll(player_dex, monster_dex)
        player_starts = confrontation.get_result("先攻")
        self.current_turn = "inv" if player_starts else "mon"

        dex1 = f"{self.player_name}进行敏捷鉴定: {confrontation.dice1}/{confrontation.skill1}【{self.get_success_record_description(confrontation.level1)}】"
        dex2 = f"{self.monster.名字}进行敏捷鉴定: {confrontation.dice2}/{confrontation.skill2}【{self.get_success_record_description(confrontation.level2)}】"

        return f"{dex1}\n{dex2}\n{self._get_next_turn_prompt()}"

    def _get_next_turn_prompt(self) -> str:
        self.available_actions = self.investigator.get_available_actions()
        turn_owner = "你的" if self.current_turn == "inv" else "怪物"

        prompt = f"--- {turn_owner}回合 ---\n当前HP: {self.hp_record['inv']}\n"
        if self.environment:
            prompt = f"【{self.environment.get('name', '当前环境')}: {self.environment.get('描述', '')}】\n{prompt}"
        prompt += "请选择行动:\n"
        if self.gun:
            prompt = f"当前子弹: {self.bullet}/{self.max_bullet}\n" + prompt

        actions = self.available_actions.get(self.current_turn, [])
        prompt += "".join([f"【/行动 {action}】\n" for action in actions])
        return prompt.strip()

    def execute_action(self, action: str) -> tuple:
        self.current_action = action
        handler = {
            "inv": self._execute_player_action,
            "mon": self._execute_monster_action,
        }.get(self.current_turn)
        if handler:
            return handler(action)
        return ("错误的战斗回合状态。",)

    def _execute_player_action(self, action: str) -> tuple:
        import random
        if self.is_madness and self.madness_duration > 0:
            self.madness_duration -= 1
            available = self.investigator.get_available_actions().get("inv", [])
            random_action = random.choice(available) if available else "格斗"
            madness_end_msg = ""
            if self.madness_duration == 0:
                self.is_madness = False
                madness_end_msg = "\n疯狂褪去了..."
            msg = f"你处于疯狂状态！不受控制地使用: {random_action}{madness_end_msg}"
            res = self._execute_player_action(random_action)
            return (msg, *res)

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

    def _execute_monster_action(self, action: str) -> tuple:
        if action in ("反击", "闪避"):
            return self._handle_defensive_action(action)
        return (f"未知的防御行动: {action}",)

    # --- Melee ---
    def _melee_attack(self) -> tuple:
        weapon_id = self.investigator.get_equipped_id(self.current_action)
        if not weapon_id:
            return ("你没有装备格斗武器！",)

        weapon = Equipment(weapon_id)
        if not weapon.is_valid:
            return (f"装备ID {weapon_id} 无效！",)

        monster_action = self.monster.get_action(self.current_turn)
        player_skill = self._get_player_modified_skill(weapon.identify_skill, 25)
        monster_skill = monster_action["skill"] + self.environment.get("怪物", {}).get("反击技能", 0)
        confrontation = ConfrontationRoll(player_skill, monster_skill)

        roll_desc = (
            f"{self.player_name}进行格斗: {confrontation.dice1}/{confrontation.skill1}【{self.get_success_record_description(confrontation.level1, weapon.identify_skill)}】\n"
            f"{self.monster.名字}进行反击: {confrontation.dice2}/{confrontation.skill2}【{self.get_success_record_description(confrontation.level2)}】"
        )

        if confrontation.level1 == SuccessLevel.CRITICAL_FAILURE:
            failure_desc = self._handle_player_critical_failure(weapon)
            return (
                weapon.reply,
                monster_action["counterattack"],
                roll_desc,
                failure_desc,
                "",
                self._end_turn(),
            )

        if confrontation.get_result("反击"):
            return self._handle_player_melee_success(confrontation, weapon, monster_action, roll_desc)
        return self._handle_player_melee_failure(confrontation, weapon, monster_action, roll_desc)

    def _handle_player_melee_success(self, confrontation, weapon, monster_action, roll_desc):
        damage_formula = self._get_player_damage_formula(weapon)
        expr, val = calc_dmg(damage_formula, confrontation.level1, weapon.has_penetration)
        reply_key = "格斗大成功" if confrontation.level1 > SuccessLevel.HARD_SUCCESS else "格斗成功"
        player_text = (
            self._get_reply(reply_key)
            .replace("$装备", weapon.name)
            .replace("$伤害", str(val))
            .replace("$骰子", expr)
        )
        monster_text = self._apply_damage_to_monster(val)
        return (weapon.reply, monster_action["counterattack"], roll_desc, player_text, monster_text, self._end_turn())

    def _handle_player_melee_failure(self, confrontation, weapon, monster_action, roll_desc):
        if confrontation.level1 < 1 and confrontation.level2 < 1:
            player_text = self._get_reply(f"{self.current_action}失败").replace("$装备", weapon.name)
            monster_text = monster_action.get("counter_false", "")
        else:
            monster_text, player_text = self._handle_monster_attack_success(monster_action, confrontation)
        return (weapon.reply, monster_action["counterattack"], roll_desc, player_text, monster_text, self._end_turn())

    def _handle_monster_attack_success(self, monster_action, confrontation):
        armor = self.investigator.get_armor_value()
        expr, val = calc_dmg(
            monster_action["damage"],
            confrontation.level1,
            monster_action.get("ex", False),
            armor,
        )
        monster_text = (
            monster_action.get("attack_succ", monster_action.get("desc", "攻击"))
            .replace("$伤害", str(val))
            .replace("$骰子", expr)
        )
        player_text = self._apply_damage_to_player(val)
        # Apply environment monster damage buff
        dmg_mod = self.environment.get("怪物", {}).get("伤害", "")
        if dmg_mod:
            _, extra = roll_dice(dmg_mod)
            val += extra
        return monster_text, player_text

    def _get_player_damage_formula(self, weapon: Equipment) -> str:
        damage = weapon.damage_dice
        db = self.investigator.db
        if db and db != "0":
            damage = f"{damage}+{db}" if not db.startswith("-") else f"{damage}{db}"
        return damage

    def _handle_player_critical_failure(self, weapon: Equipment) -> str:
        if weapon.name == "弹簧折刀":
            expr, damage_val = roll_dice("1d4")
            self._apply_damage_to_player(damage_val)
            return (
                self._get_reply("大失败_初始")
                .replace("$骰子", "1d4")
                .replace("$伤害", expr)
            )
        self.investigator.break_equipped_item(self.current_action)
        return self._get_reply("反击大失败").replace("$装备", weapon.name)

    # --- Ranged ---
    def _ranged_attack(self, shot_count: int) -> tuple:
        if not self.gun or not self.gun.is_valid:
            return ("你没有装备远程武器。",)
        if self.bullet < shot_count:
            return (f"弹药不足！当前剩余 {self.bullet} 发。",)

        self.bullet -= shot_count
        weapon = self.gun
        player_skill = self._get_player_modified_skill(weapon.identify_skill, 20)

        if shot_count > 1:
            return self._multiple_shot(shot_count, weapon, player_skill)
        return self._single_shot(weapon, player_skill)

    def _single_shot(self, weapon: Equipment, player_skill: int) -> tuple:
        roll = DiceRoll(player_skill)
        roll_description = f"{self.player_name}进行{weapon.identify_skill}鉴定,{roll.dice}/{roll.skill}【{self.get_success_record_description(roll.level, weapon.identify_skill)}】"

        if roll.level > SuccessLevel.FAILURE:
            expr, val = calc_dmg(weapon.damage_dice, roll.level, weapon.has_penetration)
            reply_template = self._get_reply("射击大成功") if roll.level > SuccessLevel.HARD_SUCCESS else self._get_reply("射击成功")
            player_text = reply_template.replace("$伤害", str(val)).replace("$骰子", expr)
            monster_text = self._apply_damage_to_monster(val)
            return (weapon.reply, roll_description, player_text, monster_text, self._end_turn())
        if roll.level == SuccessLevel.CRITICAL_FAILURE:
            player_text = self._get_reply("射击大失败").replace("$装备", weapon.name)
            self.investigator.break_equipped_item("远程")
            self._update_gun_status()
            return (weapon.reply, roll_description, player_text, "", self._end_turn())
        player_text = self._get_reply("射击失败")
        return (weapon.reply, roll_description, player_text, "", self._end_turn())

    def _multiple_shot(self, shot_count: int, weapon: Equipment, player_skill: int) -> tuple:
        roll_descriptions = []
        total_damage = 0
        player_texts = []
        critical_failure = False

        for _i in range(shot_count):
            roll = PenaltyDiceRoll(player_skill)
            roll_descriptions.append(
                f"{self.player_name}进行{weapon.identify_skill}鉴定,P={roll.dice}[惩罚骰:{roll.penalty_rolls}] "
                f"{roll.final_result}/{roll.skill}【{self.get_success_record_description(roll.level, weapon.identify_skill)}】"
            )

            if roll.level == SuccessLevel.CRITICAL_FAILURE:
                player_texts.append(self._get_reply("射击大失败").replace("$装备", weapon.name))
                self.investigator.break_equipped_item("远程")
                self._update_gun_status()
                critical_failure = True
                break
            if roll.level > SuccessLevel.FAILURE:
                expr, val = calc_dmg(weapon.damage_dice, roll.level, weapon.has_penetration)
                player_texts.append(f"{expr}={val}")
                total_damage += val

        roll_description = "\n".join(roll_descriptions)
        player_text = "\n".join(player_texts)

        if not critical_failure and total_damage > 0:
            player_text += f"\n总伤害：{total_damage}"
            monster_text = self._apply_damage_to_monster(total_damage)
        else:
            monster_text = ""

        return (weapon.reply, roll_description, player_text, monster_text, self._end_turn())

    def _reload_weapon(self) -> tuple:
        if self.max_bullet > 0:
            self.bullet = self.max_bullet
            return ("换弹完成", self._end_turn())
        return ("你没有可换弹的武器。",)

    # --- Defensive ---
    def _handle_defensive_action(self, player_action: str) -> tuple:
        monster_action = self.monster.get_action(self.current_turn)
        weapon = None
        used_skill = ""
        if player_action == "闪避":
            used_skill = "闪避"
            player_skill = self._get_player_modified_skill("闪避", 25)
            action_reply = self._get_reply("闪避")
        else:
            player_skill = self._get_player_modified_skill("格斗", 25)
            weapon_id = self.investigator.get_equipped_id("格斗")
            if not weapon_id:
                return ("你没有装备武器来反击！",)
            weapon = Equipment(weapon_id)
            action_reply = weapon.reply
            used_skill = weapon.identify_skill

        monster_skill = monster_action["skill"] + self.environment.get("怪物", {}).get("反击技能", 0)
        confrontation = ConfrontationRoll(monster_skill, player_skill)
        roll_desc = (
            f"{self.monster.名字}进行攻击: {confrontation.dice1}/{confrontation.skill1}【{self.get_success_record_description(confrontation.level1)}】\n"
            f"{self.player_name}进行{player_action}: {confrontation.dice2}/{confrontation.skill2}【{self.get_success_record_description(confrontation.level2, used_skill)}】"
        )

        if confrontation.level2 == SuccessLevel.CRITICAL_FAILURE and weapon:
            critical_text = self._handle_player_critical_failure(weapon)
            roll_desc += f"\n{critical_text}"

        monster_succeeds = confrontation.get_result(player_action)

        if monster_succeeds:
            monster_text, player_text = self._handle_monster_attack_success(monster_action, confrontation)
        elif player_action == "闪避":
            monster_text = ""
            player_text = self._get_reply("闪避成功")
        elif confrontation.level1 < 1 and confrontation.level2 < 1:
            monster_text = monster_action.get("attack_false", monster_action.get("counterattack", ""))
            player_text = ""
        else:
            player_text, monster_text = self._handle_player_counter_success(weapon)

        return (
            monster_action.get("attack", monster_action.get("desc", "攻击")),
            action_reply,
            roll_desc,
            monster_text,
            player_text,
            self._end_turn(),
        )

    def _handle_player_counter_success(self, weapon: Optional[Equipment]) -> tuple[str, str]:
        if not weapon or not weapon.is_valid:
            return "反击失败", ""

        damage_formula = self._get_player_damage_formula(weapon)
        expr, val = calc_dmg(damage_formula)

        player_text = (
            self._get_reply("反击成功")
            .replace("$装备", weapon.name)
            .replace("$伤害", str(val))
            .replace("$骰子", expr)
        )
        monster_text = self._apply_damage_to_monster(val)
        return player_text, monster_text

    # --- Damage application ---
    def _apply_damage_to_player(self, damage: int) -> str:
        if damage <= 0:
            return self._get_reply("低伤害")

        initial_hp = self.hp_record["inv"]
        armor = self.investigator.get_armor_value()
        actual_damage = max(0, damage - armor)
        self.hp_record["inv"] = max(0, self.hp_record["inv"] - actual_damage)

        if actual_damage > initial_hp / 2:
            return self._get_reply("高伤害")
        if actual_damage < 2:
            return self._get_reply("低伤害")
        return self._get_reply("正常伤害")

    def _apply_damage_to_monster(self, damage: int) -> str:
        if damage <= 0:
            return getattr(self.monster, "低伤害", "攻击无效。")

        initial_hp = self.hp_record["mon"]
        actual_damage = max(0, damage - getattr(self.monster, "armor", 0))
        self.hp_record["mon"] = max(0, self.hp_record["mon"] - actual_damage)

        if actual_damage > initial_hp / 2:
            return getattr(self.monster, "高伤害", "造成了重创！")
        if actual_damage < 2:
            return getattr(self.monster, "低伤害", "攻击几乎无效。")
        return getattr(self.monster, "正常伤害", "对其造成了伤害。")

    # --- Turn end ---
    def _end_turn(self) -> str:
        end_message = self._check_combat_over()
        if end_message:
            return end_message
        self.current_turn = "mon" if self.current_turn == "inv" else "inv"
        return self._get_next_turn_prompt()

    def _check_combat_over(self) -> Optional[str]:
        if self.hp_record["inv"] <= 0:
            self.investigator.is_survive = False
            self.investigator.save()
            return f"{self.player_name}死亡..."
        if self.hp_record["mon"] <= 0:
            return self._handle_victory()
        return None

    def _handle_victory(self) -> str:
        sep = Separator

        search_skill = self.investigator.get_skill("侦查", 25)
        search_roll = DiceRoll(search_skill)
        search_desc = f"{self.player_name}进行侦查: {search_roll.dice}/{search_roll.skill}【{self.get_success_record_description(search_roll.level, '侦查')}】"

        bonus_text = ""
        if search_roll.level > SuccessLevel.FAILURE:
            gold, dropped_item, bonus_text = self.monster.generate_loot()
            add_gold(self.investigator.qq, gold)
            if dropped_item:
                self.investigator.add_item_to_inventory(dropped_item.id, 1)
        else:
            bonus_text = self._get_reply("侦查失败")

        self.investigator.hp = self.hp_record["inv"]
        self.investigator.day += 1

        en_skill = "成长鉴定:\n"
        for skill_name in self.succeded_skill:
            skill = self.investigator.get_skill(skill_name, 25)
            dice = DiceRoll(skill)
            des = self.get_success_record_description(dice.level)
            en_skill += f"进行【{skill_name}】成长鉴定：{dice.dice}/{skill}【{des}】\n"
            if dice.level < 1:
                _expr, res = roll_dice("1d10")
                en_skill += f"技能成长：1d10={res}\n"
                skill += res
                self.investigator.set_skill(skill_name, skill)

        self.investigator.save()
        return f"{getattr(self.monster, '结局', '怪物倒下了。')}{sep}{search_desc}{sep}{bonus_text}{sep}{en_skill}"

    def fight_is_over(self):
        return self.hp_record["inv"] <= 0 or self.hp_record["mon"] <= 0
