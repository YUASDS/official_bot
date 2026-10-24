"""统计系统三期 · 展示层冒烟验证（只读渲染，可保留）。

模拟调用 3 个命令（/统计、/周目回顾、/排行榜）+ 生涯段 + 运营日报文本，
验证渲染输出与空态提示；不修改任何游戏/统计表。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "StoryTeller"))

from src.models.stats import DailyStat  # noqa: E402
from src.services.stat_render import (  # noqa: E402
    build_daily_report,
    build_rank_reply,
    build_review_reply,
    build_stats_reply,
    career_section,
)

DEMO_QQ = "test_fullflow_01"
SMOKE_DATE = "2099-12-31"


def _seed_daily_row() -> None:
    if DailyStat.select().where(DailyStat.date == SMOKE_DATE).count() == 0:
        DailyStat.create(
            date=SMOKE_DATE, active_players=9, adventures=25, battles=22,
            wins=16, deaths=4, endings='{"E01": 1, "E06": 1}',
            gold_in=240, gold_out=90, avg_day=9,
        )


def main() -> None:
    print("=" * 50)
    print("[1] /统计（个人聚合）")
    print("=" * 50)
    print(build_stats_reply(DEMO_QQ))
    print()

    print("=" * 50)
    print("[2] /周目回顾（最近 5 局）")
    print("=" * 50)
    print(build_review_reply(DEMO_QQ))
    print()

    print("=" * 50)
    print("[3] /排行榜（三榜）")
    print("=" * 50)
    print(build_rank_reply(""))
    print()

    print("=" * 50)
    print("[4] /个人信息「生涯」段")
    print("=" * 50)
    print(career_section(DEMO_QQ) or "（无生涯数据，不显示）")
    print()

    print("=" * 50)
    print("[5] 运营日报（读 daily_stats 快照）")
    print("=" * 50)
    _seed_daily_row()
    print(build_daily_report(SMOKE_DATE) or "（当日无汇总行）")
    print()

    print("=" * 50)
    print("[6] 空态提示（无数据玩家）")
    print("=" * 50)
    print(build_stats_reply("no_such_qq_smoke"))
    print(build_review_reply("no_such_qq_smoke"))
    print(career_section("no_such_qq_smoke") or "（生涯段为空串）")
    print()

    DailyStat.delete().where(DailyStat.date == SMOKE_DATE).execute()
    print("[SMOKE] ALL PASS")


if __name__ == "__main__":
    main()
