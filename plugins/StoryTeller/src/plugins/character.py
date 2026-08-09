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
from ..utils.md_format import (
    build_keyboard,
    cmd_tag,
    is_md_enabled,
    md_message,
    md_to_html,
    need_create_message,
    pic_enabled,
)
from .adventure import _render_pic, _send_pic
from database.db import get_info

# --- State storage ---
_user_states: dict[str, Any] = {}

_t = data_loader.get_text

_CREATE_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "create_card.html"

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


# --- 创建流程图片卡片 ---
def _create_card_html(
    icon: str, title: str, panels: str, hint_md: str, meta: str = ""
) -> str:
    """创建流程通用卡片 HTML。"""
    return (
        _CREATE_TEMPLATE.read_text(encoding="utf-8")
        .replace("__ICON__", icon)
        .replace("__TITLE__", title)
        .replace("__META__", meta)
        .replace("__PANELS__", panels)
        .replace("__HINT__", md_to_html(hint_md))
    )


def _attrs_panel_html(inv: dict, title: str) -> str:
    """属性面板：9 项核心属性 + SAN/HP/DB。"""
    labels = {"san": "SAN", "hp": "HP", "db": "DB"}
    keys = [*InvestigatorFormatter.DISPLAY_ATTRS, "san", "hp", "db"]
    items = "".join(
        f'<span class="p-item"><i>{labels.get(k, k)}</i><b>{inv.get(k, 0)}</b></span>'
        for k in keys
    )
    return (
        f'<div class="panel"><div class="p-title">{title}</div>'
        f'<div class="p-grid">{items}</div></div>'
    )


def _skills_panel_html(inv: dict) -> str:
    """技能面板：成对布局 + 闪避单行。"""
    items = "".join(
        f'<span class="p-item"><i>{a}</i><b>{inv.get(a, 0)}</b></span>'
        f'<span class="p-item"><i>{b}</i><b>{inv.get(b, 0)}</b></span>'
        for a, b in (("手枪", "步枪"), ("格斗", "侦查"), ("急救", "医学"))
    )
    items += f'<span class="p-item"><i>闪避</i><b>{inv.get("闪避", 0)}</b></span>'
    return (
        f'<div class="panel"><div class="p-title">技能</div>'
        f'<div class="p-grid">{items}</div></div>'
    )


def _candidate_card_html(investigators: list[dict]) -> str:
    """候选列表卡片（3 名候补，属性面板 + 选择提示）。"""
    panels = "".join(
        _attrs_panel_html(inv, _t("player.candidate_panel", index=i))
        for i, inv in enumerate(investigators, 1)
    )
    return _create_card_html(
        "🌙", "欢迎来到克苏鲁的世界", panels, _t("character.choose_hint")
    )


def _choose_success_card_html(ci: CreateInvestigator, name: str) -> str:
    """选择成功卡片：属性 + 技能 + /st 分配提示。"""
    panels = _attrs_panel_html(ci.select, _t("character.info_attrs"))
    panels += _skills_panel_html(ci.select)
    return _create_card_html(
        "✅",
        "选择成功",
        panels,
        _t("character.skill_alloc_hint", points=ci.skill_point),
        meta=_t("character.name_label", name=name),
    )


def _create_done_card_html(ci: CreateInvestigator, name: str) -> str:
    """创建完成卡片：属性 + 技能 + 今日冒险提示。"""
    panels = _attrs_panel_html(ci.select, _t("character.info_attrs"))
    panels += _skills_panel_html(ci.select)
    return _create_card_html(
        "🎉",
        _t("character.create_done_title"),
        panels,
        _t("character.create_done_hint"),
        meta=_t("character.name_label", name=name),
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
    """选择成功面板：属性/技能每行两项布局。"""
    labels = {"san": "SAN", "hp": "HP", "db": "DB"}

    # 属性配对（每行 ≤15 字符：幸运配 HP、SAN 配 DB）
    attr_pairs = [
        ("力量", "体质"),
        ("体型", "敏捷"),
        ("外貌", "智力"),
        ("意志", "教育"),
        ("幸运", "hp"),
        ("san", "db"),
    ]
    attr_lines = [
        _t(
            "player.attr_pair",
            a=labels.get(a, a),
            av=ci.select.get(a, 0),
            b=labels.get(b, b),
            bv=ci.select.get(b, 0),
        )
        for a, b in attr_pairs
    ]

    # 技能配对 + 闪避单行
    skill_pairs = [("手枪", "步枪"), ("格斗", "侦查"), ("急救", "医学")]
    skill_lines = [
        _t(
            "player.attr_pair",
            a=a,
            av=ci.select.get(a, 0),
            b=b,
            bv=ci.select.get(b, 0),
        )
        for a, b in skill_pairs
    ]
    skill_lines.append(
        _t("player.attr_single", a="闪避", av=ci.select.get("闪避", 0))
    )

    return (
        f"\n{_t('character.choose_success')}\n\n"
        f"{_t('character.name_label', name=name)}\n\n"
        f"{_t('character.info_attrs')}\n{chr(10).join(attr_lines)}\n\n"
        f"{_t('character.skill_title')}\n{chr(10).join(skill_lines)}\n\n"
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
    name = msg.extract_plain_text().strip()
    if not name:
        await create_cmd.finish(
            md_message(
                f"\n{_t('character.need_name')}\n\n"
                f"{cmd_tag('/创建调查员', show=_t('character.create_button'))}",
                bot,
                mention=user_id,
            )
        )

    existing = investigator_repo.find_by_qq(user_id)
    if existing is not None and existing.issurvive:
        await create_cmd.finish(
            md_message(
                f"\n{_t('character.has_alive')}\n\n"
                f"{cmd_tag('/调查员信息', show=_t('character.info_button'))}\n"
                f"{cmd_tag('/今日冒险', show=_t('adventure.adventure_button'))}",
                bot,
                mention=user_id,
            )
        )

    reply = build_create_reply(user_id, name)
    ci = _user_states[user_id]["creator"]

    # 候选列表图片卡片（全平台）+ 选择按钮；图片失败回退 md
    img = await _render_pic(_candidate_card_html(ci.investigators_data))
    if img is not None and await _send_pic(bot, img, create_cmd.send):
        kb = build_keyboard(
            [
                [
                    (_t("character.choose_button", index=i), f"choose:{i}")
                    for i in range(1, 4)
                ]
            ]
        )
        btn_msg = md_message(f"\n{_t('character.choose_hint')}", bot, mention=user_id)
        if kb is not None and not isinstance(btn_msg, str):
            btn_msg.append(kb)
        await create_cmd.send(btn_msg)
        return

    send_msg = md_message(reply, bot, mention=user_id)
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
        await choose_cmd.finish(
            md_message(f"\n{_t('character.need_create')}", bot, mention=user_id)
        )

    arg = msg.extract_plain_text().strip()
    if not arg.isdigit():
        await choose_cmd.finish(
            md_message(f"\n{_t('character.need_number')}", bot, mention=user_id)
        )

    idx = int(arg)
    if idx < 1 or idx > 3:
        await choose_cmd.finish(
            md_message(f"\n{_t('character.out_of_range')}", bot, mention=user_id)
        )

    reply = choose_reply(user_id, idx)
    if reply is None:
        await choose_cmd.finish(
            md_message(f"\n{_t('character.choose_failed')}", bot, mention=user_id)
        )

    # 选择成功图片卡片（含 /st 分配提示）；图片失败回退 md
    state = _user_states[user_id]
    img = await _render_pic(_choose_success_card_html(state["creator"], state["name"]))
    if img is not None and await _send_pic(bot, img, choose_cmd.send):
        return
    await choose_cmd.finish(md_message(reply, bot, mention=user_id))


# --- /st <skills> ---
@skill_cmd.handle()
async def handle_skill(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    state = _user_states.get(user_id)

    if not state or "creator" not in state:
        await skill_cmd.finish(
            md_message(f"\n{_t('character.need_create')}", bot, mention=user_id)
        )

    ci: CreateInvestigator = state["creator"]
    name: str = state["name"]
    skills = msg.extract_plain_text().strip()

    if not skills:
        await skill_cmd.finish(
            md_message(f"\n{_t('character.need_skill_input')}", bot, mention=user_id)
        )

    ok, reply_msg = ci.set_skill(skills)
    if not ok:
        await skill_cmd.finish(md_message(f"\n{reply_msg}", bot, mention=user_id))

    inv = ci.create_investigator(user_id, name)
    attrs = InvestigatorFormatter.format_investigator_info(name, ci.select)
    del _user_states[user_id]

    # 创建完成图片卡片 + 今日冒险按钮；图片失败回退 md
    img = await _render_pic(_create_done_card_html(ci, name))
    if img is not None and await _send_pic(bot, img, skill_cmd.send):
        await skill_cmd.send(
            md_message(
                f"\n{cmd_tag('/今日冒险', show=_t('adventure.adventure_button'))}",
                bot,
                mention=user_id,
            )
        )
        return

    await skill_cmd.finish(
        md_message(
            f"\n{_t('character.create_done', name=inv.name)}\n\n{attrs}",
            bot,
            mention=user_id,
        )
    )


# --- 法术显示辅助 ---
def _spell_data(spell_id: str) -> dict:
    """法术原始数据；无效返回空 dict。"""
    return data_loader.spell_data.get(spell_id) or {}


def _spell_name(spell_id: str) -> str:
    return _spell_data(spell_id).get("name", spell_id)


def _spell_brief(spell_id: str) -> str:
    """法术简述：MP/SAN/效果。"""
    spell = _spell_data(spell_id)
    if not spell:
        return ""
    effect = spell.get("effect", {})
    etype = effect.get("type", "damage")
    dice = effect.get("dice", "")
    effect_key = {
        "damage": "spell.effect_damage",
        "heal": "spell.effect_heal",
        "temp_hp": "spell.effect_temp_hp",
    }.get(etype, "")
    eff = _t(effect_key, dice=dice) if effect_key else ""
    return f"MP {spell.get('mp_cost', 1)}｜SAN {spell.get('san_cost', 0)}｜{eff}"


# --- /调查员信息 ---
_CARD_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "info_card.html"


def _pic_enabled() -> bool:
    return pic_enabled()


def _info_card_html(inv: Investigator, gold: int) -> str:
    """构建调查员信息 HTML 卡片。"""
    attrs = inv.get_full_attributes_dict()
    attrs["HP"] = inv.get_max_hp()
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

    spell_rows = "".join(
        f'<div class="row"><span class="k">{_spell_name(sid)}</span>'
        f'<span class="v">{_spell_brief(sid)}</span></div>'
        for sid in inv.get_spells()
    ) or f'<div class="row"><span class="k">{_t("spell.list_empty")}</span></div>'

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
        .replace("__SPELLS__", spell_rows)
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
    """指令消息：背包「使用」标签 + 底部「今日冒险」按钮。"""
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
    parts = [f"\n**{_t('player.use_hint')}**"]
    if tags:
        parts.append("\n".join(tags))
    parts.append(cmd_tag("/今日冒险", show=_t("adventure.adventure_button")))
    return md_message("\n\n".join(parts), bot, mention=user_id)


async def _send_info_flow(user_id: str, bot: Bot, send: Callable, finish: Callable) -> None:
    """调查员档案发送流程：图片模式双消息（卡片 + 指令标签），否则单条文本。"""
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
    attrs["HP"] = inv.get_max_hp()
    survival = _t("character.dead") if not inv.is_survive else _t("character.survive")

    attr_rows = [_t("character.attr_table_header"), _t("character.attr_table_sep")]
    for k, v in attrs.items():
        attr_rows.append(_t("character.attr_table_row", name=k, value=v))

    spell_lines = [
        f"{_spell_name(sid)}（{_spell_brief(sid)}）" for sid in inv.get_spells()
    ]
    res = (
        f"\n{_t('character.info_title')}\n\n"
        f"{_t('character.info_status', status=survival, day=inv.day, gold=get_info(user_id).gold)}\n\n"
        f"{_t('character.info_attrs')}\n{chr(10).join(attr_rows)}\n\n"
        f"{inv.str_equipments()}\n\n"
        f"{_t('spell_list_title')}\n"
        + ("\n".join(spell_lines) if spell_lines else _t("spell.list_empty"))
    )
    if is_md_enabled():
        res += f"\n\n{cmd_tag('/今日冒险', show=_t('adventure.adventure_button'))}"
    msg = md_message(res, bot, mention=user_id)

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
    if investigator_repo.find_by_qq(user_id) is None:
        await info_cmd.finish(need_create_message(bot, mention=user_id))
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
    if investigator_repo.find_by_qq(user_id) is None:
        await use_item_cmd.finish(need_create_message(bot, mention=user_id))
    item_id = msg.extract_plain_text().strip()
    if not item_id:
        await use_item_cmd.finish(
            md_message(f"\n{_t('player.use_need_id')}", bot, mention=user_id)
        )

    ok, res = investigator_repo.equip_item(user_id, item_id)
    if ok:
        battle = battle_manager.get_battle(user_id)
        if battle:
            battle.investigator.update_equipment()
            if battle.current_turn == "inv":
                battle._update_gun_status()
    await use_item_cmd.finish(md_message(f"\n{res}", bot, mention=user_id))


# --- 按钮回调处理器 ---
async def handle_equip_button(
    user_id: str,
    item_id: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """背包「使用」按钮回调：直接装备物品。"""
    if investigator_repo.find_by_qq(user_id) is None:
        await _send_to_user(
            bot,
            user_id,
            need_create_message(bot, mention=user_id),
            group_openid,
        )
        return
    ok, res = investigator_repo.equip_item(user_id, item_id)
    if ok:
        battle = battle_manager.get_battle(user_id)
        if battle:
            battle.investigator.update_equipment()
            if battle.current_turn == "inv":
                battle._update_gun_status()
    await _send_to_user(
        bot,
        user_id,
        md_message(f"\n{res}", bot, mention=user_id),
        group_openid,
    )


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
        await _send_to_user(
            bot,
            user_id,
            md_message(f"\n{_t('character.need_create')}", bot, mention=user_id),
            group_openid,
        )
        return

    async def _send(msg: Any) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)

    state = _user_states[user_id]
    img = await _render_pic(_choose_success_card_html(state["creator"], state["name"]))
    if img is not None and await _send_pic(bot, img, _send):
        return
    await _send(md_message(reply, bot, mention=user_id))


async def handle_create_button(
    user_id: str,
    payload: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """死亡后「创建调查员」按钮回调：名字必填，引导输入 /创建调查员 <名字>。"""
    await _send_to_user(
        bot,
        user_id,
        md_message(
            f"\n{_t('character.need_name')}\n\n"
            f"{cmd_tag('/创建调查员', show=_t('character.create_button'))}",
            bot,
            mention=user_id,
        ),
        group_openid,
    )


async def handle_info_button(
    user_id: str,
    payload: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """「调查员信息」按钮回调。"""
    if investigator_repo.find_by_qq(user_id) is None:
        await _send_to_user(
            bot,
            user_id,
            need_create_message(bot, mention=user_id),
            group_openid,
        )
        return

    async def _send(msg):
        await _send_to_user(bot, user_id, msg, group_openid)

    await _send_info_flow(user_id, bot, send=_send, finish=_send)


# --- 按钮回调注册 ---
register_button_handler("equip", handle_equip_button)
register_button_handler("choose", handle_choose_button)
register_button_handler("create", handle_create_button)
register_button_handler("info", handle_info_button)
