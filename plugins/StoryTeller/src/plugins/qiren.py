"""隐藏挑战「启」（怪物 38）触发与选择。

每日冒险开局（环境/选怪之前）概率掷骰触发对话（挑战/不挑战按钮）：
- 挑战 → 置位 `qiren_pending`，重入今日冒险强制怪物 38 + 环境「庄园.黑色满月」；
- 不挑战 → 跳过今日冒险：day+1 并走日常结算管线。

触发条件门独立为 `qiren_unlocked(inv)`，概率配置为 d20/触发值 1。
"""

from __future__ import annotations

from nonebot.adapters import Bot
from nonebot.exception import FinishedException

from ..models.player import Investigator, investigator_repo
from ..services.data_loader import data_loader
from ..services.dice_roller import roll_dice
from ..utils.buttons import _send_to_user, register_button_handler
from ..utils.md_format import build_keyboard, md_message, need_create_message

# 触发配置
_QIREN_TRIGGER_ITEMS = ("501", "508")  # 持有任一即可解锁（他掉落的信物会让他再度现身）
_QIREN_MIN_DAY = 20                    # 中后期开放
_QIREN_DICE = "d20"                    # 概率骰
_QIREN_TRIGGER_VALUE = 1               # 掷出触发值才触发

# 待挑战旗标：user_id -> True（点「挑战」后置位，下一次 /今日冒险 强制怪物 38）
qiren_pending: dict[str, bool] = {}


def qiren_unlocked(inv: Investigator) -> bool:
    """触发条件门（独立函数）：day>=20 且 day<40 且持有 501/508 任一。

    day40 门扉归守门人，启不参与（结局线由守门人战斗接管）。
    """
    if inv.day < _QIREN_MIN_DAY or inv.day >= 40:
        return False
    equipments, _ = inv.get_equipments()
    return any(equipments.get(iid, 0) > 0 for iid in _QIREN_TRIGGER_ITEMS)


def qiren_should_trigger(inv: Investigator) -> bool:
    """每日冒险开局概率触发：解锁后掷 d20 出触发值则 True。"""
    if not qiren_unlocked(inv):
        return False
    _expr, val = roll_dice(_QIREN_DICE)
    return val == _QIREN_TRIGGER_VALUE


async def qiren_send_dialogue(user_id: str, bot: Bot, send) -> None:
    """发送启的现身台词 + 「挑战 / 不挑战」按钮（等待玩家选择）。"""
    t = data_loader.get_text
    kb = build_keyboard(
        [
            [
                (t("qiren.challenge"), "qiren:挑战"),
                (t("qiren.decline"), "qiren:不挑战"),
            ]
        ]
    )
    msg = md_message(
        f"\n{t('qiren.title')}\n\n{t('qiren.appear')}",
        bot,
        mention=user_id,
    )
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def handle_qiren_button(
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
            qiren_pending[user_id] = True
            await _send(
                md_message(
                    f"\n{t('qiren.challenge_pending')}",
                    bot,
                    mention=user_id,
                )
            )
            # 重入今日冒险流程：qiren_pending 旗标 → 强制怪物 38 + 环境
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
                    f"\n{t('qiren.skip_done')}",
                    bot,
                    mention=user_id,
                )
            )
    except FinishedException:
        pass


register_button_handler("qiren", handle_qiren_button)
