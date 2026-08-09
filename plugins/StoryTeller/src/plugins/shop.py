from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg

from ..models.item import Equipment
from ..models.player import investigator_repo
from ..services.shop_service import shop_service
from ..services.data_loader import data_loader
from ..utils.buttons import _send_to_user, register_button_handler
from ..utils.md_format import build_keyboard, md_message, need_create_message

_t = data_loader.get_text

shop_cmd = on_command("今日商店", aliases={"today_shop", "商店"}, priority=10, block=True)

@shop_cmd.handle()
async def handle_shop(event: Event, bot: Bot) -> None:
    user_id = event.get_user_id()
    items = shop_service.get_todays_shop("seed")
    text = shop_service.format_shop_text(items)
    msg = md_message(text, bot, mention=user_id)

    # 商品「购买」按钮（QQ 平台）
    kb_rows = []
    row = []
    for item_id, price in items.items():
        item = Equipment(item_id)
        if not item.is_valid:
            continue
        row.append((_t("shop.buy_button", name=item.name), f"buy:{item_id}"))
        if len(row) == 3:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    kb = build_keyboard(kb_rows)
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await shop_cmd.finish(msg)

buy_cmd = on_command("购买", aliases={"buy_item"}, priority=10, block=True)

@buy_cmd.handle()
async def handle_buy(event: Event, bot: Bot, msg: Message = CommandArg()) -> None:
    user_id = event.get_user_id()
    if investigator_repo.find_by_qq(user_id) is None:
        await buy_cmd.finish(need_create_message(bot, mention=user_id))
    args = msg.extract_plain_text().strip().split()
    if not args:
        await buy_cmd.finish(
            md_message(f"\n{_t('shop.input_hint')}", bot, mention=user_id)
        )

    item_id = args[0]
    quantity = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1

    items = shop_service.get_todays_shop("seed")
    ok, res = shop_service.buy_item(user_id, item_id, quantity, items)
    send_msg = md_message(f"\n{res}", bot, mention=user_id)

    # 购买成功后附加「调查员信息」按钮（QQ 平台）
    if ok:
        kb = build_keyboard([[(data_loader.get_text("character.info_button"), "info")]])
        if kb is not None and not isinstance(send_msg, str):
            send_msg.append(kb)
    await buy_cmd.finish(send_msg)


# --- 按钮回调处理器 ---
async def handle_buy_button(
    user_id: str,
    item_id: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """商店「购买」按钮回调（默认数量 1）。"""
    if investigator_repo.find_by_qq(user_id) is None:
        await _send_to_user(
            bot,
            user_id,
            need_create_message(bot, mention=user_id),
            group_openid,
        )
        return
    items = shop_service.get_todays_shop("seed")
    ok, res = shop_service.buy_item(user_id, item_id, 1, items)
    msg = md_message(f"\n{res}", bot, mention=user_id)

    if ok:
        kb = build_keyboard(
            [[(data_loader.get_text("character.info_button"), "info")]]
        )
        if kb is not None and not isinstance(msg, str):
            msg.append(kb)
    await _send_to_user(bot, user_id, msg, group_openid)


# --- 按钮回调注册 ---
register_button_handler("buy", handle_buy_button)
