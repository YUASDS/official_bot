"""BattleService · 法术：施法校验、对抗检定、效果应用。"""

from __future__ import annotations

from ..data_loader import data_loader
from ..dice_roller import ConfrontationRoll, roll_dice
from ...utils.md_format import report_quote, report_section


class BattleSpellsMixin:
    def _cast_spell(self, spell_id: str) -> tuple:
        """@description 释放法术：校验失败（未学会/无MP/SAN不足）不消耗回合；对抗失败仍结算代价。"""
        t = self._t
        spells = self.investigator.get_spells()
        if not spell_id:
            # 裸「施法」：给出格式与已学法术（或获取途径）
            hint = t("spell.no_id")
            if spells:
                known = "、".join(
                    f"{data_loader.spell_data.get(sid, {}).get('name', sid)}"
                    f"（施法{sid}）"
                    for sid in spells
                )
                hint += f"\n> 已学会：{known}"
            else:
                hint += f"\n> {t('spell.learn_hint')}"
            return (hint,)
        if not self.investigator.has_spell(spell_id):
            return (
                f"{t('spell.not_learned')}\n> {t('spell.learn_hint')}",
            )
        spell = data_loader.spell_data.get(spell_id)
        if not spell:
            return (t("spell.not_learned"),)

        mp_cost = int(spell.get("mp_cost", 1))
        san_cost = int(spell.get("san_cost", 0))
        if self.mp < mp_cost:
            return (t("spell.no_mp", mp=self.mp, max_mp=self.max_mp),)
        if self.investigator.get_skill("san", 0) < san_cost:
            return (t("spell.no_san"),)

        # 扣除资源（无论成败）
        self.mp -= mp_cost
        if san_cost:
            san = max(0, self.investigator.get_skill("san", 0) - san_cost)
            self.investigator.set_skill("san", san)
        # 统计二期：法术施放计数（资源已消耗即记一次）
        self._stat_spells[spell_id] = self._stat_spells.get(spell_id, 0) + 1
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

        if etype == "damage":
            expr, val = roll_dice(dice)
            monster_text = self._apply_damage_to_monster(val, dmg_type="magic")
            return f"{expr}={val}，造成 {val} 点伤害", monster_text
        if etype == "heal":
            expr, val = roll_dice(dice)
            max_hp = self.investigator.get_max_hp()
            before = self.hp_record["inv"]
            self.hp_record["inv"] = min(max_hp, before + val)
            healed = self.hp_record["inv"] - before
            return t("spell.heal_self", value=healed), ""
        if etype == "temp_hp":
            expr, val = roll_dice(dice)
            self.temp_hp += val
            return t("spell.temp_gain", value=val), ""
        if etype == "dot":
            # 持续伤害：登记对怪 dot，每回合初 tick（回合数耗尽清除）
            turns = int(effect.get("回合", 3))
            tick_text = (
                effect.get("tick_text")
                or spell.get("回复")
                or data_loader.get_text("battle.dot_tick", default="")
            )
            self._dot_on_monster = {
                "dice": dice,
                "剩余": max(1, turns),
                "tick_text": tick_text,
            }
            return (
                data_loader.get_text(
                    "battle.dot_apply",
                    default="诅咒缠上怪物，它将在每回合初承受 {dice} 点持续伤害（{回合} 回合）。",
                    dice=dice,
                    回合=turns,
                ),
                "",
            )
        if etype == "属性增减":
            return self._apply_attr_change(spell, effect)
        if etype == "san":
            # 玩家对怪无效：怪无 san，validator 或文案兜底，不做玩家 san 自伤
            return t("spell.san_no_effect"), ""
        return "", ""

    def _apply_attr_change(self, spell: dict, effect: dict) -> tuple[str, str]:
        """属性增减：目标=自身 → 玩家 buff；目标=怪物 → 对怪 debuff（战斗内临时修正）。"""
        target = effect.get("目标", "自身")
        attr = effect.get("属性", "")
        dice = effect.get("骰子") or effect.get("dice", "1d3")
        _expr, val = roll_dice(dice)
        if target == "怪物":
            self._apply_monster_attr_mod(attr, -val)
            text = (
                effect.get("文案")
                or spell.get("回复")
                or data_loader.get_text(
                    "battle.attr_debuff_mon",
                    default="「{属性}」被压制，怪物的攻势被削弱了 {值} 点。",
                    属性=attr,
                    值=val,
                )
            )
            return text, ""
        self._apply_player_attr_mod(attr, val)
        text = (
            effect.get("文案")
            or spell.get("回复")
            or data_loader.get_text(
                "battle.attr_buff",
                default="「{属性}」在你体内涌动，临时提升 {值} 点。",
                属性=attr,
                值=val,
            )
        )
        return text, ""
