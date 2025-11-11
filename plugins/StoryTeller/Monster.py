# monster.py
from __future__ import annotations
import random
from typing import Dict, Any, Optional, Tuple

from loguru import logger

# 导入重构后的 Equipment 类和 repo
from .Equipment import Equipment, equipment_repo
from .GlobalData import data_manager


# region 1. 数据访问层 (Repository)
class MonsterRepository:
    """负责加载和查询所有怪物数据，采用单例模式。"""

    _instance = None
    _monster_data: Dict[str, Any] = {}
    _checkpoint_data: Dict[str, list[str]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._monster_data = data_manager.monster_data
            cls._checkpoint_data = data_manager.check_point
            logger.info(f"怪物数据加载完成，共 {len(cls._monster_data)} 种怪物。")
        return cls._instance

    def find_by_id(self, monster_id: str) -> Optional[Dict[str, Any]]:
        """通过ID查找怪物的原始数据字典。"""
        return self._monster_data.get(monster_id)

    def find_random_id_for_day(self, day: int | str) -> Optional[str]:
        """根据天数随机选择一个怪物ID (替换 check_point)"""
        day_str = str(day)
        available_monsters = self._checkpoint_data.get(day_str)
        if not available_monsters:
            logger.error(f"找不到第 {day_str} 天的怪物配置。")
            return None
        return random.choice(available_monsters)


# 全局仓库实例
monster_repo = MonsterRepository()
# endregion


# region 2. 领域对象 (Domain Object)
class Monster:
    """代表一个怪物实体，封装其所有数据和行为。"""

    def __init__(self, monster_id: str):
        self.id = monster_id
        data = monster_repo.find_by_id(monster_id)
        if data is None:
            raise ValueError(f"无法创建ID为 {monster_id} 的怪物，数据未找到。")
        self._data = data
        # 将常用属性直接暴露出来，便于访问
        self.hp: int = self._data.get("生命值", 1)
        self.name: str = self._data.get("名字", "未知怪物")
        self.armor: int = int(self._data.get("装甲", 0))
        self.敏捷: int = self._data.get("敏捷", 25)
        self.结局: str = self._data.get("结局", "")
        self.出场: str = self._data.get("出场", "")
        # ... 其他怪物属性

    @classmethod
    def load_random_for_day(cls, day: int) -> Optional[Monster]:
        """工厂方法：加载指定天数的一个随机怪物 (替换 get_mon)"""
        monster_id = monster_repo.find_random_id_for_day(day)
        if not monster_id:
            return None
        return cls(monster_id)

    def __getattribute__(self, name: str):
        """获取怪物属性"""
        data = object.__getattribute__(self, "_data")
        if name in data:
            return data.get(name)
        return object.__getattribute__(self, name)

    def get_action(self, turn: str) -> Dict[str, Any]:
        """获取怪物在此回合的行动详情 (替换 get_mon_action)"""
        # 注意：原逻辑中 turn 参数未使用，此处保留以兼容，但可简化
        attack_options = self._data.get("攻击", {})
        if not attack_options:
            return {"skill": 50, "damage": "1d3", "desc": "猛击"}  # 提供一个默认攻击
        chosen_action_key = random.choice(list(attack_options.keys()))
        return attack_options[chosen_action_key]

    def generate_loot(self) -> Tuple[int, Optional[Equipment], str]:
        """
        生成此怪物的战利品 (替换 LootService)。

        Returns:
            一个元组 (金币数量, 掉落的装备对象或None, 描述文本)。
        """
        reward_data = self._data.get("奖励")
        if not reward_data:
            return 0, None, "你在怪物身上没有找到任何有价值的物品。"

        # 1. 计算金币
        max_gold = reward_data.get("乌帕", 10)
        gold_reward = random.randint(1, max_gold)

        # 2. 计算掉落物品
        item_ids = reward_data.get("物品", [])
        dropped_item: Optional[Equipment] = None
        if item_ids:
            selected_item_id = random.choice(item_ids)
            item = Equipment(selected_item_id)
            if item.is_valid:
                dropped_item = item

        # 3. 生成描述文本
        if dropped_item:
            message = (
                f"于嘈杂中的环境寻找，或是幸运，或是偶然，你在某个阴暗角落发现了一只"
                f"【{dropped_item.name}】，看起来{dropped_item.description}"
                f"伤害：{dropped_item.damage}。同时你还找到了 {gold_reward} 枚乌帕。"
            )
        else:
            message = f"你找到了 {gold_reward} 乌帕，但没有发现其他有价值的物品。"

        return gold_reward, dropped_item, message


# endregion
