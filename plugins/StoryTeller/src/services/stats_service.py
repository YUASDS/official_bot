"""统计系统二期 · 埋点写入服务（写后不理，绝不回抛影响主流程）。

- 全部写入 try/except 吞异常并记日志，异常不阻断游戏主流程；
- 表落 inv.db（见 `models.stats`），模型导入时自动建表；
- 提供 battle / run_stats / gold_ledger / event_logs / growth_logs / daily_stats
  六类写入函数，以及 gold_ledger 的 source 上下文标记。

设计规格：`.qa/reports/stats-design.md` §4.1/§4.2/§4.3。
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime
from typing import Any, Iterator, Optional

import ujson
from loguru import logger

from ..models.player import ending_repo

# 经济流水来源上下文：调用点用 gold_source() 标记本次 add_gold/reduce_gold 的业务来源
_GOLD_CTX: contextvars.ContextVar = contextvars.ContextVar(
    "stats_gold_ctx", default=None
)

_DAILY_AGG_DATE_FMT = "%Y-%m-%d"
_TS_FMT = "%Y-%m-%d %H:%M:%S"


@contextlib.contextmanager
def gold_source(
    source: str, run_id: Optional[int] = None, ref_id: str = ""
) -> Iterator[None]:
    """为包围的 add_gold/reduce_gold 调用标记经济流水来源（幂等栈式）。"""
    token = _GOLD_CTX.set({"source": source, "run_id": run_id, "ref_id": ref_id})
    try:
        yield
    finally:
        _GOLD_CTX.reset(token)


def _gold_ctx_meta() -> dict:
    ctx = _GOLD_CTX.get()
    if not ctx:
        return {"source": "other", "run_id": None, "ref_id": ""}
    return ctx


def _now_ts() -> str:
    return datetime.datetime.now().strftime(_TS_FMT)


def _run_id_of(qq: str) -> int:
    try:
        progress = ending_repo.get_progress(qq)
        if progress is not None:
            return int(progress.run_id or 0)
    except Exception:
        pass
    return 0


# --- 战斗流水 ---
def record_battle(
    qq: str,
    day: int,
    monster_id: str,
    environment: str,
    result: str,
    turns: int,
    dmg_dealt: int,
    dmg_taken: int,
    armor_absorbed: int,
    san_loss: int,
    madness: list,
    consumables: dict,
    spells_cast: dict,
    fled: bool,
) -> None:
    """写一行 battle_logs。异常一律吞掉，绝不回抛。"""
    try:
        from ..models.stats import BattleLog

        BattleLog.create(
            qq=str(qq),
            run_id=_run_id_of(qq),
            day=int(day or 0),
            monster_id=str(monster_id),
            environment=str(environment),
            result=str(result),
            turns=int(turns or 0),
            dmg_dealt=int(dmg_dealt or 0),
            dmg_taken=int(dmg_taken or 0),
            armor_absorbed=int(armor_absorbed or 0),
            san_loss=int(san_loss or 0),
            madness=ujson.dumps(list(madness or []), ensure_ascii=False),
            consumables=ujson.dumps(dict(consumables or {}), ensure_ascii=False),
            spells_cast=ujson.dumps(dict(spells_cast or {}), ensure_ascii=False),
            fled=bool(fled),
            created_at=_now_ts(),
        )
    except Exception as e:  # noqa: BLE001 - 统计写后不理
        logger.warning(f"[stats] battle_logs 写入失败: {type(e).__name__} {e}")


# --- 周目快照 ---
def _run_kills(qq: str, run_id: int) -> dict:
    """本局击杀（result=win 按怪物计数）。"""
    try:
        from ..models.stats import BattleLog

        rows = BattleLog.select().where(
            (BattleLog.qq == str(qq))
            & (BattleLog.run_id == int(run_id))
            & (BattleLog.result == "win")
        )
        counts: dict[str, int] = {}
        for row in rows:
            mid = str(row.monster_id or "")
            counts[mid] = counts.get(mid, 0) + 1
        return counts
    except Exception:  # noqa: BLE001
        return {}


def _run_battle_agg(qq: str, run_id: int) -> tuple[int, int, int]:
    """本局 (battles, flees, deaths)。"""
    try:
        from ..models.stats import BattleLog

        battles = (
            BattleLog.select()
            .where((BattleLog.qq == str(qq)) & (BattleLog.run_id == int(run_id)))
            .count()
        )
        flees = (
            BattleLog.select()
            .where(
                (BattleLog.qq == str(qq))
                & (BattleLog.run_id == int(run_id))
                & (BattleLog.result == "flee")
            )
            .count()
        )
        deaths = (
            BattleLog.select()
            .where(
                (BattleLog.qq == str(qq))
                & (BattleLog.run_id == int(run_id))
                & (BattleLog.result.in_(("death", "revived")))
            )
            .count()
        )
        return battles, flees, deaths
    except Exception:  # noqa: BLE001
        return 0, 0, 0


def _run_gold_net(qq: str, run_id: int) -> int:
    """本局乌帕净收益（gold_ledger 求和）。"""
    try:
        from ..models.stats import GoldLedger

        rows = GoldLedger.select(GoldLedger.delta).where(
            (GoldLedger.qq == str(qq)) & (GoldLedger.run_id == int(run_id))
        )
        return sum(int(row.delta or 0) for row in rows)
    except Exception:  # noqa: BLE001
        return 0


def _knowledge_of(inv: Any) -> int:
    """知识度 = 克苏鲁神话 + 信物加成（对齐 ending_engine.knowledge）。"""
    try:
        from ..services.ending_engine import knowledge

        return int(knowledge(inv))
    except Exception:  # noqa: BLE001
        try:
            mythos = int(inv.get_skill("克苏鲁神话", 0))
            eq, _ = inv.get_equipments()
            bonus = 0
            for rid, b in ({"503": 15, "504": 5, "508": 10}).items():
                if eq.get(rid, 0) > 0:
                    bonus += b
            return mythos + bonus
        except Exception:  # noqa: BLE001
            return 0


def _relics_of(inv: Any) -> int:
    """当前持有信物数（对齐 ending_engine.relics_count）。"""
    try:
        from ..services.ending_engine import relics_count

        return int(relics_count(inv))
    except Exception:  # noqa: BLE001
        return 0


def snapshot_run(
    inv: Any,
    ending_id: str = "",
    variant: Optional[str] = None,
    progress: Any = None,
) -> None:
    """写一行 run_stats 周目快照（幂等：已有正式结局不覆盖）。

    ending_id 为空表示"未结算局兜底快照"（new_run 重建前）。
    """
    try:
        from ..models.stats import RunStats

        qq = str(inv.qq)
        if progress is None:
            progress = ending_repo.get_progress(qq)
        if progress is None:
            return
        run_id = int(progress.run_id or 1)
        existing = (
            RunStats.select()
            .where((RunStats.qq == qq) & (RunStats.run_id == run_id))
            .first()
        )
        # 幂等：已有正式结局（ending_id 非空）时不再覆盖
        if existing is not None and existing.ending_id:
            return
        # 本次为兜底（ending_id 空）且已有兜底行 → 直接跳过（避免重复累加计数）
        if existing is not None and not ending_id:
            return

        kills = _run_kills(qq, run_id)
        battles, flees, deaths = _run_battle_agg(qq, run_id)
        spells = inv.get_spells()
        payload = {
            "ended_at": _now_ts(),
            "ending_id": str(ending_id or ""),
            "variant": str(variant or ""),
            "days_survived": int(getattr(inv, "day", 0) or 0),
            "final_san": int(inv.get_skill("san", 0) or 0),
            "final_hp": int(getattr(inv, "hp", 0) or 0),
            "knowledge": _knowledge_of(inv),
            "mythos_gained": int(inv.get_skill("克苏鲁神话", 0) or 0),
            "door_choice": str(getattr(progress, "door_choice", "") or ""),
            "boss36_defeated": bool(getattr(progress, "boss36_defeated", False)),
            "dead_once": bool(getattr(progress, "dead_once", False)),
            "refought": bool(getattr(progress, "refought", False)),
            "mirror_defeated": bool(getattr(progress, "mirror_defeated", False)),
            "fled_day40": bool(getattr(progress, "fled_day40", False)),
            "san_zero_hit": bool(getattr(progress, "san_zero_hit", False)),
            "kills": ujson.dumps(kills, ensure_ascii=False),
            "relics_count": _relics_of(inv),
            "spells_count": len(spells),
            "gold_net": _run_gold_net(qq, run_id),
            "battles": battles,
            "flees": flees,
            "deaths": deaths,
        }
        if existing is None:
            RunStats.create(qq=qq, run_id=run_id, **payload)
        else:
            for key, value in payload.items():
                setattr(existing, key, value)
            existing.save()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[stats] run_stats 写入失败: {type(e).__name__} {e}")


# --- 经济流水 ---
def record_gold(
    qq: str,
    delta: int,
    source: Optional[str] = None,
    run_id: Optional[int] = None,
    ref_id: str = "",
) -> None:
    """写一行 gold_ledger（balance_after 冗余快照来自 userData.db 余额）。"""
    try:
        from database.db import get_info

        from ..models.stats import GoldLedger

        balance_after = int(get_info(qq).gold)
        ctx = _gold_ctx_meta()
        if source is None:
            source = ctx["source"] or "other"
        if run_id is None:
            run_id = ctx["run_id"]
        if not ref_id:
            ref_id = ctx["ref_id"] or ""
        if run_id is None:
            run_id = _run_id_of(qq)
        GoldLedger.create(
            qq=str(qq),
            run_id=int(run_id or 0),
            ts=_now_ts(),
            delta=int(delta or 0),
            balance_after=balance_after,
            source=str(source),
            ref_id=str(ref_id),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[stats] gold_ledger 写入失败: {type(e).__name__} {e}")


# --- 事件 / 成长流水 ---
def _find_event_key(option: dict) -> str:
    try:
        from ..services.data_loader import data_loader

        for key, ev in data_loader.event_data.items():
            for opt in (ev.get("选项") or []):
                if isinstance(opt, dict) and opt.get("输入") == option.get("输入"):
                    return str(key)
    except Exception:  # noqa: BLE001
        pass
    return ""


def record_event(
    inv: Any,
    option: dict,
    check_skill: str = "",
    passed: Optional[bool] = None,
    effects: Optional[dict] = None,
) -> None:
    """写一行 event_logs（效果应用后调用）。"""
    try:
        from ..models.stats import EventLog

        EventLog.create(
            qq=str(inv.qq),
            run_id=_run_id_of(inv.qq),
            day=int(getattr(inv, "day", 0) or 0),
            event_key=_find_event_key(option or {}),
            option=str((option or {}).get("输入", "")),
            check_skill=str(check_skill or ""),
            passed=1 if passed else (0 if passed is False else -1),
            effects=ujson.dumps(dict(effects or {}), ensure_ascii=False),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[stats] event_logs 写入失败: {type(e).__name__} {e}")


def record_growth(
    qq: str,
    day: int,
    skill: str,
    before: int,
    after: int,
    source: str = "battle",
) -> None:
    """写一行 growth_logs。"""
    try:
        from ..models.stats import GrowthLog

        GrowthLog.create(
            qq=str(qq),
            run_id=_run_id_of(qq),
            day=int(day or 0),
            skill=str(skill),
            before=int(before or 0),
            after=int(after or 0),
            source=str(source),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[stats] growth_logs 写入失败: {type(e).__name__} {e}")


# --- 每日汇总（0 点轮转钩子）---
def _agg_date() -> str:
    """聚合目标自然日：0 点轮转时为"昨天"（前一个自然日）。"""
    return (datetime.datetime.now() - datetime.timedelta(days=1)).strftime(
        _DAILY_AGG_DATE_FMT
    )


def _daily_stats_snapshot() -> list[str]:
    """每日 0 点：对前一自然日做全服汇总快照（写后不理，返回空消息列表）。"""
    try:
        from ..models.stats import BattleLog, DailyStat, GoldLedger, RunStats

        date = _agg_date()
        prefix = f"{date}%"

        # 活跃玩家：当日 battle/gold 任意一条的去重 qq 数
        qqs: set[str] = set()
        for model, col in (
            (BattleLog, BattleLog.created_at),
            (GoldLedger, GoldLedger.ts),
        ):
            try:
                rows = model.select(model.qq).where(col.startswith(prefix))
                qqs.update(str(r.qq) for r in rows)
            except Exception:  # noqa: BLE001
                pass

        # 战斗统计
        battles = (
            BattleLog.select()
            .where(BattleLog.created_at.startswith(prefix))
            .count()
        )
        wins = (
            BattleLog.select()
            .where(
                (BattleLog.created_at.startswith(prefix))
                & (BattleLog.result == "win")
            )
            .count()
        )
        deaths = (
            BattleLog.select()
            .where(
                (BattleLog.created_at.startswith(prefix))
                & (BattleLog.result.in_(("death", "revived")))
            )
            .count()
        )
        avg_day_row = (
            BattleLog.select(BattleLog.day)
            .where(BattleLog.created_at.startswith(prefix))
        )
        days = [int(r.day or 0) for r in avg_day_row]
        avg_day = sum(days) // len(days) if days else 0

        # 经济统计
        gold_rows = GoldLedger.select(GoldLedger.delta).where(
            GoldLedger.ts.startswith(prefix)
        )
        gold_in = sum(max(0, int(r.delta or 0)) for r in gold_rows)
        gold_out = sum(max(0, -int(r.delta or 0)) for r in gold_rows)

        # 结局统计
        ending_counts: dict[str, int] = {}
        end_rows = RunStats.select(RunStats.ending_id).where(
            RunStats.ended_at.startswith(prefix)
        )
        for r in end_rows:
            eid = str(r.ending_id or "")
            if eid:
                ending_counts[eid] = ending_counts.get(eid, 0) + 1

        row = DailyStat.select().where(DailyStat.date == date).first()
        payload = {
            "active_players": len(qqs),
            "adventures": battles,
            "battles": battles,
            "wins": wins,
            "deaths": deaths,
            "endings": ujson.dumps(ending_counts, ensure_ascii=False),
            "gold_in": gold_in,
            "gold_out": gold_out,
            "avg_day": avg_day,
        }
        if row is None:
            DailyStat.create(date=date, **payload)
        else:
            for key, value in payload.items():
                setattr(row, key, value)
            row.save()
    except Exception as e:  # noqa: BLE001 - 单日汇总失败不阻断每日刷新
        logger.warning(f"[stats] daily_stats 汇总失败: {type(e).__name__} {e}")
    return []


# 注册 0 点每日轮转钩子（复用结局引擎已注册机制；幂等，重复导入不重复注册）
try:
    from util.DaylyRecord import register_daily_rollover

    register_daily_rollover(_daily_stats_snapshot)
except Exception as e:  # noqa: BLE001
    logger.warning(f"[stats] daily_stats 轮转注册失败: {type(e).__name__} {e}")
