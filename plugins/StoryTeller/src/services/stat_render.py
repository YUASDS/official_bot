"""统计系统三期 · 展示层渲染服务（只读统计表，输出 md 文本）。

- 只读 run_stats / battle_logs / gold_ledger / growth_logs / daily_stats /
  ending_collection / userData.db(user_info)，绝不写任何游戏/统计表；
- 命令无数据时返回友好空态提示；
- 对外 qq 一律脱敏（昵称 + 尾号）。
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

import ujson

from ..models.player import ending_repo, investigator_repo
from ..utils.md_format import report_quote, report_section
from .battle_cards import get_display_config
from .data_loader import data_loader

_t = data_loader.get_text

# 展示配置段（display_data.json `stat`，缺失回退默认）
_DISPLAY_STAT = get_display_config().get("stat") or {}

# 结局图鉴总数（E01~E10，对齐 stats-design.md A3）
_TOTAL_ENDINGS_DEFAULT = 10
_total_endings_cfg = _DISPLAY_STAT.get("total_endings")
TOTAL_ENDINGS = (
    _total_endings_cfg if isinstance(_total_endings_cfg, int) else _TOTAL_ENDINGS_DEFAULT
)

# run_stats.door_choice 存储终局 id（_finish_door 落 end_id）或空 → 展示标签
_DOOR_LABELS_DEFAULT = {
    "E01": "A门·合流",
    "E02": "B门·封印",
    "E03": "C门·离去",
    "E04": "碎镜·自由",
    "E05": "战败归于门",
    "E06": "永夜低语",
    "E07": "墓园拾骨",
    "E08": "驻足旁观",
    "E09": "见证·庄园",
    "E10": "长眠",
}
_DOOR_LABELS = dict(_DOOR_LABELS_DEFAULT)
_door_labels_cfg = _DISPLAY_STAT.get("door_labels")
if isinstance(_door_labels_cfg, dict):
    _DOOR_LABELS.update({k: v for k, v in _door_labels_cfg.items() if v is not None})

_SPELL_NAME_CACHE: dict[str, str] = {}
_MONSTER_NAME_CACHE: dict[str, str] = {}


# --- JSON 解析（异常一律回退空值，防脏数据炸渲染）---
def _load_records(raw: str) -> list[dict[str, Any]]:
    try:
        data = ujson.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _load_marks(raw: str) -> dict[str, Any]:
    try:
        data = ujson.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _load_dict(raw: str) -> dict[str, Any]:
    try:
        data = ujson.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _load_list(raw: str) -> list[Any]:
    try:
        data = ujson.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


# --- 通用辅助 ---
def _spell_name(spell_id: str) -> str:
    if spell_id not in _SPELL_NAME_CACHE:
        meta = (data_loader.spell_data or {}).get(spell_id) or {}
        _SPELL_NAME_CACHE[spell_id] = meta.get("name") or spell_id
    return _SPELL_NAME_CACHE[spell_id]


def _monster_name(monster_id: str) -> str:
    if monster_id not in _MONSTER_NAME_CACHE:
        meta = (data_loader.monster_data or {}).get(str(monster_id)) or {}
        _MONSTER_NAME_CACHE[monster_id] = meta.get("名字") or monster_id
    return _MONSTER_NAME_CACHE[monster_id]


_TAIL_LEN = 4


def _mask_qq(qq: str) -> str:
    """对外展示脱敏：昵称 + qq 尾号；无昵称仅尾号。"""
    qq = str(qq or "")
    tail = qq[-_TAIL_LEN:] if len(qq) >= _TAIL_LEN else qq
    inv = investigator_repo.find_by_qq(qq)
    name = inv.name if inv is not None else ""
    return f"{name}·{tail}" if name else tail


def _ending_name(eid: str) -> str:
    meta = ((data_loader.text_data or {}).get("ending") or {}).get(eid) or {}
    return meta.get("name") or eid


def _door_label(value: str) -> str:
    value = str(value or "")
    return _DOOR_LABELS.get(value) or (value or "未达门扉")


def _current_gold(qq: str) -> int:
    """只读 userData.db 余额（不存在用户返回 0，不落库）。"""
    try:
        from database.db import User

        row = User.select(User.gold).where(User.user_id == str(qq)).first()
        return int(row.gold or 0) if row is not None else 0
    except Exception:  # noqa: BLE001 - 只读容错
        return 0


def _empty(key: str, default: str) -> str:
    return _t(key, default=default)


# --- /统计：个人聚合 ---
def _battle_agg(qq: str) -> dict[str, int]:
    try:
        from ..models.stats import BattleLog

        q = BattleLog.select().where(BattleLog.qq == str(qq))
        total = q.count()
        wins = (
            BattleLog.select()
            .where((BattleLog.qq == str(qq)) & (BattleLog.result == "win"))
            .count()
        )
        flees = (
            BattleLog.select()
            .where((BattleLog.qq == str(qq)) & (BattleLog.result == "flee"))
            .count()
        )
        deaths = (
            BattleLog.select()
            .where(
                (BattleLog.qq == str(qq))
                & (BattleLog.result.in_(("death", "revived")))
            )
            .count()
        )
        return {"battles": total, "wins": wins, "flees": flees, "deaths": deaths}
    except Exception:  # noqa: BLE001
        return {"battles": 0, "wins": 0, "flees": 0, "deaths": 0}


def _gold_agg(qq: str) -> dict[str, int]:
    try:
        from ..models.stats import GoldLedger

        rows = GoldLedger.select(GoldLedger.delta).where(
            GoldLedger.qq == str(qq)
        )
        income = sum(max(0, int(r.delta or 0)) for r in rows)
        expense = sum(max(0, -int(r.delta or 0)) for r in rows)
        return {"income": income, "expense": expense}
    except Exception:  # noqa: BLE001
        return {"income": 0, "expense": 0}


def _growth_top(qq: str, limit: int = 5) -> list[tuple[str, int, int]]:
    """技能成长 Top：按成长次数排序，附累计增量。"""
    try:
        from ..models.stats import GrowthLog

        rows = GrowthLog.select(
            GrowthLog.skill, GrowthLog.before, GrowthLog.after
        ).where(GrowthLog.qq == str(qq))
        agg: dict[str, list[int]] = {}
        for r in rows:
            skill = str(r.skill or "")
            if not skill:
                continue
            entry = agg.setdefault(skill, [0, 0])
            entry[0] += 1
            entry[1] += max(0, int(r.after or 0) - int(r.before or 0))
        ranked = sorted(agg.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))
        return [(skill, cnt, gain) for skill, (cnt, gain) in ranked[:limit]]
    except Exception:  # noqa: BLE001
        return []


def _spell_use_top(qq: str, limit: int = 5) -> list[tuple[str, str, int]]:
    """法术使用 Top：(spell_id, 名称, 次数)。"""
    try:
        from ..models.stats import BattleLog

        rows = BattleLog.select(BattleLog.spells_cast).where(
            BattleLog.qq == str(qq)
        )
        counts: dict[str, int] = {}
        for r in rows:
            data = _load_dict(r.spells_cast)
            for raw_sid, n in data.items():
                sid = str(raw_sid)
                counts[sid] = counts.get(sid, 0) + int(n or 0)
        ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        return [(sid, _spell_name(sid), cnt) for sid, cnt in ranked[:limit]]
    except Exception:  # noqa: BLE001
        return []


def _encounter_count(qq: str, monster_id: str) -> int:
    try:
        from ..models.stats import BattleLog

        return (
            BattleLog.select()
            .where(
                (BattleLog.qq == str(qq))
                & (BattleLog.monster_id == str(monster_id))
            )
            .count()
        )
    except Exception:  # noqa: BLE001
        return 0


def build_stats_reply(qq: str) -> str:
    """/统计：个人战斗 / 经济 / 成长 / 法术 / 「启」遭遇 聚合。"""
    battle = _battle_agg(qq)
    gold = _gold_agg(qq)
    growth = _growth_top(qq)
    spells = _spell_use_top(qq)
    qi = _encounter_count(qq, "38")

    has_data = (
        battle["battles"]
        or gold["income"]
        or gold["expense"]
        or growth
        or spells
        or qi
    )
    if not has_data:
        return _empty("stat.empty", "📊 暂无统计数据，先去冒险吧！")

    lines = [report_section(_t("stat.title", default="📊 个人统计"))]

    # 战斗战绩
    lines.append(_t("stat.battle_title", default="战斗战绩"))
    wr = (battle["wins"] / battle["battles"] * 100) if battle["battles"] else 0
    lines.append(
        f"- 总战斗：{battle['battles']} 场 ｜ 胜利 {battle['wins']}（胜率 {wr:.1f}%）"
    )
    lines.append(
        f"- 逃跑 {battle['flees']} 次 ｜ 死亡 {battle['deaths']} 次"
    )
    lines.append(f"- 「{_monster_name('38')}」遭遇：{qi} 次")
    lines.append("")

    # 经济账本
    lines.append(_t("stat.gold_title", default="经济账本"))
    lines.append(
        f"- 累计收入：+{gold['income']} 乌帕 ｜ 累计支出：-{gold['expense']} 乌帕"
    )
    net = gold["income"] - gold["expense"]
    lines.append(
        f"- 净结余 {net:+d} 乌帕 ｜ 当前余额：{_current_gold(qq)} 乌帕"
    )
    lines.append("")

    # 技能成长
    lines.append(_t("stat.growth_title", default="技能成长 Top"))
    if growth:
        for skill, cnt, gain in growth:
            lines.append(f"- {skill} ×{cnt}（累计 +{gain}）")  # noqa: RUF001
    else:
        lines.append(f"- {_t('stat.no_growth', default='暂无成长记录')}")
    lines.append("")

    # 法术使用
    lines.append(_t("stat.spell_title", default="法术使用 Top"))
    if spells:
        for _, name, cnt in spells:
            lines.append(f"- {name} ×{cnt}")  # noqa: RUF001
    else:
        lines.append(f"- {_t('stat.no_spell', default='暂无施法记录')}")

    return "\n".join(lines)


# --- /周目回顾：最近 N 局 ---
def _run_kills_total(raw: str) -> int:
    return sum(int(v) for v in _load_dict(raw).values())


def build_review_reply(qq: str, limit: int = 5) -> str:
    """/周目回顾：最近 limit 局快照（结局 / 天数 / 知识度 / 击杀 / 乌帕 / 门扉）。"""
    try:
        from ..models.stats import RunStats

        rows = list(
            RunStats.select()
            .where(RunStats.qq == str(qq))
            .order_by(RunStats.run_id.desc())
            .limit(limit)
        )
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        return _empty("review.empty", "🕰 暂无周目记录，快去开启你的冒险吧！")

    lines = [report_section(_t("review.title", default="🕰 周目回顾"))]
    lines.append(
        report_quote(
            [_t("review.note", default="最近 {n} 局", n=len(rows))]
        )
    )
    for r in rows:
        eid = str(r.ending_id or "")
        if eid:
            label = f"{eid} {_ending_name(eid)}"
            if r.variant:
                label += f"·{r.variant}"
        else:
            label = _t("review.unsettled", default="未结算")
        lines.append(f"- 第 {r.run_id} 周目 ｜ {label}")
        days_line = (
            f"{_t('review.days', default='存活')} {r.days_survived} 天 ｜ "
            f"{_t('review.knowledge', default='终局知识度')} {r.knowledge} ｜ "
            f"SAN {r.final_san}"
        )
        result_line = (
            f"{_t('review.kills', default='击杀')} {_run_kills_total(r.kills)} ｜ "
            f"{_t('review.gold', default='乌帕净收益')} {r.gold_net:+d} ｜ "
            f"{_t('review.door', default='门扉')} {_door_label(r.door_choice)}"
        )
        lines.append(report_quote([days_line, result_line]))
    return "\n".join(lines)


# --- /排行榜：三榜 ---
def _rank_kills_data(limit: int = 10) -> list[tuple[str, int]]:
    try:
        from ..models.stats import RunStats

        rows = RunStats.select(RunStats.qq, RunStats.kills)
        agg: dict[str, int] = {}
        for r in rows:
            agg[str(r.qq)] = agg.get(str(r.qq), 0) + _run_kills_total(r.kills)
        ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)
        return [(qq, n) for qq, n in ranked[:limit] if n > 0]
    except Exception:  # noqa: BLE001
        return []


def _rank_gold_data(limit: int = 10) -> list[tuple[str, int]]:
    try:
        from database.db import User

        rows = list(
            User.select(User.user_id, User.gold)
            .where(User.gold > 0)
            .order_by(User.gold.desc())
            .limit(limit)
        )
        return [(str(r.user_id), int(r.gold or 0)) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def _rank_ending_data(limit: int = 10) -> list[tuple[str, int]]:
    try:
        from ..models.player import EndingCollectionModel

        rows = EndingCollectionModel.select(
            EndingCollectionModel.qq, EndingCollectionModel.endings
        )
        agg: dict[str, int] = {}
        for r in rows:
            records = _load_records(r.endings)
            distinct = len({rec.get("id") for rec in records if rec.get("id")})
            if distinct:
                agg[str(r.qq)] = distinct
        ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)
        return [(qq, n) for qq, n in ranked[:limit]]
    except Exception:  # noqa: BLE001
        return []


def _rank_board(title: str, rows: list[tuple[str, int]], unit: str = "") -> str:
    parts = [report_section(title)]
    if not rows:
        parts.append(f"- {_t('rank.empty', default='暂无数据')}")
        return "\n".join(parts)
    for idx, (qq, val) in enumerate(rows, 1):
        parts.append(f"{idx}. {_mask_qq(qq)}：{val}{unit}")
    return "\n".join(parts)


def _build_rank_kills() -> str:
    return _rank_board(
        _t("rank.kills", default="⚔️ 击杀榜"),
        _rank_kills_data(),
        unit=_t("rank.kills_unit", default=" 击杀"),
    )


def _build_rank_gold() -> str:
    return _rank_board(
        _t("rank.gold", default="💰 乌帕榜"),
        _rank_gold_data(),
        unit=_t("rank.gold_unit", default=" 乌帕"),
    )


def _build_rank_endings() -> str:
    return _rank_board(
        _t("rank.ending", default="🏆 图鉴进度榜"),
        _rank_ending_data(),
        unit=_t("rank.ending_unit", default=f"/{TOTAL_ENDINGS} 结局"),
    )


_RANK_BOARDS: dict[str, str] = {
    "击杀": "kills",
    "kills": "kills",
    "乌帕": "gold",
    "金币": "gold",
    "gold": "gold",
    "图鉴": "ending",
    "结局": "ending",
    "ending": "ending",
}


def build_rank_reply(board: str = "") -> str:
    """/排行榜：board 为空则三榜齐出；否则仅对应榜。"""
    key = (board or "").strip().lower()
    mode = _RANK_BOARDS.get(key)
    if mode == "kills":
        return _build_rank_kills()
    if mode == "gold":
        return _build_rank_gold()
    if mode == "ending":
        return _build_rank_endings()
    return "\n\n".join(
        [
            report_section(_t("rank.title", default="📈 排行榜")),
            _build_rank_kills(),
            _build_rank_gold(),
            _build_rank_endings(),
        ]
    )


# --- /个人信息「生涯」段 ---
def career_section(qq: str) -> str:
    """「生涯」段：周目数 / 结局图鉴 / 信物图鉴 / 累计存活天数 / NG+ 等级。

    无任何数据时返回空串（不显示该段），绝不报错。
    """
    try:
        collection = ending_repo.get_collection(str(qq))
        if collection is None:
            return ""
        records = _load_records(collection.endings)
        marks = _load_marks(collection.collection)
        if (
            not collection.total_runs
            and not collection.total_days
            and not records
            and not marks
        ):
            return ""
        unlocked = len({rec.get("id") for rec in records if rec.get("id")})
        try:
            from ..services.ending_engine import relic_ids
        except Exception:  # noqa: BLE001
            relic_ids = list
        ids = relic_ids()
        owned = sum(1 for rid in ids if marks.get(str(rid)))
        lines = [report_section(_t("career.title", default="🗂 生涯"))]
        lines.append(
            f"- {_t('career.runs', default='累计周目')}：{collection.total_runs}"
        )
        lines.append(
            f"- {_t('career.endings', default='结局图鉴')}："
            f"{unlocked}/{TOTAL_ENDINGS}"
        )
        lines.append(
            f"- {_t('career.relics', default='信物图鉴')}：{owned}/{len(ids)}"
        )
        lines.append(
            f"- {_t('career.days', default='累计存活天数')}："
            f"{collection.total_days}"
        )
        lines.append(f"- NG+ 等级：Lv.{collection.ng_plus}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 - 生涯段失败不影响个人信息
        return ""


# --- 运营日报文本 ---
def _agg_report_date() -> str:
    """聚合目标自然日：0 点轮转为前一个自然日（对齐 stats_service）。"""
    return (
        datetime.date.today() - datetime.timedelta(days=1)  # noqa: DTZ011 - 对齐 stats_service
    ).strftime("%Y-%m-%d")


def build_daily_report(date: Optional[str] = None) -> str:
    """运营日报文本（读 daily_stats 快照行；无数据返回空串）。"""
    try:
        from ..models.stats import DailyStat

        date = date or _agg_report_date()
        row = DailyStat.select().where(DailyStat.date == date).first()
        if row is None:
            return ""
        endings = _load_dict(row.endings)
        ending_total = sum(int(v) for v in endings.values())
        ending_kind = len(endings)
        kind_text = (
            "、".join(f"{k}×{v}" for k, v in endings.items())  # noqa: RUF001
            if endings
            else _t("report.none", default="无")
        )
        lines = [
            report_section(_t("report.title", default="📊 运营日报")),
            f"> {_t('report.date', default='日期')}：{date}",
            f"> {_t('report.active', default='日活玩家')}：{row.active_players}",
            f"> {_t('report.adventures', default='冒险次数')}：{row.adventures}",
            f"> {_t('report.battles', default='战斗场次')}：{row.battles}"
            f"（{_t('report.wins', default='胜利')} {row.wins} ｜ "
            f"{_t('report.deaths', default='死亡')} {row.deaths}）",
            f"> {_t('report.endings', default='当日结局')}："
            f"{ending_kind} 种 {ending_total} 个（{kind_text}）",
            f"> {_t('report.gold', default='金库变化')}："
            f"+{row.gold_in} / -{row.gold_out} 乌帕",
            f"> {_t('report.avg_day', default='平均战斗日')}：{row.avg_day}",
        ]
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 - 日报生成失败仅记日志
        from loguru import logger

        logger.warning(f"[统计日报] 生成失败: {type(e).__name__} {e}")
        return ""
