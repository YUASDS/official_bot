from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Optional

from database.db import add_gold

from ..models.item import Equipment
from .damage_calculator import calculate_damage as calc_dmg
from .data_loader import data_loader
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
        self._weapon_reply_shown = False
        self.fled = False

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

    def _t(self, key: str, **kwargs) -> str:
        return data_loader.get_text(key, **kwargs)

    def _get_reply(self, key: str) -> str:
        return data_loader.reply_data.get(key, f"[{key}]")

    @staticmethod
    def _fill_damage(template: str, expr: str, val: int) -> str:
        """填充伤害模板：$骰子=骰子明细，$伤害=数值。expr 形如 '1d4+1d4=2+1'。

        $骰子=$伤害 组合会展开为完整表达式，如 '1d4+1d4=2+1=3'。
        """
        pair = f"{expr}={val}" if "=" in expr else expr
        return (
            template.replace("$骰子=$伤害", pair)
            .replace("$骰子", expr)
            .replace("$伤害", str(val))
        )

    def _get_weapon_reply(self, weapon: Equipment) -> str:
        if self._weapon_reply_shown:
            return ""
        self._weapon_reply_shown = True
        return weapon.reply

    def start_turn(self) -> str:
        player_dex = self._get_player_modified_skill("敏捷")
        monster_dex = self._get_monster_modified("dex", 50)
        confrontation = ConfrontationRoll(player_dex, monster_dex)
        player_starts = confrontation.get_result("先攻")
        self.current_turn = "inv" if player_starts else "mon"

        desc1 = self.get_success_record_description(confrontation.level1)
        desc2 = self.get_success_record_description(confrontation.level2)
        dex1 = self._t(
            "battle.dex_check",
            name=self.player_name,
            dice=confrontation.dice1,
            target=confrontation.skill1,
            result=desc1,
        )
        dex2 = self._t(
            "battle.dex_check",
            name=self.monster.名字,
            dice=confrontation.dice2,
            target=confrontation.skill2,
            result=desc2,
        )

        return (
            f"{self._t('battle.initiative_title')}\n"
            f"{dex1}\n{dex2}\n\n"
            f"{self._get_next_turn_prompt()}"
        )

    def _get_next_turn_prompt(self) -> str:
        self.available_actions = self.investigator.get_available_actions()
        owner = self._t("battle.your_turn") if self.current_turn == "inv" else self._t("battle.monster_turn")

        lines: list[str] = []
        if self.environment:
            env_name = self.environment.get("name", "")
            if env_name:
                lines.append(f"【{env_name}】")
        if self.gun:
            lines.append(self._t("battle.ammo_label", bullet=self.bullet, max_bullet=self.max_bullet))
        lines.append(self._t("battle.turn_title", owner=owner))
        lines.append(
            self._t(
                "battle.hp_label",
                inv=self.hp_record["inv"],
                mon=self.hp_record["mon"],
            )
        )
        lines.append(self._t("battle.choose_action"))

        actions = self.available_actions.get(self.current_turn, [])
        lines.append(" ".join(self._t("battle.action_hint", action=a) for a in actions))
        return "\n".join(lines)

    def execute_action(self, action: str) -> tuple:
        self.current_action = action
        handler = {
            "inv": self._execute_player_action,
            "mon": self._execute_monster_action,
        }.get(self.current_turn)
        if handler:
            return handler(action)
        return (self._t("battle.error_state"),)

    def _execute_player_action(self, action: str) -> tuple:
        import random
        if self.is_madness and self.madness_duration > 0:
            self.madness_duration -= 1
            available = self.investigator.get_available_actions().get("inv", [])
            random_action = random.choice(available) if available else "格斗"
            madness_end_msg = ""
            if self.madness_duration == 0:
                self.is_madness = False
                madness_end_msg = self._t("battle.madness_end")
            msg = self._t("battle.madness_action", action=random_action) + madness_end_msg
            res = self._execute_player_action(random_action)
            return (msg, *res)

        action_handlers = {
            "格斗": self._melee_attack,
            "射击": lambda: self._ranged_attack(1),
            "二连射": lambda: self._ranged_attack(2),
            "三连射": lambda: self._ranged_attack(3),
            "换弹": self._reload_weapon,
            "逃跑": self._flee,
        }
        handler = action_handlers.get(action)
        if handler:
            return handler()
        return (self._t("battle.unknown_player_action", action=action),)

    def _execute_monster_action(self, action: str) -> tuple:
        if action in ("反击", "闪避"):
            return self._handle_defensive_action(action)
        return (self._t("battle.unknown_defense_action", action=action),)

    # --- Melee ---
    def _melee_attack(self) -> tuple:
        weapon_id = self.investigator.get_equipped_id(self.current_action)
        if not weapon_id:
            return (self._t("battle.no_melee_weapon"), self._end_turn())

        weapon = Equipment(weapon_id)
        if not weapon.is_valid:
            return (self._t("battle.invalid_equip_id", id=weapon_id), self._end_turn())

        monster_action = self.monster.get_action(self.current_turn)
        player_skill = self._get_player_modified_skill(weapon.identify_skill, 25)
        monster_skill = monster_action["skill"] + self.environment.get("怪物", {}).get("反击技能", 0)
        confrontation = ConfrontationRoll(player_skill, monster_skill)

        roll_desc = (
            self._t(
                "battle.fight_check",
                name=self.player_name,
                dice=confrontation.dice1,
                target=confrontation.skill1,
                result=self.get_success_record_description(confrontation.level1, weapon.identify_skill),
            )
            + "\n"
            + self._t(
                "battle.counter_check",
                name=self.monster.名字,
                dice=confrontation.dice2,
                target=confrontation.skill2,
                result=self.get_success_record_description(confrontation.level2),
            )
        )

        if confrontation.level1 == SuccessLevel.CRITICAL_FAILURE:
            failure_desc = self._handle_player_critical_failure(weapon)
            return (
                self._get_weapon_reply(weapon),
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
        player_text = self._fill_damage(
            self._get_reply(reply_key), expr, val
        ).replace("$装备", weapon.name)
        monster_text = self._apply_damage_to_monster(val)
        return (
            self._get_weapon_reply(weapon),
            monster_action["counterattack"],
            roll_desc,
            player_text,
            monster_text,
            self._end_turn(),
        )

    def _handle_player_melee_failure(self, confrontation, weapon, monster_action, roll_desc):
        if confrontation.level1 < 1 and confrontation.level2 < 1:
            player_text = self._get_reply(f"{self.current_action}失败").replace("$装备", weapon.name)
            monster_text = monster_action.get("counter_false", "")
        else:
            monster_text, player_text = self._handle_monster_attack_success(monster_action, confrontation)
        return (
            self._get_weapon_reply(weapon),
            monster_action["counterattack"],
            roll_desc,
            player_text,
            monster_text,
            self._end_turn(),
        )

    def _handle_monster_attack_success(self, monster_action, confrontation):
        armor = self.investigator.get_armor_value()
        expr, val = calc_dmg(
            monster_action["damage"],
            confrontation.level1,
            monster_action.get("ex", False),
            armor,
        )
        monster_text = self._fill_damage(
            monster_action.get("attack_succ", monster_action.get("desc", "攻击")),
            expr,
            val,
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
            return (self._t("battle.no_ranged_weapon"),)
        if self.bullet < shot_count:
            return (self._t("battle.no_ammo", count=self.bullet),)

        self.bullet -= shot_count
        weapon = self.gun
        player_skill = self._get_player_modified_skill(weapon.identify_skill, 20)

        if shot_count > 1:
            return self._multiple_shot(shot_count, weapon, player_skill)
        return self._single_shot(weapon, player_skill)

    def _single_shot(self, weapon: Equipment, player_skill: int) -> tuple:
        roll = DiceRoll(player_skill)
        roll_description = self._t(
            "battle.shoot_check",
            name=self.player_name,
            skill=weapon.identify_skill,
            dice=roll.dice,
            target=roll.skill,
            result=self.get_success_record_description(roll.level, weapon.identify_skill),
        )

        if roll.level > SuccessLevel.FAILURE:
            expr, val = calc_dmg(weapon.damage_dice, roll.level, weapon.has_penetration)
            reply_template = self._get_reply("射击大成功") if roll.level > SuccessLevel.HARD_SUCCESS else self._get_reply("射击成功")
            player_text = self._fill_damage(reply_template, expr, val)
            monster_text = self._apply_damage_to_monster(val)
            return (self._get_weapon_reply(weapon), roll_description, player_text, monster_text, self._end_turn())
        if roll.level == SuccessLevel.CRITICAL_FAILURE:
            player_text = self._get_reply("射击大失败").replace("$装备", weapon.name)
            self.investigator.break_equipped_item("远程")
            self._update_gun_status()
            return (self._get_weapon_reply(weapon), roll_description, player_text, "", self._end_turn())
        player_text = self._get_reply("射击失败")
        return (self._get_weapon_reply(weapon), roll_description, player_text, "", self._end_turn())

    def _multiple_shot(self, shot_count: int, weapon: Equipment, player_skill: int) -> tuple:
        roll_descriptions = []
        total_damage = 0
        player_texts = []
        critical_failure = False

        for _i in range(shot_count):
            roll = PenaltyDiceRoll(player_skill)
            roll_descriptions.append(
                self._t(
                    "battle.shoot_check",
                    name=self.player_name,
                    skill=weapon.identify_skill,
                    dice=roll.final_result,
                    target=roll.skill,
                    result=self.get_success_record_description(roll.level, weapon.identify_skill),
                )
                + self._t("battle.penalty_hint", rolls=roll.penalty_rolls)
            )

            if roll.level == SuccessLevel.CRITICAL_FAILURE:
                player_texts.append(self._get_reply("射击大失败").replace("$装备", weapon.name))
                self.investigator.break_equipped_item("远程")
                self._update_gun_status()
                critical_failure = True
                break
            if roll.level > SuccessLevel.FAILURE:
                expr, val = calc_dmg(weapon.damage_dice, roll.level, weapon.has_penetration)
                player_texts.append(self._t("battle.multi_shot_damage", expr=expr, value=val))
                total_damage += val

        roll_description = "\n".join(roll_descriptions)
        player_text = "\n".join(player_texts)

        if not critical_failure and total_damage > 0:
            player_text += f"\n{self._t('battle.total_damage', total=total_damage)}"
            monster_text = self._apply_damage_to_monster(total_damage)
        else:
            monster_text = ""

        return (self._get_weapon_reply(weapon), roll_description, player_text, monster_text, self._end_turn())

    def _reload_weapon(self) -> tuple:
        if self.max_bullet > 0:
            self.bullet = self.max_bullet
            return (self._t("battle.reload_done"), self._end_turn())
        return (self._t("battle.no_reloadable"),)

    def _flee(self) -> tuple:
        flee_skill = self._get_player_modified_skill("敏捷", 25)
        roll = DiceRoll(flee_skill)
        desc = self.get_success_record_description(roll.level)
        flee_check = self._t(
            "battle.flee_check", dice=roll.dice, skill=roll.skill, result=desc
        )
        if roll.level > SuccessLevel.FAILURE:
            self.fled = True
            self.investigator.hp = self.hp_record["inv"]
            self.investigator.is_adventure = False
            self.investigator.save()
            return (
                f"{flee_check}\n"
                f"{self._t('battle.flee_success')}",
            )
        monster_action = self.monster.get_action(self.current_turn)
        expr, val = calc_dmg(monster_action["damage"])
        self.hp_record["inv"] = max(0, self.hp_record["inv"] - val)
        return (
            f"{flee_check}\n"
            f"{self._t('battle.flee_fail', damage_expr=expr, damage=val)}",
            self._end_turn(),
        )

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
                return (self._t("battle.no_counter_weapon"), self._end_turn())
            weapon = Equipment(weapon_id)
            action_reply = self._get_weapon_reply(weapon)
            used_skill = weapon.identify_skill

        monster_skill = monster_action["skill"] + self.environment.get("怪物", {}).get("反击技能", 0)
        confrontation = ConfrontationRoll(monster_skill, player_skill)
        check_key = "battle.dodge_check" if player_action == "闪避" else "battle.counter_check"
        roll_desc = (
            self._t(
                "battle.attack_check",
                name=self.monster.名字,
                dice=confrontation.dice1,
                target=confrontation.skill1,
                result=self.get_success_record_description(confrontation.level1),
            )
            + "\n"
            + self._t(
                check_key,
                name=self.player_name,
                dice=confrontation.dice2,
                target=confrontation.skill2,
                result=self.get_success_record_description(confrontation.level2, used_skill),
            )
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
            return self._t("battle.counter_failed"), ""

        damage_formula = self._get_player_damage_formula(weapon)
        expr, val = calc_dmg(damage_formula)

        player_text = self._fill_damage(
            self._get_reply("反击成功"), expr, val
        ).replace("$装备", weapon.name)
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
            return getattr(self.monster, "低伤害", self._t("battle.no_damage"))

        initial_hp = self.hp_record["mon"]
        actual_damage = max(0, damage - getattr(self.monster, "armor", 0))
        self.hp_record["mon"] = max(0, self.hp_record["mon"] - actual_damage)

        if actual_damage > initial_hp / 2:
            return getattr(self.monster, "高伤害", self._t("monster.high_damage"))
        if actual_damage < 2:
            return getattr(self.monster, "低伤害", self._t("monster.low_damage"))
        return getattr(self.monster, "正常伤害", self._t("monster.normal_damage"))

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
            return f"\n{self._t('battle.death_text', name=self.player_name)}"
        if self.hp_record["mon"] <= 0:
            return self._handle_victory()
        return None

    def _handle_victory(self) -> str:
        search_skill = self.investigator.get_skill("侦查", 25)
        search_roll = DiceRoll(search_skill)
        search_desc_level = self.get_success_record_description(
            search_roll.level, "侦查"
        )
        search_desc = self._t(
            "battle.search_check",
            dice=search_roll.dice,
            target=search_roll.skill,
            result=search_desc_level,
        )

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

        growth_lines: list[str] = []
        for skill_name in self.succeded_skill:
            skill = self.investigator.get_skill(skill_name, 25)
            dice = DiceRoll(skill)
            des = self.get_success_record_description(dice.level)
            growth_lines.append(
                " " + self._t("battle.growth_line", skill=skill_name, dice=dice.dice, target=skill, result=des)
            )
            if dice.level < 1:
                _expr, res = roll_dice("1d10")
                growth_lines.append("  " + self._t("battle.growth_inc", value=res))
                skill += res
                self.investigator.set_skill(skill_name, skill)

        self.investigator.save()

        ending = getattr(self.monster, "结局", self._t("battle.monster_dead"))
        parts = [
            f"\n{self._t('battle.victory_title')}\n\n{ending}",
            f"\n{search_desc}\n{bonus_text}",
        ]
        if growth_lines:
            parts.append(f"\n{self._t('battle.victory_growth')}\n" + "\n".join(growth_lines))
        return "\n".join(parts)

    def fight_is_over(self) -> bool:
        return (
            self.fled
            or self.hp_record["inv"] <= 0
            or self.hp_record["mon"] <= 0
        )
