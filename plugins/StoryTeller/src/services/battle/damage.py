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
        # 怪物装甲已在 calc_dmg 调用点按 armor=monster.armor 平扣，此处不再重复减伤
        actual_damage = max(0, damage)
        # 怪物临时生命（护盾）优先抵扣
        if self.monster_temp_hp > 0:
            absorbed = min(self.monster_temp_hp, actual_damage)
            self.monster_temp_hp -= absorbed
            actual_damage -= absorbed
        self.hp_record["mon"] = max(0, self.hp_record["mon"] - actual_damage)
        # 同步怪物实例 HP（AI 受伤检测 / 怪物施法依赖）
        self.monster.hp = self.hp_record["mon"]

        if actual_damage > initial_hp / 2:
            return getattr(self.monster, "高伤害", self._t("monster.high_damage"))
        if actual_damage < 2:
            return getattr(self.monster, "低伤害", self._t("monster.low_damage"))
        return getattr(self.monster, "正常伤害", self._t("monster.normal_damage"))
