import os
import asyncio
import re
from typing import Union, Dict, Any
from nonebot_plugin_alconna import funcommand
from nonebot import on_command, on_regex, on_message
from nonebot.params import CommandArg, RegexGroup, EventMessage
from loguru import logger
from nonebot.adapters import Event, Message
from nonebot.adapters.qq import MessageEvent as QQMessageEvent
from arclet.alconna import Alconna, Args
from nonebot_plugin_alconna import on_alconna, AlconnaMatch, Match


# from nonebot.adapters.onebot.v11 import MessageEvent as OneBotV11MessageEvent
from nonebot.adapters.console import MessageEvent as ConsoleMessageEvent
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .Equipment import Equipment
from .start import get_qq_equipment, set_attr, check_issurvive, Adventure
from .Investigator import (
    Investigator,
    CreateInvestigator,
    InvestigatorFormatter,
    investigator_repo,
)
from .GlobalData import data_manager, Separator
from .Shop import shop_today, buy_item

# 定义插件元信息
__plugin_meta__ = PluginMetadata(
    name="克苏鲁冒险",
    description="克苏鲁风格的文字冒险游戏",
    usage="/冒险 - 开始冒险\n/创建调查员 - 创建调查员\n/调查员信息 - 查看调查员信息",
    type="application",
    homepage="https://github.com/your-repo/official_bot",
    supported_adapters={"onebot.v11", "qq", "console"},
)

# 创建命令处理器
adventure_cmd = on_command(
    "今日冒险", aliases={"今日冒险", "开始冒险"}, priority=10, block=True
)
create_investigator_cmd = on_command(
    "创建调查员", aliases={"调查员创建"}, priority=10, block=True
)
investigator_info_cmd = on_command("调查员信息", priority=10, block=True)
investigator_equipments_cmd = on_command("查看背包", priority=10, block=True)

change_equipments_cmd = on_alconna(Alconna("/使用物品", Args["item_id", int]))
check_equipments_cmd = on_alconna(Alconna("/装备详情", Args["item_id", int]))


fight_cmd = on_alconna(Alconna("/行动", Args["action", str]))

# choose_investigator_cmd = on_regex(r"^/选择调查员(\s*)(\d+)$", priority=16, block=True)
choose_investigator_cmd = on_alconna(Alconna("/选择调查员", Args["choose", int]))

# set_skill_cmd = on_regex(r"^/st\s+(.+)$", priority=16, block=True)
set_skill_cmd = on_alconna(Alconna("/st", Args["skill", str]))

shop_cmd = on_command("今日商店", priority=10, block=True)
buy_cmd = on_alconna(Alconna("/购买", Args["item_id", int], Args["num", int]))

# 全局状态存储
user_states: Dict[str, Any] = {}
print("start")


@buy_cmd.handle()
async def handle_buy_cmd(
    event: Event,
    matched_item_id: Match[int] = AlconnaMatch("item_id"),
    matched_num: Match[int] = AlconnaMatch("num"),
):
    logger.info("check_equipments_cmd")
    item_id = str(matched_item_id.result)
    num = matched_num.result
    if isinstance(event, ConsoleMessageEvent):
        qq = "console_user"
    else:
        qq = str(event.get_user_id())
    res = buy_item(qq, item_id, num)
    await check_equipments_cmd.finish(res)


@shop_cmd.handle()
async def handle_shop_cmd(event: Event):
    res: str = shop_today()
    await shop_cmd.finish(res)


@check_equipments_cmd.handle()
async def handle_check_equipments(
    event: Event, matched_item_id: Match[int] = AlconnaMatch("item_id")
):
    logger.info("check_equipments_cmd")
    item_id = matched_item_id.result
    res = "\n" + Equipment(str(item_id)).get_full_description()
    await check_equipments_cmd.finish(res)


@change_equipments_cmd.handle()
async def handle_change_equipments(
    event: Event, matched_item_id: Match[int] = AlconnaMatch("item_id")
):
    logger.info("change_equipments_cmd")
    if isinstance(event, ConsoleMessageEvent):
        qq = "console_user"
    else:
        qq = str(event.get_user_id())
    item_id = matched_item_id.result

    res = investigator_repo.equip_item(qq, str(item_id))
    await change_equipments_cmd.finish(res[1])


@investigator_equipments_cmd.handle()
async def handle_equipments(event: Event):
    logger.info("investigator_equipments_cmd")
    if isinstance(event, ConsoleMessageEvent):
        qq = "console_user"
    else:
        qq = str(event.get_user_id())
    equipments = get_qq_equipment(qq)
    await investigator_equipments_cmd.finish(equipments)


@adventure_cmd.handle()
async def handle_adventure(event: Event):
    """处理冒险命令"""
    # 获取用户ID
    logger.info("adventure_cmd")
    if isinstance(event, ConsoleMessageEvent):
        qq = "console_user"
    else:
        qq = str(event.get_user_id())

    # 检查是否可以冒险
    # res = check(qq)
    # if not res[0]:
    #     await adventure_cmd.finish(res[1])

    # 获取调查员信息
    inv_info = Investigator.load(qq)
    if not inv_info:
        await adventure_cmd.finish("调查员信息获取失败，请稍后再试~")

    # 开始冒险
    name = "调查员"  # 这里需要根据实际情况获取用户名

    adventure = Adventure(qq, name)
    monster, replies = adventure.StartAdventure()

    # 获取当日事件
    day_event = data_manager.get_event(str(inv_info.day))

    # 存储冒险状态
    user_states[qq] = {"adventure": adventure, "in_fight": True}

    # 发送冒险信息
    messages = [day_event, monster, replies]
    await send_forward_messages(event, messages, name)


@fight_cmd.handle()
async def handle_fight(event: Event, action: Match[str] = AlconnaMatch("action")):
    """处理战斗指令"""
    logger.info("handle_fight")

    if isinstance(event, ConsoleMessageEvent):
        qq = "console_user"
    else:
        qq = str(event.get_user_id())

    # 检查是否在战斗中
    if qq not in user_states or not user_states[qq].get("in_fight"):
        return

    adventure: Adventure = user_states[qq]["adventure"]

    # 执行战斗动作
    flag, res = adventure.run_adventure(action.result)

    if flag:
        # 战斗结束
        user_states[qq]["in_fight"] = False
        await send_forward_messages(event, res, "调查员")
    else:
        # 继续战斗
        await send_forward_messages(event, res, "调查员")


@create_investigator_cmd.handle()
async def handle_create_investigator(event: Event):
    """处理创建调查员命令"""
    logger.info("handle_create_investigator")

    if isinstance(event, ConsoleMessageEvent):
        qq = "console_user"
    else:
        qq = str(event.get_user_id())

    # 检查是否已存在调查员
    inv = Investigator.load(qq)
    res = check_issurvive(inv)
    hp = inv.hp
    if res[0] and hp > 0:
        pass
        # await create_investigator_cmd.finish("当前已经有存在的调查员了哦~")

    # 创建调查员
    name = "调查员"  # 这里需要根据实际情况获取用户名
    times = 3
    creator = CreateInvestigator(times)

    # 存储创建器状态
    user_states[qq] = {"creator": creator, "creating": True, "times": times}

    reply = (
        f"欢迎来到克苏鲁的世界~\n请选择你想要创建的调查员属性:\n/选择调查员[1-{times}]"
    )
    reply += InvestigatorFormatter.format_investigator_info(
        name, creator.investigators_data
    )

    await create_investigator_cmd.send(reply)


@choose_investigator_cmd.handle()
async def handle_choose_investigator(
    event: Event, matched_choose: Match[int] = AlconnaMatch("choose")
):
    """处理选择调查员"""
    logger.info("handle_choose_investigator")
    choose = matched_choose.result
    if isinstance(event, ConsoleMessageEvent):
        qq = "console_user"
    else:
        qq = str(event.get_user_id())

    # 检查是否在选择调查员状态
    if qq not in user_states or not user_states[qq].get("creating"):
        return
    times = user_states[qq]["times"]
    creator = user_states[qq]["creator"]
    name = "调查员"

    if choose > times:
        await choose_investigator_cmd.finish("超过了可以选择的范围哦~")

    # 选择调查员
    user_select = creator.choose_investigator(choose)
    skill_point = creator.skill_point

    reply = InvestigatorFormatter.format_investigator_info(name, creator.select)
    await choose_investigator_cmd.finish(
        f"选择成功\n{reply}\n接下来需要选择分配技能了哦~\n"
        f"共有【{skill_point}】点技能点可以分配，请按格式输入技能分配(例如: /st 手枪30步枪20)\n技能上限75"
    )


@set_skill_cmd.handle()
async def handle_set_skill(
    event: Event, matched_skill: Match[str] = AlconnaMatch("skill")
):
    """处理设置技能"""
    logger.info("handle_set_skill")
    skill_input = matched_skill.result
    if isinstance(event, ConsoleMessageEvent):
        qq = "console_user"
    else:
        qq = str(event.get_user_id())

    # 检查是否在设置技能状态
    if qq not in user_states or not user_states[qq].get("creating"):
        return

    creator = user_states[qq]["creator"]
    name = "调查员"

    # 设置技能
    flag, reply = creator.set_skill(skill_input)
    if not flag:
        await set_skill_cmd.finish(reply)

    # 创建调查员
    creator.create_investigator(qq, name)
    attr = InvestigatorFormatter.format_investigator_info(name, creator.select)

    # 清除创建状态
    user_states[qq]["creating"] = False

    await set_skill_cmd.finish(f"调查员创建完成了哦~\n{attr}")


@investigator_info_cmd.handle()
async def handle_investigator_info(event: Event):
    """处理调查员信息命令"""
    logger.info("handle_investigator_info")

    if isinstance(event, ConsoleMessageEvent):
        qq = "console_user"
    else:
        qq = str(event.get_user_id())

    # 检查调查员状态

    inv = Investigator.load(qq)
    inv_info = inv.model_to_dict()
    res = check_issurvive(inv)
    if not res[0]:
        await investigator_info_cmd.finish(res[1])
    if not inv_info:
        await investigator_info_cmd.finish("调查员信息获取失败，请稍后再试~")

    name = "调查员"
    state = "\n"
    state += InvestigatorFormatter.format_investigator_info(name, inv_info)
    await investigator_info_cmd.finish(state)


async def send_forward_messages(
    event: Event, messages, name: str  # type:ignore
):
    """发送合并转发消息（适配不同平台）"""
    if isinstance(messages, str):
        return await adventure_cmd.send(messages)
    if isinstance(event, QQMessageEvent):
        # QQ 适配器的处理（可能需要根据具体平台调整）

        combined_msg = Separator.join([str(msg) for msg in messages if msg])
        combined_msg = "\n" + combined_msg
        await adventure_cmd.send(combined_msg)
    else:
        # 其他适配器的处理
        for msg in messages:
            await adventure_cmd.send(str(msg))
