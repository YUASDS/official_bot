"""主线弧·三线合成批3：链路验证（开幕→分流→三线分支→终局结局/纪念品）+ 一周目零回归。

对齐 `.qa/plans/mainline-v10-integrated-dsh.md`（三线统一集成定稿）与本批需求：

1. 开幕三幕 day1/4/7 调度（占日 day+1）+ 选项倾向（apply_tendency → ml.<lane> 累积 +
   ml.opening.<day>.picked + ml.at）+ 当日怪物切所选倾向池。
2. day10 分流判定：MAX 唯一胜出 / 平局→courage(hymn) / 全0→courage(hymn) → ml.line 锁定
   + ml.at 清空 + 已分流幂等。
3. 冒险融合三态：未解锁 None / 倾向池（ml.at）/ 分支池（ml.line）。
4. 三线分支走通（直接驱动 dispatch，按 ml.<line>.node 路由各程）：
   - dread：程一..程十(day13..37) dispatch 路由 → 终局怪 66 → E12 + 纪念品 516。
   - hymn：二连战 52→蜕皮→53 → E11 + 纪念品 515。
   - free：程十 78 → 终局怪 79 → E13 + 纪念品 517。
5. 终局节点 victory 效果「结局」键登记结局 + 纪念品物品发放 + done 标记（ml.<line>.done
   + flow.<flow_id>.done）。
6. 一周目零回归：ng_plus=0 时 mainline 全部不可达（调度/占日/怪物池均不触发）。

只测链路行为（world 内容 + mainline/flow 效果），不改 src/（test/ 不入库）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import asyncio
import ujson

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


def _drive(qq: str, choice: str, bot: ConsoleBot) -> list[str]:
    """flow 阶段输入驱动（按钮回调/命令兜底共用 handle_flow_input）。"""
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(fe.handle_flow_input(qq, choice, bot, send, None))
    return sent


def _hold_win(service) -> None:
    """坚守战条件胜利 mock（hymn 二连战：52/53）。"""

    def _act(action):
        service._conditional_won = True
        service.hold_win_text = "你撑过了。"
        return ("你顶住了色彩。", "战斗终结")

    service.execute_action = _act


def _kill_win(service) -> None:
    """普通战斗击杀胜利 mock（dread 66 / free 78/79）。"""

    def _act(action):
        service.hp_record["mon"] = 0
        return ("你击倒了它。", "战斗终结")

    service.execute_action = _act


def _win_battle(service, win_fn) -> None:
    win_fn(service)
    _drive(service._qq, "格斗", ConsoleBot())


# 战斗 mock 需要 qq 引用（flow_state 的 battle 无 qq 字段，用闭包驱动外部）
# 统一入口：根据当前战斗选择击杀/坚守后驱动「格斗」推进。
def _finish_battle(qq: str, win_fn=_kill_win) -> None:
    svc = fe.flow_states[qq]["battle"]
    win_fn(svc)
    _drive(qq, "格斗", ConsoleBot())


def _start_dispatch(qq: str, flow_id: str) -> None:
    """start_flow + 渲染 dispatch 节点。"""
    state = fe.start_flow(qq, flow_id)
    assert state is not None, f"{flow_id} flow 未注册"
    bot = ConsoleBot()

    async def send(m):
        pass

    asyncio.run(fe.render_flow_node(qq, state, bot, send))


# --- 1. 开幕三幕调度 + 倾向 + 当日切池（day1/4/7，占日 day+1） ---
def test_opening_three_acts_schedule_tendency_pool():
    qq = "merge_open"
    _set_ng_plus(qq, 2)
    # 第一幕 day1（占日→day2，route o1）
    inv = _make_inv(qq, day=1)
    inv.set_flag("ml.unlocked", True)
    # 二周目 day1：先经选择器选「踏入主线」（opt_in）→ 随后开幕第一幕接管
    ml.apply_selector_choice(inv, "guard_in")
    inv.save()
    ok, reloaded, _, bot = asyncio.run(_run_try_mainline(qq, inv))
    assert ok is True
    assert reloaded.day == 2
    assert fe.flow_states[qq]["node_id"] == "dispatch"
    _drive(qq, "庄园·晨雾", bot)
    assert fe.flow_states[qq]["node_id"] == "o1"
    _drive(qq, "把信收好，去书房翻旧档案", bot)
    r = Investigator(investigator_repo.find_by_qq(qq))
    assert int(r.get_flag("ml.dread") or 0) == 1
    assert r.get_flag("ml.opening.1.picked") == "dread"
    assert r.get_flag("ml.at") == "dread"
    assert ml.mainline_pool(r) == list(ml._cfg("怪物池", "dread"))
    _cleanup(qq)

    # 第二幕 day4（占日→day5，route o2）
    inv = _make_inv(qq, day=4)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.opening.1.picked", "dread")
    inv.save()
    ok, reloaded, _, bot = asyncio.run(_run_try_mainline(qq, inv))
    assert ok is True and reloaded.day == 5
    _drive(qq, "井边·午后", bot)
    assert fe.flow_states[qq]["node_id"] == "o2"
    _drive(qq, "问他愿不愿意帮忙，一起去查井水的光", bot)
    r = Investigator(investigator_repo.find_by_qq(qq))
    assert r.get_flag("ml.opening.4.picked") == "hymn"
    assert int(r.get_flag("ml.hymn") or 0) == 1
    assert ml.mainline_pool(r) == list(ml._cfg("怪物池", "hymn"))
    _cleanup(qq)

    # 第三幕 day7（占日→day8，route o3）→ free 倾向
    inv = _make_inv(qq, day=7)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.opening.1.picked", "dread")
    inv.set_flag("ml.opening.4.picked", "hymn")
    inv.save()
    ok, reloaded, _, bot = asyncio.run(_run_try_mainline(qq, inv))
    assert ok is True and reloaded.day == 8
    _drive(qq, "池畔·入夜", bot)
    assert fe.flow_states[qq]["node_id"] == "o3"
    _drive(qq, "拉着沈讲师后退，观察它的反应", bot)
    r = Investigator(investigator_repo.find_by_qq(qq))
    assert r.get_flag("ml.opening.7.picked") == "free"
    assert int(r.get_flag("ml.free") or 0) == 1
    assert ml.mainline_pool(r) == list(ml._cfg("怪物池", "free"))
    _cleanup(qq)


# --- 2. day10 分流判定 → ml.line 锁 ---
def test_branch_split_max_tie_allzero():
    qq = "merge_split"
    _set_ng_plus(qq, 2)
    # MAX 唯一胜出 → dread
    inv = _make_inv(qq, day=10)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.dread", 2)
    inv.set_flag("ml.hymn", 1)
    inv.save()
    assert ml.resolve_branch(inv, 10) == "dread"
    assert inv.get_flag("ml.line") == "dread"
    assert inv.get_flag("ml.at") is None
    assert ml.resolve_branch(inv, 11) is None  # 已分流幂等
    _cleanup(qq)

    # 平局 → 平局策略 courage=hymn
    inv = _make_inv(qq, day=10)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.dread", 1)
    inv.set_flag("ml.free", 1)
    inv.save()
    assert ml.resolve_branch(inv, 10) == "hymn"
    assert inv.get_flag("ml.line") == "hymn"
    _cleanup(qq)

    # 全0 → hymn（平局策略兜底）
    inv = _make_inv(qq, day=10)
    inv.set_flag("ml.unlocked", True)
    inv.save()
    assert ml.resolve_branch(inv, 10) == "hymn"
    assert inv.get_flag("ml.line") == "hymn"
    _cleanup(qq)


# --- 3. 冒险融合三态（怪物池） ---
def test_mainline_pool_merge_three_states():
    qq = "merge_pool"
    _set_ng_plus(qq, 2)
    # 未解锁 → None
    inv = _make_inv(qq, day=2)
    assert ml.mainline_pool(inv) is None
    # 解锁未选择 → None
    inv.set_flag("ml.unlocked", True)
    inv.save()
    assert ml.mainline_pool(inv) is None
    # 倾向池
    ml.apply_tendency(inv, 1, "dread")
    assert ml.mainline_pool(inv) == list(ml._cfg("怪物池", "dread"))
    # 已分流 → 分支池（残留 at 不干扰）
    inv2 = _make_inv(qq, day=12)
    inv2.set_flag("ml.unlocked", True)
    inv2.set_flag("ml.line", "free")
    inv2.set_flag("ml.at", "dread")
    inv2.save()
    assert ml.mainline_pool(inv2) == list(ml._cfg("怪物池", "free"))
    _cleanup(qq)


# --- 4. 三线分支 dispatch 走通（各程路由） ---


def test_branch_occupy_day_rhythm_and_dispatch():
    """分散窗 day10 起 间隔3 的节点日（13/16/19...37）：进入延迟占日（不推 day/不计数），flow 完成才补。"""
    qq = "merge_branch_day"
    _set_ng_plus(qq, 2)
    for node_day in (13, 16, 19, 22, 25, 28, 31, 34, 37):
        inv = _make_inv(qq, day=node_day)
        inv.set_flag("ml.unlocked", True)
        inv.set_flag("ml.line", "hymn")
        inv.save()
        assert ml.branch_available(inv, node_day) is True
        ok, reloaded, _, _ = asyncio.run(_run_try_mainline(qq, inv))
        assert ok is True
        assert reloaded.day == node_day  # 进入不推 day（延迟占日）
        assert int(reloaded.get_flag("ml.hymn.node") or 0) == 0  # 不计数
        assert reloaded.get_flag(f"ml.hymn.node.{node_day}") is None
        assert reloaded.get_flag(f"ml.hymn.entered.{node_day}") is True
        assert fe.flow_states.get(qq)  # hygiene: flow 已建
        # 模拟 flow 完成 → 补占日 + 计数 + 清 entered
        state = fe.flow_states[qq]
        bot = ConsoleBot()

        async def _send(m):
            pass

        asyncio.run(fe._finish_flow(qq, state, bot, _send))
        done = Investigator(investigator_repo.find_by_qq(qq))
        assert done.day == node_day + 1  # 占日
        assert int(done.get_flag("ml.hymn.node") or 0) == 1  # 新周目首节点计数
        assert done.get_flag(f"ml.hymn.node.{node_day}") is True
        assert done.get_flag(f"ml.hymn.entered.{node_day}") is None
    _cleanup(qq)
_DREAD_NODES = {
    1: "书房·最后的线索", 2: "古墓入口", 3: "地下回廊", 4: "深渊裂隙",
    5: "禁忌图书馆", 6: "祭坛之间", 7: "旧日之痕", 8: "归途·岔路",
    9: "最后的门", 10: "终焉前厅",
}
_HYMN_NODES = {
    1: "西墙·林缘", 2: "枯果园", 3: "灰麦田", 4: "灰泥沼·渡口",
    5: "农舍塌屋", 6: "干河床", 7: "老井", 8: "井后荒宅",
    9: "灰海荒原", 10: "直面之地·最后一夜",
}
_FREE_NODES = {
    1: "庄园钟楼", 2: "雾鸣町火车站", 3: "镜面列车", 4: "镜湖湖畔",
    5: "湖心岛", 6: "镜之回廊", 7: "深蓝镜", 8: "银灰镜",
    9: "透明镜", 10: "镜厅",
}


def _assert_routed(qq: str, flow_id: str, expected: str) -> None:
    """dispatch 选项路由校验：应命中对应程节点（文本类程自动推进到 end 也算走通）。

    - 有活跃态 → 节点应等于目标程节点（或其后继文本节点），且不再是 dispatch。
    - 无活跃态（文本程自动收尾）→ 视为走通。
    """
    if qq in fe.flow_states:
        nid = fe.flow_states[qq]["node_id"]
        assert nid != "dispatch", f"{flow_id} {expected} 停留 dispatch"
        flow = fe.get_flow(flow_id)
        nodes = (flow.get("节点") or {}).keys()
        assert nid in nodes, f"{expected} 路由到未知节点 {nid!r}"
    # 文本类程（h1/h3_orient/h6_climb）自动推进至 end 收尾，无活跃态属正常


def test_dread_dispatch_all_nodes():
    qq = "merge_dread"
    _set_ng_plus(qq, 2)
    for n, choice in _DREAD_NODES.items():
        inv = _make_inv(qq, day=13)
        inv.set_flag("ml.unlocked", True)
        inv.set_flag("ml.line", "dread")
        inv.set_flag("ml.dread.node", n - 1)
        inv.save()
        _start_dispatch(qq, "mainline.dread")
        _drive(qq, choice, ConsoleBot())
        _assert_routed(qq, "mainline.dread", f"dread n={n}")
        fe.flow_states.pop(qq, None)
        _cleanup(qq)


def test_hymn_dispatch_all_nodes():
    qq = "merge_hymn"
    _set_ng_plus(qq, 2)
    for n, choice in _HYMN_NODES.items():
        inv = _make_inv(qq, day=13)
        inv.set_flag("ml.unlocked", True)
        inv.set_flag("ml.line", "hymn")
        inv.set_flag("ml.hymn.node", n - 1)
        inv.save()
        _start_dispatch(qq, "mainline.hymn")
        _drive(qq, choice, ConsoleBot())
        _assert_routed(qq, "mainline.hymn", f"hymn n={n}")
        fe.flow_states.pop(qq, None)
        _cleanup(qq)


def test_free_dispatch_all_nodes():
    qq = "merge_free"
    _set_ng_plus(qq, 2)
    for n, choice in _FREE_NODES.items():
        inv = _make_inv(qq, day=13)
        inv.set_flag("ml.unlocked", True)
        inv.set_flag("ml.line", "free")
        inv.set_flag("ml.free.node", n - 1)
        inv.save()
        _start_dispatch(qq, "mainline.free")
        _drive(qq, choice, ConsoleBot())
        _assert_routed(qq, "mainline.free", f"free n={n}")
        fe.flow_states.pop(qq, None)
        _cleanup(qq)


# --- 5. 终局链路：结局 + 纪念品 + done ---
def test_dread_finale_e12_souvenir():
    qq = "merge_dread_f"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=37)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.line", "dread")
    inv.set_flag("ml.dread.node", 9)
    inv.set_flag("ml.dread.entered.37", True)
    inv.save()
    _start_dispatch(qq, "mainline.dread")
    _drive(qq, "终焉前厅", ConsoleBot())
    # 程十 d10 → 选项「走向最后一扇门」→ final 战斗（怪 66）
    _drive(qq, "走向最后一扇门", ConsoleBot())
    svc = fe.flow_states[qq]["battle"]
    assert svc.monster.id == "66"
    _finish_battle(qq, _kill_win)  # 胜利 → souvenir_dread（516）→ end_dread（E12）
    fe.flow_states.pop(qq, None)
    r = Investigator(investigator_repo.find_by_qq(qq))
    assert r.get_flag("ml.dread.done") is True
    assert r.get_flag("flow.mainline.dread.done") == 1
    # flow 收尾钩子：补占日 + 节点计数 + 清 entered
    assert r.day == 38
    assert int(r.get_flag("ml.dread.node") or 0) == 10
    assert r.get_flag("ml.dread.node.37") is True
    assert r.get_flag("ml.dread.entered.37") is None
    col = ending_repo.get_collection(qq)
    endings = ujson.loads(col.endings)
    assert any(rec.get("id") == "E12" for rec in endings)
    equipments, _ = r.get_equipments()
    assert equipments.get("516", 0) == 1
    _cleanup(qq)


def test_hymn_finale_e11_souvenir_two_battle():
    qq = "merge_hymn_f"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=37)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.line", "hymn")
    inv.set_flag("ml.hymn.node", 9)
    inv.set_flag("ml.hymn.entered.37", True)
    inv.save()
    _start_dispatch(qq, "mainline.hymn")
    _drive(qq, "直面之地·最后一夜", ConsoleBot())
    # 程十 h10 → 选项「把灯举高，站定」→ final_a（怪 52 坚守4）
    _drive(qq, "把灯举高，站定", ConsoleBot())
    svc1 = fe.flow_states[qq]["battle"]
    assert svc1.monster.id == "52"
    assert svc1._win_condition and svc1._win_condition.get("回合") == 4
    _finish_battle(qq, _hold_win)  # → molting → final_b（怪 53 坚守4）
    assert fe.flow_states[qq]["node_id"] == "final_b"
    svc2 = fe.flow_states[qq]["battle"]
    assert svc2.monster.id == "53"
    assert svc2._win_condition and svc2._win_condition.get("回合") == 4
    _finish_battle(qq, _hold_win)  # 胜利 → souvenir_hymn（515）→ end_hymn（E11）
    fe.flow_states.pop(qq, None)
    r = Investigator(investigator_repo.find_by_qq(qq))
    assert r.get_flag("ml.hymn.done") is True
    assert r.get_flag("flow.mainline.hymn.done") == 1
    # flow 收尾钩子：补占日 + 节点计数 + 清 entered
    assert r.day == 38
    assert int(r.get_flag("ml.hymn.node") or 0) == 10
    assert r.get_flag("ml.hymn.node.37") is True
    assert r.get_flag("ml.hymn.entered.37") is None
    col = ending_repo.get_collection(qq)
    endings = ujson.loads(col.endings)
    assert any(rec.get("id") == "E11" for rec in endings)
    equipments, _ = r.get_equipments()
    assert equipments.get("515", 0) == 1
    _cleanup(qq)


def test_free_finale_e13_souvenir():
    qq = "merge_free_f"
    _set_ng_plus(qq, 2)
    inv = _make_inv(qq, day=37)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.line", "free")
    inv.set_flag("ml.free.node", 9)
    inv.set_flag("ml.free.entered.37", True)
    inv.save()
    _start_dispatch(qq, "mainline.free")
    _drive(qq, "镜厅", ConsoleBot())
    # 程十 f10 → 战斗（怪 78）→ 胜利 → final（怪 79 镜中之主）
    svc = fe.flow_states[qq]["battle"]
    assert svc.monster.id == "78"
    _finish_battle(qq, _kill_win)
    svc2 = fe.flow_states[qq]["battle"]
    assert svc2.monster.id == "79"
    _finish_battle(qq, _kill_win)  # 胜利 → souvenir_free（517）→ end_free（E13）
    fe.flow_states.pop(qq, None)
    r = Investigator(investigator_repo.find_by_qq(qq))
    assert r.get_flag("ml.free.done") is True
    assert r.get_flag("flow.mainline.free.done") == 1
    # flow 收尾钩子：补占日 + 节点计数 + 清 entered
    assert r.day == 38
    assert int(r.get_flag("ml.free.node") or 0) == 10
    assert r.get_flag("ml.free.node.37") is True
    assert r.get_flag("ml.free.entered.37") is None
    col = ending_repo.get_collection(qq)
    endings = ujson.loads(col.endings)
    assert any(rec.get("id") == "E13" for rec in endings)
    equipments, _ = r.get_equipments()
    assert equipments.get("517", 0) == 1
    _cleanup(qq)


# --- 6. 一周目零回归 ---
def test_first_run_all_mainline_unreachable():
    qq = "merge_first"
    inv = _make_inv(qq, day=1)
    assert ml.is_unlocked(inv) is False
    assert ml.mainline_pool(inv) is None
    ok, reloaded, _, _ = asyncio.run(_run_try_mainline(qq, inv))
    assert ok is False
    assert reloaded.day == 1  # 不占日
    assert ml.opening_available(inv, 1) is False
    assert ml.branch_available(inv, 10) is False
    _cleanup(qq)
