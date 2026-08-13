"""BattleService · 状态管理：战斗全部可变状态与环境修正。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Optional

from ...models.item import Equipment

if TYPE_CHECKING:
    from ...models.monster import Monster
    from ...models.player import Investigator


class BattleBaseMixin:
    def __init__(self, investigator: Investigator, monster: Monster) -> None:
        self.investigator = investigator
        self.monster = monster
        self.player_name = investigator.name
        self.hp_record = {"inv": investigator.hp, "mon": monster.hp}
        self.current_turn: Literal["inv", "mon"] = "inv"
        self.current_action = "格斗"
        # 战斗发生时的天数（胜利结算 day+1 后仍显示本场战斗的天数）
        self._battle_day = investigator.day

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
        self._mp_hint_shown = False
        # 怪物临时生命（AI 怪物施法护盾先抵伤害）
        self.monster_temp_hp = 0

        # 骨哨助战剩余回合（506 消耗品，怪物行动前额外 1d4 伤害）
        self.bone_whistle = 0

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
        self.madness_total = duration if is_madness else 0

    def _get_player_modified_skill(self, skill_name: str, default: int = 0) -> int:
        base = self.investigator.get_skill(skill_name, default)
        player_mods = self.environment.get("玩家", {})
        if skill_name in player_mods:
            base += player_mods[skill_name]
        # 「射击」为枪械通用修正，作用于手枪/步枪两类鉴定技能
        elif skill_name in ("手枪", "步枪") and "射击" in player_mods:
            base += player_mods["射击"]
        return max(0, base)

    def _get_monster_attack_skill(self, monster_action: dict) -> int:
        """怪物攻击技能：攻击表技能 + 环境反击技能/格斗修正；远程行动吃「射击」修正。

        AI 怪物处于「闪避模式」且玩家攻击回合时，怪物改用闪避技能防御
        （闪避 99 + 环境闪避修正）。
        """
        mods = self.environment.get("怪物", {})
        if (
            getattr(self.monster, "is_dodging", False)
            and self.current_turn == "inv"
        ):
            return int(getattr(self.monster, "_ai_dodge", 99)) + mods.get(
                "闪避", 0
            )
        skill = (
            monster_action["skill"]
            + mods.get("反击技能", 0)
            + mods.get("格斗", 0)
        )
        if monster_action.get("type") == "ranged":
            skill += mods.get("射击", 0)
        return skill

    def _get_monster_modified(self, attr: str, default: int = 0) -> int:
        base = getattr(self.monster, attr, default)
        monster_mods = self.environment.get("怪物", {})
        # 数据键「敏捷」与代码属性 dex 互为别名
        if attr == "dex" and "敏捷" in monster_mods:
            attr = "敏捷"
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
