from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from ..services.data_loader import data_loader


class EquipmentRepository:
    """Repository for loading and querying equipment data."""
    _instance = None
    _equipment_data: dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._equipment_data = data_loader.goods_data
            logger.info(f"Equipment data loaded: {len(cls._instance._equipment_data)} items.")
        return cls._instance

    def find_by_id(self, equipment_id: str) -> Optional[dict[str, Any]]:
        """Find raw equipment data by ID."""
        if equipment_id not in self._equipment_data:
            logger.warning(f"Attempted to access non-existent equipment ID: {equipment_id}")
            return None
        return self._equipment_data.get(equipment_id)

    def brief_equipment(self, equipments: dict[str, int]) -> str:
        """Brief description for a collection of equipments."""
        result = ""
        for equipment_id, quantity in equipments.items():
            equipment = Equipment(equipment_id)
            result += f" {equipment.name}\n{equipment.get_brief_description()} Quantity: {quantity}\n"
        return result

equipment_repo = EquipmentRepository()

class Equipment:
    """Represents an equipment item, encapsulating its data and behavior."""
    def __init__(self, equipment_id: str) -> None:
        self.id = equipment_id
        self._data = equipment_repo.find_by_id(equipment_id) or {}
        self.name = self._data.get("name", "Unknown Item")
        self.type = self._data.get("type", "misc")
        self.description = self._data.get("des", "No description available.")
        self.price = self._data.get("price", 0)
        self.part = self._data.get("part", "misc")
        self.damage_dice = self._data.get("damage", "0")
        self.skill_bonus = self._data.get("skill", []) # e.g. ["Fight", "Shoot"]
        self.armor_point = int(self._data.get("armor", 0))
        self.identify_skill = self._data.get("identify_skill", "格斗")
        self.reply = self._data.get("reply", "")
        self.has_penetration = self._data.get("ex", False)
        self.bullet = self._data.get("bullet", 0)
        # Assuming max_bullet is synonymous with bullet in static data
        self.max_bullet = self.bullet

    @property
    def is_valid(self) -> bool:
        return bool(self._data)

    def __str__(self) -> str:
        return self.name

    def get_brief_description(self) -> str:
        if not self.is_valid:
            return f"ID: {self.id}\n无效物品"
        attributes = [
            ("ID", self.id),
            ("护甲", str(self.armor_point) if self.armor_point else ""),
            ("伤害", self.damage_dice),
            ("价格", str(self.price)),
        ]
        non_empty = [(k, v) for k, v in attributes if v]
        return " ".join(f"{k}: {v}" for k, v in non_empty)

    def get_full_description(self) -> str:
        if not self.is_valid:
            return f"ID: {self.id}\n无效物品"
        skill_str = ", ".join(self.skill_bonus) if isinstance(self.skill_bonus, list) else str(self.skill_bonus)
        attributes = [
            ("ID", self.id),
            ("名称", self.name),
            ("护甲", str(self.armor_point) if self.armor_point else ""),
            ("伤害", self.damage_dice),
            ("行动", skill_str or ""),
            ("部位", self.part),
            ("鉴定技能", self.identify_skill),
            ("描述", self.description),
        ]
        non_empty = [(k, v) for k, v in attributes if v]
        return "\n".join(f"{k}: {v}" for k, v in non_empty)

    def is_weapon(self) -> bool:
        return self.type in ["melee", "ranged"]

    def is_armor(self) -> bool:
        return self.type == "armor"
