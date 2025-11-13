# equipment.py
from __future__ import annotations
import random
from typing import Dict, Any, Optional, Tuple

from loguru import logger

# 假设 GlobalData 和 Monster 模块路径正确
from .GlobalData import data_manager


# region 1. 数据访问层 (Repository)
class EquipmentRepository:
    """负责加载和查询所有装备数据，采用单例模式避免重复加载。"""

    _instance = None
    _equipment_data: Dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # 在第一次创建实例时加载数据
            cls._equipment_data = data_manager.goods_data
            logger.info(f"装备数据加载完成，共 {len(cls._equipment_data)} 件装备。")
        return cls._instance

    def find_by_id(self, equipment_id: str) -> Optional[Dict[str, Any]]:
        """通过ID查找装备的原始数据字典。"""
        if equipment_id not in self._equipment_data:
            logger.warning(f"尝试访问不存在的装备ID: {equipment_id}")
            return None
        return self._equipment_data.get(equipment_id)

    def brief_equipment(self, equipments) -> str:
        """对于装备内容进行简要描述"""
        result = ""
        for equipment_id, quantity in equipments.items():
            equipment = Equipment(equipment_id)
            result += f" {equipment.name}\n{equipment.get_brief_description()} 数量：{quantity}\n"
        return result


# 全局仓库实例
equipment_repo = EquipmentRepository()
# endregion


# region 2. 领域对象 (Domain Object)
class Equipment:
    """代表一件装备，封装其所有数据和相关行为。"""

    def __init__(self, equipment_id: str):
        self.id = equipment_id
        data = equipment_repo.find_by_id(equipment_id)
        if data is None:
            # 如果装备不存在，可以抛出异常或创建一个默认的“无效物品”
            logger.error(f"无法初始化ID为 {equipment_id} 的装备，数据未找到。")
            self._data = {}  # 提供一个空字典以避免后续AttributeError
        else:
            self._data = data

    @property
    def bullet(self) -> int:
        return self._data.get("bullet", 0)

    @property
    def name(self) -> str:
        return self._data.get("name", "未知装备")

    @property
    def reply(self) -> str:
        return self._data.get("reply", "")

    @property
    def damage(self) -> str:
        return self._data.get("damage", "")

    @property
    def has_penetration(self) -> bool:
        return self._data.get("ex", False)

    @property
    def identify_skill(self) -> str:
        return self._data.get("identify_skill", "")

    @property
    def actions(self) -> list[str]:
        return self._data.get("skill", "")

    @property
    def part(self) -> str:
        return self._data.get("part", "")

    @property
    def armor(self) -> str:
        return self._data.get("armor", "")

    @property
    def description(self) -> str:
        return self._data.get("des", "")

    @property
    def is_valid(self) -> bool:
        """判断该装备实例是否有效加载了数据。"""
        return bool(self._data)

    def get_brief_description(self) -> str:
        """获取装备的简单描述字符串"""
        if not self.is_valid:
            return f"ID: {self.id}\n无效物品"

        # 定义所有属性及其显示文本
        attributes = [
            ("ID", self.id),
            ("护甲", self.armor),
            ("伤害", self.damage),
        ]

        # 过滤掉值为空的属性
        non_empty_attributes = [(label, value) for label, value in attributes if value]

        # 构建结果字符串
        return " ".join(f"{label}: {value}" for label, value in non_empty_attributes)

    def get_full_description(self) -> str:
        """获取装备的详细描述字符串"""
        if not self.is_valid:
            return f"ID: {self.id}\n无效物品"

        # 定义所有属性及其显示文本
        attributes = [
            ("ID", self.id),
            ("名称", self.name),
            ("护甲", self.armor),
            ("伤害", self.damage),
            ("行动", ", ".join(self.actions) if self.actions else ""),
            ("部位", self.part),
            ("鉴定技能", self.identify_skill),
            ("描述", self.description),
        ]

        # 过滤掉值为空的属性
        non_empty_attributes = [(label, value) for label, value in attributes if value]

        # 构建结果字符串
        return "\n".join(f"{label}: {value}" for label, value in non_empty_attributes)

    def __str__(self) -> str:
        return self.name


# endregion
