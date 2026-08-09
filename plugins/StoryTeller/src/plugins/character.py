from pathlib import Path
from typing import Any, Callable

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg

from ..models.item import Equipment
from ..models.player import (
    CreateInvestigator,
    Investigator,
    InvestigatorFormatter,
    investigator_repo,
)
from ..services.data_loader import data_loader
from ..utils.active_battles import battle_manager
from ..utils.buttons import _send_to_user, register_button_handler
from ..utils.md_format import build_keyboard, cmd_tag, md_message
from database.db import get_info

# --- State storage ---
_user_states: dict[str, Any] = {}

_t = data_loader.get_text

# --- Commands ---
create_cmd = on_command(
    "创建调查员", aliases={"create_investigator"}, priority=10, block=True
)

choose_cmd = on_command(
    "选择调查员", aliases={"/选择调查员"}, priority=16, block=True
)

skill_cmd = on_command(
    "st", aliases={"/st"}, priority=16, block=True
)

info_cmd = on_command(
    "调查员信息", aliases={"investigator_info", "查看状态"}, priority=10, block=True
)

use_item_cmd = on_command(
    "使用物品", aliases={"equip_item", "装备"}, priority=10, block=True
)


# --- /创建调查员 ---
def build_create_reply(user_id: str, name: str) -> str:
    """构建候选列表回复，并保存创建状态（供命令与按钮共用）。"""
    ci = CreateInvestigator(3)
    formatted = InvestigatorFormatter.format_investigator_info(name, ci.investigators_data)
    _user_states[user_id] = {"creator": ci, "name": name}
    return (
        f"\n{_t('character.create_title')}\n\n"
        f"{formatted}\n\n"
        f"{_t('character.choose_hint')}"
    )


def _choose_success_text(ci: CreateInvestigator, name: str) -> str:
    """选择成功面板：属性/技能表格。"""
    core_attrs = [
        "力量", "体质", "体型", "敏捷",
        "外貌", "智力", "意志", "教育", "幸运",
    ]
    attr_rows = [_t("character.attr_table_header"), _t("character.attr_table_sep")]
    for k in core_attrs:
        attr_rows.append(_t("character.attr_table_row", name=k, value=ci.select.get(k, 0)))
    for k, label in (("san", "SAN"), ("hp", "HP"), ("db", "DB")):
        attr_rows.append(_t("character.attr_table_row", name=label, value=ci.select.get(k, 0)))

    skill_keys = ["手枪", "步枪", "格斗", "侦查", "急救", "医学"]
    skill_rows = [_t("character.skill_table_header"), _t("character.attr_table_sep")]
    for k in skill_keys:
        skill_rows.append(_t("character.attr_table_row", name=k, value=ci.select.get(k, 0)))

    return (
        f"\n{_t('character.choose_success')}\n\n"
        f"{_t('character.name_label', name=name)}\n\n"
        f"{_t('character.info_attrs')}\n{chr(10).join(attr_rows)}\n\n"
        f"{chr(10).join(skill_rows)}\n\n"
        f"{_t('character.skill_alloc_hint', points=ci.skill_point)}"
    )


def choose_reply(user_id: str, idx: int) -> str | None:
    """按钮/命令共用：选择候选，返回成功面板；无状态或失败返回 None。"""
    state = _user_states.get(user_id)
    if not state or "creator" not in state:
        return None
    ci: CreateInvestigator = state["creator"]
    if not ci.choose_investigator(idx):
        return None
    return _choose_success_text(ci, state["name"])


@create_cmd.handle()
async def handle_create(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    name = msg.extract_plain_text().strip() or "调查员"

    reply = build_create_reply(user_id, name)
    send_msg = md_message(reply, bot)

    # 候选「选择」按钮（QQ 平台）
    kb = build_keyboard(
        [[(_t("character.choose_button", index=i), f"choose:{i}") for i in range(1, 4)]]
    )
    if kb is not None and not isinstance(send_msg, str):
        send_msg.append(kb)
    await create_cmd.finish(send_msg)


# --- /选择调查员 <N> ---
@choose_cmd.handle()
async def handle_choose(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()

    if user_id not in _user_states or "creator" not in _user_states.get(user_id, {}):
        await choose_cmd.finish(md_message(f"\n{_t('character.need_create')}", bot))

    arg = msg.extract_plain_text().strip()
    if not arg.isdigit():
        await choose_cmd.finish(md_message(f"\n{_t('character.need_number')}", bot))

    idx = int(arg)
    if idx < 1 or idx > 3:
        await choose_cmd.finish(md_message(f"\n{_t('character.out_of_range')}", bot))

    reply = choose_reply(user_id, idx)
    if reply is None:
        await choose_cmd.finish(md_message(f"\n{_t('character.choose_failed')}", bot))
    await choose_cmd.finish(md_message(reply, bot))


# --- /st <skills> ---
@skill_cmd.handle()
async def handle_skill(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    state = _user_states.get(user_id)

    if not state or "creator" not in state:
        await skill_cmd.finish(md_message(f"\n{_t('character.need_create')}", bot))

    ci: CreateInvestigator = state["creator"]
    name: str = state["name"]
    skills = msg.extract_plain_text().strip()

    if not skills:
        await skill_cmd.finish(md_message(f"\n{_t('character.need_skill_input')}", bot))

    ok, reply_msg = ci.set_skill(skills)
    if not ok:
        await skill_cmd.finish(md_message(f"\n{reply_msg}", bot))

    inv = ci.create_investigator(user_id, name)
    attrs = InvestigatorFormatter.format_investigator_info(name, ci.select)
    del _user_states[user_id]

    await skill_cmd.finish(
        md_message(f"\n{_t('character.create_done', name=inv.name)}\n\n{attrs}", bot)
    )


# --- /调查员信息 ---
_CARD_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "info_card.html"

_PIC_ENV_KEY = "STORYTELLER_PIC"


def _pic_enabled() -> bool:
    try:
        from nonebot import get_driver

        value = getattr(get_driver().config, _PIC_ENV_KEY.lower(), "")
        return str(value).lower() in ("1", "true", "yes", "on")
    except Exception:
        return False


def _info_card_html(inv: Investigator, gold: int) -> str:
    """构建调查员信息 HTML 卡片。"""
    attrs = inv.get_full_attributes_dict()
    attr_items = "".join(
        f'<div class="attr"><span class="k">{k}</span><span class="v">{v}</span></div>'
        for k, v in attrs.items()
    )

    equipments, res_name = inv.get_equipments()
    equip_rows = ""
    for part, item_id in inv._equipped.items():
        if not part:
            continue
        equip_rows += (
            f'<div class="row"><span class="k">{part}</span>'
            f'<span class="v">→ {res_name.get(item_id, item_id)}</span></div>'
        )
    if "防具" not in inv._equipped:
        equip_rows += '<div class="row"><span class="k">防具</span><span class="v">→ 无</span></div>'

    bag_rows = "".join(
        f'<div class="row"><span class="k">{item.name} x{qty}</span></div>'
        for item_id, qty in list(equipments.items())[:6]
        for item in [Equipment(item_id)]
        if item.is_valid
    ) or '<div class="row"><span class="k">（空）</span></div>'

    survival = _t("character.dead") if not inv.is_survive else _t("character.survive")
    html = _CARD_TEMPLATE.read_text(encoding="utf-8")
    return (
        html.replace("__NAME__", inv.name)
        .replace("__STATUS__", survival)
        .replace("__DAY__", str(inv.day))
        .replace("__GOLD__", str(gold))
        .replace("__ATTRS__", attr_items)
        .replace("__EQUIPS__", equip_rows)
        .replace("__BAG__", bag_rows)
    )


async def _card_msg(user_id: str):
    """渲染调查员信息图片（QQ 平台）；失败返回 None。"""
    try:
        from nonebot.adapters.qq.message import Message as QQMessage
        from nonebot.adapters.qq.message import MessageSegment

        from util.html2pic import html_to_pic

        inv = Investigator.load(user_id)
        html = _info_card_html(inv, get_info(user_id).gold)
        img = await html_to_pic(html, selector=".card", wait=0.8)
        return QQMessage(MessageSegment.file_image(img))
    except Exception:
        return None


def _info_kb_msg(user_id: str, bot: Bot):
    """指令消息：简短提示 + 背包「使用」指令标签（点击后回车发送 /使用物品）。"""
    inv = Investigator.load(user_id)

    equipments, res_name = inv.get_equipments()
    item_ids = list(equipments.keys())[:6]
    tags = [
        cmd_tag(
            f"/使用物品 {item_id}",
            show=_t("player.use_button", name=res_name.get(item_id, item_id)),
        )
        for item_id in item_ids
    ]
    if not tags:
        return md_message(f"\n**{_t('player.use_hint')}**", bot)

    body = f"\n**{_t('player.use_hint')}**\n\n" + "\n".join(tags)
    return md_message(body, bot)


async def _send_info_flow(user_id: str, bot: Bot, send: Callable, finish: Callable) -> None:
    """调查员档案发送流程：图片模式双消息（卡片 + 按钮），否则单条文本。"""
    if _pic_enabled() and getattr(bot, "type", "") == "QQ":
        card = await _card_msg(user_id)
        if card is not None:
            await send(card)
            await finish(_info_kb_msg(user_id, bot))
            return
    await finish(await build_info_message(user_id, bot))


async def build_info_message(user_id: str, bot: Bot):
    """构建调查员档案消息：QQ 平台渲染图片卡片，其余走 MD 文本+按钮。"""
    inv = Investigator.load(user_id)

    if _pic_enabled() and getattr(bot, "type", "") == "QQ":
        card = await _card_msg(user_id)
        if card is not None:
            return card

    attrs = inv.get_full_attributes_dict()
    survival = _t("character.dead") if not inv.is_survive else _t("character.survive")

    attr_rows = [_t("character.attr_table_header"), _t("character.attr_table_sep")]
    for k, v in attrs.items():
        attr_rows.append(_t("character.attr_table_row", name=k, value=v))

    res = (
        f"\n{_t('character.info_title')}\n\n"
        f"{_t('character.info_status', status=survival, day=inv.day, gold=get_info(user_id).gold)}\n\n"
        f"{_t('character.info_attrs')}\n{chr(10).join(attr_rows)}\n\n"
        f"{inv.str_equipments()}"
    )
    msg = md_message(res, bot)

    # 背包物品「使用」按钮（QQ 平台）
    equipments, res_name = inv.get_equipments()
    item_ids = list(equipments.keys())[:6]
    if item_ids:
        kb_rows = [
            [
                (
                    _t("player.use_button", name=res_name.get(item_id, item_id)),
                    f"equip:{item_id}",
                )
                for item_id in item_ids[i : i + 3]
            ]
            for i in range(0, len(item_ids), 3)
        ]
        kb = build_keyboard(kb_rows)
        if kb is not None and not isinstance(msg, str):
            msg.append(kb)
    return msg


@info_cmd.handle()
async def handle_info(event: Event, bot: Bot):
    user_id = event.get_user_id()
    await _send_info_flow(
        user_id,
        bot,
        send=info_cmd.send,
        finish=info_cmd.finish,
    )


# --- /使用物品 <ID> ---
@use_item_cmd.handle()
async def handle_use_item(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    item_id = msg.extract_plain_text().strip()
    if not item_id:
        await use_item_cmd.finish(md_message(f"\n{_t('player.use_need_id')}", bot))

    ok, res = investigator_repo.equip_item(user_id, item_id)
    if ok:
        battle = battle_manager.get_battle(user_id)
        if battle:
            battle.investigator.update_equipment()
            if battle.current_turn == "inv":
                battle._update_gun_status()
    await use_item_cmd.finish(md_message(f"\n{res}", bot))


# --- 按钮回调处理器 ---
async def handle_equip_button(
    user_id: str,
    item_id: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """背包「使用」按钮回调：直接装备物品。"""
    ok, res = investigator_repo.equip_item(user_id, item_id)
    if ok:
        battle = battle_manager.get_battle(user_id)
        if battle:
            battle.investigator.update_equipment()
            if battle.current_turn == "inv":
                battle._update_gun_status()
    await _send_to_user(bot, user_id, md_message(f"\n{res}", bot), group_openid)


async def handle_choose_button(
    user_id: str,
    idx: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """候选「选择」按钮回调。"""
    reply = choose_reply(user_id, int(idx)) if idx.isdigit() else None
    if reply is None:
        reply = f"\n{_t('character.need_create')}"
    await _send_to_user(bot, user_id, md_message(reply, bot), group_openid)


async def handle_create_button(
    user_id: str,
    payload: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """死亡后「创建调查员」按钮回调。"""
    reply = build_create_reply(user_id, "调查员")
    msg = md_message(reply, bot)
    kb = build_keyboard(
        [
            [
                (_t("character.choose_button", index=i), f"choose:{i}")
                for i in range(1, 4)
            ]
        ]
    )
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await _send_to_user(bot, user_id, msg, group_openid)


async def handle_info_button(
    user_id: str,
    payload: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """「调查员信息」按钮回调。"""

    async def _send(msg):
        await _send_to_user(bot, user_id, msg, group_openid)

    await _send_info_flow(user_id, bot, send=_send, finish=_send)


# --- 按钮回调注册 ---
register_button_handler("equip", handle_equip_button)
register_button_handler("choose", handle_choose_button)
register_button_handler("create", handle_create_button)
register_button_handler("info", handle_info_button)
