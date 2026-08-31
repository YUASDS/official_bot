"""BattleService · 伤害结算：玩家/怪物两套伤害管线（护甲、临时生命、伤害分级文案）。"""

from __future__ import annotations

from loguru import logger
from ..dice_roller import roll_dice

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
        except (TypeError, ValueError) as e:
            logger.warning(f"静默异常[TypeError/ValueError] in _monster_resist_rate: {e}")
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

    # --- 攻击吸收（批次2）：命中检定成功后不走伤害路径，改走吸收结算 ---
    def _apply_absorb(self, monster_action: dict) -> tuple[str, str]:
        """怪物攻击吸收结算：按类型分派，返回 (怪物文案, 玩家文案)。

        - hp：玩家扣血 = 怪物回血（扣血不走护甲减伤，直接扣 hp 记录）
        - 护盾：玩家 temp_hp 转移为怪 monster_temp_hp（转移量 = min(骰值, temp_hp)；
          玩家无护盾落空，伤害 0 无额外惩罚）
        - 属性：扣玩家属性（默认战斗内临时，战斗结束恢复；持久=永久扣减 + 警示）
        """
        absorb = monster_action.get("吸收")
        if not isinstance(absorb, dict):
            return "", ""
        a_type = absorb.get("类型")
        _expr, val = roll_dice(str(absorb.get("骰子", "1d1")))
        custom = absorb.get("文案") or ""
        if a_type == "hp":
            return self._absorb_hp(val, custom)
        if a_type == "护盾":
            return self._absorb_shield(val, custom)
        if a_type == "属性":
            return self._absorb_attr(absorb, val, custom)
        return "", ""

    def _absorb_hp(self, val: int, custom: str) -> tuple[str, str]:
        """吸血：玩家扣血（护甲不防，直接扣 hp）＝ 怪物回血（封顶 max_hp）。"""
        before = self.hp_record["inv"]
        self.hp_record["inv"] = max(0, before - val)
        actual = before - self.hp_record["inv"]
        self._stat_dmg_taken = getattr(self, "_stat_dmg_taken", 0) + actual
        healed = self.monster.hp
        self.monster.hp = min(self.monster.max_hp, healed + val)
        self.hp_record["mon"] = self.monster.hp
        absorbed = self.hp_record["mon"] - healed
        monster_text = custom or self._t("battle.absorb_hp", 值=absorbed)
        if actual > before / 2:
            player_text = self._get_reply("高伤害")
        elif actual < 2:
            player_text = self._get_reply("低伤害")
        else:
            player_text = self._get_reply("正常伤害")
        return monster_text, player_text

    def _absorb_shield(self, val: int, custom: str) -> tuple[str, str]:
        """护盾吞噬：玩家 temp_hp 转移为怪 monster_temp_hp；无护盾落空无惩罚。"""
        if self.temp_hp <= 0:
            # 无护盾：吸收落空（专属文案描述成功吸收，落空一律用落空模板）
            return self._t("battle.absorb_shield_miss"), ""
        transfer = min(val, self.temp_hp)
        self.temp_hp -= transfer
        self.monster_temp_hp += transfer
        monster_text = custom or self._t("battle.absorb_shield", 值=transfer)
        return monster_text, ""

    def _absorb_attr(self, absorb: dict, val: int, custom: str) -> tuple[str, str]:
        """属性吸收：默认战斗内临时（战斗结束恢复）；持久=true 永久扣减 + 警示文案。"""
        target = absorb.get("目标", "")
        if target not in self._absorb_snapshot:
            # 兜底：目标不在快照白名单（非法配置绕过校验），不结算吸收
            return custom or self._t("battle.absorb_attr", 属性=target, 值=0), ""
        permanent = bool(absorb.get("持久", False))
        current = self.investigator.get_skill(target, 0)
        deducted = min(val, current)
        self.investigator.set_skill(target, current - deducted)
        if permanent:
            # 累计持久扣减（供临时恢复时保留持久结果）
            self._absorb_perm_delta[target] = (
                self._absorb_perm_delta.get(target, 0) + deducted
            )
        else:
            self._absorb_modified.add(target)
        monster_text = custom or self._t("battle.absorb_attr", 属性=target, 值=deducted)
        if permanent:
            monster_text = (
                f"{monster_text}\n"
                f"{self._t('battle.absorb_attr_perm', 属性=target, 值=deducted)}"
            )
        return monster_text, ""
