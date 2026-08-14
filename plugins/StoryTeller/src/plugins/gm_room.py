"""GM 房间彩蛋：梦之碎片（账号级纪念道具）。

每日冒险（/今日冒险）1% 概率（d100 ≤ 1）且 day ≥ 10 触发「空间扭曲」：
玩家被拉入一间不属于庄园的 GM 的房间——获得账号级纪念道具「梦之碎片」
（跨周目/重建保留，不进普通背包）+ 当次冒险「梦醒前的余韵」战斗强化
（全技能 +30、伤害翻倍、+25 临时生命）。

梦之碎片（一次性，使用后从账号级存储移除）两种用途：
- 用途 A（战斗强化）：/使用梦之碎片 战斗中激活——全技能 +30、伤害翻倍、+25 临时生命
- 用途 B（门扉重掷）：第 40 天门扉抉择判定后使用——撤销判定，重新展示门扉选项并再次判定

与结局系统隔离：全部效果限定于战斗/冒险过程，不触碰 knowledge/san/信物/进度，
E01~E10 判定规则本身不变。
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.exception import FinishedException

from ..models.player import Investigator, ending_repo, investigator_repo
from ..services.combat_messaging import send_combat_result
from ..services.data_loader import data_loader
from ..services.dice_roller import roll_dice
from ..services.ending_engine import (
    pending_door_choice,
    render_door_choice,
    reroll_door_choice,
)
from ..utils.active_battles import battle_manager
from ..utils.md_format import (
    build_keyboard,
    md_message,
    need_create_message,
    report_quote,
)

# 触发配置
_GM_MIN_DAY = 10          # day >= 10（新手期不触发）
_GM_MAX_DAY = 40          # day40 门扉冻结，不参与
_GM_DICE = "d100"         # 概率骰
_GM_TRIGGER_VALUE = 1     # d100 ≤ 1 触发（1%）

# 当次冒险「梦醒前的余韵」旗标：user_id -> True（由 adventure 在创建战斗时消费注入）
gm_afterglow: dict[str, bool] = {}


def gm_room_unlocked(inv: Investigator) -> bool:
    """触发条件门：10 ≤ day < 40（新手期 / 门扉冻结不触发）。"""
    return _GM_MIN_DAY <= inv.day < _GM_MAX_DAY


def gm_room_should_trigger(inv: Investigator) -> bool:
    """每日冒险开局概率触发：解锁后掷 d100 ≤ 1 则 True。"""
    if not gm_room_unlocked(inv):
        return False
    _expr, val = roll_dice(_GM_DICE)
    return val <= _GM_TRIGGER_VALUE


async def gm_room_enter(user_id: str, inv: Investigator, bot: Bot, send) -> None:
    """进入 GM 的房间：叙事 + 获得「梦之碎片」（账号级，跨周目保留）。

    当次冒险的「梦醒前的余韵」由调用方读取 gm_afterglow 旗标注入 BattleService。
    """
    t = data_loader.get_text
    ending_repo.add_dream_fragment(user_id, 1)
    lines = [
        f"**{t('gm_room.title')}**",
        report_quote(
            [t("gm_room.enter"), t("gm_room.admin"), t("gm_room.fragment_desc")]
        ),
        f"> 获得：🌙 **{t('dream_fragment.name')}**（账号级纪念道具，跨周目保留）",
        report_quote([t("gm_room.afterglow")]),
    ]
    await send(md_message("\n" + "\n\n".join(lines), bot, mention=user_id))


async def _use_dream_fragment(user_id: str, bot: Bot, send, finish) -> None:
    """梦之碎片使用流程（命令与测试共用）。finish 发送后须抛出 FinishedException 中断。"""
    t = data_loader.get_text
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        await finish(need_create_message(bot, mention=user_id))
    if ending_repo.get_dream_fragments(user_id) <= 0:
        await finish(
            md_message(f"\n{t('dream_fragment.no_fragment')}", bot, mention=user_id)
        )
    inv = Investigator(inv_model)

    # 用途 A：战斗中激活战斗强化（全技能 +30、伤害翻倍、+25 临时生命）
    battle = battle_manager.get_battle(user_id)
    if battle and not battle.fight_is_over() and battle.investigator.is_survive:
        ending_repo.remove_dream_fragment(user_id)
        buff_text = battle.apply_dream_buff()
        await send_combat_result(
            battle, bot, (buff_text, battle._end_turn()), send=send
        )
        return

    # 用途 B：第 40 天门扉抉择判定后重掷（撤销判定 → 重新展示门扉 → 再次判定）
    from .adventure import door_states  # 懒加载避免循环导入

    reroll_text = reroll_door_choice(inv)
    if reroll_text:
        ending_repo.remove_dream_fragment(user_id)
        door_render = render_door_choice(inv)
        door_states[user_id] = {"choices": door_render["choices"]}
        choices = door_render["choices"]
        rows = [
            [(c["label"], f"door:{c['key']}") for c in choices[i : i + 3]]
            for i in range(0, len(choices), 3)
        ]
        kb = build_keyboard(rows)
        msg = md_message(
            f"\n{t('dream_fragment.reroll_show')}\n\n{reroll_text}\n\n"
            f"{door_render['text']}",
            bot,
            mention=user_id,
        )
        if kb is not None and not isinstance(msg, str):
            msg.append(kb)
        await finish(msg)
        return

    # 门扉待抉择但尚未判定：没有可重掷的判定
    if pending_door_choice(inv):
        await finish(
            md_message(f"\n{t('dream_fragment.door_no_judge')}", bot, mention=user_id)
        )

    await finish(
        md_message(f"\n{t('dream_fragment.not_usable')}", bot, mention=user_id)
    )


dream_fragment_cmd = on_command(
    "使用梦之碎片",
    aliases={"use_dream_fragment", "梦之碎片"},
    priority=5,
    block=True,
)


@dream_fragment_cmd.handle()
async def handle_use_dream_fragment(event: Event, bot: Bot) -> None:
    try:
        await _use_dream_fragment(
            event.get_user_id(), bot, dream_fragment_cmd.send, dream_fragment_cmd.finish
        )
    except FinishedException:
        pass
