"""隐藏挑战「启」（怪物 38）——已收编为 boss_framework 薄壳。

原镜像逻辑（触发/对话/按钮/挑战重入/跳过今日）全部下沉至 `data/boss_data.json` 的
「qiren」条目与 `src/services/boss_framework.py`；本文件仅保留兼容导出
（qiren_unlocked / qiren_should_trigger / qiren_pending / qiren_send_dialogue /
handle_qiren_button），确保旧调用方与既有测试零改动，行为逐字节不变。

按钮回调由 boss_framework 模块导入时按 boss id 自动注册（kind="qiren"）。
"""

from __future__ import annotations

from typing import Optional

from nonebot.adapters import Bot

from ..services.boss_framework import (
    boss_should_trigger_standalone,
    boss_unlocked,
    handle_boss_button,
    send_boss_dialogue,
)
from ..services.dice_roller import roll_dice
from ..utils.state_registry import register_state_store

BOSS_ID = "qiren"

# 待挑战旗标：user_id -> True（兼容旧调用方直接写/读；通用解析由 boss_framework 统一收口）
qiren_pending: dict[str, bool] = {}
register_state_store(qiren_pending)


def qiren_unlocked(inv) -> bool:
    """触发条件门：day20-39 且持有 501/508 任一（读 boss_data 触发，缺省回退 reply_data triggers）。"""
    return boss_unlocked(BOSS_ID, inv)


def qiren_should_trigger(inv) -> bool:
    """独立概率触发（兼容导出）：解锁后掷 d20 出触发值则 True。"""
    return boss_should_trigger_standalone(BOSS_ID, inv, roll_fn=roll_dice)


async def qiren_send_dialogue(user_id: str, bot: Bot, send) -> None:
    """发送启的现身台词 + 「挑战 / 不挑战」按钮（等待玩家选择）。"""
    await send_boss_dialogue(BOSS_ID, user_id, bot, send)


async def handle_qiren_button(
    user_id: str,
    choice: str,
    bot: Bot,
    group_openid: str = "",
    token: Optional[int] = None,
) -> None:
    """挑战/不挑战 按钮回调（薄壳转发 boss_framework）。"""
    await handle_boss_button(
        BOSS_ID, user_id, choice, bot, group_openid, token
    )
