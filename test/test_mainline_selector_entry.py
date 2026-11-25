"""追加：真实入口解锁测试（防回归：ml.unlocked 由主流程幂等置位，不需手动设）。

线上 bug：多周目玩家 day1 /今日冒险 无选择器按钮——
根因：try_mainline_daily 未调 is_unlocked → ml.unlocked 恒空 → selector_available False。
修复：try_mainline_daily 开头 is_unlocked(inv)。
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.models.player import (
    Investigator,
    InvestigatorGenerator,
    ending_repo,
    investigator_repo,
)
from src.plugins import mainline as ml


def _cleanup(qq: str):
    try:
        investigator_repo.delete_by_qq(qq)
    except Exception:
        pass


def _make_inv(qq: str, day: int = 1) -> Investigator:
    _cleanup(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    model = investigator_repo.create_and_save(qq, "UnlockRepro", data)
    inv = Investigator(model)
    inv.day = day
    return inv


def _set_ng_plus(qq: str, n: int):
    coll = ending_repo.get_collection(qq)
    if coll is None:
        coll = ending_repo.ensure_collection(qq)
    coll.ng_plus = n
    coll.save()


async def _run_try_mainline(qq: str, inv: Investigator):
    async def noop(*a, **k):
        return None

    signal = await ml.try_mainline_daily(qq, inv, None, noop, noop)
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    return signal, reloaded


def test_real_entry_unlocks_and_returns_selector_signal():
    """真实入口（无手动旗标）：多周目 day1 → 自动置 ml.unlocked + 返回 SELECTOR_SIGNAL。

    修复前：ml.unlocked 恒空 → 返回 False（无选择器按钮，线上 bug）。
    """
    qq = "b4a_real_entry"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=1)
    assert inv.get_flag("ml.unlocked") is None  # 真实玩家初始旗标为空

    signal, reloaded = asyncio.run(_run_try_mainline(qq, inv))

    assert signal is ml.SELECTOR_SIGNAL or signal == "mainline.selector"
    assert reloaded.get_flag("ml.unlocked") is True  # 幂等置位（is_unlocked）
    _cleanup(qq)


def test_real_entry_week1_stays_disabled():
    """一周目（ng_plus=0）：真实入口不置 ml.unlocked、不返回选择器信号（零回归）。"""
    qq = "b4a_real_week1"
    inv = _make_inv(qq, day=1)
    assert inv.get_flag("ml.unlocked") is None

    signal, reloaded = asyncio.run(_run_try_mainline(qq, inv))

    assert signal is False
    assert reloaded.get_flag("ml.unlocked") is None
    _cleanup(qq)
