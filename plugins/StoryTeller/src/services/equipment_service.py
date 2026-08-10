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
                battle._update_gun_status()
    return ok, res
