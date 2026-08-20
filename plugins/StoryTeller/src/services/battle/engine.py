"""BattleService · 回合引擎：行动分发、回合推进、战斗结束判定、疯狂失控结算。"""

from __future__ import annotations

from loguru import logger
import random
from typing import Optional

from ..data_loader import data_loader
from ..dice_roller import roll_dice
from ..ending_engine import (
    on_battle_40_end,
    register_e07,
    render_door_choice,
)
from ...models.player import ending_repo

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
        """@description 玩家可用行动：装备技能 + 已学法术（施法<ID>）+ 急救（供按钮/命令直达）。"""
        actions = self.investigator.get_available_actions()
        spell_actions = {f"施法{sid}" for sid in self.investigator.get_spells()}
        actions["inv"] = sorted(set(actions["inv"]) | spell_actions | {"急救"})
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
        result = "\n\n---\n\n".join(p for p in parts if p)
        # 饰品触发：进入战斗 / 回合开始（开场 buff 型饰品挂点）
        trinket_lines = self._trigger_trinkets("进入战斗") + self._trigger_trinkets(
            "回合开始"
        )
        if trinket_lines:
            result = "\n\n".join(trinket_lines) + "\n\n" + result
        return result

    def execute_action(self, action: str) -> tuple:
        self.current_action = action
        self.last_actor = self.current_turn  # 记录行动发起者（卡片标题用）
        if self.is_madness and self.madness_duration > 0:
            return self._resolve_madness()
        # 行动合法性校验（玩家回合）：仅允许当前可用行动，防命令直输越权
        if self.current_turn == "inv" and not action.startswith(("施法", "使用")):
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
        if action.startswith("使用"):
            # 消耗品动作：/行动 使用505（回复 HP/临时）、使用506（骨哨助战）
            return self._use_item_action(action)
        action_handlers = {
            "格斗": self._melee_attack,
            "射击": lambda: self._ranged_attack(1),
            "二连射": lambda: self._ranged_attack(2),
            "三连射": lambda: self._ranged_attack(3),
            "换弹": self._reload_weapon,
            "逃跑": self._flee,
            "急救": self._first_aid,
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
        parts: list[str] = []
        # 条件胜利（坚守战）：怪物行动前检查——坚守已达成则怪物停手（防「坚持住却被补刀」）
        cond_msg = self._check_conditional_victory()
        if cond_msg:
            return (cond_msg,)
        # 周目联动：被猎犬杀死过的调查员首次遭遇猎犬时，它迟疑一回合（不攻击，轮到玩家）
        if getattr(self, "hound_hesitates", False):
            self.hound_hesitates = False
            self.investigator.set_flag("past.hound_hesitated", True)
            self.investigator.save()
            parts.append(
                data_loader.get_text(
                    "legacy.hound_hesitate",
                    default="它看着你，像在确认某个旧账。獠牙悬在半空，没有落下。",
                )
            )
            parts.append(self._end_turn())
            return tuple(parts)
        # 助战（伙伴/骨哨二选一）：怪物行动前额外伤害（伙伴优先于 506 骨哨）
        if getattr(self, "companion", None):
            comp = self.companion
            comp["剩余"] = comp.get("剩余", int(comp["回合数"]))
            _expr, val = roll_dice(comp["每回合"])
            val = max(0, val - self.monster.armor)  # 装甲减伤与 calc_dmg 一致
            self._apply_damage_to_monster(val)
            comp["剩余"] -= 1
            try:
                comp_text = comp["文案"].format(
                    damage=val, remaining=comp["剩余"]
                )
            except (KeyError, IndexError, ValueError) as e:
                logger.warning(f"静默异常[KeyError/IndexError/ValueError] in _execute_monster_action: {e}")
                comp_text = comp.get("文案", "")
            parts.append(comp_text)
            if comp["剩余"] <= 0:
                self.companion = None
            if self.hp_record["mon"] <= 0:
                over = self._check_combat_over()
                if over:
                    parts.append(over)
                return tuple(parts)
        elif getattr(self, "bone_whistle", 0) > 0:
            self.bone_whistle -= 1
            # 骨哨每回合伤害读 goods_data 506 use_effect.damage（配置驱动，缺省回退 2d4 现状）
            _cfg = (data_loader.goods_data or {}).get("506", {}).get("use_effect") or {}
            _dmg = str(_cfg.get("damage", "2d4"))
            _expr, val = roll_dice(_dmg)
            val = max(0, val - self.monster.armor)  # 装甲减伤与 calc_dmg 一致
            self._apply_damage_to_monster(val, dmg_type="magic")
            parts.append(
                data_loader.get_text(
                    "battle.bone_whistle_tick",
                    default="🦴 骨哨猎犬撕咬怪物，造成 {damage} 点伤害（剩余 {remaining} 回合）。",
                    damage=val,
                    remaining=self.bone_whistle,
                )
            )
            if self.hp_record["mon"] <= 0:
                over = self._check_combat_over()
                if over:
                    parts.append(over)
                return tuple(parts)
        # AI 怪物：回合前推进状态（一次性），若为施法回合则自动施法并结束回合
        if getattr(self.monster, "_ai_data", None) is not None:
            self.monster.advance_ai()
            # 变身瞬间文案：首次进入受伤后阶段一次性展示（读后清空）
            transform_text = getattr(self.monster, "_ai_transform_text", None)
            if transform_text:
                self.monster._ai_transform_text = None
                parts.append(transform_text)
            ai_action = self.monster.get_ai_action("mon")
            if ai_action.get("type") == "spell":
                parts.extend(self._execute_monster_spell(ai_action))
                self.monster.complete_spell_turn()
                parts.append(self._end_turn())
                return tuple(parts)
        if action in ("反击", "闪避"):
            parts.extend(self._handle_defensive_action(action))
            return tuple(parts)
        return (
            self._t(
                "battle.unknown_defense_action",
                action=action,
                actions="反击、闪避",
            ),
        )

    def _execute_monster_spell(self, monster_action: dict) -> list[str]:
        """AI 怪物回合法术：释放预取法术（愈合术/肉体守护），应用效果并展示专属文案。"""
        t = self._t
        spell_id = monster_action.get("spell", "")
        spell = data_loader.spell_data.get(spell_id, {})
        name = spell.get("name", spell_id)

        spell_texts = self.monster.法术文案
        cast_text = (
            spell_texts.get(spell_id, "") if isinstance(spell_texts, dict) else ""
        ) or spell.get("des", "")

        # 消耗怪物 MP（意志//5）
        mp_cost = int(spell.get("mp_cost", 1))
        if getattr(self.monster, "mp", 0) is not None:
            self.monster.mp = max(0, self.monster.mp - mp_cost)

        effect = spell.get("effect", {})
        etype = effect.get("type", "damage")
        dice = effect.get("dice", "1d3")
        expr, val = roll_dice(dice)
        if etype == "heal":
            before = self.hp_record["mon"]
            self.hp_record["mon"] = min(self.monster.max_hp, before + val)
            self.monster.hp = self.hp_record["mon"]
            healed = self.hp_record["mon"] - before
            effect_line = t(
                "battle.monster_spell_heal", expr=expr, val=val, value=healed
            )
        elif etype == "temp_hp":
            self.monster_temp_hp += val
            effect_line = t(
                "battle.monster_spell_shield", expr=expr, val=val, value=val
            )
        elif etype == "dot":
            # 持续伤害：怪物对玩家施加 dot，每回合初 tick（回合数耗尽清除）
            turns = int(effect.get("回合", 3))
            tick_text = (
                effect.get("tick_text")
                or spell.get("回复")
                or data_loader.get_text("battle.dot_tick", default="")
            )
            self._dot_on_player = {
                "dice": dice,
                "剩余": max(1, turns),
                "tick_text": tick_text,
            }
            effect_line = data_loader.get_text(
                "battle.dot_apply_inv",
                default="诅咒缠上你，你将在每回合初承受 {dice} 点持续伤害（{回合} 回合）。",
                dice=dice,
                回合=turns,
            )
        elif etype == "属性增减":
            effect_line = self._apply_monster_attr_spell(spell, effect)
        elif etype == "san":
            # san 伤害：扣玩家理智（走现有 set_skill 修改路径，下限 0）
            san_before = self.investigator.get_skill("san", 0)
            loss = min(val, san_before)
            self.investigator.set_skill("san", max(0, san_before - loss))
            effect_line = t(
                "battle.monster_spell_san", expr=expr, val=val, value=loss
            )
        else:
            # damage 型法术：怪物施放对玩家造成实际伤害（魔法标签），走玩家伤害管道
            effect_line = self._apply_damage_to_player(val, dmg_type="magic")
        lines = [t("battle.monster_spell_title", name=name), cast_text, effect_line]
        return ["\n".join(x for x in lines if x)]

    def _apply_monster_attr_spell(self, spell: dict, effect: dict) -> str:
        """怪物施法 属性增减：目标=自身 → 怪物 buff；目标=玩家 → 玩家 debuff（临时修正）。"""
        target = effect.get("目标", "自身")
        attr = effect.get("属性", "")
        dice = effect.get("骰子") or effect.get("dice", "1d3")
        _expr, val = roll_dice(dice)
        if target == "玩家":
            self._apply_player_attr_mod(attr, -val)
            return (
                effect.get("文案")
                or spell.get("回复")
                or data_loader.get_text(
                    "battle.attr_debuff",
                    default="你感到「{属性}」被压制，临时降低 {值} 点。",
                    属性=attr,
                    值=val,
                )
            )
        self._apply_monster_attr_mod(attr, val)
        return (
            effect.get("文案")
            or spell.get("回复")
            or data_loader.get_text(
                "battle.attr_buff_mon",
                default="「{属性}」在怪物体内涌动，临时提升 {值} 点。",
                属性=attr,
                值=val,
            )
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
                self.current_action = random_action  # 疯狂随机行动同步 current_action，防武器判定读到残留动作
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
        # 饰品触发：回合结束（行动后、战斗继续时）
        turn_end_lines = self._trigger_trinkets("回合结束")
        self._advance_turn()
        self.current_turn = "mon" if self.current_turn == "inv" else "inv"
        # 持续伤害（批次4）：回合开始挂点——新回合为目标回合时结算 dot tick。
        # 对齐骨哨每回合伤害模式：玩家对怪 dot 在怪物回合初、怪对玩家 dot 在玩家回合初。
        dot_lines = self._apply_dot_ticks()
        if dot_lines:
            after = self._check_combat_over()
            if after:
                return "\n\n".join(dot_lines + [after])
        prompt = self._get_next_turn_prompt()
        # 濒死免死等挂点暂存的触发文案并入本回合提示
        pending = getattr(self, "_trinket_report", [])
        if pending:
            self._trinket_report = []
        if pending or turn_end_lines or dot_lines:
            prompt = (
                "\n\n".join((pending or []) + turn_end_lines + dot_lines)
                + "\n\n"
                + prompt
            )
        return prompt

    def _apply_dot_ticks(self) -> list[str]:
        """持续伤害结算（回合开始挂点）：新回合为目标回合时 tick 一次，回合数耗尽清除。

        玩家对怪 dot（_dot_on_monster）在怪物回合初结算、怪对玩家 dot（_dot_on_player）
        在玩家回合初结算；双方独立记录互不影响。tick 文案按 tick_text → 法术回复 →
        battle.dot_tick 模板缺省回退。
        """
        lines: list[str] = []
        dot = (
            self._dot_on_monster
            if self.current_turn == "mon"
            else self._dot_on_player
        )
        if not dot:
            return lines
        expr, val = roll_dice(dot["dice"])
        tick_text = dot.get("tick_text") or data_loader.get_text(
            "battle.dot_tick", default="持续伤害发作：{expr}={val}。"
        )
        try:
            line = tick_text.format(expr=expr, val=val, damage=val)
        except (KeyError, IndexError, ValueError) as e:
            logger.warning(f"静默异常[KeyError/IndexError/ValueError] in _apply_dot_ticks: {e}")
            line = tick_text
        if self.current_turn == "mon":
            self._apply_damage_to_monster(val, dmg_type="magic")
        else:
            self._apply_damage_to_player(val, dmg_type="magic")
        lines.append(line)
        dot["剩余"] -= 1
        if dot["剩余"] <= 0:
            if self.current_turn == "mon":
                self._dot_on_monster = None
            else:
                self._dot_on_player = None
        return lines

    def _check_conditional_victory(self) -> Optional[str]:
        """坚守战条件胜利：每回合开始检查 `_turn_counter >= N 且玩家存活` → 条件胜利。

        达成后标记 conditional_won，走现有胜利收尾（GM 房间隔离 / 普通战斗结算），
        怪物不再攻击（防「坚持住却被补刀」）。文案用 胜利条件.文案 缺省回退 battle.hold_win。
        """
        if not getattr(self, "_win_condition", None) or self._conditional_won:
            return None
        if self.hp_record["inv"] <= 0:
            return None
        try:
            need = int(self._win_condition.get("回合", 0))
        except (TypeError, ValueError) as e:
            logger.warning(f"静默异常[TypeError/ValueError] in _check_conditional_victory: {e}")
            need = 0
        if self._turn_counter < need:
            return None
        self._conditional_won = True
        text = self._win_condition.get("文案") or self._t("battle.hold_win")
        self.hold_win_text = text
        self._restore_absorb_snapshot()
        self._log_battle("win")
        if getattr(self, "is_gm_room", False):
            # flow 战斗（隔离）：结束分支由 flow_engine 按 胜利 配置发放效果/回复/下一步
            header = f"{self._t('battle.victory_title')}\n\n{text}"
            self.end_parts = (header, "")
            return header
        return self._handle_victory()

    def _check_combat_over(self) -> Optional[str]:
        if self.hp_record["inv"] <= 0:
            # 饰品触发：濒死免死（挂点最顶、判定生效前；GM 房间/乱入隔离不触发）
            if not getattr(self, "is_gm_room", False):
                self.trinket_death_saved = False
                trinket_lines = self._trigger_trinkets("濒死")
                if getattr(self, "trinket_death_saved", False):
                    # 免死成功：战斗继续，跳过 GM/day40/E07/E05 判定（进度/结局零改动）
                    self._trinket_report = trinket_lines
                    return None
            if getattr(self, "is_gm_room", False):
                # GM 房间·战败隔离：不落 is_survive、不登记 E07、不碰 SAN/进度，
                # 仅标记战败由 gm_room 接管（GM 复活 / 空手退出）。
                self.gm_room_defeated = True
                self._restore_absorb_snapshot()
                header = data_loader.get_text(
                    "gm_room_v2.battle_defeat",
                    default="守卫的最后一击将你击倒在地，眼前一阵发黑。",
                )
                self.end_parts = (header, "")
                self._log_battle("death")
                return header
            if self._battle_day == 40:
                # 出口①：第 40 天战败走 1.3 分支（501 复活 → 门扉抉择 / 无 501 → E05）
                msg = self._handle_day40_defeat()
                door = getattr(self, "door_choice", None) or {}
                if door.get("revived_dead_once"):
                    self._log_battle("revived")
                else:
                    self._log_battle("death")
                    # 无 501 战败终局（E05）补周目快照（此时 battle_logs 已写）
                    if door.get("ended"):
                        self._snapshot_run_ending(
                            str(door.get("ending", "E05")),
                            str(door.get("variant") or ""),
                        )
                return msg
            # 周目联动：被 32 廷达洛斯之猎犬杀死（非逃跑）→ 记录跨周目行为
            if getattr(self.monster, "id", "") == "32":
                self._record_run_choice("hound", "killed_by")
            self.investigator.is_survive = False
            self._restore_absorb_snapshot()
            self.investigator.save()
            header = self._t("battle.death_text", name=self.player_name)
            detail = (
                f"{self._settlement()}\n\n"
                f"> {self._t('battle.death_ending')}\n\n"
                f"{self._t('battle.death_hint')}"
            )
            # 出口③：非 day40 死亡登记 E07 墓园拾骨（未持 501 时）
            e07_text = register_e07(self.investigator)
            if e07_text:
                detail = f"{e07_text}\n\n{detail}"
            self.end_parts = (header, detail)
            self._log_battle("death")
            return f"{header}\n\n{detail}"
        if self.hp_record["mon"] <= 0:
            if getattr(self, "is_gm_room", False):
                # GM 房间·胜利隔离：不走掉落/成长/day+1/门扉/SAN 回复，
                # 奖励由 gm_room 状态机按分支发放。
                self.gm_room_victory = True
                self._restore_absorb_snapshot()
                ending = getattr(self.monster, "结局", self._t("battle.monster_dead"))
                header = f"{self._t('battle.victory_title')}\n\n{ending}"
                self.end_parts = (header, "")
                self._log_battle("win")
                return header
            # 周目联动：击杀 32 猎犬 / 击败 37 镜中之人 → 记录跨周目行为
            mid = getattr(self.monster, "id", "")
            if mid == "32":
                self._record_run_choice("hound", "killed")
            elif mid == "37":
                self._record_run_choice("mirror", "defeated")
            msg = self._handle_victory()
            self._log_battle("win")
            return msg
        # 条件胜利（坚守战）：坚守满 N 回合且玩家存活 → 条件胜利（怪物不再攻击）。
        # 置于玩家死亡 / 怪物死亡判定之后：玩家被击杀仍走死亡路径，怪物已死走正常胜利。
        cond_msg = self._check_conditional_victory()
        if cond_msg:
            return cond_msg
        return None

    def _record_run_choice(self, key: str, value) -> None:
        """周目联动写入点：跨周目行为记录（幂等首遇优先，写后不理防破坏战斗）。"""
        try:
            ending_repo.record_run_choice(self.investigator.qq, key, value)
        except Exception as e:  # noqa: BLE001 - 记录失败不影响战斗
            logger.warning(f"静默异常[Exception] in _record_run_choice: {e}")
            pass

    def _handle_day40_defeat(self) -> str:
        """第 40 天战败（1.3）：持 501 自动复活并进入门扉抉择；无 501 直接 E05。"""
        inv = self.investigator
        inv.is_survive = False
        self._restore_absorb_snapshot()
        inv.save()
        result = on_battle_40_end(inv, win=False)
        self.door_choice = result
        if result.get("ended"):
            header = self._t("battle.death_text", name=self.player_name)
            detail = result["message"]
            self.end_parts = (header, detail)
            return f"{header}\n\n{detail}"
        # 复活成功 → 门扉抉择（含「重赴门前/驻足旁观」）
        door_render = render_door_choice(inv)
        result["choices"] = door_render["choices"]
        revive_title = data_loader.get_text(
            "battle.day40_revive_title", default="🌌 灯焰摇曳"
        )
        revive_text = data_loader.get_text(
            "battle.day40_revive",
            default="你倒下的一瞬，怀中古圣者的遗愿燃起微光，将你从门缝中拉了回来。",
        )
        header = revive_title
        detail = f"{self._quote(revive_text)}\n\n{door_render['text']}"
        self.end_parts = (header, detail)
        return f"{header}\n\n{detail}"

    def fight_is_over(self) -> bool:
        return (
            self.fled
            or self.hp_record["inv"] <= 0
            or self.hp_record["mon"] <= 0
            or getattr(self, "_conditional_won", False)
        )
