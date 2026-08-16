"""每日冒险状态服务（daily_service）：承载 plugins 层共享的「今日冒险」状态逻辑。

架构修复 P0-批次B 循环②：guest/gm_room 原惰性导入 `src.plugins.adventure` 的
`_mark_adventure_done` / `_skip_daily` / `door_states`，与 adventure 顶层导入
guest/gm_room 构成插件层互相导入环。现将这些共享状态/函数下沉到 services 层：

- `_adventure_done_today` / `_mark_adventure_done`：自然日「今日已完成」标记
  （util.DaylyRecord，0 点自动重置）。
- `_skip_daily`：统一「跳过今日」（解除冒险态 + day+1 day40 冻结 + 落库）。
- `door_states`：门扉抉择内存状态（adventure 渲染 / gm_room 梦之碎片重掷共用）。

迁移后行为逐字节不变；`plugins/adventure.py` 保留 re-export
（from ..services.daily_service import ...），旧调用方与既有测试零改动。
"""

from __future__ import annotations

from util.DaylyRecord import add_data, get_data, write_json

from ..models.player import Investigator

# 门扉抉择状态（类比 event_states）：user_id -> {"choices": [...]}
door_states: dict[str, dict] = {}


def _adventure_done_today(user_id: str) -> bool:
    """今日冒险是否已完成（按自然日记录，0 点自动重置）。"""
    return bool(get_data(user_id, "adventure_done"))


def _mark_adventure_done(user_id: str) -> None:
    """记录今日冒险已完成。"""
    add_data(user_id, "adventure_done", True)
    write_json()


def _skip_daily(user_id: str, inv: Investigator) -> None:
    """统一「跳过今日」：解除冒险态 + day+1（day40 冻结）+ 落库。

    jk/qiren「不挑战」、guest/gm_room 归途共用（四处各自 day+1 的收口）。
    day40 冻结保留：终局由守门人战斗结算，不再额外 +1。
    _mark_adventure_done / check_daily 由调用方按原语义自行调用（guest/gm_room 不调用，
    它们在入场时已标记当日完成，结算时仅推进 day）。
    """
    inv.is_adventure = False
    if inv.day < 40:
        inv.day += 1
    inv.save()
