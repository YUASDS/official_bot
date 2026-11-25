"""隐藏挑战「JK」（怪物 48）——已收编为 boss_framework 薄壳。

原镜像逻辑（触发/对话/按钮/挑战重入/跳过今日）全部下沉至 `data/boss_data.json` 的
「jk」条目与 `src/services/boss_framework.py`；本文件仅保留兼容导出
（hidden_should_trigger / jk_unlocked / jk_pending / jk_send_dialogue / handle_jk_button），
确保旧调用方与既有测试零改动，行为逐字节不变。

按钮回调由 boss_framework 模块导入时按 boss id 自动注册（kind="jk"）。
"""

from __future__ import annotations

from typing import Optional

from nonebot.adapters import Bot

from ..services.boss_framework import (
    boss_pick_daily,
    boss_unlocked,
    handle_boss_button,
    send_boss_dialogue,
)
from ..services.dice_roller import roll_dice

BOSS_ID = "jk"

# 待挑战旗标：user_id -> True（兼容旧调用方直接写/读；通用解析由 boss_framework 统一收口）
jk_pending: dict[str, bool] = {}


def jk_unlocked(inv) -> bool:
    """触发条件门：day13-39（读 boss_data 触发，缺省回退 reply_data triggers，含边界）。"""
    return boss_unlocked(BOSS_ID, inv)


def hidden_should_trigger(inv) -> Optional[str]:
    """共享隐藏触发：遍历 boss 配置表，一次 d20 出触发值后从满足条件的候选随机选一。

    返回 "jk" / "qiren"（或未来同「hidden」共享组的新 BOSS id）；未掷出触发值或无候选返回 None。
    """
    return boss_pick_daily(inv, roll_fn=roll_dice)


async def jk_send_dialogue(user_id: str, bot: Bot, send) -> None:
    """发送 JK 的现身台词 + 「挑战 / 不挑战」按钮（等待玩家选择）。"""
    await send_boss_dialogue(BOSS_ID, user_id, bot, send)


async def handle_jk_button(
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
