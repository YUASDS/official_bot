"""结局收集册插件（设计文档 3.5）。

- `/结局` 图鉴总览（10 格 + 信物进度 + 周目数）
- `/结局 <编号>` 结局详情（名称/正文/解锁日期/周目/变体/隐藏线索）
- `/结局 图鉴` 信物收集页（8 件信物，跨周目累计，与当前背包解耦）
- `/结局图鉴` / `/ending` 便捷入口
"""

from __future__ import annotations

from loguru import logger
from typing import Any

import ujson
from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg

from ..models.player import ending_repo
from ..services.battle_cards import ending_card_html
from ..services.data_loader import data_loader
from ..services.ending_engine import (
    _variant_keys,
    ending_card_payload,
    relic_ids,
    relic_name,
    relic_source,
)
from ..utils.image_sender import render_pic, send_pic
from ..utils.md_format import md_message, report_quote, report_section

ending_cmd = on_command(
    "结局", aliases={"结局图鉴", "ending"}, priority=10, block=True
)


def _endings_map() -> dict:
    return ((data_loader.ending_data or {}).get("endings") or {})


def _order() -> list[str]:
    return list((data_loader.ending_data or {}).get("order") or [])


def _load_records(raw: str) -> list[dict[str, Any]]:
    try:
        data = ujson.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except (ValueError, TypeError) as e:
        logger.warning(f"静默异常[ValueError/TypeError] in _load_records: {e}")
        return []


def _load_marks(raw: str) -> dict[str, bool]:
    try:
        data = ujson.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError) as e:
        logger.warning(f"静默异常[ValueError/TypeError] in _load_marks: {e}")
        return {}


def _invoked_as_relic_alias(event: Event) -> bool:
    """以别名「结局图鉴」直接触发时（无参数）进入信物页。"""
    try:
        raw = (event.get_plaintext() or "").strip()
    except Exception as e:
        logger.warning(f"静默异常[Exception] in _invoked_as_relic_alias: {e}")
        return False
    return raw.replace("/", "").replace(" ", "") == "结局图鉴"


def _ending_text_meta(eid: str) -> dict:
    return ((data_loader.text_data or {}).get("ending") or {}).get(eid) or {}


def _variant_text(eid: str, variant: str) -> str:
    """结局变体正文（键映射复用 ending_engine._variant_keys 单源）。"""
    if not variant:
        return ""
    vk = _variant_keys().get(eid, {}).get(variant)
    return _ending_text_meta(eid).get(vk, "") if vk else ""


def _player_name(qq: str) -> str:
    from ..models.player import investigator_repo

    model = investigator_repo.find_by_qq(qq)
    return model.name if model is not None else "调查员"


def _build_overview(qq: str) -> str:
    """图鉴总览：10 格解锁状态 + 信物进度 + 周目数（全读表 B）。"""
    collection = ending_repo.ensure_collection(qq)
    records = _load_records(collection.endings)
    unlocked = {r.get("id") for r in records}
    endings = _endings_map()
    order = _order() or list(endings.keys())

    title = data_loader.get_text(
        "ending.title", default="🌙 结局·{name}", name=_player_name(qq)
    )
    lines = [report_section(title)]
    for eid in order:
        meta = endings.get(eid) or {}
        if eid in unlocked:
            tmeta = _ending_text_meta(eid)
            name = tmeta.get("name") or meta.get("name") or eid
            lines.append(f"✅ {eid} {name}")
        elif meta.get("hidden"):
            continue  # 隐藏结局未解锁：连编号也隐藏（防剧透）
        else:
            lines.append(f"❓ {eid}")

    ids = relic_ids()
    marks = _load_marks(collection.collection)
    owned = sum(1 for rid in ids if marks.get(rid))
    lines.append("")
    footer = data_loader.get_text("ending.footer", default="信物收集")
    runs = data_loader.get_text("ending.runs", default="累计周目")
    lines.append(f"> {footer}：{owned}/{len(ids)} ｜ {runs}：{collection.total_runs}")
    if collection.ng_plus >= 1:
        ng = data_loader.get_text(
            "ending.ng_plus",
            default="新周目加成：Lv.{level}",
            level=collection.ng_plus,
        )
        lines.append(f"> {ng}")
    return "\n".join(lines)


def _build_detail(qq: str, eid: str) -> str:
    """结局详情：正文、解锁日期、达成周目、子变体、隐藏线索。"""
    endings = _endings_map()
    if eid not in endings:
        return data_loader.get_text("ending.not_found", default="没有这个结局编号。")
    meta = endings[eid]
    tmeta = _ending_text_meta(eid)
    name = tmeta.get("name") or meta.get("name", eid)
    etype = meta.get("type", "")

    collection = ending_repo.ensure_collection(qq)
    records = _load_records(collection.endings)
    record = next((r for r in records if r.get("id") == eid), None)

    if record is None:
        if meta.get("hidden"):
            hint = data_loader.get_text(
                "ending.hidden_hint",
                default="隐藏结局，继续你的冒险以发现它。",
            )
            return f"❓ **{eid}（未解锁）**\n> {hint}"
        lines = [f"❓ **{eid} {name}**"]
        if etype:
            lines.append(f"> 类型：{etype}")
        if meta.get("trigger"):
            trigger = data_loader.get_text("ending.trigger", default="达成条件")
            lines.append(report_quote([trigger, meta["trigger"]]))
        return "\n".join(lines)

    lines = [f"✅ **{eid} {name}**"]
    if etype:
        lines.append(f"> 类型：{etype}")
    unlocked_at = data_loader.get_text(
        "ending.unlocked_at",
        default="解锁于 {date}",
        date=record.get("unlocked_at", ""),
    )
    run_text = data_loader.get_text(
        "ending.run", default="第 {run} 周目", run=record.get("run", "?")
    )
    lines.append(f"> {unlocked_at}")
    lines.append(f"> {run_text}")
    variant = record.get("variant")
    variants = meta.get("variants") or []
    vbody = _variant_text(eid, variant) if variant else ""
    if variant:
        lines.append(f"> 变体：{variant}")
    elif variants:
        lines.append(f"> 子变体：{' / '.join(variants)}")
    body = tmeta.get("body") or meta.get("outline") or ""
    if body:
        lines.append("")
        body_title = data_loader.get_text("ending.body", default="正文")
        lines.append(report_section(body_title))
        lines.append(report_quote([body]))
    if vbody:
        lines.append(report_quote([vbody]))
    if meta.get("reach_hint"):
        lines.append("")
        hint_title = data_loader.get_text("ending.hint", default="隐藏线索")
        lines.append(report_section(hint_title))
        lines.append(report_quote([meta["reach_hint"]]))
    return "\n".join(lines)


def _build_relics(qq: str) -> str:
    """信物收集页：8 件信物 已获得/未获得 + 掉落来源（跨周目累计）。"""
    collection = ending_repo.ensure_collection(qq)
    marks = _load_marks(collection.collection)
    ids = relic_ids()
    owned = data_loader.get_text("ending.relic_owned", default="✅ 已获得")
    missing = data_loader.get_text("ending.relic_missing", default="❌ 未获得")

    lines = [
        report_section(
            data_loader.get_text("ending.relic_title", default="信物图鉴")
        )
    ]
    lines.append(
        data_loader.get_text("ending.relic_header", default="| 信物 | 状态 | 来源 |")
    )
    lines.append(
        data_loader.get_text("ending.relic_sep", default="| --- | --- | --- |")
    )
    for rid in ids:
        status = owned if marks.get(rid) else missing
        lines.append(f"| {relic_name(rid)} | {status} | {relic_source(rid)} |")
    count = sum(1 for rid in ids if marks.get(rid))
    lines.append("")
    lines.append(
        f"> {data_loader.get_text('ending.footer', default='信物收集')}："
        f"{count}/{len(ids)}"
    )
    return "\n".join(lines)


def _find_record(qq: str, eid: str) -> dict[str, Any] | None:
    collection = ending_repo.ensure_collection(qq)
    records = _load_records(collection.endings)
    return next((r for r in records if r.get("id") == eid), None)


@ending_cmd.handle()
async def handle_ending(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    arg = msg.extract_plain_text().strip()
    if not arg and _invoked_as_relic_alias(event):
        await ending_cmd.finish(
            md_message(f"\n{_build_relics(user_id)}", bot, mention=user_id)
        )
    if not arg:
        await ending_cmd.finish(
            md_message(f"\n{_build_overview(user_id)}", bot, mention=user_id)
        )
    if arg == "图鉴":
        await ending_cmd.finish(
            md_message(f"\n{_build_relics(user_id)}", bot, mention=user_id)
        )
    eid = arg.upper()
    # 已解锁结局详情：结局卡片优先；渲染失败回退 md 文本
    record = _find_record(user_id, eid) if eid in _endings_map() else None
    if record is not None:
        payload = ending_card_payload(
            eid, variant=record.get("variant") or "", record=record
        )
        img = await render_pic(ending_card_html(**payload))
        if img is not None and await send_pic(bot, img, ending_cmd.send):
            return
    await ending_cmd.finish(
        md_message(f"\n{_build_detail(user_id, eid)}", bot, mention=user_id)
    )
