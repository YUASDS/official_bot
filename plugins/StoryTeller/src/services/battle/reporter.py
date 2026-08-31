"""BattleService · 战报渲染：把战斗状态渲染为 md 小节（纯读，不修改状态）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..data_loader import data_loader
from ..dice_roller import (
    SuccessLevel,
    get_success_description,
    get_success_icon,
)
from ...utils.md_format import report_check_table, report_quote, report_section

if TYPE_CHECKING:
    from ...models.item import Equipment


class BattleReporterMixin:
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

    def get_status_table(self) -> str:
        """当前回合行 + 双方状态表格（SAN / HP）。"""
        t = self._t
        owner = (
            t("battle.your_turn")
            if self.current_turn == "inv"
            else t("battle.monster_turn")
        )
        inv = self.investigator
        rows = [
            t(
                "report.status_inv_mini",
                icon=t("report.icon_inv"),
                name=self.player_name,
                san=inv.get_skill("san", 0),
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
        """@description 【行动·抉择】行动列表小节：攻击类一行，其余逐个列出，法术附名称与MP。"""
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
            if a.startswith("施法"):
                continue
            lines.append(t("report.action", action=a))
        lines.extend(self._spell_action_lines(actions))
        return "\n".join(lines)

    def _spell_action_lines(self, actions: list[str]) -> list[str]:
        """@description 当前回合可用法术的行动提示行：`施法<ID> 名称（MP消耗）`。"""
        t = self._t
        lines = []
        for action in actions:
            if not action.startswith("施法"):
                continue
            sid = action.removeprefix("施法")
            spell = data_loader.spell_data.get(sid) or {}
            name = spell.get("name", sid)
            mp = spell.get("mp_cost", 1)
            lines.append(t("report.action", action=f"施法{sid} {name}（MP{mp}）"))
        return lines

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
        rows = [
            t(
                "report.status_inv_mini",
                icon=t("report.icon_inv"),
                name=self.player_name,
                san=inv.get_skill("san", 0),
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
                value=f"{inv.get_skill('san', 0)}",
            ),
            t(
                "battle.settle_row",
                label=t("battle.settle_day"),
                value=str(self._battle_day),
            ),
        ]
        return f"{report_section(t('battle.settle_title'))}\n" + "\n".join(rows)

    def _get_next_turn_prompt(self) -> str:
        self.available_actions = self._player_actions()
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
        # 坚守战剩余回合提示：玩家行动前、剩余回合 ≤ 2 时展示 battle.hold_remaining
        hold_hint = self._hold_remaining_hint()
        if hold_hint:
            lines.append(hold_hint)
        if self.gun:
            lines.append(
                t("battle.ammo_label", bullet=self.bullet, max_bullet=self.max_bullet)
            )
        if self.max_mp > 0:
            mp_line = f"**{t('spell.mp_label')}：{self.mp}/{self.max_mp}**"
            if not getattr(self, "_mp_hint_shown", False):
                self._mp_hint_shown = True
                mp_line += f" {t('spell.mp_source')}"
            lines.append(mp_line)
        if self.temp_hp > 0:
            lines.append(f"**{t('spell.temp_hp_label')}：{self.temp_hp}**")
        lines.append(self.get_action_section())
        return "\n\n".join(lines)
