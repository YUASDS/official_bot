"""主线弧核心逻辑（mainline）：二周目解锁 / 开幕插曲调度 / 分流判定 / 分支节点调度 / 冒险融合钩子。

对齐 `.qa/plans/mainline-arc-design.md`（同源分歧 · 二周目解锁 · 冒险融合）：

- **二周目解锁**：`is_unlocked(inv)` 读 ending_repo.ng_plus（通过结局数）≥ `二周目解锁.ng_plus_min`
  → 置 `ml.unlocked` 旗标（幂等）。一周目（ng_plus<1）未置位 → 全部主线机制关闭（零回归）。
- **开幕插曲**（day1/4/7，`mainline.opening` flow）：`opening_available` 为调度入口——
  `ml.unlocked` 且 day∈开幕插曲.days 且 `ml.line` 未设 且当日未处理。冒险互斥链优先触发。
- **倾向与分流**：开幕选择 → `ml.dread/hymn/free` 倾向 +1 并记 `ml.opening.<day>.picked`；
  day≥分歧窗口 时 `resolve_branch` 三倾向 MAX 判定（唯一最大且≥阈值 → 该支；平局/全0 → 平局策略
  courage 兜底），置 `ml.line` 锁分支（后续插曲不再触发）。
- **冒险融合**：`mainline_pool(inv)` 三态——未选线 None（走 check_point 默认）、
  有最近倾向 `ml.at` → 该倾向池、已分流 `ml.line` → 分支池；四周目零回归（未解锁恒 None）。
- **分支节点**：`branch_available` —— `ml.line` 已设时每 `分支节奏.间隔` 天一个节点，占日走
  `mainline.<line>` flow。

**循环防护**：adventure.py 顶层 import 本模块；本模块不顶层 import plugins（需要时函数内延迟导入）。
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Optional

import ujson
from loguru import logger

from ..models.player import ending_repo
from ..services.data_loader import data_loader

# 三主线倾向键（顺序固定，用于 MAX 判定 / 怪物池读取）
_LANES = ("dread", "hymn", "free")

# 平局策略别名 → 实际线键：courage=勇气线=hymn（设计 §2 默认值）
_TIEBREAK_ALIAS = {"courage": "hymn", "dread": "dread", "hymn": "hymn", "free": "free"}

# 二周目选择器信号：try_mainline_daily 返回此值表示「需选择」（不发 opening），由调用方展示选择 UI。
# 唯一特殊返回：False=零动作；True=主线已占日接管；SELECTOR_SIGNAL=day1 需选择。
SELECTOR_SIGNAL = "mainline.selector"

# 配置缓存（对齐 boss_framework._load_boss_file）：首次读取后缓存，测试可重置
_mainline_config: Optional[dict] = None
_ML_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "mainline_config.json"
)


def _load_config() -> dict:
    """加载 data/mainline_config.json（损坏/缺失回退空 dict）。"""
    global _mainline_config
    if _mainline_config is None:
        try:
            with open(_ML_CONFIG_PATH, encoding="utf-8-sig") as f:
                _mainline_config = ujson.load(f) or {}
        except Exception:  # noqa: BLE001 - 数据缺失不阻断主流程
            logger.warning(f"mainline_config.json 加载失败：{_ML_CONFIG_PATH}")
            _mainline_config = {}
    return _mainline_config


def reset_mainline_config() -> None:
    """清空配置缓存（测试在临时数据目录内重载用）。"""
    global _mainline_config
    _mainline_config = None


def _cfg(*path: str) -> Any:
    """读取配置嵌套值；缺失逐级回退默认。"""
    node: Any = _load_config()
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _ng_plus(inv) -> int:
    """通过结局数（ending_repo 表 B ng_plus；无记录/损坏回退 0）。"""
    collection = ending_repo.get_collection(inv.qq)
    if collection is None:
        return 0
    try:
        return int(collection.ng_plus or 0)
    except (TypeError, ValueError) as e:
        logger.warning(f"静默异常[TypeError/ValueError] in _ng_plus: {e}")
        return 0


def is_unlocked(inv) -> bool:
    """二周目解锁判定：nd_plus ≥ ng_plus_min → 置 ml.unlocked（幂等）。

    通过结局数 ≥ 阈值即解锁；记录越阈仍返回 True（已置位不回收）。一周目恒 False。
    """
    min_ng = _cfg("二周目解锁", "ng_plus_min")
    min_ng = int(min_ng) if min_ng is not None else 1
    unlocked = _ng_plus(inv) >= min_ng
    if unlocked and not inv.get_flag("ml.unlocked"):
        inv.set_flag("ml.unlocked", True)
        inv.save()
    return bool(inv.get_flag("ml.unlocked"))


def _unlocked(inv) -> bool:
    """旗标读法：ml.unlocked 已置位（is_unlocked 未显式调用时的快路径）。"""
    return bool(inv.get_flag("ml.unlocked"))


def _mainline_enabled(inv) -> bool:
    """主线机制总开关：二周目已解锁 且 未「重游门扉」（ml.opt_out）。

    选择器选「重游门扉」后本局为纯门扉推进（opening/branch/怪物池全部关闭，零主线）。
    """
    return _unlocked(inv) and not inv.get_flag("ml.opt_out")


def _opening_days() -> list[int]:
    days = _cfg("开幕插曲", "days")
    if not isinstance(days, list):
        return []
    out = []
    for d in days:
        try:
            out.append(int(d))
        except (TypeError, ValueError) as e:
            logger.warning(f"静默异常[TypeError/ValueError] in _opening_days: {e}")
            continue
    return out


def opening_available(inv, day) -> bool:
    """开幕插曲调度判定：解锁 + day∈开幕插曲.days + 未分流（ml.line 未设） + 当日未处理。

    「当日未处理」= ml.opening.<day>.picked 未置位。返回 True 表示冒险互斥链应接管当日，
    由调用方占日并走 `flow mainline.opening`。
    """
    if not _mainline_enabled(inv):
        return False
    if inv.get_flag("ml.line"):
        return False
    if int(day) not in _opening_days():
        return False
    if inv.get_flag(f"ml.opening.{day}.picked"):
        return False
    return True


def apply_tendency(inv, day, tendency: str, save: bool = True) -> None:
    """开幕选择应用：倾向 +1 + 记当日已选（ml.opening.<day>.picked）+ 更新最近倾向 ml.at。

    由 flow 效果层（批1c）或测试调用；tendency ∈ {dread,hymn,free}。幂等（同日重复置同向）。
    写旗标但不 save（调用方可批量后统一 save），默认 save=True 与既有旗标写入习惯一致。
    """
    if tendency not in _LANES:
        return
    cur = int(inv.get_flag(f"ml.{tendency}") or 0)
    inv.set_flag(f"ml.{tendency}", cur + 1)
    inv.set_flag(f"ml.opening.{day}.picked", tendency)
    inv.set_flag("ml.at", tendency)
    if save:
        inv.save()


def _tendency_totals(inv) -> dict[str, int]:
    return {lane: int(inv.get_flag(f"ml.{lane}") or 0) for lane in _LANES}


def _tiebreak_lane() -> str:
    """平局/全0 策略（默认 courage=勇气线=hymn，直面=主角底色）。"""
    strategy = _cfg("平局策略")
    return _TIEBREAK_ALIAS.get(strategy, "hymn")


def resolve_branch(inv, day) -> str | None:
    """分流判定（day ≥ 分歧窗口 且 ml.line 未设 且 解锁时调用）：

    MAX(dread,hymn,free) ≥ 倾向阈值.min 且唯一最大 → 该支；平局/全0 → 平局策略（默认勇气线）。
    分流后置 ml.line（锁分支，后续插曲不再触发）+ 清 ml.at。已分流返回 None（幂等）。
    """
    if inv.get_flag("ml.line"):
        return None
    if not _mainline_enabled(inv):
        return None
    win_day = _cfg("分歧窗口", "day")
    win_day = int(win_day) if win_day is not None else 10
    if int(day) < win_day:
        return None
    totals = _tendency_totals(inv)
    min_th = _cfg("倾向阈值", "min")
    min_th = int(min_th) if min_th is not None else 1
    mx = max(totals.values())
    max_lanes = [lane for lane, v in totals.items() if v == mx and v > 0]
    if mx >= min_th and len(max_lanes) == 1:
        lane = max_lanes[0]
    elif mx >= min_th and len(max_lanes) > 1:
        lane = _tiebreak_lane()
    elif mx < min_th:
        # 全0 或全低于阈值 → 平局策略兜底（默认勇气线）
        lane = _tiebreak_lane()
    else:  # 理论不可达
        lane = _tiebreak_lane()
    inv.set_flag("ml.line", lane)
    inv.clear_flag("ml.at")
    inv.save()
    return lane


def _pool_lane(inv) -> str | None:
    """当前怪物池倾向：已分流 → ml.line；否则最近倾向 ml.at。"""
    line = inv.get_flag("ml.line")
    if line:
        return line
    at = inv.get_flag("ml.at")
    if at in _LANES:
        return at
    return None


def mainline_pool(inv) -> Optional[list[str]]:
    """冒险融合钩子：返回当日怪物应取自的主线池（id 列表）或 None。

    三态：
    -     未解锁（一周目/红旗未解锁）→ None（走 check_point 默认，零回归）。
    - 已分流（ml.line）→ 分支池。
    - 未分流但有最近倾向（ml.at）→ 倾向池。
    - 其余（未做任何主线选择）→ None（check_point 默认）。
    - 「重游门扉」（ml.opt_out）→ None（纯门扉推进，零主线）。
    """
    if not _mainline_enabled(inv):
        return None
    lane = _pool_lane(inv)
    if lane is None:
        return None
    pool = _cfg("怪物池", lane)
    if not isinstance(pool, list) or not pool:
        return None
    return list(pool)


def pick_mainline_monster(inv) -> Optional[str]:
    """从当前主线池随机抽怪（adventure 怪物抽取点用）；无池返回 None。"""
    pool = mainline_pool(inv)
    if not pool:
        return None
    return random.choice(pool)


def _branch_interval() -> int:
    interval = _cfg("分支节奏", "间隔")
    return int(interval) if interval is not None else 3


def _branch_day_offset(inv, day) -> int:
    """距分歧窗的步数（用于按间隔对齐分支节点日）。"""
    win_day = _cfg("分歧窗口", "day")
    win_day = int(win_day) if win_day is not None else 10
    return int(day) - win_day


def branch_available(inv, day) -> bool:
    """分支节点调度判定：解锁 + 已分流（ml.line） + day≥分歧窗口 + 对齐间隔 + 当日未处理。

    「当日未处理」= ml.<line>.node.<day> 未置位。命中的分支节点由冒险互斥链占日走
    `flow mainline.<line>`，并计数 ml.<line>.node。

    终局互斥：三线终局（ml.<line>.done）达成后不再重排分支节点——day40 不再重触发终局
    （防「双结局/双 day40」），且不落入门扉守门人判定（mainline 玩家 day40 归主线侧）。
    """
    if not _mainline_enabled(inv):
        return False
    line = inv.get_flag("ml.line")
    if not line:
        return False
    if inv.get_flag(f"ml.{line}.done"):
        return False
    offset = _branch_day_offset(inv, day)
    if offset < 0:
        return False
    interval = _branch_interval()
    if offset % interval != 0:
        return False
    if inv.get_flag(f"ml.{line}.node.{day}"):
        return False
    return True


def mark_branch_node(inv, day, save: bool = True) -> None:
    """分支节点完结：节点计数 +1 + 标记当日已处理（幂等）。"""
    line = inv.get_flag("ml.line")
    if not line:
        return
    node_count = int(inv.get_flag(f"ml.{line}.node") or 0)
    inv.set_flag(f"ml.{line}.node", node_count + 1)
    inv.set_flag(f"ml.{line}.node.{day}", True)
    if save:
        inv.save()


def register_mainline_ending(inv, ending_id: str, line: str | None = None) -> None:
    """结局钩子：分支终局达成 → 登记独立结局（ending_repo.add_ending）并置 ml.<line>.done。

    读 ending_repo（表 B 结局收集，跨周目）；line 缺省取 ml.line。幂等（add_ending 去重）。
    """
    line = line or inv.get_flag("ml.line")
    if line:
        inv.set_flag(f"ml.{line}.done", True)
    ending_repo.add_ending(inv.qq, ending_id)
    inv.save()


# --- 二周目选择器（day1 踏入主线 / 重游门扉） ---
def selector_enabled() -> bool:
    """选择器是否启用（mainline_config `选择器.启用`，缺省 True）。"""
    en = _cfg("选择器", "启用")
    return bool(en) if en is not None else True


def selector_copy() -> dict:
    """选择器文案段（`选择器.文案`，缺省空 dict）。"""
    copy = _cfg("选择器", "文案") or {}
    return copy if isinstance(copy, dict) else {}


def selector_chosen_text() -> str:
    """已选过文案（`选择器.选过文案`，缺省回退）。"""
    return str(_cfg("选择器", "选过文案") or "你已做过选择。")


def selector_available(inv) -> bool:
    """day1 选择器判定：启用 + 二周目已解锁 + 未选过（ml.opt_choice 未设）+ day==1。

    二周目玩家 day1 有且仅有一次明确选择机会；选定后（opt_choice）恒不再出现。
    day>1 仍未选不强制卡住——直接走既有主线/普通冒险流程。
    """
    if not selector_enabled():
        return False
    if not _unlocked(inv):
        return False
    if inv.get_flag("ml.opt_choice"):
        return False
    return int(inv.day) == 1


def apply_selector_choice(inv, choice: str, save: bool = True) -> bool:
    """应用选择器选项：guard_in→ml.opt_in（踏入主线）；door→ml.opt_out（重游门扉）。

    任一选择都置 ml.opt_choice=true（本局已选，不再弹选择器）。已选过返回 False（幂等）。
    opt_out 后本局 opening/branch/怪物池全部关闭（纯门扉推进）。
    """
    if inv.get_flag("ml.opt_choice"):
        return False
    choice = str(choice)
    if choice in ("guard_in", "guard", "主线", "踏入主线"):
        inv.set_flag("ml.opt_in", True)
    elif choice in ("door", "门扉", "重游门扉"):
        inv.set_flag("ml.opt_out", True)
    else:
        return False
    inv.set_flag("ml.opt_choice", True)
    if save:
        inv.save()
    return True


# --- 冒险互斥链调度入口（adventure.py 调用） ---
def _occupy_day(user_id: str, inv) -> None:
    """主线节点占日：统一 skip_daily(day+1, day40 冻结) + mark_adventure_done + check_daily。"""
    from ..services.daily_service import _mark_adventure_done, _skip_daily
    from ..services.ending_engine import check_daily

    _skip_daily(user_id, inv)
    _mark_adventure_done(user_id)
    check_daily(inv)


async def _start_mainline_flow(user_id: str, flow_id: str, bot, send) -> bool:
    """流转发：start_flow + render_flow_node（flow 未建/无入口返回 False，不抛错）。

    背景：批1c 才建世界文件；批1b 仅搭调度骨架——flow 缺失时仍返回 False，由调用方发占日回执。
    """
    from ..services.flow_engine import render_flow_node, start_flow

    state = start_flow(user_id, flow_id)
    if state is None:
        return False
    await render_flow_node(user_id, state, bot, send)
    return True


def _process_mainline_inv(user_id: str) -> Any:
    """重读最新调查员（分流/占日可能已改 day/flags）。无角色返回 None。"""
    from ..models.player import Investigator, investigator_repo

    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        return None
    return Investigator(inv_model)


async def try_mainline_daily(
    user_id: str, inv, bot, send, finish
) -> Any:
    """冒险互斥链主线优先级入口。

    返回：
    - `SELECTOR_SIGNAL`：二周目 day1 待选择——调用方展示选择器 UI（不占日、不发 opening），
      玩家选中后重进当日冒险。
    - True：主线命中占日接管当日。
    - False：不接管（走既有 乱入/普通冒险，零回归）。

    顺序：选择器(day1) → opt_out 短路 → 分流判定(resolve_branch) → 开幕插曲(opening)
        → 分支节点(branch)。命中分支/开幕 → 占日(day+1) + 走对应 flow。
    """
    # 幂等解锁检查：真实主流程每次入口置位 ml.unlocked（读结局数 ng_plus >= 门槛）。
    # 缺此调用会导致旗标恒空——选择器/开幕/分支永不触发（线上 bug：多周目无选择器按钮）。
    is_unlocked(inv)
    if selector_available(inv):
        return SELECTOR_SIGNAL
    if inv.get_flag("ml.opt_out"):
        return False

    resolved = resolve_branch(inv, inv.day)
    if resolved:
        inv = _process_mainline_inv(user_id) or inv

    if opening_available(inv, inv.day):
        _occupy_day(user_id, inv)
        flow_id = _cfg("开幕插曲", "flow") or "mainline.opening"
        if not await _start_mainline_flow(user_id, str(flow_id), bot, send):
            await _fallback_opening_message(user_id, bot, send)
        return True

    if branch_available(inv, inv.day):
        line = inv.get_flag("ml.line")
        node_day = inv.day
        _occupy_day(user_id, inv)
        mark_branch_node(inv, node_day, save=False)
        inv.save()
        if not await _start_mainline_flow(user_id, f"mainline.{line}", bot, send):
            await _fallback_branch_message(user_id, line, bot, send)
        return True

    return False


async def _fallback_opening_message(user_id: str, bot, send) -> None:
    """占时的开幕回执（flow 未建时的占位，批1c 用真实 flow 文案替换）。"""
    from ..utils.md_format import md_message

    await send(
        md_message(
            f"\n{data_loader.get_text('mainline.opening_placeholder')}",
            bot,
            mention=user_id,
        )
    )


async def _fallback_branch_message(user_id: str, line: str, bot, send) -> None:
    """分支节点占位回执（flow 未建时）。"""
    from ..utils.md_format import md_message

    await send(
        md_message(
            f"\n{data_loader.get_text('mainline.branch_placeholder', line=line)}",
            bot,
            mention=user_id,
        )
    )
