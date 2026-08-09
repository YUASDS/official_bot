from __future__ import annotations

import random
from typing import TYPE_CHECKING, Literal, Optional

from database.db import add_gold

from ..models.item import Equipment
from ..utils.md_format import report_check_table, report_quote, report_section
from .damage_calculator import calculate_damage as calc_dmg
from .data_loader import data_loader
from .dice_roller import (
    ConfrontationRoll,
    DiceRoll,
    PenaltyDiceRoll,
    SuccessLevel,
    get_success_description,
    get_success_icon,
    roll_dice,
)

if TYPE_CHECKING:
    from ..models.monster import Monster
    from ..models.player import Investigator

# 失控自动结算的迭代上限（保险丝，正常每回合 2 步内必推进）
_MAX_MADNESS_STEPS = 100


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
        self._turn_counter = 0
        self.end_parts: tuple[str, str] = ("", "")
        self.end_card_ext: dict = {}

        # 法术资源：MP = 意志/5（战斗中不回复）；临时生命（先抵伤害）
        self.max_mp = investigator.get_skill("意志", 0) // 5
        self.mp = self.max_mp
        self.temp_hp = 0

    def get_turn_token(self) -> int:
        """当前回合令牌（用于按钮防重复点击）。"""
        return self._turn_counter

    def _advance_turn(self) -> None:
        self._turn_counter += 1

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

    def roll_initiative(self) -> None:
        """按敏捷值直接决定先手（不掷骰）：敏捷高者先行动，平局玩家先手。"""
        self._advance_turn()
        player_dex = self._get_player_modified_skill("敏捷")
        monster_dex = self._get_monster_modified("dex", 50)
        self.current_turn = "inv" if player_dex >= monster_dex else "mon"

    def get_status_table(self) -> str:
        """当前回合行 + 双方状态表格（SAN / HP）。"""
        t = self._t
        owner = (
            t("battle.your_turn")
            if self.current_turn == "inv"
            else t("battle.monster_turn")
        )
        inv = self.investigator
        max_san = inv.get_skill("意志") or inv.get_skill("san", 0)
        rows = [
            t(
                "report.status_inv_mini",
                icon=t("report.icon_inv"),
                name=self.player_name,
                san=inv.get_skill("san", 0),
                max_san=max_san,
                hp=self.hp_record["inv"],
                max_hp=inv.get_max_hp(),
            ),
            t(
                "report.status_mon_mini",
                icon=t("report.icon_mon"),
                name=self.monster.名字,
                hp=self.hp_record["mon"],
                max_hp=self.monster.max_hp,
            ),
        ]
        return (
            f"{t('battle.turn_line', owner=owner)}\n\n"
            + "\n".join([t("report.status_mini_header"), t("report.status_mini_sep"), *rows])
        )

    def get_dex_compare_section(self) -> str:
        """【敏捷·对比】小节：双方敏捷值与先手判定（不掷骰）。"""
        t = self._t
        player_dex = self._get_player_modified_skill("敏捷")
        monster_dex = self._get_monster_modified("dex", 50)
        winner = (
            self.player_name
            if self.current_turn == "inv"
            else self.monster.名字
        )
        line = (
            f"🧑‍🎤 {self.player_name} 敏捷 {player_dex}"
            f" ｜ 👾 {self.monster.名字} 敏捷 {monster_dex}"
        )
        return (
            f"{report_section(t('battle.dex_compare_title'))}\n"
            f"{report_quote([line, f'→ {winner} 先手'])}"
        )

    def get_danger_section(self) -> str:
        """怪物回合的「濒危一刻」叙事小节（玩家回合返回空串）。"""
        t = self._t
        if self.current_turn != "mon":
            return ""
        action = self.monster.get_action("mon")
        attack_text = action.get("attack", action.get("desc", ""))
        return (
            f"{report_section(t('battle.danger_title'))}\n"
            f"{t('battle.danger_line', text=attack_text)}"
        )

    def get_action_section(self) -> str:
        """【行动·抉择】行动列表小节：攻击类一行，其余逐个列出。"""
        t = self._t
        actions = self.get_available_actions_for_turn()
        attack_list = data_loader.get_text("battle.attack_actions") or []
        attack = [a for a in actions if a in attack_list]
        others = [a for a in actions if a not in attack_list]

        lines = [report_section(t("report.action_title"))]
        if attack:
            codes = " ".join(t("report.action_code", action=a) for a in attack)
            lines.append(t("report.action_group", actions=codes))
        for a in others:
            lines.append(t("report.action", action=a))
        return "\n".join(lines)

    def start_turn(self) -> str:
        """开场战报：状态表 + 濒危一刻 + 行动抉择。"""
        self.roll_initiative()
        parts = [self.get_status_table(), self.get_danger_section(), self.get_action_section()]
        return "\n\n---\n\n".join(p for p in parts if p)

    def get_available_actions_for_turn(self) -> list[str]:
        """当前回合可用行动列表（供按钮展示）。"""
        if not hasattr(self, "available_actions"):
            self.available_actions = self.investigator.get_available_actions()
        return self.available_actions.get(self.current_turn, [])

    def _check_row(self, name: str, skill: str, dice: int, target: int, level: int) -> str:
        """构造检定表格行。"""
        t = self._t
        # 记录调查员成功使用的技能（供胜利后的成长鉴定）
        if level > SuccessLevel.FAILURE and skill and name == self.player_name:
            self.succeded_skill.add(skill)
        icon = get_success_icon(level)
        result = t(
            "report.check_result",
            icon=icon,
            level=get_success_description(level),
        )
        return t(
            "report.check_row",
            icon=icon,
            name=name,
            skill=skill,
            dice=dice,
            target=target,
            result=result,
        )

    def _check_section(self, skill: str, rows: list[str]) -> str:
        """检定小节：分节头 + 表格。"""
        return f"{report_section(self._t('battle.check_title', skill=skill))}\n{report_check_table(rows)}"

    def _quote(self, text: str) -> str:
        """单段叙事引用块（空文本返回空串）。"""
        return report_quote([text]) if text else ""

    def _exchange(self, monster: list[str], player: list[str]) -> str:
        """【战斗·交锋】小节：怪物叙述与玩家叙述分两段引用块。"""
        t = self._t
        monster = [x for x in monster if x]
        player = [x for x in player if x]
        if not monster and not player:
            return ""

        blocks: list[str] = []
        if monster:
            blocks.append(
                t(
                    "report.exchange_mon",
                    icon=t("report.icon_mon"),
                    name=self.monster.名字,
                )
                + "\n"
                + report_quote(monster)
            )
        if player:
            blocks.append(
                t(
                    "report.exchange_inv",
                    icon=t("report.icon_inv"),
                    name=self.player_name,
                )
                + "\n"
                + report_quote(player)
            )
        return (
            f"{report_section(t('battle.exchange_title'))}\n"
            + "\n\n".join(blocks)
        )

    def _get_mini_status_table(self) -> str:
        """回合中的迷你状态表（SAN + HP，无先攻列）。"""
        t = self._t
        inv = self.investigator
        max_san = inv.get_skill("意志") or inv.get_skill("san", 0)
        rows = [
            t(
                "report.status_inv_mini",
                icon=t("report.icon_inv"),
                name=self.player_name,
                san=inv.get_skill("san", 0),
                max_san=max_san,
                hp=self.hp_record["inv"],
                max_hp=inv.get_max_hp(),
            ),
            t(
                "report.status_mon_mini",
                icon=t("report.icon_mon"),
                name=self.monster.名字,
                hp=self.hp_record["mon"],
                max_hp=self.monster.max_hp,
            ),
        ]
        return "\n".join(
            [t("report.status_mini_header"), t("report.status_mini_sep"), *rows]
        )

    def _settlement(self) -> str:
        """【结算·战报】小节：战斗结束时的状态汇总。"""
        t = self._t
        inv = self.investigator
        max_san = inv.get_skill("意志") or inv.get_skill("san", 0)
        rows = [
            t("battle.settle_header"),
            t("battle.settle_sep"),
            t(
                "battle.settle_row",
                label=t("battle.settle_hp"),
                value=f"{self.hp_record['inv']}/{inv.get_max_hp()}",
            ),
            t(
                "battle.settle_row",
                label=t("battle.settle_san"),
                value=f"{inv.get_skill('san', 0)}/{max_san}",
            ),
            t(
                "battle.settle_row",
                label=t("battle.settle_day"),
                value=str(inv.day),
            ),
        ]
        return f"{report_section(t('battle.settle_title'))}\n" + "\n".join(rows)

    def _get_next_turn_prompt(self) -> str:
        self.available_actions = self.investigator.get_available_actions()
        t = self._t
        owner = (
            t("battle.your_turn")
            if self.current_turn == "inv"
            else t("battle.monster_turn")
        )

        lines = [
            t("battle.turn_line", owner=owner),
            self._get_mini_status_table(),
        ]
        if self.gun:
            lines.append(
                t("battle.ammo_label", bullet=self.bullet, max_bullet=self.max_bullet)
            )
        if self.max_mp > 0:
            lines.append(f"**{t('spell.mp_label')}：{self.mp}/{self.max_mp}**")
        if self.temp_hp > 0:
            lines.append(f"**{t('spell.temp_hp_label')}：{self.temp_hp}**")
        lines.append(self.get_action_section())
        return "\n\n".join(lines)

    def execute_action(self, action: str) -> tuple:
        self.current_action = action
        if self.is_madness and self.madness_duration > 0:
            return self._resolve_madness()
        handler = {
            "inv": self._execute_player_action,
            "mon": self._execute_monster_action,
        }.get(self.current_turn)
        if handler:
            return handler(action)
        return (self._t("battle.error_state"),)

    def _execute_player_action(self, action: str) -> tuple:
        if action.startswith("施法"):
            spell_id = action.removeprefix("施法").strip()
            return self._cast_spell(spell_id)
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

    # --- Spells ---
    def _cast_spell(self, spell_id: str) -> tuple:
        """释放法术：MP/SAN 校验 → 扣资源 → 对抗（部分法术）→ 效果结算。"""
        t = self._t
        if not self.investigator.has_spell(spell_id):
            return (t("spell.not_learned"), self._end_turn())
        spell = data_loader.spell_data.get(spell_id)
        if not spell:
            return (t("spell.not_learned"), self._end_turn())

        mp_cost = int(spell.get("mp_cost", 1))
        san_cost = int(spell.get("san_cost", 0))
        if self.mp < mp_cost:
            return (t("spell.no_mp", mp=self.mp, max_mp=self.max_mp), self._end_turn())
        if self.investigator.get_skill("san", 0) < san_cost:
            return (t("spell.no_san"), self._end_turn())

        # 扣除资源（无论成败）
        self.mp -= mp_cost
        if san_cost:
            san = max(0, self.investigator.get_skill("san", 0) - san_cost)
            self.investigator.set_skill("san", san)
        cost_line = t("spell.mp_cost", mp=mp_cost) + (
            f"、{san_cost} 点理智" if san_cost else ""
        )

        monster_action = self.monster.get_action(self.current_turn)

        # 对抗法术：法术技能 vs 怪物技能
        if spell.get("对抗"):
            player_skill = max(
                1,
                self._get_player_modified_skill(
                    spell.get("learn_skill", "智力"), 25
                ),
            )
            monster_skill = monster_action["skill"]
            confrontation = ConfrontationRoll(player_skill, monster_skill)
            roll_desc = self._check_section(
                spell["name"],
                [
                    self._check_row(
                        self.player_name,
                        spell["name"],
                        confrontation.dice1,
                        confrontation.skill1,
                        confrontation.level1,
                    ),
                    self._check_row(
                        self.monster.名字,
                        "抵抗",
                        confrontation.dice2,
                        confrontation.skill2,
                        confrontation.level2,
                    ),
                ],
            )
            # 法术检定不参与成长鉴定
            self.succeded_skill.discard(spell["name"])
            if not confrontation.get_result("反击"):
                fail_text = t("spell.cast_fail", mp=mp_cost, san=san_cost or 0)
                return (
                    roll_desc,
                    self._exchange([monster_action["counterattack"]], [fail_text]),
                    self._end_turn(),
                )
        else:
            roll_desc = (
                f"{report_section(t('spell.cast_title', name=spell['name']))}\n"
                f"{report_quote([cost_line])}"
            )

        effect_text, monster_text = self._apply_spell_effect(spell)
        player_text = f"{spell.get('回复', '')}\n\n{effect_text}"
        exchange = self._exchange([monster_text], [player_text])
        return (roll_desc, exchange, self._end_turn())

    def _apply_spell_effect(self, spell: dict) -> tuple[str, str]:
        """应用法术效果，返回 (玩家效果文本, 怪物反应文本)。"""
        t = self._t
        effect = spell.get("effect", {})
        etype = effect.get("type", "damage")
        dice = effect.get("dice", "1d3")
        expr, val = roll_dice(dice)

        if etype == "damage":
            monster_text = self._apply_damage_to_monster(val)
            return f"{expr}={val}，造成 {val} 点伤害", monster_text
        if etype == "heal":
            max_hp = self.investigator.get_max_hp()
            before = self.hp_record["inv"]
            self.hp_record["inv"] = min(max_hp, before + val)
            healed = self.hp_record["inv"] - before
            return t("spell.heal_self", value=healed), ""
        if etype == "temp_hp":
            self.temp_hp += val
            return t("spell.temp_gain", value=val), ""
        return "", ""

    def _resolve_madness(self) -> tuple:
        """疯狂失控：按战斗轮顺序自动结算，怪物行动一次+玩家随机行动一次为一回合，直至疯狂结束。"""
        parts: list[str] = []
        guard = 0
        while (
            self.is_madness
            and self.madness_duration > 0
            and not self.fight_is_over()
        ):
            guard += 1
            if guard > _MAX_MADNESS_STEPS:
                self.is_madness = False
                break
            if self.current_turn == "inv":
                self.madness_duration -= 1
                available = self.investigator.get_available_actions().get("inv", [])
                random_action = random.choice(available) if available else "格斗"
                msg = self._t("battle.madness_action", action=random_action)
                if self.madness_duration == 0:
                    self.is_madness = False
                    msg += self._t("battle.madness_end")
                parts.append(msg)
                parts.extend(self._execute_player_action(random_action))
                if self.current_turn == "inv" and not self.fight_is_over():
                    parts.append(self._end_turn())
            else:
                defensive = "闪避"
                if self.investigator.get_equipped_id("格斗"):
                    defensive = random.choice(["反击", "闪避"])
                parts.extend(self._execute_monster_action(defensive))
                if self.current_turn == "mon" and not self.fight_is_over():
                    parts.append(self._end_turn())
        return tuple(parts)

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
            exchange = self._exchange(
                [monster_action["counterattack"]],
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
        roll_description = self._check_section(
            weapon.identify_skill,
            [self._check_row(self.player_name, weapon.identify_skill, roll.dice, roll.skill, roll.level)],
        )

        if roll.level > SuccessLevel.FAILURE:
            expr, val = calc_dmg(weapon.damage_dice, roll.level, weapon.has_penetration)
            reply_template = self._get_reply("射击大成功") if roll.level > SuccessLevel.HARD_SUCCESS else self._get_reply("射击成功")
            player_text = self._fill_damage(reply_template, expr, val)
            monster_text = self._apply_damage_to_monster(val)
            exchange = self._exchange([monster_text], [self._get_weapon_reply(weapon), player_text])
            return (roll_description, exchange, self._end_turn())
        if roll.level == SuccessLevel.CRITICAL_FAILURE:
            player_text = self._get_reply("射击大失败").replace("$装备", weapon.name)
            self.investigator.break_equipped_item("远程")
            self._update_gun_status()
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
                player_texts.append(self._get_reply("射击大失败").replace("$装备", weapon.name))
                self.investigator.break_equipped_item("远程")
                self._update_gun_status()
                critical_failure = True
                break
            if roll.level > SuccessLevel.FAILURE:
                expr, val = calc_dmg(weapon.damage_dice, roll.level, weapon.has_penetration)
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
        self.hp_record["inv"] = max(0, self.hp_record["inv"] - val)
        return (
            f"{flee_check}\n\n"
            f"{self._t('battle.flee_fail', damage_expr=expr, damage=val)}",
            self._end_turn(),
        )

    # --- Defensive ---
    def _handle_defensive_action(self, player_action: str) -> tuple:
        monster_action = self.monster.get_action(self.current_turn)
        weapon = None
        if player_action == "闪避":
            player_skill = self._get_player_modified_skill("闪避", 25)
            action_reply = self._get_reply("闪避")
        else:
            player_skill = self._get_player_modified_skill("格斗", 25)
            weapon_id = self.investigator.get_equipped_id("格斗")
            if not weapon_id:
                return (self._t("battle.no_counter_weapon"), self._end_turn())
            weapon = Equipment(weapon_id)
            action_reply = self._get_weapon_reply(weapon)

        monster_skill = monster_action["skill"] + self.environment.get("怪物", {}).get("反击技能", 0)
        confrontation = ConfrontationRoll(monster_skill, player_skill)
        check_key = "闪避" if player_action == "闪避" else "反击"
        roll_desc = self._check_section(
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

        # 反击按"格斗"技能结算成长鉴定（战报仍显示"反击"）
        if player_action == "反击":
            self.succeded_skill.discard("反击")
            if confrontation.level2 > SuccessLevel.FAILURE:
                self.succeded_skill.add("格斗")

        critical_text = ""
        if confrontation.level2 == SuccessLevel.CRITICAL_FAILURE and weapon:
            critical_text = self._handle_player_critical_failure(weapon)

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

        exchange = self._exchange(
            [
                monster_action.get("attack", monster_action.get("desc", "攻击")),
                monster_text,
            ],
            [action_reply, player_text, critical_text],
        )

        return (roll_desc, exchange, self._end_turn())

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
        # 临时生命（护盾）优先抵扣
        if self.temp_hp > 0:
            absorbed = min(self.temp_hp, actual_damage)
            self.temp_hp -= absorbed
            actual_damage -= absorbed
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
        self._advance_turn()
        self.current_turn = "mon" if self.current_turn == "inv" else "inv"
        return self._get_next_turn_prompt()

    def _check_combat_over(self) -> Optional[str]:
        if self.hp_record["inv"] <= 0:
            self.investigator.is_survive = False
            self.investigator.save()
            header = self._t("battle.death_text", name=self.player_name)
            detail = f"{self._settlement()}\n\n{self._t('battle.death_hint')}"
            self.end_parts = (header, detail)
            return f"{header}\n\n{detail}"
        if self.hp_record["mon"] <= 0:
            return self._handle_victory()
        return None

    def _victory_parts(self) -> tuple[str, str]:
        """胜利消息拆分为（标题+结局, 侦查/战利品/成长/结算明细）。"""
        search_skill = self.investigator.get_skill("侦查", 25)
        search_roll = DiceRoll(search_skill)
        self.get_success_record_description(search_roll.level, "侦查")
        search_desc = self._check_section(
            "侦查",
            [self._check_row(self.player_name, "侦查", search_roll.dice, search_roll.skill, search_roll.level)],
        )

        bonus_text = ""
        if search_roll.level > SuccessLevel.FAILURE:
            gold, dropped_item, bonus_text = self.monster.generate_loot()
            add_gold(self.investigator.qq, gold)
            if dropped_item:
                self.investigator.add_item_to_inventory(dropped_item.id, 1)
        else:
            bonus_text = self._quote(self._get_reply("侦查失败"))

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

        # 研读法术残卷：智力检定，成功学会（消耗残卷），失败扣 SAN（残卷保留）
        learn_lines = self._learn_scrolls()
        if learn_lines:
            self.investigator.save()

        # 缓存结构化结算数据（供图片卡片渲染）
        self.end_card_ext = {
            "search": {
                "dice": search_roll.dice,
                "target": search_roll.skill,
                "level": search_roll.level,
                "passed": search_roll.level > SuccessLevel.FAILURE,
            },
            "bonus": bonus_text,
            "growth": growth_lines,
            "learn": learn_lines,
        }

        ending = getattr(self.monster, "结局", self._t("battle.monster_dead"))
        header = f"{self._t('battle.victory_title')}\n\n{ending}"
        detail_parts = [f"{search_desc}\n{bonus_text}"]
        if learn_lines:
            detail_parts.append(
                f"{report_section(self._t('spell.learn_title'))}\n"
                + "\n".join(learn_lines)
            )
        if growth_lines:
            detail_parts.append(
                f"{self._t('battle.victory_growth')}\n" + "\n".join(growth_lines)
            )
        detail_parts.append(self._settlement())
        return header, "\n\n".join(detail_parts)

    def _learn_scrolls(self) -> list[str]:
        """研读背包中的法术残卷（逐个智力检定）。返回学习结果行。"""
        t = self._t
        inv = self.investigator
        from ..models.player import investigator_repo

        learn_lines: list[str] = []
        equipments, _ = inv.get_equipments()
        for scroll_id in equipments:
            item = Equipment(scroll_id)
            spell_id = item.spell
            if (
                not item.is_valid
                or item.type != "spell_scroll"
                or not spell_id
                or inv.has_spell(spell_id)
            ):
                continue
            spell = data_loader.spell_data.get(spell_id)
            if not spell:
                continue
            skill_name = spell.get("learn_skill", "智力")
            skill_val = max(1, inv.get_skill(skill_name, 0))
            roll = DiceRoll(skill_val)
            icon = get_success_icon(roll.level)
            level = get_success_description(roll.level)
            row = t(
                "spell.learn_row",
                icon=icon,
                dice=roll.dice,
                target=roll.skill,
                result=t(
                    "report.check_result",
                    icon=icon,
                    level=level,
                ),
            )
            learn_lines.append(f" {row}")
            if roll.level > SuccessLevel.FAILURE:
                inv.add_spell(spell_id)
                investigator_repo.remove_item_from_inventory(
                    inv.qq, scroll_id, 1
                )
                learn_lines.append(
                    "  " + t("spell.learn_ok", name=spell["name"])
                )
            else:
                learn_san = int(spell.get("learn_san", 2))
                san = max(0, inv.get_skill("san", 0) - learn_san)
                inv.set_skill("san", san)
                learn_lines.append("  " + t("spell.learn_fail", san=learn_san))
        return learn_lines

    def _handle_victory(self) -> str:
        header, detail = self._victory_parts()
        self.end_parts = (header, detail)
        return f"{header}\n\n{detail}"

    def get_end_card_data(self) -> dict:
        """结算卡片数据（图片模式渲染用）。"""
        inv = self.investigator
        max_san = inv.get_skill("意志") or inv.get_skill("san", 0)
        return {
            "fled": self.fled,
            "victory": self.hp_record["mon"] <= 0,
            "ending": getattr(self.monster, "结局", self._t("battle.monster_dead")),
            "hp": self.hp_record["inv"],
            "max_hp": inv.get_max_hp(),
            "san": inv.get_skill("san", 0),
            "max_san": max_san,
            "day": inv.day,
        }

    def fight_is_over(self) -> bool:
        return (
            self.fled
            or self.hp_record["inv"] <= 0
            or self.hp_record["mon"] <= 0
        )
