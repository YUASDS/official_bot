"""装备物品服务：装备后同步战斗中调查员的装备/枪械状态（命令与按钮共用）。"""

from ..models.player import investigator_repo
from ..services.data_loader import data_loader
from ..utils.active_battles import battle_manager


def equip_item_and_sync(user_id: str, item_id: str) -> tuple[bool, str]:
    """装备物品；战斗中只允许走 `/行动 使用<ID>`（会消耗回合）。

    非战斗路径直接装备。战斗中的命令/按钮路径不再提供免费换装：
    换枪会重置弹药，旧实现可零回合补满弹夹；现统一由 adventure 的 `/行动` 分支结算回合。
    """
    battle = battle_manager.get_battle(user_id)
    if battle is not None:
        if battle.current_turn != "inv":
            return False, data_loader.get_text(
                "battle.not_player_turn", default="现在不是你的回合，无法更换装备。"
            )
        return False, data_loader.get_text(
            "battle.equip_use_action",
            default="战斗中请使用 /行动 使用<物品ID> 更换装备（会消耗你的回合）。",
        )
    return investigator_repo.equip_item(user_id, item_id)
