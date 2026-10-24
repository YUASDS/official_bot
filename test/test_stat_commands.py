"""统计系统三期展示层测试：/统计、/周目回顾、/排行榜、生涯段、运营日报。

验证只读渲染函数（stat_render）在有数据/无数据两种场景下的输出，
以及新命令插件在 nonebot 环境下的可导入性。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.models.player import (
    EndingCollectionModel,
    InvestigatorGenerator,
    ending_repo,
    investigator_repo,
)
from src.models.stats import (
    BattleLog,
    DailyStat,
    GoldLedger,
    GrowthLog,
    RunStats,
)
from src.services.stat_render import (
    build_daily_report,
    build_rank_reply,
    build_review_reply,
    build_stats_reply,
    career_section,
)

QQ = "1234567890"
DAILY_DATE = "2099-01-02"


def _cleanup() -> None:
    from src.models.player import EndingProgressModel

    EndingProgressModel.delete().where(EndingProgressModel.qq == QQ).execute()
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == QQ).execute()
    investigator_repo.delete_by_qq(QQ)
    BattleLog.delete().where(BattleLog.qq == QQ).execute()
    RunStats.delete().where(RunStats.qq == QQ).execute()
    GoldLedger.delete().where(GoldLedger.qq == QQ).execute()
    GrowthLog.delete().where(GrowthLog.qq == QQ).execute()
    DailyStat.delete().where(DailyStat.date == DAILY_DATE).execute()


def _seed() -> None:
    _cleanup()
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    investigator_repo.create_and_save(QQ, "测试统计", data)

    # 战斗流水：2 胜（含 1 次「启」）+ 1 逃 + 1 死
    BattleLog.create(
        qq=QQ, run_id=1, day=1, monster_id="26", environment="失重",
        result="win", turns=5, dmg_dealt=20, dmg_taken=5, san_loss=1,
        spells_cast='{"s001": 2, "s002": 1}', fled=False,
    )
    BattleLog.create(
        qq=QQ, run_id=1, day=5, monster_id="38", environment="月光庭院",
        result="win", turns=8, dmg_dealt=45, dmg_taken=10, san_loss=2,
        spells_cast="{}", fled=False,
    )
    BattleLog.create(
        qq=QQ, run_id=2, day=2, monster_id="27", environment="",
        result="flee", turns=1, dmg_dealt=0, dmg_taken=0, san_loss=0,
        spells_cast="{}", fled=True,
    )
    BattleLog.create(
        qq=QQ, run_id=2, day=3, monster_id="28", environment="",
        result="death", turns=3, dmg_dealt=8, dmg_taken=30, san_loss=5,
        spells_cast="{}", fled=False,
    )

    # 经济流水：+100 收入 / -30 支出
    GoldLedger.create(qq=QQ, run_id=1, delta=100, balance_after=100, source="battle")
    GoldLedger.create(qq=QQ, run_id=2, delta=-30, balance_after=70, source="shop")

    # 成长流水：手枪 2 次（+10/+8）、格斗 1 次（+5）
    GrowthLog.create(
        qq=QQ, run_id=1, day=1, skill="手枪", before=20, after=30, source="battle"
    )
    GrowthLog.create(
        qq=QQ, run_id=1, day=5, skill="手枪", before=30, after=38, source="battle"
    )
    GrowthLog.create(
        qq=QQ, run_id=2, day=2, skill="格斗", before=40, after=45, source="event"
    )

    # 周目快照：E01·清醒合流、E06、未结算
    RunStats.create(
        qq=QQ, run_id=1, ended_at="2099-01-01 00:00:00", ending_id="E01",
        variant="清醒合流", days_survived=40, final_san=30, knowledge=45,
        door_choice="E01", kills='{"27": 2, "38": 1}', relics_count=2,
        gold_net=100, battles=2, flees=0, deaths=0,
    )
    RunStats.create(
        qq=QQ, run_id=2, ended_at="2099-01-02 00:00:00", ending_id="E06",
        variant="", days_survived=12, final_san=0, knowledge=10,
        door_choice="", kills="{}", relics_count=0,
        gold_net=-30, battles=2, flees=1, deaths=1,
    )
    RunStats.create(
        qq=QQ, run_id=3, ended_at="2099-01-03 00:00:00", ending_id="",
        variant="", days_survived=3, final_san=50, knowledge=0,
        door_choice="", kills="{}", relics_count=0,
        gold_net=0, battles=0, flees=0, deaths=0,
    )

    # 账号级收集：E01 + E01(崩溃合流) + E06 → 结局图鉴 2/10；信物 2 件
    ending_repo.ensure_collection(QQ)
    EndingCollectionModel.update(
        total_runs=3,
        total_days=55,
        endings='[{"id":"E01","run":1,"variant":"清醒合流"},'
        '{"id":"E01","run":1,"variant":"崩溃合流"},{"id":"E06","run":2}]',
        ng_plus=2,
        collection='{"400": true, "502": true}',
    ).where(EndingCollectionModel.qq == QQ).execute()

    # 每日汇总行
    DailyStat.create(
        date=DAILY_DATE, active_players=12, adventures=30, battles=28,
        wins=20, deaths=5, endings='{"E01": 2, "E05": 1}',
        gold_in=320, gold_out=150, avg_day=12,
    )


def test_stats_reply_aggregates() -> None:
    _seed()
    text = build_stats_reply(QQ)
    assert "总战斗：4 场" in text
    assert "胜利 2（胜率 50.0%）" in text
    assert "逃跑 1 次" in text
    assert "死亡 1 次" in text
    assert "启」遭遇：1 次" in text
    assert "累计收入：+100 乌帕" in text
    assert "累计支出：-30 乌帕" in text
    assert "净结余 +70 乌帕" in text
    assert "手枪 ×2（累计 +18）" in text  # noqa: RUF001
    assert "枯萎术 ×2" in text  # noqa: RUF001
    assert "愈合术 ×1" in text  # noqa: RUF001


def test_stats_reply_empty_state() -> None:
    assert "暂无统计数据" in build_stats_reply("no_such_qq_zzz")


def test_review_reply_lists_recent_runs() -> None:
    _seed()
    text = build_review_reply(QQ)
    assert "第 3 周目" in text  # 最新局在前
    assert "未结算" in text
    assert "第 1 周目" in text
    assert "E01" in text and "清醒合流" in text
    assert "击杀 3" in text  # run1 kills 2+1
    assert "乌帕净收益 +100" in text
    assert "门扉 A门·合流" in text
    assert "SAN 30" in text
    assert "终局知识度 45" in text


def test_review_reply_empty_state() -> None:
    assert "暂无周目记录" in build_review_reply("no_such_qq_zzz")


def test_rank_reply_three_boards() -> None:
    _seed()
    text = build_rank_reply("")
    assert "击杀榜" in text
    assert "乌帕榜" in text
    assert "图鉴进度榜" in text
    assert "测试统计·7890" in text  # qq 脱敏：昵称 + 尾号


def test_rank_reply_single_board() -> None:
    _seed()
    kills = build_rank_reply("击杀")
    assert "击杀榜" in kills and "乌帕榜" not in kills
    gold = build_rank_reply("乌帕")
    assert "乌帕榜" in gold and "击杀榜" not in gold
    endings = build_rank_reply("图鉴")
    assert "图鉴进度榜" in endings and "乌帕榜" not in endings


def test_career_section_shows_data() -> None:
    _seed()
    text = career_section(QQ)
    assert "累计周目：3" in text
    assert "结局图鉴：2/12" in text
    assert "信物图鉴：2/8" in text
    assert "累计存活天数：55" in text
    assert "NG+ 等级：Lv.2" in text


def test_career_section_empty_no_error() -> None:
    assert career_section("no_such_qq_zzz") == ""


def test_daily_report_text() -> None:
    _seed()
    text = build_daily_report(DAILY_DATE)
    assert "运营日报" in text
    assert "日活玩家：12" in text
    assert "冒险次数：30" in text
    assert "战斗场次：28（胜利 20 ｜ 死亡 5）" in text
    assert "E01×2" in text and "E05×1" in text  # noqa: RUF001
    assert "+320 / -150" in text
    assert "平均战斗日：12" in text


def test_daily_report_missing_row() -> None:
    assert build_daily_report("2000-01-01") == ""


def test_stat_plugin_importable() -> None:
    from src.plugins import stat as stat_plugin

    assert stat_plugin.stats_cmd is not None
    assert stat_plugin.review_cmd is not None
    assert stat_plugin.rank_cmd is not None
    assert stat_plugin.report_target() == ("", "")  # 未配置目标不报错
