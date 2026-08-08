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

    def find_random_id_for_day(self, day: int | str) -> Optional[str]:
        """Select a random monster ID based on the day (using check_point data)."""
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

        return random.choice(available_monsters)

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
        self.damage_dice = self._data.get("damage", "1d3")
        self.dex = self._data.get("敏捷", 50)
        self.str = self._data.get("力量", 50)
        self.fight = self._data.get("fight", 50)
        self.armor = int(self._data.get("装甲", 0))
        self.is_alive = True
        self.敏捷 = self.dex
        self.名字 = self.name

    def __getattr__(self, name):
        if name.startswith("_") or name in self.__dict__:
            raise AttributeError(name)
        return self._data.get(name, "")

    def get_action(self, turn: str) -> dict[str, Any]:
        """获取怪物在此回合的行动详情"""
        t = data_loader.get_text
        attack_options = self._data.get("攻击", {})
        if not attack_options:
            return {
                "skill": 50,
                "damage": "1d3",
                "desc": t("monster.default_action"),
                "counterattack": t("monster.default_action"),
            }

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
        return action

    def generate_loot(self):
        """Generate loot for this monster. Returns (gold, dropped_item, message)."""
        t = data_loader.get_text
        reward_data = self._data.get("奖励", {})
        gold_max = reward_data.get("乌帕", 10)
        gold = random.randint(1, gold_max)

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
        except ValueError:
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
