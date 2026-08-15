import ujson

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg

from ..models.player import (
    Investigator,
    InvestigatorFormatter,
    ending_repo,
    investigator_repo,
)
from ..services.character_cards import (
    candidate_card_html,
    choose_success_card_html,
    create_done_card_html,
)
from ..services.character_info import build_info_message, send_info_flow
from ..services.character_service import (
    build_create_reply,
    choose_reply,
    user_states,
)
from ..services.data_loader import data_loader
from ..services.ending_engine import relic_ids
from ..services.equipment_service import equip_item_and_sync
from ..utils.active_battles import battle_manager
from ..utils.buttons import _send_to_user, register_button_handler
from ..utils.image_sender import render_pic, send_pic
from ..utils.md_format import (
    build_keyboard,
    cmd_tag,
    md_message,
    need_create_message,
    report_section,
)

_t = data_loader.get_text


def _build_legacy_summary(qq: str) -> str:
    """前尘往事：账号历史结局图鉴摘要（创建成功时追加展示）。

    轻量版：只列已解锁结局（✅ E0X 名称），不剧透未解锁/隐藏结局；
    复用 ending._build_overview 的渲染逻辑，但用 get_collection（无历史不建行）
    且只展示已解锁项，故不复用其完整文本。无历史记录返回空串。
    """
    collection = ending_repo.get_collection(qq)
    if collection is None:
        return ""
    try:
        records = ujson.loads(collection.endings or "[]")
    except (ValueError, TypeError):
        records = []
    if not isinstance(records, list) or not records:
        return ""

    unlocked = {r.get("id") for r in records if r.get("id")}
    endings = (data_loader.ending_data or {}).get("endings") or {}
    order = (data_loader.ending_data or {}).get("order") or list(endings.keys())

    lines = [
        report_section(_t("character.legacy_title", default="📜 前尘往事"))
    ]
    for eid in order:
        if eid not in unlocked:
            continue
        meta = endings.get(eid) or {}
        tmeta = ((data_loader.text_data or {}).get("ending") or {}).get(eid) or {}
        name = tmeta.get("name") or meta.get("name") or eid
        lines.append(f"✅ {eid} {name}")

    marks = {}
    try:
        marks = ujson.loads(collection.collection or "{}")
    except (ValueError, TypeError):
        marks = {}
    if not isinstance(marks, dict):
        marks = {}
    ids = relic_ids()
    owned = sum(1 for rid in ids if marks.get(rid))
    footer = _t("ending.footer", default="信物收集")
    runs = _t("ending.runs", default="累计周目")
    lines.append("")
    lines.append(
        f"> {footer}：{owned}/{len(ids)} ｜ {runs}：{collection.total_runs}"
    )
    return "\n".join(lines)


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

new_chapter_cmd = on_command(
    "迈向新篇", aliases={"new_chapter"}, priority=10, block=True
)

query_cmd = on_command(
    "查询", aliases={"item_info", "物品查询"}, priority=10, block=True
)

_ITEM_TYPE_LABELS = {
    "武器": "武器",
    "防具": "防具",
    "饰品": "饰品",
    "spell_scroll": "法术残卷",
    "misc": "道具",
}

_ITEM_PART_LABELS = {
    "近战": "近战",
    "远程": "远程",
    "防具": "防具",
    "饰品": "饰品",
    "法术": "法术",
    "misc": "道具",
}


def query_item_info(user_id: str, item_id: str) -> str:
    """查询物品信息（MD 文本）。

    visible=false 的物品（信物/管理员系列/JK 掉落/未实装等）对非 GM 返回
    「未知物品」——不泄露其存在；GM（is_master）不受限。
    """
    from ..models.item import Equipment
    from .gm import is_master

    raw = (data_loader.goods_data or {}).get(item_id)
    if raw is None or (not raw.get("visible", True) and not is_master(user_id)):
        return _t("player.unknown_item")

    item = Equipment(item_id)
    fields = [
        (_t("item.label_id"), item.id),
        (_t("item.label_name"), item.name),
        (_t("item.label_type"), _ITEM_TYPE_LABELS.get(item.type, item.type)),
        (_t("item.label_part"), _ITEM_PART_LABELS.get(item.part, item.part or "—")),
        (
            _t("item.label_damage"),
            item.damage_dice if item.damage_dice and item.damage_dice != "0" else "",
        ),
        (_t("item.label_armor"), str(item.armor_point) if item.armor_point else ""),
        (
            _t("item.label_skill"),
            ", ".join(item.skill_bonus)
            if isinstance(item.skill_bonus, list)
            else str(item.skill_bonus or ""),
        ),
        (_t("item.label_price"), str(item.price) if item.price else ""),
        (_t("item.label_desc"), item.description),
    ]
    lines = [_t("item.info_title")]
    for label, value in fields:
        if not value:
            continue
        lines.append(_t("item.row", label=label, value=value))
    return "\n".join(lines)


# --- /迈向新篇 ---
@new_chapter_cmd.handle()
async def handle_new_chapter(event: Event, bot: Bot):
    """结局完成后开启新周目：解除存活拦截 + 清理残留状态 + 引导创建调查员。"""
    user_id = event.get_user_id()
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        await new_chapter_cmd.finish(
            md_message(
                f"\n{_t('character.need_create', default='请先创建调查员。')}\n\n"
                f"{cmd_tag('/创建调查员', show=_t('character.create_button'))}",
                bot,
                mention=user_id,
            )
        )
    inv = Investigator(inv_model)
    progress = ending_repo.get_progress(user_id)

    # 结局已完成 → 解除存活拦截 + 清理状态 + 引导创建
    if progress is not None and progress.ended:
        inv.is_survive = False
        inv.save()
        _cleanup_ended_state(user_id)
        await new_chapter_cmd.finish(
            md_message(
                f"\n{_t('character.new_chapter_old_ended', default='旧章已合上，新的故事等你落笔。')}\n\n"
                f"{cmd_tag('/创建调查员', show=_t('character.create_button'))}",
                bot,
                mention=user_id,
            )
        )

    # 冒险未结束
    if inv.is_survive:
        await new_chapter_cmd.finish(
            md_message(
                f"\n{_t('character.new_chapter_alive', default='你的旅程仍在继续，不必急着开启新篇。')}\n\n"
                f"{cmd_tag('/调查员信息', show=_t('character.info_button'))}\n"
                f"{cmd_tag('/今日冒险', show=_t('adventure.adventure_button'))}",
                bot,
                mention=user_id,
            )
        )

    # 已死亡（E07 等）→ 直接引导创建
    await new_chapter_cmd.finish(
        md_message(
            f"\n{_t('character.new_chapter_dead', default='尘埃落定，是时候书写新的故事了。')}\n\n"
            f"{cmd_tag('/创建调查员', show=_t('character.create_button'))}",
            bot,
            mention=user_id,
        )
    )


def _cleanup_ended_state(user_id: str) -> None:
    """结局后清理残留状态：战斗 / 门扉 / 事件 / NPC / 乱入 / GM / 启挑战。"""
    # 延迟导入避免插件间循环依赖（adventure 等插件在运行期才加载完整）
    stores = []
    try:
        battle_manager.remove_battle(user_id)
    except Exception:
        pass
    try:
        from .adventure import door_states

        stores.append(door_states)
    except Exception:
        pass
    try:
        from ..services.event_service import event_states

        stores.append(event_states)
    except Exception:
        pass
    try:
        from .guest import guest_active

        stores.append(guest_active)
    except Exception:
        pass
    try:
        from .gm_room import gm_room_active

        stores.append(gm_room_active)
    except Exception:
        pass
    try:
        from .qiren import qiren_pending

        stores.append(qiren_pending)
    except Exception:
        pass
    try:
        from .npc import npc_states

        stores.append(npc_states)
    except Exception:
        pass
    for store in stores:
        try:
            store.pop(user_id, None)
        except Exception:
            pass


# --- /创建调查员 ---
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
    ci = user_states[user_id]["creator"]

    # 候选列表图片卡片（全平台）+ 选择按钮；图片失败回退 md
    img = await render_pic(candidate_card_html(ci.investigators_data))
    if img is not None and await send_pic(bot, img, create_cmd.send):
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

    if user_id not in user_states or "creator" not in user_states.get(user_id, {}):
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
    state = user_states[user_id]
    img = await render_pic(choose_success_card_html(state["creator"], state["name"]))
    if img is not None and await send_pic(bot, img, choose_cmd.send):
        return
    await choose_cmd.finish(md_message(reply, bot, mention=user_id))


# --- /st <skills> ---
@skill_cmd.handle()
async def handle_skill(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    state = user_states.get(user_id)

    if not state or "creator" not in state:
        await skill_cmd.finish(
            md_message(f"\n{_t('character.need_create')}", bot, mention=user_id)
        )

    ci = state["creator"]
    name = state["name"]
    skills = msg.extract_plain_text().strip()

    if not skills:
        await skill_cmd.finish(
            md_message(f"\n{_t('character.need_skill_input')}", bot, mention=user_id)
        )

    ok, reply_msg = ci.set_skill(skills)
    if not ok:
        await skill_cmd.finish(md_message(f"\n{reply_msg}", bot, mention=user_id))

    # 历史结局图鉴（在 new_run 之前取，total_runs 为「已完成周目」，更贴合前尘往事）
    legacy = _build_legacy_summary(user_id)

    inv = ci.create_investigator(user_id, name)
    attrs = InvestigatorFormatter.format_investigator_info(name, ci.select)
    del user_states[user_id]

    # 创建完成图片卡片 + 今日冒险按钮；图片失败回退 md
    img = await render_pic(create_done_card_html(ci, name))
    if img is not None and await send_pic(bot, img, skill_cmd.send):
        tail = cmd_tag('/今日冒险', show=_t('adventure.adventure_button'))
        if legacy:
            tail = f"{legacy}\n\n{tail}"
        await skill_cmd.send(
            md_message(
                f"\n{tail}",
                bot,
                mention=user_id,
            )
        )
        return

    text = f"\n{_t('character.create_done', name=inv.name)}\n\n{attrs}"
    if legacy:
        text += f"\n\n{legacy}"
    await skill_cmd.finish(md_message(text, bot, mention=user_id))


# --- /调查员信息 ---
@info_cmd.handle()
async def handle_info(event: Event, bot: Bot):
    user_id = event.get_user_id()
    if investigator_repo.find_by_qq(user_id) is None:
        await info_cmd.finish(need_create_message(bot, mention=user_id))
    await send_info_flow(
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

    ok, res = equip_item_and_sync(user_id, item_id)
    await use_item_cmd.finish(md_message(f"\n{res}", bot, mention=user_id))


# --- /查询 <物品ID> ---
@query_cmd.handle()
async def handle_query(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    item_id = msg.extract_plain_text().strip()
    if not item_id:
        await query_cmd.finish(
            md_message(f"\n{_t('item.need_id')}", bot, mention=user_id)
        )
    await query_cmd.finish(
        md_message(f"\n{query_item_info(user_id, item_id)}", bot, mention=user_id)
    )


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
    ok, res = equip_item_and_sync(user_id, item_id)
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

    async def _send(msg) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)

    state = user_states[user_id]
    img = await render_pic(choose_success_card_html(state["creator"], state["name"]))
    if img is not None and await send_pic(bot, img, _send):
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

    await send_info_flow(user_id, bot, send=_send, finish=_send)


# --- 按钮回调注册 ---
register_button_handler("equip", handle_equip_button)
register_button_handler("choose", handle_choose_button)
register_button_handler("create", handle_create_button)
register_button_handler("info", handle_info_button)
