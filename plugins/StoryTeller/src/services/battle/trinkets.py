"""BattleService · 饰品触发：集中式 dispatcher（BattleTrinketMixin）。

设计对齐 `.qa/plans/trinket-system-eval.md`：
- 集中式收口挂点（damage.py 两处 / engine.py 濒死 + 回合边界），效果只写战斗局部状态
  （hp_record / temp_hp / san / monster_temp_hp），不碰 progress / knowledge / 信物 / is_survive。
- 每次触发现读 `investigator.get_equipped_id("饰品")`（支持战斗中换装，不缓存快照）。
- 条件：概率 / 伤害>= / 生命比例 / 每场次数 / 每回合；未声明冷却的饰品默认每回合 1 次
  （防骨哨 / 连射高频触发刷收益）。
- 结算顺序由调用点保证：饰品减伤在护甲前（`_apply_damage_to_player` 入口）、
  伤害增幅在 dream_buff 后护盾前（`_apply_damage_to_monster` 内）。
- GM 房间 / 乱入（is_gm_room=True）：濒死类不触发（engine.py 濒死挂点短路守卫）；
  受击 / 造成伤害 / 回合类生效（期望行为）。
"""

from __future__ import annotations

import random

from ...models.item import Equipment
from ..dice_roller import roll_dice

# 饰品槽位键（直接匹配 equipped_items 字典键；action2part 未映射，get_equipped_id 回落字面量）
_TRINKET_SLOTS = ("饰品", "饰品2")


class BattleTrinketMixin:
    def _trigger_trinkets(
        self,
        trigger: str,
        ctx: dict | None = None,
        defer_heal: bool = False,
    ) -> list[str]:
        """集中式饰品触发器：读已装备饰品 → 按 触发/条件/概率/冷却 过滤 → 应用效果。

        trigger：时机枚举（进入战斗/回合开始/回合结束/造成伤害/受击/受到伤害/濒死/...）。
        ctx：可变上下文（如 {"damage": n}），reduce / damage_bonus 效果可改写后由调用点读回。
        defer_heal=True 时 heal/temp_hp 效果暂存 ctx["_deferred"]，由调用点扣血后 flush
        （受击回血在扣血后执行，先扣血看是否濒死再回血）。
        返回触发叙述行（空列表 = 无触发，对现有行为逐字节等价）。
        """
        ctx = ctx or {}
        lines: list[str] = []
        if getattr(self, "no_trinket", False):
            return lines
        for slot in _TRINKET_SLOTS:
            item_id = self.investigator.get_equipped_id(slot)
            if not item_id:
                continue
            item = Equipment(item_id)
            if not item.is_valid:
                continue
            eff = item._data.get("effect") or {}
            if not eff or eff.get("触发") != trigger:
                continue
            if not self._trinket_matches(item_id, eff, ctx):
                continue
            lines.extend(
                self._trinket_apply(item_id, item, eff, ctx, defer_heal=defer_heal)
            )
        return lines

    # --- 条件 / 概率 / 冷却求值 ---
    def _trinket_state(self, item_id: str) -> dict:
        """战斗内饰品冷却/计数状态（随 BattleService 销毁自动清零，天然防跨场无限触发）。"""
        if not hasattr(self, "trinket_state"):
            self.trinket_state = {}
        return self.trinket_state.setdefault(item_id, {})

    def _trinket_matches(self, item_id: str, eff: dict, ctx: dict) -> bool:
        """全部条件满足才触发；冷却封顶防无限触发。"""
        cond = eff.get("条件") or {}
        state = self._trinket_state(item_id)

        prob = cond.get("概率")
        if prob is not None and random.random() >= prob:
            return False

        raw_damage = ctx.get("damage", 0)
        if "伤害>=" in cond and raw_damage < cond["伤害>="]:
            return False
        if "伤害>" in cond and raw_damage <= cond["伤害>"]:
            return False
        if "伤害<" in cond and raw_damage >= cond["伤害<"]:
            return False

        max_hp = self.investigator.get_max_hp() or 1
        ratio = max(0, self.hp_record["inv"]) / max_hp
        if "生命<" in cond and ratio >= cond["生命<"]:
            return False
        if "生命>" in cond and ratio <= cond["生命>"]:
            return False

        per_battle = cond.get("每场次数")
        if per_battle is not None and state.get("每场次数", 0) >= per_battle:
            return False

        per_turn = cond.get("每回合")
        if per_turn is None and per_battle is None:
            per_turn = 1  # 未声明冷却的饰品默认每回合 1 次（防高频触发刷收益）
        if per_turn is not None:
            current_turn = getattr(self, "_turn_counter", 0)
            if state.get("turn") != current_turn:
                state["turn"] = current_turn
                state["回合计数"] = 0
            if state.get("回合计数", 0) >= per_turn:
                return False
        return True

    # --- 效果应用 ---
    def _trinket_apply(
        self,
        item_id: str,
        item: Equipment,
        eff: dict,
        ctx: dict,
        defer_heal: bool = False,
    ) -> list[str]:
        fx = eff.get("效果") or {}
        etype = fx.get("type", "")
        state = self._trinket_state(item_id)
        state["每场次数"] = state.get("每场次数", 0) + 1
        state["回合计数"] = state.get("回合计数", 0) + 1

        if etype in ("heal", "temp_hp"):
            if defer_heal:
                ctx.setdefault("_deferred", []).append((item, eff, fx))
                return []
            return [self._trinket_apply_heal_temp(item, eff, fx)]

        if etype == "reduce":
            expr, val = self._trinket_fx_value(fx)
            ctx["damage"] = max(0, ctx.get("damage", 0) - val)
            return [self._trinket_render(eff, expr, val, name=item.name)]

        if etype == "damage_bonus":
            expr, val = self._trinket_fx_value(fx)
            ctx["damage"] = ctx.get("damage", 0) + val
            return [self._trinket_render(eff, expr, val, name=item.name)]

        if etype == "san":
            expr, val = self._trinket_fx_value(fx)
            san = max(0, self.investigator.get_skill("san", 0) + val)
            self.investigator.set_skill("san", san)
            return [self._trinket_render(eff, expr, val, name=item.name)]

        if etype == "shield":
            expr, val = self._trinket_fx_value(fx)
            self.monster_temp_hp = getattr(self, "monster_temp_hp", 0) + val
            return [self._trinket_render(eff, expr, val, name=item.name)]

        if etype == "immune_death":
            expr, val = self._trinket_fx_value(fx, "回血", "1d4")
            max_hp = self.investigator.get_max_hp()
            before = self.hp_record["inv"]
            self.hp_record["inv"] = max(1, min(max_hp, before + val))
            healed = self.hp_record["inv"] - max(0, before)
            self.trinket_death_saved = True
            line = self._trinket_render(
                eff, expr, val, {"数值": healed}, name=item.name
            )
            if "数值" not in (eff.get("文案") or ""):
                line = f"{line}\n> （HP +{healed}）"
            if fx.get("消耗"):
                self.investigator.break_equipped_item(item.part)
                self.investigator.update_equipment()
            return [line]

        if etype == "append_attack":
            expr, val = self._trinket_fx_value(fx, "dice", "1d3")
            dmg_text = self._apply_damage_to_monster(val)
            line = self._trinket_render(eff, expr, val, name=item.name)
            if dmg_text:
                line = f"{line}\n{dmg_text}"
            return [line]

        return []

    def _trinket_flush_deferred(self, ctx: dict) -> list[str]:
        """扣血后执行延后的回血/临时生命类饰品效果，返回其叙述行。"""
        lines: list[str] = []
        for item, eff, fx in ctx.pop("_deferred", []):
            lines.append(self._trinket_apply_heal_temp(item, eff, fx))
        return lines

    def _trinket_apply_heal_temp(self, item: Equipment, eff: dict, fx: dict) -> str:
        """heal / temp_hp 效果统一执行（对齐凝胶/法术回血同式：min(max_hp, before+val)）。"""
        if fx.get("type") == "heal":
            expr, val = self._trinket_fx_value(fx, "dice", "1d3")
            max_hp = self.investigator.get_max_hp()
            before = self.hp_record["inv"]
            self.hp_record["inv"] = min(max_hp, before + val)
            healed = self.hp_record["inv"] - before
            return self._trinket_render(
                eff, expr, val, {"数值": healed}, name=item.name
            )
        expr, val = self._trinket_fx_value(fx, "dice", None)
        self.temp_hp = getattr(self, "temp_hp", 0) + val
        return self._trinket_render(eff, expr, val, name=item.name)

    def _trinket_fx_value(
        self, fx: dict, key: str = "dice", default_dice: str | None = None
    ) -> tuple[str, int]:
        """效果数值取值：优先骰子表达式（掷骰），否则用「值」字段。返回 (表达式, 总值)。"""
        dice = fx.get(key) or (
            default_dice if default_dice is not None else fx.get("值")
        )
        if isinstance(dice, (int, float)):
            return str(int(dice)), int(dice)
        if dice:
            expr, val = roll_dice(str(dice))
            return expr, val
        val = int(fx.get("值", 0) or 0)
        return str(val), val

    def _trinket_render(
        self,
        eff: dict,
        expr: str,
        val: int,
        extra: dict | None = None,
        name: str = "饰品",
    ) -> str:
        """渲染触发文案：支持 $骰子/$数值/$饰品 占位；缺失时回退通用文案。"""
        text = eff.get("文案") or self._t("trinket.generic")
        data = {"骰子": expr, "数值": val, "饰品": name}
        if extra:
            data.update(extra)
        try:
            return text.format(**data)
        except (KeyError, IndexError, ValueError):
            return text
