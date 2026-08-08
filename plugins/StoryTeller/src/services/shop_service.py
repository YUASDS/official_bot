import random
import sys
from pathlib import Path

from database.db import add_gold, reduce_gold

from ..models.item import Equipment
from ..services.data_loader import data_loader

# Import DaylyRecord for daily persistence
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))
from util.DaylyRecord import add_data, get_data, write_json


class ShopService:
    def __init__(self) -> None:
        pass

    def get_todays_shop(self, day_seed: str = "") -> dict[str, int]:
        cached = get_data("shop", "items")
        if cached:
            return cached

        shop_items = {}
        for price_key, item_ids in data_loader.shop_data.items():
            if price_key == "0":
                for item_id, price in item_ids.items():
                    shop_items[item_id] = price
            elif isinstance(item_ids, list) and item_ids:
                item_id = random.choice(item_ids)
                shop_items[item_id] = int(price_key)

        add_data("shop", "items", shop_items)
        write_json()
        return shop_items

    def format_shop_text(self, shop_items: dict[str, int]) -> str:
        res = "\n════ 今日商店 ════\n\n"
        for item_id, price in shop_items.items():
            item = Equipment(item_id)
            if item.is_valid:
                res += f" · {item.name}（ID: {item.id}）- {price} 乌帕\n"
        res += "\n输入 /购买 <物品ID> [数量] 进行购买"
        return res

    def buy_item(self, user_qq: str, item_id: str, quantity: int, shop_items: dict[str, int]) -> str:
        if item_id not in shop_items:
            return "该物品今日未出售。"

        price = shop_items[item_id]
        total_cost = price * quantity

        from ..models.player import investigator_repo

        if reduce_gold(user_qq, total_cost):
            inv = investigator_repo.find_by_qq(user_qq)
            if inv:
                item = Equipment(item_id)
                investigator_repo.add_item_to_inventory(inv, item_id, quantity)
                return f"成功购买了 {quantity} x {item.name}，花费 {total_cost} 乌帕。"
            add_gold(user_qq, total_cost)
            return "调查员不存在，购买已取消。"
        return "乌帕不足。"

shop_service = ShopService()
