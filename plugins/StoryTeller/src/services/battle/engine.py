"""BattleService · 回合引擎：行动分发、回合推进、战斗结束判定、疯狂失控结算。"""

from __future__ import annotations

import random
from typing import Optional

# 失控自动结算的迭代上限（保险丝，正常每回合 2 步内必推进）
_MAX_MADNESS_STEPS = 100


class BattleEngineMixin:
    def roll_initiative(self) -> None:
        """按敏捷值直接决定先手（不掷骰）：敏捷高者先行动，平局玩家先手。"""
        self._advance_turn()
        player_dex = self._get_player_modified_skill("敏捷")
        monster_dex = self._get_monster_modified("dex", 50)
        self.current_turn = "inv" if player_dex >= monster_dex else "mon"

    def _player_actions(self) -> dict[str, list[str]]:
        """@description 玩家可用行动：装备技能 + 已学法术（施法<ID> 供按钮/命令直达）。"""
        actions = self.investigator.get_available_actions()
        spell_actions = {f"施法{sid}" for sid in self.investigator.get_spells()}
        actions["inv"] = sorted(set(actions["inv"]) | spell_actions)
        return actions

    def get_available_actions_for_turn(self) -> list[str]:
        """@description 当前回合可用行动列表（供按钮展示）。"""
        if not hasattr(self, "available_actions"):
            self.available_actions = self._player_actions()
        return self.available_actions.get(self.current_turn, [])

    def start_turn(self) -> str:
        """开场战报：状态表 + 濒危一刻 + 行动抉择。"""
        self.roll_initiative()
        parts = [self.get_status_table(), self.get_danger_section(), self.get_action_section()]
        return "\n\n---\n\n".join(p for p in parts if p)

    def execute_action(self, action: str) -> tuple:
        self.current_action = action
        if self.is_madness and self.madness_duration > 0:
            return self._resolve_madness()
        # 行动合法性校验（玩家回合）：仅允许当前可用行动，防命令直输越权
        if self.current_turn == "inv" and not action.startswith("施法"):
            available = self.get_available_actions_for_turn()
            if available and action not in available:
                return (
                    self._t(
                        "battle.unknown_player_action",
                        action=action,
                        actions="、".join(available),
                    ),
                )
        handler = {
            "inv": self._execute_player_action,
            "mon": self._execute_monster_action,
        }.get(self.current_turn)
        if handler:
            return handler(action)
        return (self._t("battle.error_state"),)

    def _execute_player_action(self, action: str) -> tuple:
        if action.startswith("施法"):
            # 容忍「施法701 枯萎术（MP2）」这类带名称后缀的输入，取首个 token 为法术 ID
            spell_id = action.removeprefix("施法").strip().split(" ", 1)[0].strip()
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
        available = self.get_available_actions_for_turn()
        return (
            self._t(
                "battle.unknown_player_action",
                action=action,
                actions="、".join(available),
            ),
        )

    def _execute_monster_action(self, action: str) -> tuple:
        if action in ("反击", "闪避"):
            return self._handle_defensive_action(action)
        return (
            self._t(
                "battle.unknown_defense_action",
                action=action,
                actions="反击、闪避",
            ),
        )

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
                remaining = self.madness_duration
                self.madness_duration -= 1
                available = self.investigator.get_available_actions().get("inv", [])
                random_action = random.choice(available) if available else "格斗"
                msg = self._t("battle.madness_action", action=random_action)
                if remaining > 1:
                    msg += self._t("adventure.madness_remaining", count=remaining - 1)
                elif remaining == 1:
                    msg += self._t("adventure.madness_last")
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
            detail = (
                f"{self._settlement()}\n\n"
                f"> {self._t('battle.death_ending')}\n\n"
                f"{self._t('battle.death_hint')}"
            )
            self.end_parts = (header, detail)
            return f"{header}\n\n{detail}"
        if self.hp_record["mon"] <= 0:
            return self._handle_victory()
        return None

    def fight_is_over(self) -> bool:
        return (
            self.fled
            or self.hp_record["inv"] <= 0
            or self.hp_record["mon"] <= 0
        )
