"""GM 房间彩蛋 V2：flow 节点机（薄壳触发层）。

每日冒险（/今日冒险）1% 概率（d100 ≤ 1）且 10 ≤ day < 40 触发「空间扭曲」：
玩家被拉入 GM 的房间，与神秘管理员完成两轮对话，随后对抗三选一的高危守卫
（哈斯塔 40 / 奈亚拉托提普 41 / 克苏鲁 42，均为 GM 房间专属，已移出每日池）。

P1b 迁移：原 ~775 行状态机已收敛为 `data/worlds/gm_room.json`（流程与奖励全配置化）
+ 本薄壳（触发判定 + 进入衔接）。守卫战、GM 复活/助力、三轮奖励选择、四分支收尾
（一轮胜 / 一轮败→二轮胜 / 二轮败 / 三拒特殊 CG）全部由 flow_engine 节点链驱动。

薄壳仅保留：触发门槛（gm_room_unlocked / gm_room_should_trigger）、进入入口
（gm_room_enter → start_flow + render_flow_node）、梦之碎片命令（保留现状一字不改）。
旧按钮 payload 直接下线：残留按钮点击走 flow 未知输入守卫，玩家重新触发即可。
"""

from __future__ import annotations

from loguru import logger
from typing import Optional

from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.exception import FinishedException

from ..models.player import Investigator, ending_repo, investigator_repo
from ..services.data_loader import data_loader
from ..services.dice_roller import roll_dice
from ..services.ending_engine import (
    pending_door_choice,
    render_door_choice,
    reroll_door_choice,
)
from ..services.flow_engine import flow_states, render_flow_node, start_flow
from ..utils.active_battles import battle_manager
from ..utils.md_format import build_keyboard, md_message, need_create_message


# 触发配置：读 reply_data.json `triggers` 段（id "gm_room"），缺省回退现状 10/39/d100/1（零行为）
def _gm_trigger() -> dict:
    return data_loader.get_trigger("gm_room")


# 当次冒险「梦醒前的余韵」旗标：V2 不再使用，保留定义以兼容旧引用（adventure 导入）
gm_afterglow: dict[str, bool] = {}

# 当日守卫（game-day）：user_id -> 已进入 GM 房间的当日 day。
# 内存 dict 为快路径；持久化镜像写 inv.flags `gm_room.met_day`，读时双读。
_gm_met_day: dict[str, int] = {}


def _gm_met_flag(inv: Investigator) -> Optional[int]:
    """读当日守卫持久化镜像：flags `gm_room.met_day`（重启后仍生效）。"""
    try:
        return int(inv.get_flag("gm_room.met_day"))
    except (TypeError, ValueError) as e:
        logger.warning(f"静默异常[TypeError/ValueError] in _gm_met_flag: {e}")
        return None


def gm_room_unlocked(inv: Investigator) -> bool:
    """触发条件门：10 ≤ day < 40（新手期 / 门扉冻结不触发；day_min/day_max 读配置）。"""
    t = _gm_trigger()
    day_min = int(t.get("day_min", 10))
    day_max = int(t.get("day_max", 39))
    return day_min <= inv.day <= day_max


def gm_room_should_trigger(inv: Investigator) -> bool:
    """每日冒险开局概率触发：解锁后掷 d100 ≤ 1 则 True（骰/触发值读配置）。

    当日守卫（game-day）：已进入过 GM 房间的同一天不再触发（内存 + flags 双读，
    重启后 `gm_room.met_day` 仍生效，防同 game-day 二次进入）。
    """
    if not gm_room_unlocked(inv):
        return False
    if _gm_met_day.get(inv.qq) == inv.day:
        return False
    if _gm_met_flag(inv) == inv.day:
        return False
    t = _gm_trigger()
    dice = t.get("dice", "d100")
    trigger_value = int(t.get("value", 1))
    _expr, val = roll_dice(dice)
    return val <= trigger_value


def _gm_mark_done(user_id: str) -> None:
    """记录冒险完成（今日状态标记，services 层，避免插件互相导入）。"""
    from ..services.daily_service import _mark_adventure_done

    _mark_adventure_done(user_id)


async def gm_room_enter(user_id: str, inv: Investigator, bot: Bot, send) -> None:
    """进入 GM 房间（V2·flow 薄壳）：标记当日完成与会面守卫 → 启动 flow 节点机。

    触发即替代当日冒险；房间结束由 flow 收尾（restore_hp + skip_daily）承担。
    发送首节点后抛 FinishedException，中断 adventure._run_adventure 的后续流程。
    """
    if flow_states.get(user_id):
        return
    _gm_mark_done(user_id)
    _gm_met_day[user_id] = inv.day
    inv.set_flag("gm_room.met_day", inv.day)  # 当日守卫持久化：重启后同 game-day 不二次进入
    inv.save()
    state = start_flow(user_id, "gm_room")
    if state is None:
        await send(
            md_message(
                f"\n{data_loader.get_text('flow.unknown')}", bot, mention=user_id
            )
        )
        return
    await render_flow_node(user_id, state, bot, send)
    raise FinishedException()


# ============ 梦之碎片：用途与存储（保留现状，一字不改） ============
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
        from ..services.combat_messaging import send_combat_result

        await send_combat_result(
            battle, bot, (buff_text, battle._end_turn()), send=send
        )
        return

    # 用途 B：第 40 天门扉抉择判定后重掷（撤销判定 → 重新展示门扉 → 再次判定）
    from ..services.daily_service import door_states

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