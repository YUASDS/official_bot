"""BattleService · 伤害结算：玩家/怪物两套伤害管线（护甲、临时生命、伤害分级文案）。"""

from __future__ import annotations


# 伤害标签 → 怪物数据键（免疫/抗性 用「物理/魔法」，标签用 physical/magic）
_DMG_TAG_TO_CN = {"physical": "物理", "magic": "魔法"}


class BattleDamageMixin:
    def _apply_damage_to_player(
        self, damage: int, armor_absorbed: bool = False, dmg_type: str = "physical"
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
        # 饰品触发：致死预判（护甲减伤 + 临时生命抵扣后、扣血前——判断本次伤害是否即将致死）
        # 与 602 古神断指（濒死已发生 → _check_combat_over 拦截死亡判定）不同：此挂点是伤害预判型，
        # 即将致死（HP 将 ≤0）时掷概率，成功则本次伤害免疫（扣血值归 0）。效果只写战斗局部状态。
        # GM 房间 / 乱入（is_gm_room=True）按濒死同款隔离不触发（沙盒内不跑终局类饰品）。
        if self.hp_record["inv"] - actual_damage <= 0 and not getattr(
            self, "is_gm_room", False
        ):
            lethal_ctx: dict = {"damage": actual_damage}
            lethal_lines = self._trigger_trinkets("致死预判", lethal_ctx)
            if lethal_ctx.get("block_lethal"):
                actual_damage = 0
            trinket_lines += lethal_lines
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

    def _monster_immune_cn(self, dmg_type: str) -> str:
        """命中怪物的伤害标签对应的免疫键（「物理」/「魔法」），无对应返回空串。"""
        return _DMG_TAG_TO_CN.get(dmg_type, "")

    def _monster_resist_rate(self, dmg_type: str) -> float:
        """怪物对该标签的抗性比例（0~1），无配置返回 0。"""
        cn = self._monster_immune_cn(dmg_type)
        if not cn:
            return 0.0
        rate = self.monster.抗性.get(cn, 0)
        try:
            return float(rate)
        except (TypeError, ValueError):
            return 0.0

    def _immunity_text(self, dmg_type: str) -> str:
        """免疫专属文案（怪物「免疫文案」字段缺省回退模板键 battle.immunity_default）。"""
        cn = self._monster_immune_cn(dmg_type)
        text = ""
        if cn:
            text = self.monster.免疫文案.get(cn, "")
        return text or self._t("battle.immunity_default")

    def _apply_damage_to_monster(self, damage: int, dmg_type: str = "physical") -> str:
        # 免疫优先（0 伤 + 专属文案）：先于抗性，避免双重判定歧义
        if self._monster_immune_cn(dmg_type) in self.monster.免疫:
            return self._immunity_text(dmg_type)
        # 抗性：百分比减伤（val = int(val*(1-rate))，向下取整），叠加装甲（装甲已在调用点平扣）
        rate = self._monster_resist_rate(dmg_type)
        if rate:
            damage = int(damage * (1 - rate))
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
