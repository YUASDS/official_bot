from __future__ import annotations

import random
from typing import Any, Optional

from loguru import logger

from ..services.data_loader import data_loader


class MonsterRepository:
    """Repository for loading and querying monster data."""
    _instance = None
    _monster_data: dict[str, Any] = {}
    _checkpoint_data: dict[str, list[str]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._monster_data = data_loader.monster_data
            cls._instance._checkpoint_data = data_loader.check_point
            logger.info(f"Monster data loaded: {len(cls._instance._monster_data)} monsters.")
        return cls._instance

    def find_by_id(self, monster_id: str) -> Optional[dict[str, Any]]:
        """Find raw monster data by ID."""
        return self._monster_data.get(monster_id)

    def find_random_id_for_day(
        self, day: int | str, weights: dict | None = None
    ) -> Optional[str]:
        """Select a random monster ID based on the day (using check_point data).

        weights: {monster_id: 权重} 可选——按权重重复入池后随机（周目联动用，如
        被猎犬杀死过的调查员猎犬权重 ×2）。缺省不传 = 现状均匀随机，零行为变化。
        """
        day_str = str(day)
        available_monsters = self._checkpoint_data.get(day_str)
        if not available_monsters:
            logger.warning(f"No monster configuration found for day {day_str}.")
            if self._checkpoint_data:
                valid_keys = [int(k) for k in self._checkpoint_data if k.isdigit()]
                if valid_keys:
                    max_day = max(valid_keys)
                    fallback_pool = self._checkpoint_data.get(str(max_day), [])
                    if fallback_pool:
                        selected = random.choice(fallback_pool)
                        logger.warning(f"Falling back to day {max_day} pool, selected {selected}.")
                        return selected
            return None

        if not weights:
            return random.choice(available_monsters)
        pool: list[str] = []
        for mid in available_monsters:
            count = int(weights.get(mid, 1))
            pool.extend([mid] * max(1, count))
        return random.choice(pool)

monster_repo = MonsterRepository()

class Monster:
    """Represents a monster entity."""
    def __init__(self, monster_id: str) -> None:
        self.id = monster_id
        self._data = monster_repo.find_by_id(monster_id)
        if not self._data:
            raise ValueError(f"Monster ID '{monster_id}' not found in data.")

        self.is_valid = True
        self.name = self._data.get("名字", data_loader.get_text("monster.unknown"))
        self.hp = self._data.get("hp", 10)
        self.max_hp = self.hp
        self.san_loss = self._data.get("理智值丧失", "0/0")
        self.description = self._data.get("出场", data_loader.get_text("monster.default_intro"))
        # 伤害展示与战斗结算同源：优先攻击表聚合，其次顶层 damage，最后默认 1d3
        self.damage_dice = self._data.get("damage") or " / ".join(
            self._attack_damage_list()
        ) or "1d3"
        self.dex = self._data.get("敏捷", 50)
        self.str = self._data.get("力量", 50)
        self.fight = self._data.get("fight", 50)
        self.armor = int(self._data.get("装甲", 0))
        self.is_alive = True
        self.敏捷 = self.dex
        self.名字 = self.name

        # AI 状态机（仅含 `ai` 字段的怪物启用；老怪物零回归）
        self._ai_data = self._data.get("ai")
        self._ai_phase = "开火" if self._ai_data else None
        self._shots_fired = 0
        self._ai_pending_spell = None
        self._ai_dodging = False
        self._ai_dodge = 99
        # 变身一次性状态：_ai_transformed 标记已变身（防反复触发）；_ai_transform_text 变身瞬间文案
        self._ai_transformed = False
        self._ai_transform_text = None
        if self._ai_data:
            self._ai_dodge = int(
                (self._ai_data.get("受伤后") or {}).get("闪避", 99)
            )
        # 怪物法术资源：MP = 意志//5（缺省 0，老怪物不受影响）
        self.max_mp = int(self._data.get("意志", 0)) // 5 if self._ai_data else 0
        self.mp = self.max_mp

    def _attack_damage_list(self) -> list[str]:
        """攻击表所有伤害骰（去重保序），与战斗结算使用同一字段。"""
        damages: list[str] = []
        for action in (self._data.get("攻击") or {}).values():
            dmg = action.get("damage")
            if dmg and str(dmg) not in damages:
                damages.append(str(dmg))
        return damages

    def damage_preview(self) -> str:
        """怪物基础伤害骰集合（如 `1d6 / 1d4+1`），可贯穿的攻击标注「可贯穿」。"""
        seen: dict[str, bool] = {}
        for action in (self._data.get("攻击") or {}).values():
            dmg = action.get("damage")
            if dmg:
                seen[str(dmg)] = seen.get(str(dmg), False) or bool(action.get("ex"))
        if not seen:
            return self.damage_dice
        return " / ".join(
            f"{d}（可贯穿）" if ex else d for d, ex in seen.items()
        )

    def __getattr__(self, name):
        if name.startswith("_") or name in self.__dict__:
            raise AttributeError(name)
        return self._data.get(name, "")

    def get_action(self, turn: str) -> dict[str, Any]:
        """获取怪物在此回合的行动详情（整场战斗固定一次，保证技能/文本/伤害一致）。"""
        if self._data.get("ai"):
            return self.get_ai_action(turn)
        t = data_loader.get_text
        if getattr(self, "_battle_action", None) is not None:
            return self._battle_action
        attack_options = self._data.get("攻击", {})
        if not attack_options:
            self._battle_action = {
                "skill": 50,
                "damage": "1d3",
                "desc": t("monster.default_action"),
                "counterattack": t("monster.default_action"),
            }
            return self._battle_action

        chosen_action_key = random.choice(list(attack_options.keys()))
        action = attack_options[chosen_action_key]
        if "counterattack" not in action:
            action["counterattack"] = action.get("desc", t("monster.default_action"))
        if "attack" not in action:
            action["attack"] = action.get("desc", t("monster.default_action"))
        if "attack_succ" not in action:
            action["attack_succ"] = action.get("desc", t("monster.default_action"))
        if "attack_false" not in action:
            action["attack_false"] = action.get("counterattack", t("monster.default_action"))
        self._battle_action = action
        return action

    # --- AI 状态机（仅 ai 怪物，`get_ai_action` 纯读、`advance_ai` 副作用） ---
    def get_ai_action(self, turn: str) -> dict[str, Any]:
        """AI 怪物当前阶段行动（纯读，不做副作用）：按阶段返回 melee/ranged/spell。"""
        if self._ai_pending_spell is not None:
            spell = data_loader.spell_data.get(self._ai_pending_spell, {})
            return {
                "type": "spell",
                "spell": self._ai_pending_spell,
                "name": spell.get("name", self._ai_pending_spell),
                "des": spell.get("des", ""),
            }
        if self._ai_phase == "开火":
            return self._ai_ranged_action()
        # 受伤后阶段可配置独立「反击型」行动（ai.受伤后.近战），缺省回退 近战（38 号行为不变）
        if self._ai_phase == "受伤后":
            injured_melee = (self._ai_data.get("受伤后") or {}).get("近战")
            if injured_melee:
                return self._ensure_ai_fields(dict(injured_melee))
        return self._ai_melee_action()

    def advance_ai(self) -> None:
        """副作用：怪物行动真实结算前推进 AI 状态（每次怪物回合调用一次）。

        受伤优先（打断开火剩余喷子）→ 预取待施法术；开火按次数推进并切换近战。
        施法回合结束后由 complete_spell_turn 清除待施法术并进入闪避模式。

        受伤触发阈值（可选）：`ai.受伤后.阈值`（0~1，缺省 = 任意受伤即触发，38 号行为不变）；
        变身瞬间文案（可选）：`ai.受伤后.变身文本`，首次进入受伤后阶段写入 `_ai_transform_text`，
        由战斗引擎读取后一次性展示（读后清空）。
        """
        if self._ai_data is None:
            return
        if self._ai_pending_spell is not None:
            return  # 本轮为施法回合，不推进攻击次数
        injured = self._ai_data.get("受伤后") or {}
        limit = self.max_hp
        if injured.get("阈值"):
            try:
                limit = int(self.max_hp * float(injured["阈值"]))
            except (TypeError, ValueError):
                limit = self.max_hp
        if self.hp < limit and not self._ai_dodging:
            if self._ai_transformed:
                return
            self._ai_transformed = True
            spells = injured.get("法术", [])
            self._ai_pending_spell = random.choice(spells) if spells else None
            self._ai_phase = "受伤后"
            self._ai_transform_text = injured.get("变身文本")
            return
        if self._ai_phase == "开火":
            self._shots_fired += 1
            if self._shots_fired >= int(self._ai_data["开火"].get("次数", 2)):
                self._ai_phase = "近战"

    def complete_spell_turn(self) -> None:
        """施法回合结算：清除待施法术，永久进入闪避模式。"""
        if self._ai_data is None:
            return
        self._ai_pending_spell = None
        self._ai_dodging = True
        self._ai_phase = "受伤后"

    @property
    def is_dodging(self) -> bool:
        """AI 怪物是否处于「闪避模式」：玩家攻击回合不反击伤害，改为闪避。"""
        return bool(getattr(self, "_ai_dodging", False))

    def _ai_ranged_action(self) -> dict[str, Any]:
        """开火行动：喷子双发文案从怪物条目 `玩家文案` 读取（玩家视角原文）。"""
        action = dict(self._ai_data["开火"])
        player_texts = self._data.get("玩家文案", {})
        if isinstance(player_texts, dict):
            if not action.get("attack_succ"):
                action["attack_succ"] = player_texts.get("射击成功", "")
            if not action.get("attack_crit"):
                action["attack_crit"] = player_texts.get("射击大成功", "")
            if not action.get("attack_false"):
                action["attack_false"] = player_texts.get("射击失败", "")
            if not action.get("attack_fumble"):
                action["attack_fumble"] = player_texts.get("射击大失败", "")
            if not action.get("counterattack"):
                action["counterattack"] = player_texts.get(
                    "射击失败", action.get("attack", "")
                )
        return self._ensure_ai_fields(action)

    def _ai_melee_action(self) -> dict[str, Any]:
        return self._ensure_ai_fields(dict(self._ai_data["近战"]))

    def _ensure_ai_fields(self, action: dict[str, Any]) -> dict[str, Any]:
        """补齐 AI 行动缺失的标准文案字段（不修改源数据，返回副本）。"""
        t = data_loader.get_text
        action = dict(action)
        if not action.get("desc"):
            action["desc"] = action.get("动作", t("monster.default_action"))
        if not action.get("attack"):
            action["attack"] = action.get("desc", t("monster.default_action"))
        if not action.get("attack_succ"):
            action["attack_succ"] = action.get("attack", t("monster.default_action"))
        if not action.get("attack_false"):
            action["attack_false"] = action.get(
                "counterattack", t("monster.default_action")
            )
        if not action.get("counterattack"):
            action["counterattack"] = action.get("attack", t("monster.default_action"))
        return action

    def generate_loot(self, day: int = 1):
        """Generate loot for this monster. Returns (gold, dropped_item, message).

        day 用于金币下限：随天数成长 max(5, day/2)~乌帕上限。
        """
        t = data_loader.get_text
        reward_data = self._data.get("奖励", {})
        gold_max = reward_data.get("乌帕", 10)
        low = max(5, day // 2)
        gold = random.randint(min(low, gold_max), gold_max)

        items = reward_data.get("物品", [])
        dropped_item = None
        message = ""
        if items:
            item_id = random.choice(items)
            from .item import Equipment
            dropped_item = Equipment(item_id)
            if dropped_item.is_valid:
                message = t(
                    "monster.loot_item",
                    item=dropped_item.name,
                    brief=dropped_item.get_brief_description(),
                    gold=gold,
                )
            else:
                message = t("monster.loot_gold", gold=gold)
        else:
            message = t("monster.loot_gold", gold=gold)

        return gold, dropped_item, message

    def half_loot_gold(self) -> int:
        """侦查失败保底：怪物基础乌帕的一半（向下取整）。"""
        reward_data = self._data.get("奖励", {})
        gold_max = reward_data.get("乌帕", 10)
        return int(gold_max) // 2

    def take_damage(self, amount: int) -> int:
        """Apply damage to monster."""
        prev_hp = self.hp
        self.hp -= amount
        if self.hp <= 0:
            self.hp = 0
            self.is_alive = False
        return prev_hp - self.hp

    def get_attack_description(self) -> str:
        return f"{self.name} attacks with {self.damage_dice} damage!"

    @classmethod
    def load_random_for_day(cls, day: int) -> Optional[Monster]:
        monster_id = monster_repo.find_random_id_for_day(day)
        if not monster_id:
            keys = list(monster_repo._monster_data.keys())
            if keys:
                return cls(random.choice(keys))
            return None
        try:
            return cls(monster_id)
        except ValueError as e:
            logger.warning(f"Monster '{monster_id}' not found, using random fallback: {e}")
            keys = list(monster_repo._monster_data.keys())
            if keys:
                return cls(random.choice(keys))
            return None

    @property
    def attack_dice(self) -> str:
        return self.damage_dice

    @property
    def intro(self) -> str:
        return self.description
