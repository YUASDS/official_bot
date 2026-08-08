from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.params import CommandArg

from ..services.shop_service import shop_service
from ..services.data_loader import data_loader

_t = data_loader.get_text

shop_cmd = on_command("今日商店", aliases={"today_shop", "商店"}, priority=10, block=True)

@shop_cmd.handle()
async def handle_shop(event: Event) -> None:
    items = shop_service.get_todays_shop("seed")
    text = shop_service.format_shop_text(items)
    await shop_cmd.finish(text)

buy_cmd = on_command("购买", aliases={"buy_item"}, priority=10, block=True)

@buy_cmd.handle()
async def handle_buy(event: Event, msg: Message = CommandArg()) -> None:
    args = msg.extract_plain_text().strip().split()
    if not args:
        await buy_cmd.finish(f"\n{_t('shop.input_hint')}")

    item_id = args[0]
    quantity = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1

    items = shop_service.get_todays_shop("seed")
    res = shop_service.buy_item(event.get_user_id(), item_id, quantity, items)
    await buy_cmd.finish(f"\n{res}")
