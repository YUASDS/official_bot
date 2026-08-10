"""复活道具服务（命令/按钮共用）。"""

from ..models.player import Investigator, investigator_repo
from .data_loader import data_loader

# 复活道具 ID（预留接口：在 goods_data.json 中加入该 ID 商品后自动生效）
RESURRECT_ITEM_ID = "501"


def do_resurrect(user_id: str) -> str:
    """使用复活道具（命令/按钮共用）。

    预留接口：背包中拥有 RESURRECT_ITEM_ID 时消耗 1 个并复活；
    无道具或未死亡时返回对应提示。
    """
    t = data_loader.get_text
    inv = Investigator.load(user_id)
    if inv.is_survive:
        return t("adventure.resurrect_alive")

    equipments, _ = inv.get_equipments()
    if RESURRECT_ITEM_ID not in equipments:
        return t("adventure.resurrect_none")

    investigator_repo.remove_item_from_inventory(user_id, RESURRECT_ITEM_ID, 1)
    inv.is_survive = True
    inv.restore_hp()
    max_san = inv.get_skill("意志") or 0
    inv.set_skill("san", max(0, max_san))
    inv.save()
    return t("adventure.resurrect_ok")
