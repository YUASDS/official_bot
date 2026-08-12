"""BattleService · 战斗动作：近战、远程、防御/逃跑。"""

from __future__ import annotations

from typing import Optional

from ...models.item import Equipment
from ...models.player import investigator_repo
from ..damage_calculator import calculate_damage as calc_dmg
from ..dice_roller import (
    ConfrontationRoll,
    DiceRoll,
    PenaltyDiceRoll,
    SuccessLevel,
    get_success_description,
    get_success_icon,
    roll_dice,
)


def _append_damage_modifier(expr: str, dmg_mod: str, extra_expr: str) -> str:
    """@description 将环境伤害加成并入骰子表达式（1d8=7 → 1d8+1d4=7+2）。纯函数。"""
    if "=" not in expr:
        return f"{expr}{dmg_mod}={expr}+{extra_expr}"
    formula, breakdown = expr.split("=", 1)
    return f"{formula}{dmg_mod}={breakdown}+{extra_expr}"


class BattleActionsMixin:
    # --- Melee ---
    def _melee_attack(self) -> tuple:
        weapon_id = self.investigator.get_equipped_id(self.current_action)
        if not weapon_id:
            return (self._t("battle.no_melee_weapon"),)

        weapon = Equipment(weapon_id)
        if not weapon.is_valid:
            return (self._t("battle.invalid_equip_id", id=weapon_id),)

        monster_action = self.monster.get_action(self.current_turn)
        player_skill = self._get_player_modified_skill(weapon.identify_skill, 25)
        monster_skill = self._get_monster_attack_skill(monster_action)
        confrontation = ConfrontationRoll(player_skill, monster_skill)

        roll_desc = self._check_section(
            weapon.identify_skill,
            [
                self._check_row(
                    self.player_name,
                    weapon.identify_skill,
                    confrontation.dice1,
                    confrontation.skill1,
                    confrontation.level1,
                ),
                self._check_row(
                    self.monster.名字,
                    "反击",
                    confrontation.dice2,
                    confrontation.skill2,
                    confrontation.level2,
                ),
            ],
        )

        if confrontation.level1 == SuccessLevel.CRITICAL_FAILURE:
            failure_desc = self._handle_player_critical_failure(weapon)
            monster_parts = [monster_action["counterattack"]]
            # 玩家大失败：仅当怪物自身检定成功（成功/困难/极难/大成功）才命中；
            # 反击伤害恒为普通伤害，不叠加暴击（critical=False）
            if confrontation.level2 > SuccessLevel.FAILURE:
                monster_dmg, _ = self._handle_monster_attack_success(
                    monster_action, confrontation, critical=False
                )
                monster_parts.append(monster_dmg)
            exchange = self._exchange(
                monster_parts,
                [self._get_weapon_reply(weapon), failure_desc],
            )
            return (roll_desc, exchange, self._end_turn())

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
        exchange = self._exchange(
            [monster_action["counterattack"], monster_text],
            [self._get_weapon_reply(weapon), player_text],
        )
        return (roll_desc, exchange, self._end_turn())

    def _handle_player_melee_failure(self, confrontation, weapon, monster_action, roll_desc):
        if confrontation.level1 < 1 and confrontation.level2 < 1:
            player_text = self._get_reply(f"{self.current_action}失败").replace("$装备", weapon.name)
            monster_text = monster_action.get("counter_false", "")
        else:
            monster_text, player_text = self._handle_monster_attack_success(monster_action, confrontation)
        exchange = self._exchange(
            [monster_action["counterattack"], monster_text],
            [self._get_weapon_reply(weapon), player_text],
        )
        return (roll_desc, exchange, self._end_turn())

    def _handle_monster_attack_success(
        self,
        monster_action,
        confrontation,
        level: int | None = None,
        critical: bool = True,
    ):
        """@description 怪物攻击成功结算：critical=False 时强制普通伤害（反击不叠暴击）。

        返回 (怪物文案, 玩家承受文案)；命中判定由调用方完成。
        """
        if level is None:
            level = confrontation.level1
        if not critical:
            level = min(level, SuccessLevel.SUCCESS)
        armor = self.investigator.get_armor_value()
        expr, val = self._monster_damage_roll(monster_action, level)
        final_val = max(0, val - armor)
        monster_text = self._fill_damage(
            monster_action.get("attack_succ", monster_action.get("desc", "攻击")),
            expr,
            final_val,
        )
        player_text = self._apply_damage_to_player(final_val, armor_absorbed=True)
        return monster_text, player_text

    def _monster_damage_roll(self, monster_action: dict, level: int) -> tuple[str, int]:
        """@description 掷怪物伤害：基础伤害骰 + 环境「怪物·伤害」加成，返回 (表达式, 总值)。"""
        expr, val = calc_dmg(
            monster_action["damage"],
            level,
            monster_action.get("ex", False),
        )
        dmg_mod = self.environment.get("怪物", {}).get("伤害", "")
        if not dmg_mod:
            return expr, val
        extra_expr, extra = roll_dice(dmg_mod)
        return _append_damage_modifier(expr, dmg_mod, extra_expr), val + extra

    def _get_player_damage_formula(self, weapon: Equipment, include_db: bool = True) -> str:
        damage = weapon.damage_dice
        if include_db:
            db = self.investigator.db
            if db and db != "0":
                damage = f"{damage}+{db}" if not db.startswith("-") else f"{damage}{db}"
        # 环境玩家伤害加成（如满月 +1d4，数据自带符号）
        dmg_mod = self.environment.get("玩家", {}).get("伤害", "")
        if dmg_mod:
            if dmg_mod.startswith(("+", "-")):
                damage = f"{damage}{dmg_mod}"
            else:
                damage = f"{damage}+{dmg_mod}"
        return damage

    def _break_weapon(self, part: str) -> str:
        """@description 武器损毁：仅当无近战装备且背包已有弹簧折刀(101)时回退装备，不补发新刀。

        返回回退提示文本（成功回退折刀时非空，供战斗文案追加）。
        """
        self.investigator.break_equipped_item(part)
        fallback = ""
        if not self.investigator.get_equipped_id("近战"):
            equipments, _ = self.investigator.get_equipments()
            if "101" in equipments:
                investigator_repo.equip_item(self.investigator.qq, "101")
                fallback = self._t("battle.weapon_fallback")
        self.investigator.update_equipment()
        self._update_gun_status()
        return fallback

    def _handle_player_critical_failure(self, weapon: Equipment, context: str = "attack") -> str:
        """玩家大失败：不可损毁武器自伤；可损毁武器损毁并回退弹簧折刀。

        context=attack 用「格斗大失败」文案，context=defense（反击）用「反击大失败」文案。
        """
        if not weapon.breakable:
            expr, damage_val = roll_dice("1d4")
            self._apply_damage_to_player(damage_val)
            return (
                self._get_reply("大失败_初始")
                .replace("$骰子", "1d4")
                .replace("$伤害", expr)
            )
        reply_key = "格斗大失败" if context == "attack" else "反击大失败"
        text = self._get_reply(reply_key).replace("$装备", weapon.name)
        fallback = self._break_weapon(self.current_action)
        return text + fallback

    def _ranged_crit_failure(self, dice: int, weapon: Equipment) -> str:
        """@description 枪械大失败结算：骰 100 枪械损毁；96-99 卡壳（弹夹清零、中断连射，需换弹）。"""
        if dice == 100:
            text = self._get_reply("射击大失败").replace("$装备", weapon.name)
            self._break_weapon("远程")
            return text
        self.bullet = 0  # 卡壳：弹夹子弹清零，需换弹才能继续
        return self._get_reply("射击卡壳").replace("$装备", weapon.name)

    def _handle_dodge_fumble(self) -> str:
        """@description 闪避大失败：仅展示叙事（武器不掉落，属设计行为）。"""
        weapon_id = self.investigator.get_equipped_id("近战")
        if not weapon_id:
            return ""
        weapon = Equipment(weapon_id)
        if not weapon.breakable:
            return ""
        return self._get_reply("闪避大失败").replace("$装备", weapon.name)

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
        roll_description = self._check_section(
            weapon.identify_skill,
            [self._check_row(self.player_name, weapon.identify_skill, roll.dice, roll.skill, roll.level)],
        )

        if roll.level > SuccessLevel.FAILURE:
            expr, val = calc_dmg(
                self._get_player_damage_formula(weapon, include_db=False),
                roll.level,
                weapon.has_penetration,
            )
            reply_template = self._get_reply("射击大成功") if roll.level > SuccessLevel.HARD_SUCCESS else self._get_reply("射击成功")
            player_text = self._fill_damage(reply_template, expr, val)
            monster_text = self._apply_damage_to_monster(val)
            exchange = self._exchange([monster_text], [self._get_weapon_reply(weapon), player_text])
            return (roll_description, exchange, self._end_turn())
        if roll.level == SuccessLevel.CRITICAL_FAILURE:
            player_text = self._ranged_crit_failure(roll.dice, weapon)
            exchange = self._exchange([], [self._get_weapon_reply(weapon), player_text])
            return (roll_description, exchange, self._end_turn())
        player_text = self._get_reply("射击失败")
        exchange = self._exchange([], [self._get_weapon_reply(weapon), player_text])
        return (roll_description, exchange, self._end_turn())

    def _multiple_shot(self, shot_count: int, weapon: Equipment, player_skill: int) -> tuple:
        rows = []
        total_damage = 0
        player_texts = []
        critical_failure = False

        for _i in range(shot_count):
            roll = PenaltyDiceRoll(player_skill)
            icon = get_success_icon(roll.level)
            rows.append(
                self._t(
                    "report.check_row_penalty",
                    icon=icon,
                    name=self.player_name,
                    skill=weapon.identify_skill,
                    dice=roll.final_result,
                    target=roll.skill,
                    rolls=",".join(map(str, roll.penalty_rolls)),
                    result=self._t(
                        "report.check_result",
                        icon=icon,
                        level=get_success_description(roll.level),
                    ),
                )
            )

            if roll.level == SuccessLevel.CRITICAL_FAILURE:
                player_texts.append(self._ranged_crit_failure(roll.final_result, weapon))
                critical_failure = True
                break
            if roll.level > SuccessLevel.FAILURE:
                self.succeded_skill.add(weapon.identify_skill)  # 多连射成功同样参与成长鉴定
                expr, val = calc_dmg(
                    self._get_player_damage_formula(weapon, include_db=False),
                    roll.level,
                    weapon.has_penetration,
                )
                player_texts.append(self._t("battle.multi_shot_damage", expr=expr, value=val))
                total_damage += val

        roll_description = self._check_section(weapon.identify_skill, rows)
        player_text = "\n".join(player_texts)

        if not critical_failure and total_damage > 0:
            player_text += f"\n{self._t('battle.total_damage', total=total_damage)}"
            monster_text = self._apply_damage_to_monster(total_damage)
        else:
            monster_text = ""

        exchange = self._exchange(
            [monster_text],
            [self._get_weapon_reply(weapon), player_text],
        )
        return (roll_description, exchange, self._end_turn())

    def _reload_weapon(self) -> tuple:
        if self.max_bullet > 0:
            self.bullet = self.max_bullet
            return (self._t("battle.reload_done"), self._end_turn())
        return (self._t("battle.no_reloadable"),)

    def _flee(self) -> tuple:
        flee_skill = self._get_player_modified_skill("敏捷", 25)
        roll = DiceRoll(flee_skill)
        self.get_success_record_description(roll.level)
        flee_check = self._check_section(
            "逃跑", [self._check_row(self.player_name, "逃跑", roll.dice, flee_skill, roll.level)]
        )
        if roll.level > SuccessLevel.FAILURE:
            self.fled = True
            self.investigator.hp = self.hp_record["inv"]
            self.investigator.is_adventure = False
            self.investigator.save()
            self.end_parts = (
                f"{flee_check}\n\n{self._t('battle.flee_success')}",
                "",
            )
            return (
                f"{flee_check}\n\n"
                f"{self._t('battle.flee_success')}",
            )
        monster_action = self.monster.get_action(self.current_turn)
        expr, val = calc_dmg(monster_action["damage"])
        self._apply_damage_to_player(val)
        return (
            f"{flee_check}\n\n"
            f"{self._t('battle.flee_fail', damage_expr=expr, damage=val)}",
            self._end_turn(),
        )

    # --- Defensive ---
    def _handle_defensive_action(self, player_action: str) -> tuple:
        """@description 防御行动入口：闪避/反击的检定展示与结果结算。"""
        monster_action = self.monster.get_action(self.current_turn)
        weapon = self._defense_weapon(player_action)
        if player_action == "反击" and weapon is None:
            return (self._t("battle.no_counter_weapon"),)
        confrontation = self._defense_confrontation(player_action, monster_action)
        roll_desc = self._defense_roll_desc(player_action, confrontation)
        self._record_counter_growth(player_action, confrontation)
        critical_text = self._defense_critical_text(player_action, weapon, confrontation)
        if player_action == "闪避":
            monster_text, player_text = self._resolve_dodge(confrontation, monster_action)
        else:
            monster_text, player_text = self._resolve_counter(
                confrontation, monster_action, weapon
            )
        exchange = self._defense_exchange(
            monster_action, monster_text, player_text, critical_text, player_action, weapon
        )
        return (roll_desc, exchange, self._end_turn())

    def _defense_weapon(self, player_action: str) -> Optional[Equipment]:
        """@description 反击所需的近战武器（闪避无武器需求），未装备返回 None。"""
        if player_action == "闪避":
            return None
        weapon_id = self.investigator.get_equipped_id("格斗")
        return Equipment(weapon_id) if weapon_id else None

    def _defense_confrontation(
        self, player_action: str, monster_action: dict
    ) -> ConfrontationRoll:
        """@description 防御检定：怪物攻击 vs 玩家闪避/反击，返回对抗结果。"""
        skill = "闪避" if player_action == "闪避" else "格斗"
        player_skill = self._get_player_modified_skill(skill, 25)
        monster_skill = self._get_monster_attack_skill(monster_action)
        return ConfrontationRoll(monster_skill, player_skill)

    def _defense_roll_desc(self, player_action: str, confrontation) -> str:
        """@description 防御检定表格：怪物攻击行 + 玩家闪避/反击行。"""
        check_key = "闪避" if player_action == "闪避" else "反击"
        return self._check_section(
            check_key,
            [
                self._check_row(
                    self.monster.名字,
                    "攻击",
                    confrontation.dice1,
                    confrontation.skill1,
                    confrontation.level1,
                ),
                self._check_row(
                    self.player_name,
                    check_key,
                    confrontation.dice2,
                    confrontation.skill2,
                    confrontation.level2,
                ),
            ],
        )

    def _record_counter_growth(self, player_action: str, confrontation) -> None:
        """@description 反击按「格斗」技能记录成长（战报仍显示「反击」）。"""
        if player_action != "反击":
            return
        self.succeded_skill.discard("反击")
        if confrontation.level2 > SuccessLevel.FAILURE:
            self.succeded_skill.add("格斗")

    def _defense_critical_text(
        self, player_action: str, weapon: Optional[Equipment], confrontation
    ) -> str:
        """@description 玩家防御大失败惩罚：闪避掉武器/反击武器损毁，返回文案。"""
        if confrontation.level2 != SuccessLevel.CRITICAL_FAILURE:
            return ""
        if player_action == "闪避":
            return self._handle_dodge_fumble()
        if weapon:
            return self._handle_player_critical_failure(weapon, context="defense")
        return ""

    def _resolve_dodge(self, confrontation, monster_action) -> tuple[str, str]:
        """@description 闪避结算：怪物须自身成功且等级高于闪避才命中；同级闪避成功；双方失败落空。返回 (怪物文案, 玩家文案)。"""
        player_dodged = (
            confrontation.level2 > SuccessLevel.FAILURE
            and confrontation.level2 >= confrontation.level1
        )
        monster_hit = (
            confrontation.level1 > SuccessLevel.FAILURE and not player_dodged
        )
        if monster_hit:
            return self._handle_monster_attack_success(monster_action, confrontation)
        if player_dodged:
            return "", self._get_reply("闪避成功")
        return (
            monster_action.get("attack_false", monster_action.get("counterattack", "")),
            "",
        )

    def _resolve_counter(
        self, confrontation, monster_action, weapon: Optional[Equipment]
    ) -> tuple[str, str]:
        """@description 反击结算：怪物攻击成功→玩家受伤；双方失败→落空；否则玩家反击命中。返回 (怪物文案, 玩家文案)。"""
        if confrontation.get_result("反击"):
            return self._handle_monster_attack_success(monster_action, confrontation)
        if confrontation.level1 < 1 and confrontation.level2 < 1:
            return (
                monster_action.get("attack_false", monster_action.get("counterattack", "")),
                "",
            )
        player_text, monster_text = self._handle_player_counter_success(weapon)
        return monster_text, player_text

    def _defense_exchange(
        self,
        monster_action: dict,
        monster_text: str,
        player_text: str,
        critical_text: str,
        player_action: str,
        weapon: Optional[Equipment],
    ) -> str:
        """@description 防御交锋小节：怪物攻击叙事 + 玩家防御叙事。"""
        action_reply = (
            self._get_reply("闪避") if player_action == "闪避" else self._get_weapon_reply(weapon)
        )
        return self._exchange(
            [
                monster_action.get("attack", monster_action.get("desc", "攻击")),
                monster_text,
            ],
            [action_reply, player_text, critical_text],
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
