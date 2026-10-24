"""主线中断悬挂恢复：分支节点延迟占日 + flow 完成补占日（重启后可同天重进）。

对齐 `.qa/audit/opencode-mainline-occupy-brief.md`（方案 3）：
1. 悬挂判定与清理：entered 有 / node 无 / 无进行中 flow → 清 entered + 清当日 adventure_done。
2. 反例：进行中 mainline flow 不误清；node.<day> 已置不误清。
3. 今日冒险入口（_run_adventure → try_mainline_daily）悬挂恢复 → 分支命中 → 同天重进同一节点。
4. 恢复后 flow 完成 → 补占日（day+1）+ 计数 + 清 entered，节奏正常推进。

只测新增行为，不改 src/（test/ 不入库）。
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    Investigator,
    InvestigatorGenerator,
    ending_repo,
    investigator_repo,
)
from src.plugins import mainline as ml
from src.plugins.adventure import _run_adventure
from src.services import flow_engine as fe
from src.services.daily_service import (
    _adventure_done_today,
    _clear_adventure_done,
    _mark_adventure_done,
)


def _make_inv(qq: str, day: int = 1) -> Investigator:
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    data["san"] = 60
    data["意志"] = 60
    inv_model = investigator_repo.create_and_save(qq, "Resume", data)
    inv = Investigator(inv_model)
    inv.day = day
    inv.save()
    return Investigator(investigator_repo.find_by_qq(qq))


def _set_ng_plus(qq: str, ng: int) -> None:
    col = ending_repo.ensure_collection(qq)
    col.ng_plus = ng
    col.save()


def _cleanup(qq: str) -> None:
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)
    fe.flow_states.pop(qq, None)
    _clear_adventure_done(qq)


class ConsoleBot:
    type = "Console"


def _hung_inv(qq: str, line: str = "hymn") -> Investigator:
    """构造中断悬挂态：day13 进入分支 → 置 adventure_done + entered.13，node.13 未计数、无进行中 flow。"""
    inv = _make_inv(qq, day=13)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.line", line)
    inv.set_flag(f"ml.{line}.entered.13", True)
    inv.save()
    _mark_adventure_done(qq)
    fe.flow_states.pop(qq, None)
    return Investigator(investigator_repo.find_by_qq(qq))


# --- 1. 悬挂判定与清理（单元） ---
def test_recover_hung_mainline_entry_clears_flags():
    qq = "rs_recover_basic"
    _set_ng_plus(qq, 2)
    inv = _hung_inv(qq)
    assert inv.get_flag("ml.hymn.entered.13") is True
    assert _adventure_done_today(qq) is True
    assert ml.recover_hung_mainline_entry(qq, inv) is True
    r = Investigator(investigator_repo.find_by_qq(qq))
    assert r.get_flag("ml.hymn.entered.13") is None  # entered 已清
    assert _adventure_done_today(qq) is False  # 当日冒险资格已还
    _cleanup(qq)


# --- 2. 反例：不误清 ---
def test_recover_skips_when_mainline_flow_active():
    qq = "rs_recover_active"
    _set_ng_plus(qq, 2)
    inv = _hung_inv(qq)
    fe.flow_states[qq] = {
        "flow_id": "mainline.hymn",
        "node_id": "h1",
        "phase": "battle",
        "entered": [],
        "buff": {},
        "battle": None,
    }
    assert ml.recover_hung_mainline_entry(qq, inv) is False
    r = Investigator(investigator_repo.find_by_qq(qq))
    assert r.get_flag("ml.hymn.entered.13") is True  # flow 进行中，不算悬挂
    _cleanup(qq)


def test_recover_skips_when_node_completed():
    qq = "rs_recover_done"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=13)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.line", "hymn")
    inv.set_flag("ml.hymn.entered.13", True)
    inv.set_flag("ml.hymn.node", 1)
    inv.set_flag("ml.hymn.node.13", True)
    inv.save()
    assert ml.recover_hung_mainline_entry(qq, inv) is False
    r = Investigator(investigator_repo.find_by_qq(qq))
    assert r.get_flag("ml.hymn.entered.13") is True  # 节点已完成，不算悬挂
    _cleanup(qq)


async def _run_daily(qq: str) -> None:
    """今日冒险入口（_run_adventure）。"""
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    async def finish(msg):
        raise SystemExit("finish")

    try:
        await _run_adventure(qq, ConsoleBot(), send, finish)
    except SystemExit:
        pass


# --- 3. 今日冒险入口：悬挂恢复 → 同天重进 ---
def test_run_adventure_resumes_hung_same_day():
    qq = "rs_run_adventure"
    _set_ng_plus(qq, 2)
    inv = _hung_inv(qq)
    assert _adventure_done_today(qq) is True  # 中断悬挂：当日冒险已被占

    asyncio.run(_run_daily(qq))

    r = Investigator(investigator_repo.find_by_qq(qq))
    assert r.day == 13  # 不推 day
    assert r.get_flag("ml.hymn.entered.13") is True  # 恢复后重进（重新占标记）
    assert _adventure_done_today(qq) is True  # 再次占用当日冒险
    assert int(r.get_flag("ml.hymn.node") or 0) == 0  # 尚未计数（完成才补）
    assert fe.flow_states.get(qq)  # 分支 flow 已重进
    assert fe.flow_states[qq]["flow_id"] == "mainline.hymn"
    _cleanup(qq)


# --- 4. 恢复后 flow 完成 → 补占日 + 计数 + 清 entered ---
def test_finish_after_resume_completes_node():
    qq = "rs_finish_after"
    _set_ng_plus(qq, 2)
    inv = _hung_inv(qq)
    asyncio.run(_run_daily(qq))
    assert fe.flow_states.get(qq)
    state = fe.flow_states[qq]
    bot = ConsoleBot()

    async def send(m):
        pass

    asyncio.run(fe._finish_flow(qq, state, bot, send))
    r = Investigator(investigator_repo.find_by_qq(qq))
    assert r.day == 14  # flow 完成补占日（day+1）
    assert int(r.get_flag("ml.hymn.node") or 0) == 1
    assert r.get_flag("ml.hymn.node.13") is True
    assert r.get_flag("ml.hymn.entered.13") is None
    _cleanup(qq)