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
        t = data_loader.get_text
        res = f"\n{t('shop.title')}\n\n"
        for item_id, price in shop_items.items():
            item = Equipment(item_id)
            if item.is_valid:
                res += f" {t('shop.item_line', name=item.name, id=item.id, price=price)}\n"
        res += f"\n{t('shop.buy_hint')}"
        return res

    def buy_item(self, user_qq: str, item_id: str, quantity: int, shop_items: dict[str, int]) -> str:
        t = data_loader.get_text
        if item_id not in shop_items:
            return t("shop.item_not_on_sale")

        price = shop_items[item_id]
        total_cost = price * quantity

        from ..models.player import investigator_repo

        if reduce_gold(user_qq, total_cost):
            inv = investigator_repo.find_by_qq(user_qq)
            if inv:
                item = Equipment(item_id)
                investigator_repo.add_item_to_inventory(inv, item_id, quantity)
                return t("shop.buy_success", quantity=quantity, name=item.name, cost=total_cost)
            add_gold(user_qq, total_cost)
            return t("shop.no_investigator")
        return t("shop.not_enough_gold")

shop_service = ShopService()
