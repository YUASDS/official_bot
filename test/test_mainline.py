"""主线弧期1批1b：mainline.py 插件核心逻辑测试。

覆盖：
1. 一周目零回归（ng_plus=0）：mainline 全部关闭——is_unlocked False / mainline_pool None /
   opening_available / branch_available False / try_mainline_daily 不占日（day 不变、返回 False）。
2. is_unlocked 二周目解锁判定（ng_plus ≥ 阈值 → 置 ml.unlocked，幂等）。
3. opening_available day1/4/7 调度判定 + 排除（未解锁/已分流/当日已处理/非开幕日）。
4. 开幕选择 apply_tendency：倾向 +1 + ml.opening.day.picked + ml.at → mainline_pool 切倾向池。
5. 分流判定 resolve_branch：MAX 唯一胜出 / 平局→courage(hymn) / 全0→courage(hymn)；ml.line 锁定 +
   ml.at 清空；已分流幂等返回 None。
6. mainline_pool 三态：未选线 None（解锁但不做选择）/ 选线池（ml.at）/ 分流后锁定池（ml.line）。
7. 分支节点节奏 branch_available（每「间隔」天一个）+ 延迟占日（进入不推 day/不计数，flow 完成补占日）。
8. 结局钩子 register_mainline_ending：ml.<line>.done + ending_repo 登记结局。
9. 调度入口 try_mainline_daily：开幕日接管（day+1 / mark_adventure_done / 走 flow 或落回执）。

只测新增行为，不改 src/（test/ 不入库）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import asyncio
from unittest.mock import AsyncMock, patch

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
from src.services import flow_engine as fe


def _make_inv(qq: str, day: int = 1, name: str = "Mainline") -> Investigator:
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
    """表 B ng_plus 直接置位（模拟通过结局数）。"""
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
    """驱动异步调度入口，收集发送消息与 day 变化。"""
    sent: list[str] = []
    bot = ConsoleBot()

    async def send(msg):
        sent.append(str(msg))

    async def finish(msg):
        raise SystemExit("finish called")

    ok = await ml.try_mainline_daily(qq, inv, bot, send, finish)
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    return ok, reloaded, sent


# --- 1. 一周目零回归 ---
def test_first_run_all_mainline_closed():
    qq = "t1"
    _make_inv(qq, day=1)
    inv = Investigator(investigator_repo.find_by_qq(qq))
    assert ml.is_unlocked(inv) is False
    assert inv.get_flag("ml.unlocked") is None
    assert ml.opening_available(inv, 1) is False
    assert ml.branch_available(inv, 1) is False
    assert ml.mainline_pool(inv) is None

    ok, reloaded, sent = asyncio.run(_run_try_mainline(qq, inv))
    assert ok is False
    assert reloaded.day == 1  # 不占日
    _cleanup(qq)


# --- 2. is_unlocked 二周目解锁判定 ---
def test_is_unlocked_ngplus_threshold():
    qq = "t2"
    _set_ng_plus(qq, 0)
    inv = _make_inv(qq, day=1)
    assert ml.is_unlocked(inv) is False
    assert inv.get_flag("ml.unlocked") is None

    _set_ng_plus(qq, 1)
    inv = Investigator(investigator_repo.find_by_qq(qq))
    assert ml.is_unlocked(inv) is True
    assert inv.get_flag("ml.unlocked") is True
    # 幂等：再次调用不报错、仍 True
    assert ml.is_unlocked(inv) is True
    _cleanup(qq)


# --- 3. opening_available 调度判定 ---
def test_opening_available_days_and_gates():
    qq = "t3"
    _set_ng_plus(qq, 3)
    inv = _make_inv(qq, day=1)
    inv.set_flag("ml.unlocked", True)
    inv.save()

    assert ml.opening_available(inv, 1) is True
    assert ml.opening_available(inv, 4) is True
    assert ml.opening_available(inv, 7) is True
    assert ml.opening_available(inv, 2) is False
    assert ml.opening_available(inv, 10) is False

    # 当日已处理（ml.opening.1.picked）→ False
    inv.set_flag("ml.opening.1.picked", "dread")
    inv.save()
    assert ml.opening_available(inv, 1) is False

    # 已分流（ml.line）→ False（后续开幕不再触发）
    inv.clear_flag("ml.opening.1.picked")
    inv.set_flag("ml.line", "hymn")
    inv.save()
    assert ml.opening_available(inv, 4) is False
    _cleanup(qq)


def test_opening_unlocked_required():
    qq = "t3b"
    _set_ng_plus(qq, 0)
    inv = _make_inv(qq, day=1)
    inv.set_flag("ml.dread", 5)  # 有倾向但不是二周目
    inv.save()
    assert ml.opening_available(inv, 1) is False
    _cleanup(qq)


# --- 4. 开幕选择 apply_tendency → 倾向+1 + picked + 切池 ---
def test_apply_tendency_increments_and_switches_pool():
    qq = "t4"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=1)
    inv.set_flag("ml.unlocked", True)
    inv.save()

    ml.apply_tendency(inv, 1, "dread")
    assert int(inv.get_flag("ml.dread") or 0) == 1
    assert inv.get_flag("ml.opening.1.picked") == "dread"
    assert inv.get_flag("ml.at") == "dread"
    # 当日怪物切倾向池（mainline_pool 返回 dread 池）
    dread_pool = ml._cfg("怪物池", "dread")
    assert ml.mainline_pool(inv) == list(dread_pool)

    # 再选 hymn 累积
    ml.apply_tendency(inv, 4, "hymn")
    assert int(inv.get_flag("ml.hymn") or 0) == 1
    assert int(inv.get_flag("ml.dread") or 0) == 1
    assert ml.mainline_pool(inv) == list(ml._cfg("怪物池", "hymn"))
    _cleanup(qq)


# --- 5. 分流判定 resolve_branch ---
def test_resolve_branch_max_unique():
    qq = "t5"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=10)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.dread", 2)
    inv.set_flag("ml.hymn", 1)
    inv.save()
    assert ml.resolve_branch(inv, 10) == "dread"
    assert inv.get_flag("ml.line") == "dread"
    assert inv.get_flag("ml.at") is None
    # 已分流幂等
    assert ml.resolve_branch(inv, 11) is None
    _cleanup(qq)


def test_resolve_branch_tie_goes_courage():
    qq = "t6"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=10)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.dread", 1)
    inv.set_flag("ml.hymn", 1)
    inv.save()
    assert ml.resolve_branch(inv, 10) == "hymn"  # courage 别名 → hymn
    assert inv.get_flag("ml.line") == "hymn"
    _cleanup(qq)


def test_resolve_branch_all_zero_goes_courage():
    qq = "t7"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=10)
    inv.set_flag("ml.unlocked", True)
    inv.save()
    assert ml.resolve_branch(inv, 10) == "hymn"
    assert inv.get_flag("ml.line") == "hymn"
    _cleanup(qq)


def test_resolve_branch_window_not_reached_or_locked():
    qq = "t8"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=5)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.dread", 9)
    inv.save()
    assert ml.resolve_branch(inv, 5) is None  # 未到分歧窗
    assert inv.get_flag("ml.line") is None

    inv2 = _make_inv(qq, day=10)
    inv2.set_flag("ml.unlocked", True)
    inv2.set_flag("ml.line", "free")
    inv2.save()
    assert ml.resolve_branch(inv2, 10) is None  # 已分流
    _cleanup(qq)


def test_resolve_branch_locked_unlocked_required():
    qq = "t9"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=10)
    inv.set_flag("ml.dread", 9)
    inv.save()
    assert ml.resolve_branch(inv, 10) is None  # 未解锁不分流
    assert inv.get_flag("ml.line") is None
    _cleanup(qq)


# --- 6. mainline_pool 三态 ---
def test_mainline_pool_three_states():
    qq = "t10"
    _set_ng_plus(qq, 2)

    # 未解锁 → None
    inv0 = _make_inv(qq, day=1)
    assert ml.mainline_pool(inv0) is None

    # 解锁但未做任何选择 → None
    inv = _make_inv(qq, day=2)
    inv.set_flag("ml.unlocked", True)
    inv.save()
    assert ml.mainline_pool(inv) is None

    # 有最近倾向 ml.at → 该倾向池
    ml.apply_tendency(inv, 1, "free")
    assert ml.mainline_pool(inv) == list(ml._cfg("怪物池", "free"))

    # 已分流 ml.line → 锁定池
    inv2 = _make_inv(qq, day=12)
    inv2.set_flag("ml.unlocked", True)
    inv2.set_flag("ml.line", "hymn")
    inv2.set_flag("ml.at", "dread")  # 残留 at 不干扰分流池
    inv2.save()
    assert ml.mainline_pool(inv2) == list(ml._cfg("怪物池", "hymn"))
    _cleanup(qq)


# --- 7. 分支节点节奏 branch_available + 占日 ---
def test_branch_available_rhythm():
    qq = "t11"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=10)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.line", "hymn")
    inv.save()

    # 分歧窗 day10 起，间隔 3：day10/13/16 是节点
    assert ml.branch_available(inv, 10) is True
    assert ml.branch_available(inv, 11) is False
    assert ml.branch_available(inv, 13) is True
    assert ml.branch_available(inv, 9) is False
    # 未分流 / 未解锁 → False
    inv.set_flag("ml.line", None)
    inv.save()
    assert ml.branch_available(inv, 10) is False
    _cleanup(qq)


def test_branch_node_occupy_day():
    qq = "t12"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=13)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.line", "hymn")
    inv.save()

    ok, reloaded, sent = asyncio.run(_run_try_mainline(qq, inv))
    assert ok is True
    # 延迟占日：进入不推 day、不计数，只占当日冒险 + 置 entered
    assert reloaded.day == 13
    assert int(reloaded.get_flag("ml.hymn.node") or 0) == 0
    assert reloaded.get_flag("ml.hymn.node.13") is None
    assert reloaded.get_flag("ml.hymn.entered.13") is True
    assert fe.flow_states.get(qq)  # flow 已建
    # flow 完成（模拟 finish）→ 补占日 + 计数 + 清 entered
    state = fe.flow_states[qq]
    bot = ConsoleBot()

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(fe._finish_flow(qq, state, bot, send))
    done = Investigator(investigator_repo.find_by_qq(qq))
    assert done.day == 14
    assert int(done.get_flag("ml.hymn.node") or 0) == 1
    assert done.get_flag("ml.hymn.node.13") is True
    assert done.get_flag("ml.hymn.entered.13") is None
    _cleanup(qq)


def test_first_branch_dispatch_has_available_option():
    """首个分支 node=0 时必须能选 d1（回归：曾因条件 1..10 全锁死）。"""
    qq = "t12b"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=10)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.line", "hymn")
    inv.save()

    ok, reloaded, sent = asyncio.run(_run_try_mainline(qq, inv))
    assert ok is True
    state = fe.flow_states.get(qq)
    assert state is not None and state["flow_id"] == "mainline.hymn"
    assert int(reloaded.get_flag("ml.hymn.node") or 0) == 0
    first = fe.get_flow("mainline.hymn")["节点"]["dispatch"]["选项"][0]["输入"]
    single = fe.data_loader.get_text("flow.single_step", next=first)
    assert any(single in s for s in sent), sent
    fe.flow_states.pop(qq, None)
    _cleanup(qq)


# --- 8. 结局钩子登记 ---
def test_register_mainline_ending():
    qq = "t13"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=20)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.line", "hymn")
    inv.save()

    ml.register_mainline_ending(inv, "E11")
    assert inv.get_flag("ml.hymn.done") is True
    col = ending_repo.get_collection(qq)
    assert col is not None
    endings = __import__("ujson").loads(col.endings)
    assert any(r.get("id") == "E11" for r in endings)
    # 幂等：重复登记不产生重复记录
    ml.register_mainline_ending(inv, "E11")
    endings2 = __import__("ujson").loads(col.endings)
    assert sum(1 for r in endings2 if r.get("id") == "E11") == 1
    _cleanup(qq)


# --- 9. 调度入口 try_mainline_daily ---
def test_try_mainline_daily_opening_occupies():
    qq = "t14"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=1)
    inv.set_flag("ml.unlocked", True)
    # 二周目 day1：先走选择器选「踏入主线」（opt_in），随后 day1 开幕插曲才接管
    assert ml.apply_selector_choice(inv, "guard_in") is True
    inv.save()

    ok, reloaded, sent = asyncio.run(_run_try_mainline(qq, inv))
    assert ok is True
    assert reloaded.day == 2  # 占日 day+1
    assert sent  # 落回执（flow 未建，批1c 补）
    _cleanup(qq)


def test_try_mainline_daily_second_run_gate():
    qq = "t15"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=4)
    inv.set_flag("ml.unlocked", True)
    inv.save()
    ok1, reloaded1, _ = asyncio.run(_run_try_mainline(qq, inv))
    assert ok1 is True
    assert reloaded1.day == 5
    # 再来一次同日：日已推进，day5 非开幕日 → 不接管
    ok2, reloaded2, _ = asyncio.run(_run_try_mainline(qq, reloaded1))
    assert ok2 is False
    assert reloaded2.day == 5
    _cleanup(qq)
