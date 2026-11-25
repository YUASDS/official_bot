"""统计系统一期：只读查询 inv.db 与 userData.db，输出中文统计报告。

用法:
    E:/anaconda3/python.exe scripts/stats_report.py
    E:/anaconda3/python.exe scripts/stats_report.py --inv-db X --user-db Y --out out.md

默认输出：控制台打印 + 写 .qa/reports/stats-report.md（可用 --no-file 跳过）。
纯标准库，只读打开数据库（URI mode=ro），不会修改任何现有文件。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INV_DB = PROJECT_ROOT / "plugins" / "StoryTeller" / "inv.db"
DEFAULT_USER_DB = PROJECT_ROOT / "database" / "userData.db"
DEFAULT_OUT = PROJECT_ROOT / ".qa" / "reports" / "stats-report.md"

ENDING_IDS = [f"E{i:02d}" for i in range(1, 11)]
RELIC_IDS = ["400", "501", "502", "503", "504", "506", "507", "508"]

DAY_BUCKETS = [
    (1, 5, "1-5 天"),
    (6, 10, "6-10 天"),
    (11, 15, "11-15 天"),
    (16, 20, "16-20 天"),
    (21, 25, "21-25 天"),
    (26, 30, "26-30 天"),
    (31, 35, "31-35 天"),
    (36, 40, "36-40 天"),
]

DOOR_CHOICES = ["", "A", "B", "C", "Hidden", "Witness"]
DOOR_LABELS = {
    "": "未抉择/无记录",
    "A": "A",
    "B": "B",
    "C": "C",
    "Hidden": "Hidden",
    "Witness": "Witness",
}


def open_db(path: Path, label: str) -> sqlite3.Connection:
    """以只读方式打开数据库；WAL 无 -shm 时回退到普通连接（仍只执行 SELECT）。"""
    if not path.exists():
        raise FileNotFoundError(f"{label} 数据库不存在: {path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r["name"] for r in rows}
    except sqlite3.Error:
        return set()


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchall()
        return True
    except sqlite3.Error:
        return False


def safe_json(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return fallback


def load_relic_names() -> dict[str, str]:
    try:
        data = json.loads(
            (PROJECT_ROOT / "plugins" / "StoryTeller" / "data" / "ending_data.json")
            .read_text(encoding="utf-8")
        )
        items = data.get("relics", {}).get("items", {})
        return {rid: item.get("name", rid) for rid, item in items.items()}
    except Exception:
        return {}


def section(lines: list[str], title: str, body: list[str]) -> None:
    lines.append("")
    lines.append(f"## {title}")
    lines.append("")
    lines.extend(body)


def fmt_rows(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for r in rows:
        lines.append("| " + " | ".join(v.ljust(w) for v, w in zip(r, widths)) + " |")
    return lines


def collect_basic(conn: sqlite3.Connection, lines: list[str]) -> None:
    if not table_exists(conn, "investigators"):
        lines.append("investigators 表不存在，跳过基础统计。")
        return
    cols = table_columns(conn, "investigators")
    body = []
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM investigators").fetchone()["c"]
        distinct_qq = conn.execute(
            "SELECT COUNT(DISTINCT qq) AS c FROM investigators"
        ).fetchone()["c"]
        body.append(f"- 玩家数（investigators 去重 QQ）：**{distinct_qq}**")
        body.append(f"- 角色数（investigators 记录数）：**{total}**")
        if "issurvive" in cols:
            alive = conn.execute(
                "SELECT COUNT(*) AS c FROM investigators WHERE issurvive = 1"
            ).fetchone()["c"]
            dead = conn.execute(
                "SELECT COUNT(*) AS c FROM investigators WHERE issurvive = 0"
            ).fetchone()["c"]
            body.append(f"- 存活/死亡分布：存活 **{alive}**，死亡 **{dead}**")
        if "day" in cols:
            avg_day = conn.execute(
                "SELECT AVG(day) AS a FROM investigators"
            ).fetchone()["a"]
            body.append(
                f"- 平均 day：**{round(avg_day, 2) if avg_day is not None else 0}**"
            )
    except sqlite3.Error as e:
        body.append(f"查询失败：{e}")
    section(lines, "一、基础", body)


def collect_day_histogram(conn: sqlite3.Connection, lines: list[str]) -> None:
    if not table_exists(conn, "investigators"):
        return
    cols = table_columns(conn, "investigators")
    if "day" not in cols:
        return
    counts = {lo: 0 for lo, hi, label in DAY_BUCKETS}
    unknown = 0
    try:
        for row in conn.execute("SELECT day FROM investigators"):
            day = row["day"]
            if day is None:
                unknown += 1
                continue
            hit = False
            for lo, hi, label in DAY_BUCKETS:
                if lo <= day <= hi:
                    counts[lo] += 1
                    hit = True
                    break
            if not hit:
                unknown += 1
        body = [f"- 进度分布（day 段直方图）："]
        for lo, hi, label in DAY_BUCKETS:
            body.append(f"  - {label}：**{counts[lo]}** 人")
        body.append(f"  - 其它/异常（<1 或 >40）：**{unknown}** 人")
    except sqlite3.Error as e:
        body = [f"查询失败：{e}"]
    section(lines, "二、进度分布", body)


def collect_endings(conn: sqlite3.Connection, lines: list[str]) -> None:
    if not table_exists(conn, "ending_collection"):
        lines.append("ending_collection 表不存在，跳过结局统计。")
        return
    body = []
    try:
        rows = conn.execute("SELECT endings, ng_plus FROM ending_collection").fetchall()
        if not rows:
            body.append("- 无收集记录。")
            section(lines, "三、结局", body)
            return
        accounts = len(rows)
        ending_unlock: dict[str, int] = {eid: 0 for eid in ENDING_IDS}
        per_account_kinds: list[int] = []
        ng_plus_dist = {"0": 0, "1": 0, "2": 0, "3+": 0}
        for row in rows:
            records = safe_json(row["endings"], [])
            if not isinstance(records, list):
                records = []
            kinds = set()
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                eid = rec.get("id")
                if not eid:
                    continue
                if eid in ending_unlock:
                    ending_unlock[eid] += 1
                kinds.add(eid)
            per_account_kinds.append(len(kinds))
            ng = 0
            try:
                ng = int(row["ng_plus"] or 0)
            except (ValueError, TypeError):
                pass
            if ng <= 0:
                ng_plus_dist["0"] += 1
            elif ng == 1:
                ng_plus_dist["1"] += 1
            elif ng == 2:
                ng_plus_dist["2"] += 1
            else:
                ng_plus_dist["3+"] += 1
        unlocked_accounts = sum(1 for k in per_account_kinds if k > 0)
        avg_all = sum(per_account_kinds) / accounts if accounts else 0
        avg_unlocked = (
            sum(per_account_kinds) / unlocked_accounts if unlocked_accounts else 0
        )
        body.append(f"- 账号数（ending_collection 记录）：**{accounts}**")
        body.append(f"- 有解锁结局的账号数：**{unlocked_accounts}**")
        body.append(f"- 人均解锁结局种类数（全部账号）：**{avg_all:.2f}**")
        body.append(f"- 人均解锁结局种类数（有解锁账号）：**{avg_unlocked:.2f}**")
        body.append("")
        body.append("| 结局 | 解锁人数 |")
        body.append("| --- | --- |")
        for eid in ENDING_IDS:
            body.append(f"| {eid} | {ending_unlock[eid]} |")
        body.append("")
        body.append("NG+ 分布：")
        for k in ["0", "1", "2", "3+"]:
            body.append(f"  - {k} 级：**{ng_plus_dist[k]}** 人")
    except sqlite3.Error as e:
        body = [f"查询失败：{e}"]
    section(lines, "三、结局", body)


def collect_relics(conn: sqlite3.Connection, lines: list[str]) -> None:
    if not table_exists(conn, "ending_collection"):
        return
    names = load_relic_names()
    body = []
    try:
        rows = conn.execute("SELECT collection FROM ending_collection").fetchall()
        if not rows:
            body.append("- 无收集记录。")
            section(lines, "四、信物", body)
            return
        accounts = len(rows)
        owned: dict[str, int] = {rid: 0 for rid in RELIC_IDS}
        for row in rows:
            marks = safe_json(row["collection"], {})
            if not isinstance(marks, dict):
                continue
            for rid in RELIC_IDS:
                if marks.get(rid):
                    owned[rid] += 1
        body.append(f"- 账号数：**{accounts}**")
        body.append("")
        body.append("| 信物 | 名称 | 持有数 |")
        body.append("| --- | --- | --- |")
        for rid in RELIC_IDS:
            name = names.get(rid, rid)
            body.append(f"| {rid} | {name} | {owned[rid]} |")
        full_count = sum(1 for v in owned.values() if v > 0)
        body.append("")
        body.append(f"- 八件信物中至少 1 人持有的种类数：**{full_count}/8**")
    except sqlite3.Error as e:
        body = [f"查询失败：{e}"]
    section(lines, "四、信物", body)


def collect_runs(conn: sqlite3.Connection, lines: list[str]) -> None:
    if not table_exists(conn, "ending_collection"):
        return
    body = []
    try:
        rows = conn.execute("SELECT total_runs FROM ending_collection").fetchall()
        if not rows:
            body.append("- 无收集记录。")
            section(lines, "五、周目", body)
            return
        dist = {"0": 0, "1": 0, "2": 0, "3+": 0}
        for row in rows:
            try:
                n = int(row["total_runs"] or 0)
            except (ValueError, TypeError):
                n = 0
            if n <= 0:
                dist["0"] += 1
            elif n == 1:
                dist["1"] += 1
            elif n == 2:
                dist["2"] += 1
            else:
                dist["3+"] += 1
        body.append("- total_runs 分布：")
        body.append(f"  - 0 周目（未开周目）：**{dist['0']}** 人")
        body.append(f"  - 1 周目：**{dist['1']}** 人")
        body.append(f"  - 2 周目：**{dist['2']}** 人")
        body.append(f"  - 3+ 周目：**{dist['3+']}** 人")
    except sqlite3.Error as e:
        body = [f"查询失败：{e}"]
    section(lines, "五、周目", body)


def collect_economy(conn: sqlite3.Connection, lines: list[str]) -> None:
    if not table_exists(conn, "user_info"):
        lines.append("user_info 表不存在，跳过经济统计。")
        return
    body = []
    try:
        rows = conn.execute("SELECT gold FROM user_info").fetchall()
        if not rows:
            body.append("- 无用户记录。")
            section(lines, "六、经济", body)
            return
        golds = []
        for row in rows:
            try:
                golds.append(int(row["gold"] or 0))
            except (ValueError, TypeError):
                golds.append(0)
        total = sum(golds)
        mean = statistics.mean(golds)
        median = statistics.median(golds)
        dist = {"0": 0, "1-50": 0, "51-200": 0, "201+": 0}
        for g in golds:
            if g <= 0:
                dist["0"] += 1
            elif g <= 50:
                dist["1-50"] += 1
            elif g <= 200:
                dist["51-200"] += 1
            else:
                dist["201+"] += 1
        body.append(f"- 用户数：**{len(golds)}**")
        body.append(f"- 乌帕总额：**{total}**")
        body.append(f"- 乌帕均值：**{mean:.2f}**")
        body.append(f"- 乌帕中位数：**{median}**")
        body.append("- 分布：")
        for k in ["0", "1-50", "51-200", "201+"]:
            body.append(f"  - {k}：**{dist[k]}** 人")
    except sqlite3.Error as e:
        body = [f"查询失败：{e}"]
    section(lines, "六、经济", body)


def collect_combat(conn: sqlite3.Connection, lines: list[str]) -> None:
    if not table_exists(conn, "ending_progress"):
        lines.append("ending_progress 表不存在，跳过战斗统计。")
        return
    cols = table_columns(conn, "ending_progress")
    body = []
    try:
        rows = conn.execute("SELECT * FROM ending_progress").fetchall()
        if not rows:
            body.append("- 无进度记录。")
            section(lines, "七、战斗相关", body)
            return
        n = len(rows)
        if "door_choice" in cols:
            door_dist: dict[str, int] = {dc: 0 for dc in DOOR_CHOICES}
            for row in rows:
                v = row["door_choice"]
                if v in door_dist:
                    door_dist[v] += 1
                else:
                    door_dist.setdefault(v, 0)
                    door_dist[v] += 1
            body.append("- door_choice 分布：")
            for dc in DOOR_CHOICES:
                body.append(f"  - {DOOR_LABELS.get(dc, dc)}：**{door_dist[dc]}**")
            extra = {k: v for k, v in door_dist.items() if k not in DOOR_CHOICES}
            for k, v in extra.items():
                body.append(f"  - {k}：**{v}**")
        else:
            body.append("- ending_progress 无 door_choice 字段，跳过。")
        if "items_first" in cols:
            counts = []
            for row in rows:
                lst = safe_json(row["items_first"], [])
                if isinstance(lst, list):
                    counts.append(len(lst))
                else:
                    counts.append(0)
            with_any = sum(1 for c in counts if c > 0)
            total_first = sum(counts)
            avg = total_first / n if n else 0
            dist = {"0": 0, "1": 0, "2": 0, "3+": 0}
            for c in counts:
                if c <= 0:
                    dist["0"] += 1
                elif c == 1:
                    dist["1"] += 1
                elif c == 2:
                    dist["2"] += 1
                else:
                    dist["3+"] += 1
            body.append("")
            body.append("- items_first 首杀信物：")
            body.append(f"  - 有首杀信物记录的账号数：**{with_any}** / {n}")
            body.append(f"  - 首杀信物总数：**{total_first}**，人均：**{avg:.2f}**")
            body.append(f"  - 0 件：**{dist['0']}**，1 件：**{dist['1']}**，"
                        f"2 件：**{dist['2']}**，3+ 件：**{dist['3+']}**")
        else:
            body.append("- ending_progress 无 items_first 字段，跳过。")
    except sqlite3.Error as e:
        body = [f"查询失败：{e}"]
    section(lines, "七、战斗相关", body)


def collect_inventory(conn: sqlite3.Connection, lines: list[str]) -> None:
    if not table_exists(conn, "inventory"):
        return
    body = []
    try:
        total_items = conn.execute(
            "SELECT COUNT(*) AS c FROM inventory"
        ).fetchone()["c"]
        distinct_ids = conn.execute(
            "SELECT COUNT(DISTINCT item_id) AS c FROM inventory"
        ).fetchone()["c"]
        holders = conn.execute(
            "SELECT COUNT(DISTINCT investigator_id) AS c FROM inventory"
        ).fetchone()["c"]
        body.append(f"- 背包物品总条目数：**{total_items}**")
        body.append(f"- 物品种类数（去重 item_id）：**{distinct_ids}**")
        body.append(f"- 持有物品的角色数：**{holders}**")
    except sqlite3.Error as e:
        body = [f"查询失败：{e}"]
    section(lines, "八、背包（附加）", body)


# --- 统计系统二期：run_stats / battle_logs / gold_ledger / daily_stats ---


def collect_run_stats(conn: sqlite3.Connection, lines: list[str]) -> None:
    """二期：周目回顾（run_stats 周目快照表）。"""
    if not table_exists(conn, "run_stats"):
        lines.append("run_stats 表不存在，跳过周目回顾（二期上线后自动生成）。")
        return
    body = []
    try:
        rows = conn.execute("SELECT * FROM run_stats").fetchall()
        if not rows:
            body.append("- 暂无周目快照记录。")
            section(lines, "九、周目回顾", body)
            return
        total = len(rows)
        settled = [r for r in rows if r["ending_id"]]
        ending_dist: dict[str, int] = {}
        door_dist: dict[str, int] = {}
        days_buckets = {lo: 0 for lo, hi, label in DAY_BUCKETS}
        unknown_days = 0
        gold_nets: list[int] = []
        knowledge_vals: list[int] = []
        for r in rows:
            eid = r["ending_id"] or "（未结算）"
            ending_dist[eid] = ending_dist.get(eid, 0) + 1
            dc = r["door_choice"] or ""
            door_dist[dc] = door_dist.get(dc, 0) + 1
            day = r["days_survived"]
            hit = False
            for lo, hi, label in DAY_BUCKETS:
                if lo <= day <= hi:
                    days_buckets[lo] += 1
                    hit = True
                    break
            if not hit:
                unknown_days += 1
            gold_nets.append(int(r["gold_net"] or 0))
            knowledge_vals.append(int(r["knowledge"] or 0))
        body.append(f"- 已记录周目数：**{total}**（已结算 **{len(settled)}**）")
        body.append("")
        body.append("| 结局 | 周目数 |")
        body.append("| --- | --- |")
        for eid in sorted(ending_dist, key=lambda x: -ending_dist[x]):
            body.append(f"| {eid} | {ending_dist[eid]} |")
        body.append("")
        body.append("| 存活天数 | 周目数 |")
        body.append("| --- | --- |")
        for lo, hi, label in DAY_BUCKETS:
            body.append(f"| {label} | {days_buckets[lo]} |")
        body.append(f"| 异常（<1 或 >40） | {unknown_days} |")
        body.append("")
        body.append("- 门扉抉择分布：")
        for dc in sorted(door_dist, key=lambda x: -door_dist[x]):
            body.append(f"  - {DOOR_LABELS.get(dc, dc)}：**{door_dist[dc]}**")
        if gold_nets:
            avg_net = statistics.mean(gold_nets)
            body.append(f"- 局内乌帕净收益：合计 **{sum(gold_nets)}**，均值 **{avg_net:.1f}**")
        if knowledge_vals:
            body.append(f"- 终局知识度：峰值 **{max(knowledge_vals)}**，均值 **{statistics.mean(knowledge_vals):.1f}**")
        # 周目回顾列表（近 8 局）
        recent = sorted(rows, key=lambda r: (r["run_id"] or 0), reverse=True)[:8]
        body.append("")
        body.append("- 最近周目回顾：")
        body.append("| 周目 | 结局 | 存活天数 | 知识度 | 击杀 | 信物 | 法术 | 乌帕净 | 战斗/逃/死 |")
        body.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for r in recent:
            kills = safe_json(r["kills"], {})
            kill_total = sum(v for v in kills.values()) if isinstance(kills, dict) else 0
            body.append(
                f"| {r['run_id']} | {r['ending_id'] or '未结算'} "
                f"| {r['days_survived']} | {r['knowledge']} | {kill_total} "
                f"| {r['relics_count']} | {r['spells_count']} | {r['gold_net']} "
                f"| {r['battles']}/{r['flees']}/{r['deaths']} |"
            )
    except sqlite3.Error as e:
        body = [f"查询失败：{e}"]
    section(lines, "九、周目回顾", body)


def collect_battle_logs(conn: sqlite3.Connection, lines: list[str]) -> None:
    """二期：逐日胜率 / 死亡率 / 怪物击杀排行（battle_logs 流水）。"""
    if not table_exists(conn, "battle_logs"):
        lines.append("battle_logs 表不存在，跳过战斗统计二期（上线后自动生成）。")
        return
    body = []
    try:
        rows = conn.execute("SELECT * FROM battle_logs").fetchall()
        if not rows:
            body.append("- 暂无战斗记录。")
            section(lines, "十、战斗统计（二期）", body)
            return
        total = len(rows)
        result_dist = {"win": 0, "flee": 0, "death": 0, "revived": 0}
        for r in rows:
            res = r["result"] or ""
            if res in result_dist:
                result_dist[res] += 1
            else:
                result_dist[res] = 1
        wins = result_dist["win"]
        deaths = result_dist["death"] + result_dist["revived"]
        body.append(f"- 战斗场次：**{total}**")
        body.append(f"- 胜负分布：胜利 **{wins}**，逃跑 **{result_dist['flee']}**，"
                    f"死亡 **{result_dist['death']}**，战败复活 **{result_dist['revived']}**")
        if total:
            body.append(f"- 胜率：**{wins / total * 100:.1f}%**；"
                        f"死亡率：**{deaths / total * 100:.1f}%**")
        # 逐日胜率（battle 日）
        per_day: dict[int, list[int]] = {}
        for r in rows:
            d = int(r["day"] or 0)
            per_day.setdefault(d, [0, 0])  # [总, 胜]
            per_day[d][0] += 1
            if r["result"] == "win":
                per_day[d][1] += 1
        if per_day:
            body.append("")
            body.append("- 逐日胜率（高危日 5/10/16/40 重点）：")
            body.append("| 战斗日 | 场次 | 胜场 | 胜率 |")
            body.append("| --- | --- | --- | --- |")
            for d in sorted(per_day):
                t, w = per_day[d]
                body.append(f"| {d} | {t} | {w} | {w / t * 100:.1f}% |")
        # 怪物击杀排行
        kill_counts: dict[str, int] = {}
        for r in rows:
            if r["result"] == "win":
                mid = str(r["monster_id"] or "?")
                kill_counts[mid] = kill_counts.get(mid, 0) + 1
        if kill_counts:
            body.append("")
            body.append("- 怪物击杀排行（result=win）：")
            body.append("| 怪物ID | 击杀数 |")
            body.append("| --- | --- |")
            for mid in sorted(kill_counts, key=lambda x: -kill_counts[x]):
                body.append(f"| {mid} | {kill_counts[mid]} |")
        # 平均回合 / 伤害
        turns = [int(r["turns"] or 0) for r in rows]
        dmg = [int(r["dmg_dealt"] or 0) for r in rows]
        if turns:
            body.append(f"- 平均回合数：**{statistics.mean(turns):.1f}**；"
                        f"平均造成伤害：**{statistics.mean(dmg):.1f}**")
    except sqlite3.Error as e:
        body = [f"查询失败：{e}"]
    section(lines, "十、战斗统计（二期）", body)


def collect_gold_ledger(conn: sqlite3.Connection, lines: list[str]) -> None:
    """二期：经济收支 / 来源分布（gold_ledger 流水）。"""
    if not table_exists(conn, "gold_ledger"):
        lines.append("gold_ledger 表不存在，跳过经济流水（上线后自动生成）。")
        return
    body = []
    try:
        rows = conn.execute("SELECT * FROM gold_ledger").fetchall()
        if not rows:
            body.append("- 暂无经济流水。")
            section(lines, "十一、经济流水（二期）", body)
            return
        total_in = sum(max(0, int(r["delta"] or 0)) for r in rows)
        total_out = sum(max(0, -int(r["delta"] or 0)) for r in rows)
        body.append(f"- 流水条数：**{len(rows)}**")
        body.append(f"- 乌帕流入：**{total_in}**；流出：**{total_out}**；净变化：**{total_in - total_out}**")
        # 来源分布
        by_source: dict[str, list[int]] = {}
        for r in rows:
            src = str(r["source"] or "other")
            by_source.setdefault(src, [0, 0])  # [in, out]
            d = int(r["delta"] or 0)
            if d > 0:
                by_source[src][0] += d
            else:
                by_source[src][1] += -d
        body.append("")
        body.append("- 来源分布：")
        body.append("| 来源 | 流入 | 流出 |")
        body.append("| --- | --- | --- |")
        for src in sorted(by_source, key=lambda x: -(by_source[x][0] + by_source[x][1])):
            i, o = by_source[src]
            body.append(f"| {src} | {i} | {o} |")
        # 余额快照抽样（最新）
        body.append(f"- 最近一条 balance_after：**{int(rows[-1]['balance_after'] or 0)}**")
    except sqlite3.Error as e:
        body = [f"查询失败：{e}"]
    section(lines, "十一、经济流水（二期）", body)


def collect_daily_stats(conn: sqlite3.Connection, lines: list[str]) -> None:
    """二期：每日汇总（daily_stats 快照）。"""
    if not table_exists(conn, "daily_stats"):
        lines.append("daily_stats 表不存在，跳过每日汇总（0 点轮转生成）。")
        return
    body = []
    try:
        rows = conn.execute(
            "SELECT * FROM daily_stats ORDER BY date"
        ).fetchall()
        if not rows:
            body.append("- 暂无每日汇总。")
            section(lines, "十二、每日汇总（二期）", body)
            return
        body.append("| 日期 | 活跃 | 冒险 | 战斗 | 胜 | 亡 | 金入 | 金出 | 平均日 | 结局 |")
        body.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for r in rows:
            endings = safe_json(r["endings"], {})
            end_sum = (
                sum(v for v in endings.values())
                if isinstance(endings, dict)
                else 0
            )
            body.append(
                f"| {r['date']} | {r['active_players']} | {r['adventures']} "
                f"| {r['battles']} | {r['wins']} | {r['deaths']} "
                f"| {r['gold_in']} | {r['gold_out']} | {r['avg_day']} | {end_sum} |"
            )
    except sqlite3.Error as e:
        body = [f"查询失败：{e}"]
    section(lines, "十二、每日汇总（二期）", body)


def build_report(inv_db: Path, user_db: Path) -> list[str]:
    lines = ["# 统计系统报告（一期 + 二期）", ""]
    lines.append(f"- 生成时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- inv.db：`{inv_db}`")
    lines.append(f"- userData.db：`{user_db}`")
    lines.append("")
    try:
        inv_conn = open_db(inv_db, "inv.db")
    except (FileNotFoundError, sqlite3.Error) as e:
        lines.append(f"⚠️ inv.db 打开失败：{e}")
        return lines
    try:
        user_conn = open_db(user_db, "userData.db")
    except (FileNotFoundError, sqlite3.Error) as e:
        lines.append(f"⚠️ userData.db 打开失败：{e}")
        user_conn = None

    collect_basic(inv_conn, lines)
    collect_day_histogram(inv_conn, lines)
    collect_endings(inv_conn, lines)
    collect_relics(inv_conn, lines)
    collect_runs(inv_conn, lines)
    if user_conn is not None:
        collect_economy(user_conn, lines)
    else:
        section(lines, "六、经济", ["- userData.db 打开失败，跳过。"])
    collect_combat(inv_conn, lines)
    collect_inventory(inv_conn, lines)
    collect_run_stats(inv_conn, lines)
    collect_battle_logs(inv_conn, lines)
    collect_gold_ledger(inv_conn, lines)
    collect_daily_stats(inv_conn, lines)
    inv_conn.close()
    if user_conn is not None:
        user_conn.close()
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="统计系统只读统计脚本（一期+二期）")
    parser.add_argument("--inv-db", type=Path, default=DEFAULT_INV_DB,
                        help=f"inv.db 路径（默认 {DEFAULT_INV_DB}）")
    parser.add_argument("--user-db", type=Path, default=DEFAULT_USER_DB,
                        help=f"userData.db 路径（默认 {DEFAULT_USER_DB}）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"输出 markdown 路径（默认 {DEFAULT_OUT}）")
    parser.add_argument("--no-file", action="store_true",
                        help="只打印控制台，不写文件")
    args = parser.parse_args()

    lines = build_report(args.inv_db, args.user_db)
    text = "\n".join(lines)
    print(text)

    if not args.no_file:
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(f"\n[已写入] {args.out}", file=sys.stderr)
        except OSError as e:
            print(f"[警告] 写文件失败：{e}", file=sys.stderr)


if __name__ == "__main__":
    main()
