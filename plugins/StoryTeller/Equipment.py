import ujson
from pathlib import Path
import random
from typing import Dict, Any, Optional
from loguru import logger


from .Monster import get_mon_bons
from .GlobalData import data_manager


class EquipmentDataManager:
    """装备数据管理器"""

    def __init__(self, data_file_path: Path):
        self.data_file_path = data_file_path
        self._equipment_data: Dict[str, Any] = data_manager.goods_data

    def get_equipment_info(
        self, equipment_id: str, field: str, default: Any = None
    ) -> Any:
        """
        获取装备信息

        Args:
            equipment_id: 装备ID
            field: 字段名
            default: 默认值

        Returns:
            装备字段值
        """
        try:
            return self._equipment_data.get(equipment_id, {}).get(field, default)
        except (KeyError, AttributeError):
            logger.exception(f"获取装备信息失败: ID={equipment_id}, Field={field}")
            return default

    def equipment_exists(self, equipment_id: str) -> bool:
        """检查装备是否存在"""
        return equipment_id in self._equipment_data


class EquipmentService:
    """装备服务类"""

    # 初始化数据管理器
    _data_manager = EquipmentDataManager(Path(__file__).parent.joinpath("goods.json"))

    @classmethod
    def get_equipment_reply(cls, equipment_id: str) -> str:
        """获取装备使用回复"""
        return cls._data_manager.get_equipment_info(equipment_id, "reply", "")

    @classmethod
    def get_equipment_name(cls, equipment_id: str) -> str:
        """获取装备名称"""
        return cls._data_manager.get_equipment_info(equipment_id, "name", "未知装备")

    @classmethod
    def get_equipment_damage(cls, equipment_id: str) -> str:
        """获取装备伤害值"""
        return cls._data_manager.get_equipment_info(equipment_id, "damage", "1d4")

    @classmethod
    def has_penetration_effect(cls, equipment_id: str) -> bool:
        """检查是否为贯穿武器"""
        return cls._data_manager.get_equipment_info(equipment_id, "ex", False)

    @classmethod
    def get_identify_skill(cls, equipment_id: str) -> str:
        """获取鉴定技能"""
        return cls._data_manager.get_equipment_info(
            equipment_id, "identify_skill", "射击"
        )

    @classmethod
    def get_equipment_skill(cls, equipment_id: str) -> str:
        """获取装备技能"""
        return cls._data_manager.get_equipment_info(equipment_id, "skill", "格斗")

    @classmethod
    def validate_equipment_id(cls, equipment_id: str) -> bool:
        """验证装备ID是否有效"""
        return cls._data_manager.equipment_exists(equipment_id)

    @classmethod
    def get_equipment_part(cls, equipment_id: str) -> str:
        """获取装备部位"""
        return cls._data_manager.get_equipment_info(equipment_id, "part", "未知部位")

    @classmethod
    def get_equipment_des(cls, equipment_id: str) -> str:
        """获取装备描述"""
        return cls._data_manager.get_equipment_info(equipment_id, "des", "看起来很普通")

    @classmethod
    def str_equipment(cls, equipment_id: str) -> str:
        """对于装备内容进行详细描述"""
        return f"ID：{equipment_id}\n名称: {cls.get_equipment_name(equipment_id)}\n伤害：{cls.get_equipment_damage(equipment_id)}\n攻击方式：{cls.get_equipment_skill(equipment_id)}\n部位：{cls.get_equipment_part(equipment_id)}\n鉴定技能：{cls.get_identify_skill(equipment_id)}\n描述：{cls.get_equipment_des(equipment_id)}"

    @classmethod
    def brief_equipment(cls, equipment_dict: Dict[str, dict[str, int]]) -> str:
        """对于装备内容进行简要描述"""
        result = ""
        for equipment_id, equipment_info in equipment_dict.items():
            for equipment_name, quitity in equipment_info.items():
                result += f" {equipment_name}\nID：{equipment_id} 伤害：{cls.get_equipment_damage(equipment_id)}数量：{quitity}\n"
        return result


class LootService:
    """战利品服务类"""

    @staticmethod
    def get_loot_reward(monster_id: str):
        """
        获取战利品奖励

        Args:
            player_id: 玩家ID
            monster_id: 怪物ID

        Returns:
            战利品描述字符串
        """
        try:
            # 获取怪物掉落配置
            monster_bonus = get_mon_bons(monster_id)

            if not monster_bonus:
                return 0, {}, "你在怪物身上没有找到任何有价值的物品。"

            # 获取金币奖励
            gold_reward = LootService._calculate_gold_reward(monster_bonus)

            # 获取物品奖励
            item_reward = LootService._calculate_item_reward(monster_bonus)

            if not item_reward:
                return (
                    gold_reward,
                    {},
                    f"你找到了 {gold_reward} 乌帕，但没有发现其他有价值的物品。",
                )

            # 添加金币到玩家账户
            # add_gold(player_id, gold_reward)

            return (
                gold_reward,
                item_reward,
                LootService._format_reward_message(item_reward, gold_reward),
            )

        except Exception as e:
            logger.exception(f"获取战利品失败:  Monster={monster_id}, Error={e}")
            return 0, {}, "在搜寻战利品时发生了意外，你什么都没找到。"

    @staticmethod
    def _calculate_gold_reward(monster_bonus: Dict[str, Any]) -> int:
        """计算金币奖励"""
        max_gold = monster_bonus.get("乌帕", 10)  # 默认最大值10
        return random.randint(1, max_gold)

    @staticmethod
    def _calculate_item_reward(
        monster_bonus: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """计算物品奖励"""
        available_items = monster_bonus.get("物品", [])

        if not available_items:
            return None

        selected_item_id = random.choice(available_items)

        # 验证物品是否存在
        if not EquipmentService.validate_equipment_id(selected_item_id):
            logger.warning(f"无效的物品ID: {selected_item_id}")
            return None

        return {
            "id": selected_item_id,
            "name": EquipmentService.get_equipment_name(selected_item_id),
            "description": EquipmentService._data_manager.get_equipment_info(
                selected_item_id, "des", "看起来很普通"
            ),
            "damage": EquipmentService.get_equipment_damage(selected_item_id),
        }

    @staticmethod
    def _format_reward_message(item_reward: Dict[str, Any], gold_amount: int) -> str:
        """格式化奖励消息"""
        return (
            f"于嘈杂中的环境寻找，或是幸运，或是偶然，你在某个阴暗角落发现了一只"
            f"【{item_reward['name']}】，看起来{item_reward['description']}"
            f"伤害：{item_reward['damage']}。同时你还找到了 {gold_amount} 枚乌帕。"
        )

    # 对于装备内容进行详细描述


class Equipment:
    """装备类"""

    def __init__(self, equipment_id: str):
        self.data = data_manager.goods_data.get(equipment_id, {})

    def __getattribute__(self, name: str):
        """获取装备属性"""
        data = object.__getattribute__(self, "data")
        if name in data:
            return data.get(name)
        return object.__getattribute__(self, name)


# 模块初始化时验证数据加载
def _initialize_module():
    """模块初始化"""
    logger.info("装备模块初始化完成")


_initialize_module()
