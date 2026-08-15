"""隐藏挑战「JK」（怪物 48）触发与选择。

JK 与「启」（qiren）**同一层**共享每日 d20 出 1（5%）的隐藏挑战触发：
命中后从满足条件的候选随机选一（jk: day13-39；qiren: 持 501/508 且 day20-39）。
- 挑战 → 置位 `jk_pending`，重入今日冒险强制怪物 48（无专属环境）；
- 不挑战 → 跳过今日冒险：day+1（day40 冻结）并走日常结算管线。

触发条件门独立为 `jk_unlocked(inv)`；概率配置为 d20/触发值 1。
"""

from __future__ import annotations

import random

from nonebot.adapters import Bot
from nonebot.exception import FinishedException

from ..models.player import Investigator, investigator_repo
from ..services.data_loader import data_loader
from ..services.dice_roller import roll_dice
from ..utils.buttons import _send_to_user, register_button_handler
from ..utils.md_format import build_keyboard, md_message, need_create_message
from .qiren import qiren_unlocked

# 触发配置
_JK_MIN_DAY = 13                    # day≥13 开放（用户需求）
_JK_MAX_DAY = 40                    # day40 门扉归守门人，JK 不参与
_HIDDEN_DICE = "d20"                # 共享概率骰
_HIDDEN_TRIGGER_VALUE = 1           # 掷出触发值才触发（5%）

# 待挑战旗标：user_id -> True（点「挑战」后置位，下一次 /今日冒险 强制怪物 48）
jk_pending: dict[str, bool] = {}


def jk_unlocked(inv: Investigator) -> bool:
    """触发条件门：day>=13 且 day<40。

    day40 门扉归守门人，JK 不参与（结局线由守门人战斗接管）。
    """
    return _JK_MIN_DAY <= inv.day < _JK_MAX_DAY


def hidden_should_trigger(inv: Investigator) -> str | None:
    """共享隐藏挑战触发：每日一次 d20 出触发值后，从满足条件的候选随机选一。

    返回 "jk" / "qiren"；未掷出触发值或无候选返回 None。
    """
    _expr, val = roll_dice(_HIDDEN_DICE)
    if val != _HIDDEN_TRIGGER_VALUE:
        return None
    candidates: list[str] = []
    if jk_unlocked(inv):
        candidates.append("jk")
    if qiren_unlocked(inv):
        candidates.append("qiren")
    return random.choice(candidates) if candidates else None


async def jk_send_dialogue(user_id: str, bot: Bot, send) -> None:
    """发送 JK 的现身台词 + 「挑战 / 不挑战」按钮（等待玩家选择）。"""
    t = data_loader.get_text
    kb = build_keyboard(
        [
            [
                (t("jk.challenge"), "jk:挑战"),
                (t("jk.decline"), "jk:不挑战"),
            ]
        ]
    )
    msg = md_message(
        f"\n{t('jk.title')}\n\n{t('jk.appear')}",
        bot,
        mention=user_id,
    )
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def handle_jk_button(
    user_id: str,
    choice: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """挑战/不挑战 按钮回调。"""
    async def _send(msg) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)

    async def _finish(msg) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)
        raise FinishedException

    try:
        t = data_loader.get_text
        if choice == "挑战":
            jk_pending[user_id] = True
            await _send(
                md_message(
                    f"\n{t('jk.challenge_pending')}",
                    bot,
                    mention=user_id,
                )
            )
            # 重入今日冒险流程：jk_pending 旗标 → 强制怪物 48
            from .adventure import _run_adventure

            await _run_adventure(user_id, bot, _send, _finish)
        elif choice == "不挑战":
            # 跳过今日冒险：day+1（day40 冻结）+ 日常结算管线（对齐事件跳过战斗）
            from .adventure import _mark_adventure_done
            from ..services.ending_engine import check_daily

            inv_model = investigator_repo.find_by_qq(user_id)
            if inv_model is None:
                await _send(need_create_message(bot, mention=user_id))
                return
            inv = Investigator(inv_model)
            if inv.day < 40:
                inv.day += 1
            inv.is_adventure = False
            inv.save()
            _mark_adventure_done(user_id)
            check_daily(inv)
            await _send(
                md_message(
                    f"\n{t('jk.skip_done')}",
                    bot,
                    mention=user_id,
                )
            )
    except FinishedException:
        pass


register_button_handler("jk", handle_jk_button)
