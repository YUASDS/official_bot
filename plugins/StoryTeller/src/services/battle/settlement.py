"""BattleService · 胜利结算：侦查检定、战利品、成长鉴定、研读法术残卷、结算数据。"""

from __future__ import annotations

from database.db import add_gold

from ...models.item import Equipment
from ...utils.md_format import report_quote, report_section
from ..data_loader import data_loader
from ..dice_roller import (
    DiceRoll,
    SuccessLevel,
    get_success_description,
    get_success_icon,
    roll_dice,
)
from ..ending_engine import (
    first_kill_drop,
    mark_mirror_defeated,
    on_battle_40_end,
    register_relic_obtained,
    render_door_choice,
)


class BattleSettlementMixin:
    def get_success_record_description(self, rank: int, skill_name: str = "") -> str:
        if rank > SuccessLevel.FAILURE and skill_name:
            self.succeded_skill.add(skill_name)
        return get_success_description(rank)

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
            "day": self._battle_day,
        }

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
        if self.monster.id == "38":
            # 隐藏挑战「启」：固定 100 乌帕 + 双物品 501/508（独立于侦查检定）
            gold = 100
            add_gold(self.investigator.qq, gold)
            item_names: list[str] = []
            for item_id in ("501", "508"):
                self.investigator.add_item_to_inventory(item_id, 1)
                register_relic_obtained(self.investigator, item_id)
                item_names.append(Equipment(item_id).name)
            bonus_text = self._t(
                "battle.qiren_reward",
                items="、".join(item_names),
                gold=gold,
            )
            # 纪念道具「启的怀表」：首杀必掉，已持有不重复掉落（纯收藏，零数值）
            trophy_id = "509"
            equipments, _ = self.investigator.get_equipments()
            if equipments.get(trophy_id, 0) <= 0:
                self.investigator.add_item_to_inventory(trophy_id, 1)
                trophy_line = self._t(
                    "battle.qiren_trophy",
                    name=Equipment(trophy_id).name,
                )
                bonus_text = f"{bonus_text}\n{trophy_line}"
        elif search_roll.level > SuccessLevel.FAILURE:
            gold, dropped_item, bonus_text = self.monster.generate_loot(self._battle_day)
            add_gold(self.investigator.qq, gold)
            if dropped_item:
                self.investigator.add_item_to_inventory(dropped_item.id, 1)
                register_relic_obtained(self.investigator, dropped_item.id)
                if dropped_item.type == "spell_scroll":
                    # 首次获得残卷：提示研读途径（胜利结算会自动研读）
                    bonus_text += f"\n> {self._t('spell.learn_hint')}"
        else:
            loot_lines = [self._get_reply("侦查失败")]
            half_gold = self.monster.half_loot_gold()
            if half_gold > 0:
                add_gold(self.investigator.qq, half_gold)
                loot_lines.append(self._t("battle.loot_half", gold=half_gold))
            bonus_text = report_quote(loot_lines)

        # 首杀必掉信物（第 5 章：独立于侦查检定，登记 items_first）
        first_relic = first_kill_drop(self.investigator, self.monster.id)
        if first_relic:
            self.investigator.add_item_to_inventory(first_relic, 1)
            first_line = data_loader.get_text(
                "battle.first_kill_relic",
                default="🎖 首杀必掉信物：{name}（已计入收集）",
                name=Equipment(first_relic).name,
            )
            bonus_text = (
                f"{bonus_text}\n{self._quote(first_line)}"
                if bonus_text
                else self._quote(first_line)
            )

        # E04 前置：击败镜中之人（37）→ 置位 mirror_defeated（门扉 H_mirror 隐藏分支条件）
        if self.monster.id == "37":
            mark_mirror_defeated(self.investigator)

        # 隐藏幸运检定：成功回复 1d3 SAN（检定过程不展示，失败无任何提示）
        luck_line = ""
        luck_roll = DiceRoll(max(1, self.investigator.get_skill("幸运", 0)))
        if luck_roll.level > SuccessLevel.FAILURE:
            _expr, gain = roll_dice("1d3")
            self.investigator.set_skill(
                "san", max(0, self.investigator.get_skill("san", 0) + gain)
            )
            luck_line = self._t("battle.victory_luck", value=gain)

        self.investigator.hp = self.hp_record["inv"]

        # 出口①：第 40 天胜利 → 冻结 day（不再 +1）并进入门扉抉择
        door_result = None
        if self._battle_day == 40:
            door_result = on_battle_40_end(self.investigator, win=True)
            self.door_choice = door_result
        else:
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
            "luck": luck_line,
        }

        ending = getattr(self.monster, "结局", self._t("battle.monster_dead"))
        header = f"{self._t('battle.victory_title')}\n\n{ending}"
        detail_parts = [f"{search_desc}\n{bonus_text}"]
        if luck_line:
            detail_parts.append(luck_line)
        if learn_lines:
            detail_parts.append(
                f"{report_section(self._t('spell.learn_title'))}\n"
                + "\n".join(learn_lines)
            )
        if growth_lines:
            detail_parts.append(
                f"{self._t('battle.victory_growth')}\n" + "\n".join(growth_lines)
            )
        # 门扉抉择（第 40 天胜利）：追加选项文本，按钮由调用方渲染
        if door_result and door_result.get("door_choice"):
            door_render = render_door_choice(self.investigator)
            door_result["choices"] = door_render["choices"]
            detail_parts.append(door_render["text"])
        detail_parts.append(self._settlement())
        return header, "\n\n".join(detail_parts)

    def _learn_scrolls(self) -> list[str]:
        """研读背包中的法术残卷（逐个智力检定）。返回学习结果行。"""
        t = self._t
        inv = self.investigator
        from ...models.player import investigator_repo

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
