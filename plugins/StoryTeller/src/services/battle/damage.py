"""BattleService · 伤害结算：玩家/怪物两套伤害管线（护甲、临时生命、伤害分级文案）。"""

from __future__ import annotations


class BattleDamageMixin:
    def _apply_damage_to_player(
        self, damage: int, armor_absorbed: bool = False
    ) -> str:
        if damage <= 0:
            return self._get_reply("低伤害")

        # 饰品触发（入口、护甲前）：reduce 即时改 damage；heal/temp_hp 暂存扣血后执行
        ctx: dict = {"damage": damage}
        trinket_lines = self._trigger_trinkets("受击", ctx, defer_heal=True)
        trinket_lines += self._trigger_trinkets("受到伤害", ctx, defer_heal=True)
        damage = ctx["damage"]

        initial_hp = self.hp_record["inv"]
        actual_damage = damage
        if not armor_absorbed:
            armor = self.investigator.get_armor_value()
            actual_damage = max(0, damage - armor)
            # 统计二期：护甲吸收（此处自行减甲的场景，如大失败自伤/逃跑失败）
            self._stat_armor_absorbed = (
                getattr(self, "_stat_armor_absorbed", 0) + min(damage, armor)
            )
        # 临时生命（护盾）优先抵扣
        if self.temp_hp > 0:
            absorbed = min(self.temp_hp, actual_damage)
            self.temp_hp -= absorbed
            actual_damage -= absorbed
        self.hp_record["inv"] = max(0, self.hp_record["inv"] - actual_damage)
        # 统计二期：实际承受伤害
        self._stat_dmg_taken = (
            getattr(self, "_stat_dmg_taken", 0) + actual_damage
        )

        # 扣血后：受击回血在扣血后执行（先扣血看是否濒死，再回血）
        trinket_lines += self._trinket_flush_deferred(ctx)

        if actual_damage > initial_hp / 2:
            reply = self._get_reply("高伤害")
        elif actual_damage < 2:
            reply = self._get_reply("低伤害")
        else:
            reply = self._get_reply("正常伤害")
        if trinket_lines:
            reply = f"{reply}\n\n" + "\n".join(trinket_lines)
        return reply

    def _apply_damage_to_monster(self, damage: int) -> str:
        if damage <= 0:
            return getattr(self.monster, "低伤害", self._t("battle.no_damage"))

        initial_hp = self.hp_record["mon"]
        # 梦之碎片余韵：玩家伤害翻倍（含骨哨助战等玩家侧伤害）
        if getattr(self, "dream_buff", False):
            damage *= 2
        # 饰品触发（dream_buff 后、护盾前）：damage_bonus 加伤害；append_attack 追加攻击
        ctx: dict = {"damage": damage}
        trinket_lines = self._trigger_trinkets("造成伤害", ctx)
        damage = ctx["damage"]
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
        # 统计二期：造成伤害
        self._stat_dmg_dealt = (
            getattr(self, "_stat_dmg_dealt", 0) + actual_damage
        )

        if actual_damage > initial_hp / 2:
            reply = getattr(self.monster, "高伤害", self._t("monster.high_damage"))
        elif actual_damage < 2:
            reply = getattr(self.monster, "低伤害", self._t("monster.low_damage"))
        else:
            reply = getattr(self.monster, "正常伤害", self._t("monster.normal_damage"))
        if trinket_lines:
            reply = f"{reply}\n\n" + "\n".join(trinket_lines)
        return reply
