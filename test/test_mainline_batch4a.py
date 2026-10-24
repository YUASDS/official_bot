"""批4A：二周目选择器（day1 踏入主线/重游门扉）+ day40 兼容性实测。

覆盖（对齐批4A 需求，只测新增行为）：
1. 选择器判定 selector_available：一周目 False / 解锁 day1 True / day2 False /
   已选（opt_choice）False / 分派启用开关。
2. apply_selector_choice：guard_in→ml.opt_in + opt_choice；door→ml.opt_out + opt_choice；
   已选幂等返回 False；非法选项返回 False。
3. try_mainline_daily day1 返回 SELECTOR_SIGNAL（不占日、不发 opening）；opt_out 短路返回 False。
4. 互斥门：opt_out 后 opening/branch/mainline_pool 全关（纯门扉）；opt_in 保持主线。
5. day40 兼容互斥：
   - 一周目 day40 → try 返回 False、不占日、守门人 36（现状不变）。
   - 二周目+主线（opt_in）day40 → 三线终局已登记；branch 不再重排（ml.<line>.done 门）、
     try 返回 False、怪物取主线池（非守门人 36）。
   - 二周目+门扉（opt_out）day40 → try 返回 False、mainline_pool None、守门人 36（回到现状）。

只测新增行为，不改 src/（test/ 不入库）。
"""
import sys
import asyncio
import ujson
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.models.monster import monster_repo
from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    Investigator,
    InvestigatorGenerator,
    ending_repo,
    investigator_repo,
)
from src.plugins import mainline as ml


def _make_inv(qq: str, day: int = 1, name: str = "Batch4A") -> Investigator:
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    data["san"] = 60
    data["意志"] = 60
    inv_model = investigator_repo.create_and_save(qq, name, data)
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


class ConsoleBot:
    type = "Console"


async def _run_try_mainline(qq: str, inv: Investigator):
    sent: list[str] = []
    bot = ConsoleBot()

    async def send(msg):
        sent.append(str(msg))

    ok = await ml.try_mainline_daily(qq, inv, bot, send, lambda m: None)
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    return ok, reloaded, sent, bot


def _day40_pool() -> list:
    return monster_repo._checkpoint_data.get("40") or []


# --- 1. 选择器判定 ---
def test_selector_available_gates():
    qq = "b4a_sel_gate"
    # 一周目（未解锁）day1 → False
    _make_inv(qq, day=1)
    inv = Investigator(investigator_repo.find_by_qq(qq))
    assert ml.selector_available(inv) is False
    _cleanup(qq)

    # 解锁 day1 → True
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=1)
    inv.set_flag("ml.unlocked", True)
    inv.save()
    inv = Investigator(investigator_repo.find_by_qq(qq))
    assert ml.selector_available(inv) is True
    # day2 → False（仅 day1）
    inv.day = 2
    assert ml.selector_available(inv) is False
    inv.day = 1

    # 已选（opt_choice）→ False
    inv.set_flag("ml.opt_choice", True)
    inv.save()
    inv = Investigator(investigator_repo.find_by_qq(qq))
    assert ml.selector_available(inv) is False
    _cleanup(qq)


# --- 2. apply_selector_choice ---
def test_apply_selector_choice_guard_in():
    qq = "b4a_sel_in"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=1)
    inv.set_flag("ml.unlocked", True)
    inv.save()
    assert ml.apply_selector_choice(inv, "guard_in") is True
    assert inv.get_flag("ml.opt_in") is True
    assert inv.get_flag("ml.opt_choice") is True
    assert inv.get_flag("ml.opt_out") is None
    # 幂等：已选后返回 False
    assert ml.apply_selector_choice(inv, "guard_in") is False
    _cleanup(qq)


def test_apply_selector_choice_door():
    qq = "b4a_sel_door"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=1)
    inv.set_flag("ml.unlocked", True)
    inv.save()
    assert ml.apply_selector_choice(inv, "door") is True
    assert inv.get_flag("ml.opt_out") is True
    assert inv.get_flag("ml.opt_choice") is True
    assert inv.get_flag("ml.opt_in") is None
    _cleanup(qq)


def test_apply_selector_choice_invalid():
    qq = "b4a_sel_bad"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=1)
    inv.set_flag("ml.unlocked", True)
    inv.save()
    assert ml.apply_selector_choice(inv, "whatever") is False
    assert inv.get_flag("ml.opt_choice") is None
    _cleanup(qq)


# --- 3. try_mainline_daily 信号 ---
def test_try_mainline_daily_selector_signal_day1():
    qq = "b4a_sig"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=1)
    inv.set_flag("ml.unlocked", True)
    inv.save()

    ok, reloaded, sent, _ = asyncio.run(_run_try_mainline(qq, inv))
    assert ok == ml.SELECTOR_SIGNAL
    assert reloaded.day == 1  # 不占日（选择器只展示 UI，等待玩家选择）
    assert reloaded.get_flag("ml.opt_choice") is None  # 尚未选择
    _cleanup(qq)


# --- 4. 互斥门（opt_out 全关 / opt_in 保持） ---
def test_opt_out_closes_all_mainline():
    qq = "b4a_optout"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=1)
    inv.set_flag("ml.unlocked", True)
    ml.apply_selector_choice(inv, "door")  # 重游门扉
    inv.set_flag("ml.dread", 5)
    inv.save()
    inv = Investigator(investigator_repo.find_by_qq(qq))

    assert ml.opening_available(inv, 1) is False
    assert ml.opening_available(inv, 4) is False
    assert ml.branch_available(inv, 10) is False
    assert ml.mainline_pool(inv) is None
    assert ml.resolve_branch(inv, 10) is None
    assert inv.get_flag("ml.line") is None
    # 调度短路：try 返回 False
    ok, reloaded, _, _ = asyncio.run(_run_try_mainline(qq, inv))
    assert ok is False
    assert reloaded.day == 1  # 不占日
    _cleanup(qq)


def test_opt_in_keeps_mainline():
    qq = "b4a_optin"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=1)
    inv.set_flag("ml.unlocked", True)
    ml.apply_selector_choice(inv, "guard_in")
    inv.set_flag("ml.at", "dread")
    inv.save()
    inv = Investigator(investigator_repo.find_by_qq(qq))

    # 选择器已过（day1 不再弹），主线照常
    assert ml.selector_available(inv) is False
    assert ml.opening_available(inv, 1) is True
    assert ml.mainline_pool(inv) == list(ml._cfg("怪物池", "dread"))
    _cleanup(qq)


# --- 5. day40 兼容互斥 ---
def test_day40_first_run_doorkeeper_unchanged():
    """一周目 day40：守门人（现状不变）——try 不接管、不占日、主怪池走 check_point 36。"""
    qq = "b4a_d40_first"
    _make_inv(qq, day=40)
    inv = Investigator(investigator_repo.find_by_qq(qq))
    assert ml.is_unlocked(inv) is False

    ok, reloaded, _, _ = asyncio.run(_run_try_mainline(qq, inv))
    assert ok is False
    assert reloaded.day == 40  # 不占日（守门人战才冻结）
    assert ml.mainline_pool(reloaded) is None
    assert _day40_pool() and set(_day40_pool()) == {"36"}
    _cleanup(qq)


def test_day40_opt_in_mainline_no_doorkeeper():
    """二周目+主线（opt_in）day40：三线终局已登记；分支不再重排、无守门人干扰。"""
    qq = "b4a_d40_in"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=37)
    inv.set_flag("ml.unlocked", True)
    ml.apply_selector_choice(inv, "guard_in")
    inv.set_flag("ml.line", "dread")
    inv.set_flag("ml.dread.node", 10)
    ml.register_mainline_ending(inv, "E12")  # 终局战登记（三线终局 + done）
    inv.day = 40
    inv.save()
    reloaded = Investigator(investigator_repo.find_by_qq(qq))

    # 三线终局战已登记
    col = ending_repo.get_collection(qq)
    endings = ujson.loads(col.endings)
    assert any(r.get("id") == "E12" for r in endings)
    assert reloaded.get_flag("ml.dread.done") is True

    # day40：分支节点不再重排（ml.dread.done 门）→ 无「双结局/双 day40」
    assert ml.branch_available(reloaded, 40) is False
    ok, r2, _, _ = asyncio.run(_run_try_mainline(qq, reloaded))
    assert ok is False  # 主线不再接管 day40
    assert r2.day == 40

    # 无守门人干扰：怪物走主线池（dread），非 36
    pool = ml.mainline_pool(r2)
    assert pool is not None
    monster = ml.pick_mainline_monster(r2)
    assert monster in pool and monster != "36"
    _cleanup(qq)


def test_day40_opt_out_doorkeeper_back_to_normal():
    """二周目+门扉（opt_out）day40：回到现状——守门人 36，无主线。"""
    qq = "b4a_d40_out"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=40)
    inv.set_flag("ml.unlocked", True)
    ml.apply_selector_choice(inv, "door")
    inv.save()
    inv = Investigator(investigator_repo.find_by_qq(qq))

    ok, reloaded, _, _ = asyncio.run(_run_try_mainline(qq, inv))
    assert ok is False
    assert reloaded.day == 40  # 不占日（守门人战才冻结）
    assert ml.mainline_pool(reloaded) is None  # 纯门扉，零主线
    assert _day40_pool() and set(_day40_pool()) == {"36"}
    _cleanup(qq)
