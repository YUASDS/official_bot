from loguru import logger
import os

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg

from database.db import give_all_gold

from ..services.data_loader import data_loader
from ..utils.md_format import md_message

_t = data_loader.get_text

gm_gift_cmd = on_command("GM赠礼", aliases={"gm_gift"}, priority=1, block=True)
gm_scroll_cmd = on_command("GM赠卷", aliases={"gm_scroll"}, priority=1, block=True)
gm_give_cmd = on_command("GM赠物", aliases={"gm_give"}, priority=1, block=True)


def get_master_ids() -> set[str]:
    """读取 MASTER 配置（.env 或环境变量），支持逗号/空格分隔多个 ID。"""
    raw = ""
    try:
        from nonebot import get_driver

        value = getattr(get_driver().config, "master", "")
        raw = value if isinstance(value, str) else ""
    except Exception as e:  # noqa: BLE001 - 未初始化时退回环境变量
        logger.warning(f"静默异常[Exception] in get_master_ids: {e}")
        pass
    if not raw:
        raw = os.environ.get("MASTER", "")
    return {x.strip() for x in str(raw).replace(",", " ").split() if x.strip()}


def is_master(user_id: str) -> bool:
    """当前用户是否为 GM。"""
    return user_id in get_master_ids()


async def gift_all_gold(num: int) -> int:
    """向全部玩家发放乌帕，返回受影响人数。"""
    from database.db import User

    await give_all_gold(num)
    return User.select().count()


def gift_all_scrolls(num: int = 1) -> int:
    """向每位调查员发放冒险卷(401)，返回调查员人数。"""
    from ..models.player import InvestigatorModel, investigator_repo

    count = 0
    for inv_model in InvestigatorModel.select():
        investigator_repo.add_item_to_inventory(inv_model, "401", num)
        count += 1
    return count


def give_item(player_id: str, item_id: str, quantity: int = 1) -> tuple[bool, str]:
    """向指定玩家发放物品，返回 (是否成功, 提示文案)。"""
    from ..models.item import Equipment
    from ..models.player import investigator_repo

    inv_model = investigator_repo.find_by_qq(player_id)
    if inv_model is None:
        return False, _t("gm.give_player_missing", player=player_id)
    item = Equipment(item_id)
    if not item.is_valid:
        return False, _t("gm.give_item_missing", item_id=item_id)
    investigator_repo.add_item_to_inventory(inv_model, item_id, quantity)
    return True, _t("gm.give_ok", player=player_id, name=item.name, num=quantity)


@gm_gift_cmd.handle()
async def handle_gm_gift(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    if not is_master(user_id):
        await gm_gift_cmd.finish(
            md_message(f"\n{_t('gm.denied')}", bot, mention=user_id)
        )
    arg = msg.extract_plain_text().strip()
    if not arg.isdigit() or int(arg) <= 0:
        await gm_gift_cmd.finish(
            md_message(f"\n{_t('gm.need_number')}", bot, mention=user_id)
        )
    num = int(arg)
    count = await gift_all_gold(num)
    await gm_gift_cmd.finish(
        md_message(f"\n{_t('gm.gift_ok', num=num, count=count)}", bot, mention=user_id)
    )


@gm_scroll_cmd.handle()
async def handle_gm_scroll(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    if not is_master(user_id):
        await gm_scroll_cmd.finish(
            md_message(f"\n{_t('gm.denied')}", bot, mention=user_id)
        )
    arg = msg.extract_plain_text().strip()
    num = int(arg) if arg.isdigit() and int(arg) > 0 else 1
    count = gift_all_scrolls(num)
    await gm_scroll_cmd.finish(
        md_message(
            f"\n{_t('gm.scroll_ok', num=num, count=count)}", bot, mention=user_id
        )
    )


@gm_give_cmd.handle()
async def handle_gm_give(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    if not is_master(user_id):
        await gm_give_cmd.finish(
            md_message(f"\n{_t('gm.denied')}", bot, mention=user_id)
        )
    parts = msg.extract_plain_text().strip().split()
    if len(parts) < 2:
        await gm_give_cmd.finish(
            md_message(f"\n{_t('gm.give_usage')}", bot, mention=user_id)
        )
    player_id, item_id = parts[0], parts[1]
    quantity = 1
    if len(parts) >= 3:
        if not parts[2].isdigit() or int(parts[2]) <= 0:
            await gm_give_cmd.finish(
                md_message(f"\n{_t('gm.give_invalid_qty')}", bot, mention=user_id)
            )
        quantity = int(parts[2])
    ok, text = give_item(player_id, item_id, quantity)
    await gm_give_cmd.finish(md_message(f"\n{text}", bot, mention=user_id))
