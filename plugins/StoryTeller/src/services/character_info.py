"""调查员档案消息构建与发送流程（图片卡片 + md 双轨）。"""

from loguru import logger
from typing import Callable

from nonebot.adapters import Bot

from database.db import get_info

from ..models.item import Equipment
from ..models.player import Investigator, ending_repo
from ..utils.md_format import (
    build_keyboard,
    cmd_tag,
    is_md_enabled,
    md_message,
    pic_enabled,
)
from .character_cards import SKILL_NAMES, info_card_html, spell_brief, spell_name
from .data_loader import data_loader

_t = data_loader.get_text

# QQ 键盘上限 5 行 × 3 按钮；超出部分在图片卡片/文本中完整展示
_MAX_USE_BUTTONS = 15


async def card_msg(user_id: str):
    """渲染调查员信息图片（QQ 平台）；失败返回 None。"""
    try:
        from nonebot.adapters.qq.message import Message as QQMessage
        from nonebot.adapters.qq.message import MessageSegment

        from util.html2pic import html_to_pic

        inv = Investigator.load(user_id)
        html = info_card_html(inv, get_info(user_id).gold)
        img = await html_to_pic(html, selector=".card", wait=0.8)
        msg = QQMessage(MessageSegment.file_image(img))
        return msg
    except Exception as e:
        logger.warning(f"静默异常[Exception] in card_msg: {e}")
        return None


def info_kb_msg(user_id: str, bot: Bot):
    """指令消息：背包「使用」标签（过滤纪念品）+ 纪念品独立块 + 底部「今日冒险」按钮。"""
    inv = Investigator.load(user_id)

    equipments, res_name = inv.get_equipments()
    souvenir_ids = [iid for iid in equipments if Equipment(iid).type == "纪念品"]
    usable_ids = [iid for iid in equipments if Equipment(iid).type != "纪念品"]
    tags = [
        cmd_tag(
            f"/使用物品 {item_id}",
            show=_t("player.use_button", name=res_name.get(item_id, item_id)),
        )
        for item_id in usable_ids
    ]
    parts = []
    if tags:
        parts.append(f"\n**{_t('player.use_hint')}**")
        parts.append("\n".join(tags))
    parts.append(cmd_tag("/今日冒险", show=_t("adventure.adventure_button")))
    if souvenir_ids:
        names = " / ".join(Equipment(iid).name for iid in souvenir_ids)
        parts.append(f"🎖️ 纪念品\n{names}")
    return md_message("\n\n".join(parts), bot, mention=user_id)


async def send_info_flow(
    user_id: str, bot: Bot, send: Callable, finish: Callable
) -> None:
    """调查员档案发送流程：图片模式双消息（卡片 + 指令标签），否则单条文本。"""
    if pic_enabled() and getattr(bot, "type", "") == "QQ":
        card = await card_msg(user_id)
        if card is not None:
            await send(card)
            await finish(info_kb_msg(user_id, bot))
            return
    await finish(await build_info_message(user_id, bot))


async def build_info_message(user_id: str, bot: Bot):
    """构建调查员档案消息：QQ 平台渲染图片卡片，其余走 MD 文本+按钮。"""
    inv = Investigator.load(user_id)

    if pic_enabled() and getattr(bot, "type", "") == "QQ":
        card = await card_msg(user_id)
        if card is not None:
            return card

    attrs = inv.get_full_attributes_dict()
    attrs["HP"] = inv.get_max_hp()
    survival = _t("character.dead") if not inv.is_survive else _t("character.survive")

    attr_rows = [_t("character.attr_table_header"), _t("character.attr_table_sep")]
    for k, v in attrs.items():
        attr_rows.append(_t("character.attr_table_row", name=k, value=v))

    skill_rows = [_t("character.skill_table_header"), _t("character.attr_table_sep")]
    for k in SKILL_NAMES:
        skill_rows.append(_t("character.attr_table_row", name=k, value=inv.get_skill(k, 0)))

    spell_lines = [
        f"{spell_name(sid)}（{spell_brief(sid)}）" for sid in inv.get_spells()
    ]
    res = (
        f"\n{_t('character.info_title')}\n\n"
        f"{_t('character.info_status', status=survival, day=inv.day, gold=get_info(user_id).gold)}\n\n"
        f"{_t('character.info_attrs')}\n{chr(10).join(attr_rows)}\n\n"
        f"{_t('character.skill_title')}\n{chr(10).join(skill_rows)}\n\n"
        f"{inv.str_equipments()}\n\n"
        f"{_t('spell_list_title')}\n"
        + ("\n".join(spell_lines) if spell_lines else _t("spell.list_empty"))
    )
    if is_md_enabled():
        res += f"\n\n{cmd_tag('/今日冒险', show=_t('adventure.adventure_button'))}"

    # 背包物品分区：纪念品独立成框（不渲染「使用」按钮），其余物品生成「使用」按钮
    equipments, res_name = inv.get_equipments()
    souvenir_ids = [iid for iid in equipments if Equipment(iid).type == "纪念品"]
    if souvenir_ids:
        names = " / ".join(Equipment(iid).name for iid in souvenir_ids)
        res += f"\n\n🎖️ 纪念品\n{names}"
    msg = md_message(res, bot, mention=user_id)

    # 背包物品「使用」按钮（QQ 平台；受键盘行数上限约束，超出部分见卡片/文本；过滤纪念品）
    usable_ids = [
        iid for iid in equipments if Equipment(iid).type != "纪念品"
    ][:_MAX_USE_BUTTONS]
    if usable_ids:
        kb_rows = [
            [
                (
                    _t("player.use_button", name=res_name.get(item_id, item_id)),
                    f"equip:{item_id}",
                )
                for item_id in usable_ids[i : i + 3]
            ]
            for i in range(0, len(usable_ids), 3)
        ]
        kb = build_keyboard(kb_rows)
        if kb is not None and not isinstance(msg, str):
            msg.append(kb)
    return msg
