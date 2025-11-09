import ujson
from random import choice
from pathlib import Path

from .GlobalData import data_manager


def check_point(day: int):
    """
    input:
        day:str
    output:
        mon_id:str
    """
    return choice(data_manager.check_point[str(day)])


def get_mon(day):
    mid = check_point(day)
    """"""
    return data_manager.monster_data[mid]


def get_mon_bons(mid):
    return data_manager.monster_data[mid]["奖励"]


def get_mon_action(id, turn):
    mon = data_manager.monster_data[id]
    if turn == "inv":
        return mon["攻击"][choice(list(mon["攻击"]))]
    else:
        return mon["攻击"][choice(list(mon["攻击"]))]


class Monster:

    def __init__(self, mon_id: str):
        self.mon_id = mon_id
        self.data = data_manager.monster_data.get(mon_id, {})
        self.hp = self.data.get("生命值", 0)
        self.armor = self.data.get("装甲", "无")

    def __getattribute__(self, name: str):
        """获取怪物属性"""
        data = object.__getattribute__(self, "data")
        if name in data:
            return data.get(name)
        return object.__getattribute__(self, name)

    def get_action(self, turn: str) -> dict:
        """获取怪物的行动"""
        if turn == "inv":
            return self.data["攻击"][choice(list(self.data["攻击"]))]
        else:
            return self.data["攻击"][choice(list(self.data["攻击"]))]

    def get_reward(self):
        """获取怪物的奖励"""
        return self.data.get("奖励", "")

    def damage_to_mon(
        self,
        damage: int,
    ) -> int:
        """计算对怪物的伤害"""
        armor = self.armor
        if armor == "无":
            return damage
        # 其他护甲计算逻辑可以在这里添加
        return max(0, damage - int(armor))  # 示例逻辑：简单减去护甲值
