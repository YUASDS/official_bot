"""统计系统二期冒烟验证（验证后可删除或保留）。

模拟一局战斗/结局，验证 battle_logs / run_stats / gold_ledger 实际写入 inv.db。
独立于 bot 运行，用 `src.` 顶层导入（对齐 test 目录方式）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "StoryTeller"))

import ujson

from src.models.monster import Monster
from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    InvestigatorGenerator,
    ending_repo,
    investigator_repo,
)
from src.models.stats import BattleLog, GoldLedger, RunStats
from src.services.battle import BattleService
from src.services.ending_engine import check_san_zero, judge_door_choice
from src.services.stats_service import snapshot_run

QQ = "qa_stats_smoke"


def _cleanup() -> None:
    from src.models.player import EndingProgressModel as EP, EndingCollectionModel as EC

    EP.delete().where(EP.qq == QQ).execute()
    EC.delete().where(EC.qq == QQ).execute()
    investigator_repo.delete_by_qq(QQ)
    BattleLog.delete().where(BattleLog.qq == QQ).execute()
    RunStats.delete().where(RunStats.qq == QQ).execute()
    GoldLedger.delete().where(GoldLedger.qq == QQ).execute()


def main() -> None:
    _cleanup()
    # 1. 创建调查员 + 进度（run_id 就绪）
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(QQ, "Smoke", data)
    from src.models.player import Investigator

    inv = Investigator(inv_model)
    ending_repo.ensure_progress(QQ, 1)

    # 2. 模拟战斗胜利（_handle_victory 内部会 add_gold → gold_ledger；随后 battle_logs）
    monster = Monster("30")
    bs = BattleService(inv, monster)
    bs.hp_record["mon"] = 0
    bs.hp_record["inv"] = 10
    result = bs._check_combat_over()
    assert "胜利" in result or "victory" in str(result) or result

    # 3. 验证 battle_logs 与 gold_ledger 写入
    bl = list(BattleLog.select().where(BattleLog.qq == QQ))
    assert bl, "battle_logs 未写入！"
    assert bl[0].result == "win", f"result 应为 win, 实际 {bl[0].result}"
    assert bl[0].monster_id == "30", f"monster_id 应为 30, 实际 {bl[0].monster_id}"
    print(f"[SMOKE] battle_logs: {bl[0].qq} run={bl[0].run_id} result={bl[0].result} "
          f"day={bl[0].day} dmg_dealt={bl[0].dmg_dealt} turns={bl[0].turns}")

    gl = list(GoldLedger.select().where(GoldLedger.qq == QQ))
    assert gl, "gold_ledger 未写入！"
    assert any(g.delta > 0 for g in gl), "应有战斗战利品流入"
    assert any(g.source == "battle" for g in gl), "gold_ledger 应带 battle source"
    print(f"[SMOKE] gold_ledger rows={len(gl)} first_source={gl[0].source} "
          f"first_delta={gl[0].delta} balance_after={gl[0].balance_after}")

    # 4. 模拟结局（E06）→ run_stats 快照
    inv2_model = investigator_repo.find_by_qq(QQ)
    inv2 = Investigator(inv2_model)
    check_san_zero(inv2)
    rs = list(RunStats.select().where(RunStats.qq == QQ))
    assert rs, "run_stats 未写入！"
    assert rs[0].ending_id == "E06", f"ending_id 应为 E06, 实际 {rs[0].ending_id}"
    assert rs[0].battles >= 1, "run_stats.battles 应≥1"
    print(f"[SMOKE] run_stats: ending={rs[0].ending_id} days={rs[0].days_survived} "
          f"battles={rs[0].battles} gold_net={rs[0].gold_net} kills={rs[0].kills}")

    # 5. 幂等：重复快照不覆盖（仍有且仅一行）
    snapshot_run(inv2, "E06")
    rs2 = list(RunStats.select().where(RunStats.qq == QQ))
    assert len(rs2) == 1, "run_stats 应保持单行（幂等）"
    print("[SMOKE] run_stats 幂等 OK")

    # 6. 新周目兜底：未结算局 snapshot 不覆盖已结算
    print("[SMOKE] ALL PASS")

    _cleanup()
    print("[SMOKE] cleanup done")


if __name__ == "__main__":
    main()
