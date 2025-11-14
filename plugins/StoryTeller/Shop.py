import random
from database.db import reduce_gold
from util.TimeTool import date_today
from util.DaylyRecord import add_data, get_data, write_json
from .GlobalData import data_manager
from .Investigator import Investigator


SHOP_DATA: dict = {}


def generate_shop():
    res = "\n今日的商店为：\n"
    for key, value in data_manager.shop_data.items():
        if key != "0":
            item_id = random.choice(value)
            item_name = data_manager.goods_data.get(item_id, {})["name"]
            res += f"{item_name}\nID：{item_id} 价格：{key}乌帕\n"
            SHOP_DATA[item_id] = int(key)
        else:
            for this_item_id, price in value.items():
                item_name = data_manager.goods_data.get(this_item_id, {})["name"]
                res += f"{item_name}\nID：{this_item_id} 价格：{price}乌帕\n"
                SHOP_DATA[this_item_id] = price
    return res


def shop_today():
    today = date_today()
    reply: str = get_data(today, "shop")  # type: ignore
    global SHOP_DATA
    SHOP_DATA = get_data(today, "shop_data")  # type: ignore
    if not reply or not SHOP_DATA:
        SHOP_DATA = {}
        reply = generate_shop()
        add_data(today, "shop", reply)
        add_data(today, "shop_data", SHOP_DATA)
        write_json()
    return reply


shop_today()


def buy_item(uid, item_id, num):
    global SHOP_DATA
    price = SHOP_DATA.get(item_id)
    if not price:
        return "\n该物品不在今日可售出的物品清单中哦~"
    if reduce_gold(uid, price * num):
        inv = Investigator.load(uid)
        inv.add_item_to_inventory(item_id, num)
        item_name = data_manager.goods_data.get(item_id, {})["name"]
        return f"\n成功购买{num}件{item_name}。"
    else:
        return f"\n乌帕不足，购买失败。"
