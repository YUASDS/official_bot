"""结局判定核心服务（设计文档第 3 章）。

实现：条件求值器 eval_option_condition、知识度计算、出口 ①~⑤ 的判定与渲染函数
（check_san_zero / check_daily / on_battle_40_end / render_door_choice /
judge_door_choice）、首杀必掉信物登记与结局登记。

出口对照（3.2）：
- 出口① 战后结算 → on_battle_40_end / render_door_choice / judge_door_choice
- 出口② SAN 归零 → check_san_zero
- 出口④ 每日推进 → check_daily
- 出口⑤ 事件选项 → eval_option_condition / register_relic_obtained
"""

from __future__ import annotations

from typing import Any, Optional

import ujson

from util.DaylyRecord import register_daily_rollover

from ..models.monster import monster_repo
from ..models.player import Investigator, InvestigatorModel, ending_repo
from ..utils.md_format import report_quote, report_section
from .data_loader import data_loader
from .stats_service import snapshot_run

# 知识度信物加成（ending_data.json door.knowledge.bonus_items 缺省时的兜底）
_KNOWLEDGE_BONUS_DEFAULT = {"503": 15, "504": 5, "508": 10}
# 8 件信物（ending_data.json relics.ids 缺省时的兜底）
_RELICS_DEFAULT = ["400", "501", "502", "503", "504", "506", "507", "508"]

_ENDING_TEXT_DEFAULT = {
    "ending.frozen_ended": "结局已结算，庄园不再回应你的呼唤。",
    "ending.frozen_door": "门扉抉择尚未完成，请先做出选择。",
    "ending.day40_guard": (
        "⚠️ 第 40 天怪物池异常：应为守门人（36），请检查 check_point.json。"
    ),
    "ending.relic_first": "🎒 信物收集里程碑：首次获得 {names}。",
    "ending.e10_tick": "庄园已 3 日无人踏入，门窗缓缓合拢。",
    "ending.e10_trigger": "你已连续 3 日未踏入庄园，黎明前的长眠降临。",
}


def _text(key: str, default: str, **kwargs: Any) -> str:
    return data_loader.get_text(key, default=default, **kwargs)


def _ending_data(end_id: str) -> dict:
    return ((data_loader.ending_data or {}).get("endings") or {}).get(end_id) or {}


def _ending_text_meta(end_id: str) -> dict:
    """text_data.json 的 ending.{id} 正文字段（body / name / variant_*）。"""
    return ((data_loader.text_data or {}).get("ending") or {}).get(end_id) or {}


def _variant_text(end_id: str, variant: Optional[str]) -> str:
    """结局变体的专属正文（text_data ending.{id}.variant_*）。"""
    if not variant:
        return ""
    keys = {
        "E01": {"清醒合流": "variant_clear", "崩溃合流": "variant_collapse"},
        "E02": {"圣灯": "variant_light", "歌谣暂封": "variant_song"},
        "E05": {"星光变体": "variant_light"},
    }
    vk = keys.get(end_id, {}).get(variant)
    return _ending_text_meta(end_id).get(vk, "") if vk else ""


def _hold_item(equipments: dict, item_id: str) -> bool:
    return equipments.get(str(item_id), 0) > 0


def _in_range(value: int, spec: Any) -> bool:
    """数值区间求值：dict（eq/min/max）或纯数值（按 min 语义）。"""
    if isinstance(spec, dict):
        if "eq" in spec and value != int(spec["eq"]):
            return False
        if "min" in spec and value < int(spec["min"]):
            return False
        return not ("max" in spec and value > int(spec["max"]))
    return value >= int(spec)


# 首杀必掉信物表兜底（第 5 章：独立于侦查检定的必掉登记；数据源 ending_data.json relics.first_kill）
_FIRST_KILL_DEFAULT = {"30": "503", "33": "504", "34": "507", "35": "508", "37": "502"}


# --- 信物 / 知识度 ---
def relic_ids() -> list[str]:
    return list(
        ((data_loader.ending_data or {}).get("relics") or {}).get("ids")
        or _RELICS_DEFAULT
    )


def relic_name(rid: str) -> str:
    items = ((data_loader.ending_data or {}).get("relics") or {}).get("items") or {}
    meta = items.get(rid) or {}
    return meta.get("name") or rid


def relic_source(rid: str) -> str:
    items = ((data_loader.ending_data or {}).get("relics") or {}).get("items") or {}
    meta = items.get(rid) or {}
    return meta.get("source") or ""


def relics_held(inv: Investigator) -> dict[str, bool]:
    equipments, _ = inv.get_equipments()
    return {rid: _hold_item(equipments, rid) for rid in relic_ids()}


def relics_count(inv: Investigator) -> int:
    return sum(1 for v in relics_held(inv).values() if v)


def e09_relics_count(inv: Investigator) -> int:
    """E09 门扉信物计数：8 件基础 + 替代组任一件持有时 +1（组内不重复计）。

    替代组数据：`ending_data.json relics.substitutes`（如 `[["509","510"]]`），
    509（启的怀表）与 510（主角的徽记）互认替代：缺 1 件基础信物时可由任一件顶替。
    509/510 **不进** `relics.ids`（保持 8 件基数，避免 E09 变 9/9 + RNG 墙）。
    """
    equipments, _ = inv.get_equipments()
    base = sum(1 for rid in relic_ids() if _hold_item(equipments, rid))
    substitutes = (
        ((data_loader.ending_data or {}).get("relics") or {}).get("substitutes") or []
    )
    boss_slot = 0
    for group in substitutes:
        if any(_hold_item(equipments, str(iid)) for iid in group):
            boss_slot += 1
    return base + boss_slot


def knowledge(inv: Investigator) -> int:
    """知识度 = 克苏鲁神话 + 持有 503?15:0 + 504?5:0 + 508?10:0。"""
    cfg = ((data_loader.ending_data or {}).get("door") or {}).get("knowledge") or {}
    bonus_items = cfg.get("bonus_items") or _KNOWLEDGE_BONUS_DEFAULT
    equipments, _ = inv.get_equipments()
    bonus = sum(b for rid, b in bonus_items.items() if _hold_item(equipments, rid))
    return inv.get_skill("克苏鲁神话", 0) + bonus


# --- 条件求值器（3.3）---
def eval_option_condition(
    cond: Any, inv: Investigator, progress: Any = None
) -> bool:
    """事件选项条件求值：all/any 组合 + 物品/SAN/HP/克苏鲁神话/日/进度/已死亡 算子。

    条件为空或含未知键时视为通过（不拦截）。
    """
    if not cond:
        return True
    if isinstance(cond, list):
        return all(eval_option_condition(c, inv, progress) for c in cond)
    if not isinstance(cond, dict):
        return True
    if "all" in cond:
        return all(eval_option_condition(c, inv, progress) for c in cond["all"])
    if "any" in cond:
        return any(eval_option_condition(c, inv, progress) for c in cond["any"])
    for key, value in cond.items():
        if not _eval_single_cond(key, value, inv, progress):
            return False
    return True


def _eval_single_cond(key: str, value: Any, inv: Investigator, progress: Any) -> bool:
    equipments, _ = inv.get_equipments()
    if key == "物品":
        if isinstance(value, list):
            return all(_hold_item(equipments, i) for i in value)
        return _hold_item(equipments, value)
    if key == "SAN":
        return _in_range(inv.get_skill("san", 0), value)
    if key == "HP":
        return _in_range(inv.hp, value)
    if key == "克苏鲁神话":
        return _in_range(inv.get_skill("克苏鲁神话", 0), value)
    if key == "日":
        return _in_range(inv.day, value)
    if key == "进度":
        if progress is None:
            return False
        return bool(getattr(progress, str(value), False))
    if key == "已死亡":
        dead = not inv.is_survive
        return (not dead) if not value else dead
    return True


# --- 结局登记 / 文案 ---
def register_ending(
    inv: Investigator, ending_id: str, variant: Optional[str] = None
) -> None:
    """登记结局到账号级收集（表 B），复用当前周目号。"""
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    ending_repo.add_ending(inv.qq, ending_id, variant=variant, run=progress.run_id)
    # 统计二期：周目快照（写后不理）
    snapshot_run(inv, ending_id, variant, progress=progress)


def _ending_result_text(
    end_id: str, variant: Optional[str] = None, note: str = ""
) -> str:
    meta = _ending_data(end_id)
    tmeta = _ending_text_meta(end_id)
    name = tmeta.get("name") or meta.get("name") or end_id
    body = tmeta.get("body") or meta.get("outline") or ""
    vbody = _variant_text(end_id, variant)
    parts = [f"**{name}**"]
    if meta.get("type"):
        parts.append(f"> 类型：{meta['type']}")
    if variant:
        parts.append(f"> 变体：{variant}")
    if body:
        parts.append(report_quote([body]))
    if vbody:
        parts.append(report_quote([vbody]))
    if note:
        parts.append(f"> {note}")
    return "\n".join(parts)


def ending_meta_rows(
    inv: Optional[Investigator] = None,
    record: Optional[dict] = None,
) -> list[tuple[str, str]]:
    """结局卡片达成信息行（纯展示）：周目/知识度/信物/进度旗标，有的展示无的不展示。

    inv 传入时读本局进度（周目/知识度快照/信物计数/进度旗标）；
    record 传入时（/结局 历史详情）优先展示达成时间与记录周目。
    """
    rows: list[tuple[str, str]] = []
    progress = None
    if inv is not None:
        progress = ending_repo.get_progress(inv.qq)
    run = None
    if record is not None:
        run = record.get("run")
        if record.get("unlocked_at"):
            rows.append(("达成时间", str(record["unlocked_at"])))
    elif progress is not None:
        run = progress.run_id
    elif inv is not None:
        p = ending_repo.ensure_progress(inv.qq, inv.day)
        run = p.run_id
    if run:
        rows.append(("周目", f"第 {int(run)} 周目"))
    if progress is not None and int(progress.knowledge or 0) > 0:
        rows.append(("知识度", str(progress.knowledge)))
    if inv is not None:
        rows.append(("信物", f"{relics_count(inv)}/{len(relic_ids())}"))
    if progress is not None:
        flags = []
        if progress.boss36_defeated:
            flags.append("击败守门人")
        if progress.mirror_defeated:
            flags.append("击败镜中人")
        if progress.dead_once:
            flags.append("战败复活")
        if progress.san_zero_hit:
            flags.append("SAN归零")
        if progress.refought:
            flags.append("重赴门前")
        if flags:
            rows.append(("进度", " / ".join(flags)))
    return rows


def ending_card_payload(
    end_id: str,
    variant: Optional[str] = None,
    note: str = "",
    inv: Optional[Investigator] = None,
    record: Optional[dict] = None,
) -> dict:
    """结局卡片数据载荷（纯展示，不动任何判定/登记逻辑）。

    字段：end_id/name/etype/variant/body/vbody/note/meta_rows，
    直接喂给 battle_cards.ending_card_html 渲染。
    """
    meta = _ending_data(end_id)
    tmeta = _ending_text_meta(end_id)
    name = tmeta.get("name") or meta.get("name") or end_id
    etype = meta.get("type", "")
    body = tmeta.get("body") or meta.get("outline") or ""
    vbody = _variant_text(end_id, variant)
    return {
        "end_id": end_id,
        "name": name,
        "etype": etype,
        "variant": variant or "",
        "body": body,
        "vbody": vbody,
        "note": note,
        "meta_rows": ending_meta_rows(inv=inv, record=record),
    }


# --- 出口②：SAN 归零 → E06 ---
def check_san_zero(inv: Investigator) -> dict:
    """SAN 归零（永久疯狂）→ E06。

    写进度表 san_zero_hit + ended，登记 E06；不破坏现有 is_survive=False 流程，
    返回 block_resurrect 供复活路径拦截（行为对齐建议见 3.4）。
    """
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    already = progress.san_zero_hit and progress.ended
    progress.san_zero_hit = True
    progress.ended = True
    progress.save()
    if not already:
        ending_repo.add_ending(inv.qq, "E06", run=progress.run_id)
    # 统计二期：E06 周目快照（写后不理）
    snapshot_run(inv, "E06", progress=progress)
    note = "你的意志已永久碎裂，成为门廊中低语的一具轮廓；任何复活手段都无法唤回。"
    return {
        "triggered": not already,
        "ending": "E06",
        "block_resurrect": True,
        "note": note,
        "message": _ending_result_text("E06", note=note),
    }


# --- 出口③：非 day40 死亡 → E07 ---
def register_e07(inv: Investigator) -> Optional[str]:
    """非 day40 死亡登记 E07 墓园拾骨。

    仅未持有复活道具（501）时登记（持 501 者可走 /复活，设计上避免 E07）。
    不写 ended：玩家事后购得 501 仍可复活继续（复活链见 resurrect.do_resurrect）。
    返回 E07 结局文案供死亡战报追加展示；未登记返回 None。
    """
    equipments, _ = inv.get_equipments()
    if _hold_item(equipments, "501"):
        return None
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    ending_repo.add_ending(inv.qq, "E07", run=progress.run_id)
    return _ending_result_text(
        "E07",
        note="庄园的阴影吞没了你的尸骨，墓碑上刻着无名的碑文。",
    )


def mark_mirror_defeated(inv: Investigator) -> None:
    """击败镜中之人（37）置位：门扉 H_mirror（E04）隐藏分支前置条件。"""
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    if not progress.mirror_defeated:
        progress.mirror_defeated = True
        progress.save()


# --- 出口④：每日轮转钩子（0 点）→ E10 ---
def daily_rollover() -> list[str]:
    """每日 0 点轮转钩子：对所有存活调查员调用 check_daily(reset=False)，
    累加连续未冒险天数（inactive_days），连续 3 日未冒险登记 E10 黎明前的长眠。

    返回 E10 结局消息（含玩家名），供运营侧广播/日志；既有冻结拦截不重复上报。
    """
    msgs: list[str] = []
    for model in InvestigatorModel.select():
        inv = Investigator(model)
        if not inv.is_survive:
            continue
        before = ending_repo.get_progress(inv.qq)
        _logs, block = check_daily(inv, reset=False)
        if not block:
            continue
        after = ending_repo.get_progress(inv.qq)
        # 仅当本次轮转新结算终局（E10）才上报；冻结拦截（frozen_ended/frozen_door）不提示
        if after is not None and (before is None or not before.ended) and after.ended:
            msgs.append(f"{inv.name}：{block}")
    return msgs


# 注册 E10 每日轮转钩子（0 点自动执行；幂等，重复导入不重复注册）
register_daily_rollover(daily_rollover)


# --- 出口④：每日推进检查 ---
def check_daily(
    inv: Investigator, reset: bool = True
) -> tuple[list[str], Optional[str]]:
    """每日推进检查。

    reset=True（每日开局进行冒险）：inactive_days 清零、day 同步与累计存活天数、
    信物收集里程碑日志、day==40 守卫、结局/门扉冻结拦截。
    reset=False（自然日轮转未冒险）：inactive_days 累加，≥3 触发 E10。

    返回 (日志行, 拦截消息)；拦截消息非 None 时调用方应阻止本次冒险。
    """
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    logs: list[str] = []

    # ④ 结局已结算 / 门扉抉择冻结 → 阻止再次推进
    if progress.ended:
        return [], _text(
            "ending.frozen_ended", _ENDING_TEXT_DEFAULT["ending.frozen_ended"]
        )
    if inv.day >= 40 and (progress.boss36_defeated or progress.dead_once):
        return [], _text(
            "ending.frozen_door", _ENDING_TEXT_DEFAULT["ending.frozen_door"]
        )

    if not reset:
        progress.inactive_days += 1
        if progress.inactive_days >= 3 and inv.is_survive:
            progress.ended = True
            progress.save()
            ending_repo.add_ending(inv.qq, "E10", run=progress.run_id)
            # 统计二期：E10 周目快照（写后不理）
            snapshot_run(inv, "E10", progress=progress)
            return (
                [_text("ending.e10_tick", _ENDING_TEXT_DEFAULT["ending.e10_tick"])],
                _ending_result_text(
                    "E10",
                    note="你已连续 3 日未踏入庄园，黎明前的长眠降临。",
                ),
            )
        progress.save()
        return logs, None

    # ① 进行冒险：连续未冒险天数清零；day 推进时累计存活天数
    progress.inactive_days = 0
    if inv.day != progress.day:
        delta = max(0, inv.day - progress.day)
        if delta:
            collection = ending_repo.ensure_collection(inv.qq)
            collection.total_days += delta
            collection.save()
        progress.day = inv.day

    # ② 信物收集里程碑日志（首获登记）
    logs.extend(_milestone_log(inv, progress))

    # ③ day==40 守卫（确保当日必出守门人 36）
    if inv.day == 40:
        pool = monster_repo._checkpoint_data.get("40") or []
        if pool and set(pool) != {"36"}:
            logs.append(
                _text(
                    "ending.day40_guard",
                    _ENDING_TEXT_DEFAULT["ending.day40_guard"],
                )
            )
    progress.save()
    return logs, None


def register_relic(inv: Investigator, progress: Any, relic_id: str) -> None:
    """登记一件信物到表 A items_first + 表 B collection（幂等，跨周目累计）。"""
    try:
        first_ids = list(ujson.loads(progress.items_first or "[]"))
    except (ValueError, TypeError):
        first_ids = []
    if relic_id not in first_ids:
        first_ids.append(relic_id)
        progress.items_first = ujson.dumps(first_ids, ensure_ascii=False)
        progress.save()
        ending_repo.touch_relic_collection(inv.qq, [relic_id])


def register_relic_obtained(inv: Investigator, relic_id: str) -> None:
    """事件/战利品获得信物时登记收集进度（非信物忽略，幂等）。"""
    if str(relic_id) not in relic_ids():
        return
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    register_relic(inv, progress, str(relic_id))


def _first_kill_table() -> dict:
    """首杀必掉信物表：数据驱动（ending_data.json relics.first_kill），缺省回退代码兜底。"""
    return (
        ((data_loader.ending_data or {}).get("relics") or {}).get("first_kill")
        or _FIRST_KILL_DEFAULT
    )


def first_kill_drop(inv: Investigator, monster_id: str) -> Optional[str]:
    """首杀必掉信物（第 5 章，独立于侦查检定）：登记 items_first 并返回信物 ID。"""
    relic_id = _first_kill_table().get(str(monster_id))
    if not relic_id:
        return None
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    try:
        first_ids = list(ujson.loads(progress.items_first or "[]"))
    except (ValueError, TypeError):
        first_ids = []
    if relic_id in first_ids:
        return None
    register_relic(inv, progress, relic_id)
    return relic_id


def _milestone_log(inv: Investigator, progress: Any) -> list[str]:
    """本次冒险前新出现的信物（背包持有且未登记过）→ 里程碑日志 + collection 标记。"""
    try:
        first_ids = list(ujson.loads(progress.items_first or "[]"))
    except (ValueError, TypeError):
        first_ids = []
    held = relics_held(inv)
    new_ids = [rid for rid, ok in held.items() if ok and rid not in first_ids]
    if not new_ids:
        return []
    for rid in new_ids:
        register_relic(inv, progress, rid)
    names = "、".join(relic_name(rid) for rid in new_ids)
    return [
        _text(
            "ending.relic_first",
            _ENDING_TEXT_DEFAULT["ending.relic_first"],
            names=names,
        )
    ]


# --- 出口①：第 40 天战后结算 ---
def on_battle_40_end(inv: Investigator, win: bool) -> dict:
    """第 40 天战后结算（出口①，1.1/1.3）。

    胜利：冻结 day（不再 +1）、写 boss36_defeated + knowledge 快照、进入门扉抉择。
    战败：持 501 自动消耗复活 → dead_once=True，门扉抉择（含「重赴门前/驻足旁观」）；
          无 501 → 直接 E05（持 508 走星光变体）。

    返回结构化结果供调用方渲染（门扉抉择渲染 render_door_choice 下一批实现）。
    """
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    progress.knowledge = knowledge(inv)

    if win:
        progress.boss36_defeated = True
        # 重赴次数门：任何 day40 胜利（含重赴再战）标记 refought，
        # 已重赴者不再渲染 defeat_choices（防无限免费刷守门人）
        progress.refought = True
        progress.day = inv.day
        inv.day = 40  # 冻结，不再推进
        inv.save()
        progress.save()
        return {
            "frozen": True,
            "door_choice": True,
            "boss36_defeated": True,
            "dead_once": progress.dead_once,
            "mirror_defeated": progress.mirror_defeated,
            "knowledge": progress.knowledge,
        }

    # 战败分支（1.3）
    equipments, _ = inv.get_equipments()
    if _hold_item(equipments, "501"):
        from .resurrect import do_resurrect

        # do_resurrect 以 DB 侧 is_survive=False 为复活前提，先落死亡态
        if inv.is_survive:
            inv.is_survive = False
            inv.save()
        do_resurrect(inv.qq)
        # 同步内存对象（do_resurrect 走 DB，调用方的 inv 需保持一致）
        inv.is_survive = True
        inv.restore_hp()
        inv.set_skill("san", max(0, inv.get_skill("意志", 0)))
        inv.save()
        progress.dead_once = True
        progress.day = inv.day
        progress.save()
        return {
            "frozen": True,
            "revived_dead_once": True,
            "door_choice": True,
            "dead_once": True,
        }

    variant = "星光变体" if _hold_item(equipments, "508") else None
    progress.ended = True
    progress.save()
    ending_repo.add_ending(inv.qq, "E05", variant=variant, run=progress.run_id)
    note = "门后的血肉星云接纳了你，你成为仪式的一部分。"
    return {
        "ended": True,
        "ending": "E05",
        "variant": variant,
        "note": note,
        "message": _ending_result_text("E05", variant=variant, note=note),
    }


# --- 门扉抉择（1.2/1.3 渲染与判定）---
def _door_config() -> dict:
    return ((data_loader.ending_data or {}).get("door") or {})


def _door_rule_ok(rule: Any, inv: Investigator, progress: Any) -> bool:
    """门扉单条规则求值（ending_data.json door.*.condition.rules）。"""
    if not isinstance(rule, dict):
        return True
    rtype = rule.get("type")
    if rtype == "knowledge":
        return _in_range(knowledge(inv), rule)
    if rtype == "san":
        return _in_range(inv.get_skill("san", 0), rule)
    if rtype == "item":
        equipments, _ = inv.get_equipments()
        return _hold_item(equipments, rule.get("id"))
    if rtype == "progress":
        if progress is None:
            return False
        return bool(getattr(progress, str(rule.get("key")), False))
    if rtype == "relics_all":
        return e09_relics_count(inv) >= len(relic_ids())
    return True


def _door_cond_ok(cond: Any, inv: Investigator, progress: Any) -> bool:
    """门扉条件组合求值：and/or 包裹 rules，缺省按单规则。"""
    if not isinstance(cond, dict):
        return True
    ctype = cond.get("type")
    rules = cond.get("rules") or []
    if ctype == "and":
        return all(_door_rule_ok(r, inv, progress) for r in rules)
    if ctype == "or":
        return any(_door_rule_ok(r, inv, progress) for r in rules)
    return _door_rule_ok(cond, inv, progress)


def _door_text(key: str, default: str = "") -> str:
    """门扉文案：优先 door.text_keys 映射，其次按 door.{key} 直取。"""
    cfg = _door_config()
    text_keys = cfg.get("text_keys") or {}
    tk = text_keys.get(key)
    return data_loader.get_text(tk or f"door.{key}", default=default)


def _nested_text(key_path: str, default: str = "") -> str:
    """按点号路径读取 text_data 嵌套字典（get_text 仅支持单层点号）。"""
    node: Any = data_loader.text_data
    for part in key_path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part)
        if node is None:
            return default
    return node if isinstance(node, str) else default


def _choice_payload(key: str, cfg: dict) -> dict:
    """门扉选项展示载荷：key + label + desc。"""
    return {
        "key": cfg.get("key", key),
        "label": _nested_text(
            cfg.get("label_key") or f"door.{key}.label", default=key
        ),
        "desc": _nested_text(
            cfg.get("desc_key") or f"door.{key}.desc", default=""
        ),
    }


def pending_door_choice(inv: Investigator) -> bool:
    """是否存在未完成的门扉抉择（供冻结拦截后重渲染，防 bot 重启后卡死）。"""
    progress = ending_repo.get_progress(inv.qq)
    if progress is None or progress.ended:
        return False
    return progress.boss36_defeated or progress.dead_once


def render_door_choice(inv: Investigator) -> dict:
    """门扉抉择可见选项（1.2/1.3）。

    主分支 A/B/C 恒可见；隐藏分支（碎镜/见证）条件满足才出现；
    战败复活（dead_once）追加「重赴门前/驻足旁观」。返回 {choices, text}。
    """
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    door = _door_config()

    choices: list[dict] = []
    for key in ("A", "B", "C"):
        cfg = (door.get("main_choices") or {}).get(key)
        if cfg:
            choices.append(_choice_payload(key, cfg))
    for key, cfg in (door.get("hidden_choices") or {}).items():
        if _door_cond_ok(cfg.get("condition"), inv, progress):
            choices.append(_choice_payload(key, cfg))
    if progress.dead_once and not progress.refought:
        for key, cfg in (door.get("defeat_choices") or {}).items():
            choices.append(_choice_payload(key, cfg))

    lines = [
        report_section(_door_text("title", "🚪 门扉抉择")),
        _door_text("intro", ""),
    ]
    for c in choices:
        lines.append(f"🔹 `{c['label']}`")
        if c["desc"]:
            lines.append(report_quote([c["desc"]]))
    lines.append("")
    lines.append(
        report_quote([_door_text("frozen_day", "（门扉已敞开，今日之日不再推进。）")])
    )
    return {"choices": choices, "text": "\n".join(lines)}


def _finish_door(
    inv: Investigator,
    progress: Any,
    end_id: Optional[str],
    variant: Optional[str],
    run: int,
) -> dict:
    """结算门扉分支：写进度表 ended/door_choice，登记结局并返回结果消息。"""
    progress.ended = True
    progress.door_choice = progress.door_choice or end_id
    progress.save()
    if end_id:
        ending_repo.add_ending(inv.qq, end_id, variant=variant, run=run)
        # 统计二期：门扉结局周目快照（写后不理）
        snapshot_run(inv, end_id, variant, progress=progress)
        # 周目联动：达成门扉 E01「清醒合流」→ 记录跨周目行为（下周目 D1 门后低语 + D40 只读提示）
        if end_id == "E01":
            ending_repo.record_run_choice(inv.qq, "door", "e01")
    notes = {
        ("E05", "星光变体"): "持格拉基之泪时，星云温柔地环绕你——你终于回家了。",
        ("E01", "清醒合流"): "知识度达标，肉身保留意志。",
        ("E01", "崩溃合流"): "SAN 崩溃，随波逐流。",
        ("E02", "歌谣暂封"): "以咏唱者的残页吟唱安眠曲，门扉沉沉睡去。",
    }
    note = notes.get((end_id, variant), "")
    return {
        "ended": True,
        "ending": end_id,
        "variant": variant,
        "note": note,
        "message": _ending_result_text(end_id, variant=variant, note=note),
    }


def judge_door_choice(inv: Investigator, key: str) -> dict:
    """门扉选项判定（1.2/1.3），写进度表并登记结局。

    返回：
    - 终局：{"ended": True, "ending", "variant", "message"}
    - 重赴：{"refight": True, "message"}（战败分支「重赴门前」）
    - 非法：{"error": True, "message"}
    """
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    door = _door_config()

    # 结局已完成守卫：ended 后禁止重复判定（防旧按钮/重复点击再次登记结局）
    if progress.ended:
        return {
            "error": True,
            "message": _text("door.already_ended", "这段旅程已经落幕。新周目，请重新创建调查员。"),
        }

    # 战败分支（dead_once 后可见；已重赴者回到三分支，不再可选）
    defeat_cfg = (door.get("defeat_choices") or {}).get(key)
    if defeat_cfg:
        if not progress.dead_once or progress.refought:
            return {
                "error": True,
                "message": _text("door.invalid_choice", "无效的门扉选择。"),
            }
        if defeat_cfg.get("end"):
            return _finish_door(inv, progress, defeat_cfg["end"], None, progress.run_id)
        # 次数门：选择「重赴门前」即置位 refought，仅允许重赴一次
        # （即使重赴再战失败/复活，也不再渲染 defeat_choices，防无限刷守门人掉落）
        progress.refought = True
        progress.save()
        return {
            "refight": True,
            "message": _nested_text(
                "door.R_refight.desc", default="回到门前，再战守门人。"
            ),
        }

    # 隐藏分支（碎镜/见证）
    hidden_cfg = (door.get("hidden_choices") or {}).get(key)
    if hidden_cfg:
        if not _door_cond_ok(hidden_cfg.get("condition"), inv, progress):
            return {
                "error": True,
                "message": _text("door.invalid_choice", "无效的门扉选择。"),
            }
        return _finish_door(
            inv, progress, hidden_cfg.get("end"), None, progress.run_id
        )

    # 主分支 A/B/C
    cfg = (door.get("main_choices") or {}).get(key)
    if not cfg:
        return {
            "error": True,
            "message": _text("door.invalid_choice", "无效的门扉选择。"),
        }

    if _door_cond_ok(cfg.get("condition"), inv, progress):
        variant = None
        if key == "A":
            threshold = int(((door.get("knowledge") or {}).get("threshold", 40)))
            variant = "清醒合流" if knowledge(inv) >= threshold else "崩溃合流"
        elif key == "B":
            variant = "圣灯"
        return _finish_door(inv, progress, cfg.get("end"), variant, progress.run_id)

    # 失败回退（B 封印 → 歌谣暂封）
    if key == "B":
        fallback = cfg.get("fallback") or {}
        if _door_cond_ok(fallback.get("condition"), inv, progress):
            return _finish_door(
                inv,
                progress,
                fallback.get("end"),
                fallback.get("variant"),
                progress.run_id,
            )

    variant = None
    if key == "A":
        equipments, _ = inv.get_equipments()
        variant = "星光变体" if _hold_item(equipments, "508") else None
    return _finish_door(inv, progress, cfg.get("fail_end"), variant, progress.run_id)


def reroll_door_choice(inv: Investigator) -> Optional[str]:
    """门扉重掷（梦之碎片·用途 B）：撤销已结算的门扉判定，恢复为待抉择状态。

    仅撤销「门扉抉择」登记的结局（progress.door_choice 非空，即 E01/E02/E03/
    E04/E08/E09 及其变体）；E05 战败 / E06 / E10 等非门扉终局不可重掷。
    判定规则本身不变——玩家获得一次重新选择的机会（重新渲染门扉并再次判定）。

    返回成功提示文本；不可重掷返回 None（调用方不得消耗梦之碎片）。
    """
    progress = ending_repo.get_progress(inv.qq)
    if progress is None or not progress.ended or not progress.door_choice:
        return None
    # 撤销结局登记：从表 B endings 移除本局门扉判定登记的记录（同周目同结局）
    collection = ending_repo.ensure_collection(inv.qq)
    try:
        records = list(ujson.loads(collection.endings or "[]"))
    except (ValueError, TypeError):
        records = []
    target = progress.door_choice
    run = progress.run_id
    removed = False
    for i in range(len(records) - 1, -1, -1):
        rec = records[i]
        if rec.get("id") == target and rec.get("run") == run:
            records.pop(i)
            removed = True
            break
    if removed:
        collection.endings = ujson.dumps(records, ensure_ascii=False)
        collection.ng_plus = len({r.get("id") for r in records if r.get("id")})
        collection.save()
    # 恢复进度表为待抉择状态
    progress.ended = False
    progress.door_choice = ""
    progress.save()
    return _text("dream_fragment.reroll_ok", "门扉在你身后重新合拢，你再次站在那扇门之前。")
