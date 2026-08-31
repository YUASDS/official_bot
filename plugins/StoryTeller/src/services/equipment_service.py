"""装备物品服务：装备后同步战斗中调查员的装备/枪械状态（命令与按钮共用）。"""

from ..models.player import investigator_repo
from ..utils.active_battles import battle_manager


def equip_item_and_sync(user_id: str, item_id: str) -> tuple[bool, str]:
    """装备物品；若在战斗中同步调查员装备/枪械状态。返回 (是否成功, 消息)。"""
    ok, res = investigator_repo.equip_item(user_id, item_id)
    if ok:
        battle = battle_manager.get_battle(user_id)
        if battle:
            battle.investigator.update_equipment()
            if battle.current_turn == "inv":
                # 跨模块调私有：BattleService._update_gun_status（battle/base.py:171）为
                # 装备/枪械状态同步的内部实现（adventure.py:979 同样直调）；加公开封装需改
                # battle/base.py，超出本批改动范围，故沿用直调并在此说明原因。
                battle._update_gun_status()
    return ok, res
