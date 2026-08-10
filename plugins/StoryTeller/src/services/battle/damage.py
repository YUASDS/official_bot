"""BattleService · 伤害结算：玩家/怪物两套伤害管线（护甲、临时生命、伤害分级文案）。"""

from __future__ import annotations


class BattleDamageMixin:
    def _apply_damage_to_player(
        self, damage: int, armor_absorbed: bool = False
    ) -> str:
        if damage <= 0:
            return self._get_reply("低伤害")

        initial_hp = self.hp_record["inv"]
        actual_damage = damage
        if not armor_absorbed:
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
