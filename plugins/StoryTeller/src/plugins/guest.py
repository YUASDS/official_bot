"""乱入遭遇系统：空间裂缝 → 异世界小剧场（多阶段状态机）。

每日冒险开局（/今日冒险）以约 3% 概率（每日骰 d100 ≤ 3）且 5 ≤ day < 40 触发
「空间裂缝」：玩家被卷入一个异世界小剧场（剑与魔法 / D&D 地牢 / 番剧高校，三选一
随机抽取），经历 3~4 个风格迥异的阶段（事件选择 + 检定 + 可能的小战斗），最后穿过
裂缝回到庄园——像做了一场梦，但完整通关可能带回一件小纪念品（310 异界纪念品）。

状态机（仿 gm_room V2 按钮状态机）：
- phase "stage"：当前阶段（世界标题 + 阶段描述 + 自动检定 + 选项按钮，
  kind "guest_stage"）
- phase "battle"：阶段内小战斗（复用 BattleService，kind "guest_battle"）
- 选项「下一步」：阶段 id（推进） / "战斗"（进入本阶段战斗） / "结束"（归途）
- 战斗胜利 → 应用胜利效果并推进；战斗失败 → 直接归途（空手，无纪念品）

隔离设计：
- 战斗复用现有引擎但置 `is_gm_room=True`：战败不落 is_survive、不登记 E07、SAN 永不
  归零（不触发 E06）；胜利不掉落/day+1/信物/门扉/周目行为——奖励全由本状态机按
  guest_data.json 配置发放（含乌帕/HP/SAN/505 消耗品，不含 400~508 信物）。
- 触发即替代当日冒险（_mark_adventure_done），归途统一 day+1（<40 冻结）。
- 每日彩蛋互斥链 qiren → guest → npc → gm_room：一天至多一个特殊事件。
- 纪念品 310「异界纪念品」为 misc 收藏物（不可装备），仅完整通关且未持有时发放。

触发骰为「确定性每日掷骰」：按 (qq, 周目, day) 播种，每个用户每天固定一个骰值——
防连点重掷/测试回归，约 3% 稀有触发（比 GM 房间 1% 略高的彩蛋）。
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

import ujson
from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.exception import FinishedException
from nonebot.params import CommandArg

from ..models.monster import Monster
from ..models.player import Investigator, ending_repo, investigator_repo
from ..services.battle import BattleService
from ..services.battle_cards import battle_round_html
from ..services.data_loader import data_loader
from ..services.dice_roller import get_success_icon, roll_dice
from ..services.event_service import apply_event_effects
from ..utils.active_battles import battle_manager
from ..utils.buttons import _send_to_user, register_button_handler
from ..utils.image_sender import render_pic, send_pic
from ..utils.md_format import (
    build_keyboard,
    md_message,
    need_create_message,
    report_section,
)

# 触发配置
_GUEST_MIN_DAY = 5           # day >= 5（新手期不触发）
_GUEST_MAX_DAY = 40          # day40 门扉冻结，不参与
_GUEST_TRIGGER_VALUE = 3     # 每日骰 d100 ≤ 3 触发（≈3%）
_SOUVENIR_ID = "310"         # 异界纪念品（收藏性质，非信物）

# 乱入状态机：user_id -> 状态字典
# {
#   "world_id": ..., "stage_id": ..., "phase": "stage"|"battle",
#   "entered": [已进入阶段 id], "buff": {临时技能修正}, "battle": BattleService|None,
#   "completed": bool,
# }
guest_active: dict[str, dict] = {}
# 每日互斥守卫：user_id -> 已触发乱入的当日 day（一天最多一次）
_guest_met_day: dict[str, int] = {}

# 世界主题数据（data/guest_data.json）：与 data_loader 解耦独立加载（缓存）
_GUEST_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "guest_data.json"
_guest_cache: Optional[dict] = None


def guest_registry() -> dict:
    """乱入世界主题注册表（guest_data.json）。"""
    global _guest_cache
    if _guest_cache is None:
        try:
            with open(_GUEST_DATA_PATH, encoding="utf-8-sig") as f:
                _guest_cache = ujson.load(f)
        except Exception:  # noqa: BLE001 - 数据缺失/损坏不影响主流程
            _guest_cache = {}
    return _guest_cache or {}


def guest_unlocked(inv: Investigator) -> bool:
    """触发条件门：5 ≤ day < 40（新手期 / 门扉冻结不触发）。"""
    return _GUEST_MIN_DAY <= inv.day < _GUEST_MAX_DAY


def _guest_daily_roll(inv: Investigator) -> int:
    """确定性每日乱入掷骰：按 (qq, 周目, day) 播种返回 1~100。

    每用户每天固定一个骰值：防连点重掷刷触发，保证既有测试（238 用例）确定性回归。
    周目号参与播种 → 每周目触发日重新洗牌，老玩家每周目都有机会遇见裂缝。
    """
    collection = ending_repo.get_collection(inv.qq)
    run = int(collection.total_runs) if collection else 0
    rng = random.Random(f"guest|{inv.qq}|{run}|{inv.day}")
    return rng.randint(1, 100)


def _world_available(inv: Investigator, world: dict) -> bool:
    """世界解锁条件求值（复用事件条件求值器，支持 日/物品/SAN 等）。"""
    from ..services.ending_engine import eval_option_condition

    cond = world.get("解锁")
    if not cond:
        return True
    return eval_option_condition(cond, inv)


def guest_should_trigger(inv: Investigator) -> Optional[str]:
    """乱入触发：解锁 + 每日骰 ≤3 → 随机抽一个已解锁世界 id（无则 None）。"""
    if not guest_unlocked(inv):
        return None
    if _guest_met_day.get(inv.qq) == inv.day:
        return None
    if guest_active.get(inv.qq):
        return None
    if _guest_daily_roll(inv) > _GUEST_TRIGGER_VALUE:
        return None
    worlds = [
        wid for wid, w in guest_registry().items() if _world_available(inv, w)
    ]
    if not worlds:
        return None
    return random.choice(worlds)


def _world(world_id: str) -> dict:
    return guest_registry().get(world_id) or {}


def _stage(world_id: str, stage_id: str) -> dict:
    for s in _world(world_id).get("阶段") or []:
        if s.get("id") == stage_id:
            return s
    return {}


def _guest_mark_done(user_id: str) -> None:
    """懒加载冒险完成记录（避免循环导入）。"""
    from .adventure import _mark_adventure_done

    _mark_adventure_done(user_id)


def _guest_stage_rows(world_id: str, stage: dict) -> list[list[tuple[str, str]]]:
    """阶段选项按钮行（每行 3 个，对齐事件按钮），payload 携带世界/阶段/选项。"""
    options = stage.get("选项") or []
    return [
        [
            (
                o["输入"],
                f"guest_stage:{world_id}|{stage.get('id')}|{o['输入']}",
            )
            for o in options[i : i + 3]
        ]
        for i in range(0, len(options), 3)
    ]


def _guest_battle_keyboard(service: BattleService):
    """乱入战斗按钮：guest_battle 回调（携带回合令牌防旧按钮）。"""
    actions = service.get_available_actions_for_turn()
    if not actions:
        return None
    token = service.get_turn_token()
    rows = [[(a, f"guest_battle:{a}:{token}") for a in actions[:4]]]
    if len(actions) > 4:
        rows.append([(a, f"guest_battle:{a}:{token}") for a in actions[4:]])
    return build_keyboard(rows)


def _guest_apply_effects(inv: Investigator, user_id: str, effects: dict) -> str:
    """应用乱入效果：标准键（san/hp/金币/物品/技能）走 apply_event_effects，返回摘要。"""
    standard = {
        k: v
        for k, v in effects.items()
        if k in ("san", "hp", "金币", "物品", "技能")
    }
    return apply_event_effects(inv, user_id, standard) if standard else ""


def _guest_check_block(user_id: str, state: dict, check: dict) -> str:
    """阶段自动检定：掷 1d100 vs 技能，应用成功/失败效果，返回「检定行 + 结果」。"""
    skill = check.get("技能", "意志")
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        return ""
    inv = Investigator(inv_model)
    skill_val = inv.get_skill(skill, 0)
    _expr, roll = roll_dice("1d100")
    passed = roll <= skill_val
    branch = check.get("成功") if passed else check.get("失败")
    t = data_loader.get_text
    icon = get_success_icon(1 if passed else 0)
    level = t("dice.success") if passed else t("dice.failure")
    lines = [
        t(
            "guest.check_line",
            icon=icon,
            skill=skill,
            roll=roll,
            target=skill_val,
            level=level,
        )
    ]
    if branch:
        reply = branch.get("回复", "")
        summary = _guest_apply_effects(inv, user_id, branch.get("效果") or {})
        if reply:
            lines.append(reply)
        if summary:
            lines.append(f"> {summary}")
    return "\n\n".join(lines)


async def guest_enter(user_id: str, inv: Investigator, bot: Bot, send, world_id: str) -> None:
    """进入乱入小剧场：标记当日完成 → 置状态 → 发入场 + 第一幕。

    触发即替代当日冒险；归途后 day+1。发送后抛 FinishedException 中断今日冒险流程。
    """
    if user_id in guest_active:
        await send(
            md_message(
                f"\n{data_loader.get_text('guest.no_active')}", bot, mention=user_id
            )
        )
        raise FinishedException()
    _guest_mark_done(user_id)
    _guest_met_day[user_id] = inv.day
    world = _world(world_id)
    if not world:
        return
    stages = world.get("阶段") or []
    if not stages:
        return
    guest_active[user_id] = {
        "world_id": world_id,
        "stage_id": stages[0].get("id", ""),
        "phase": "stage",
        "entered": [],
        "buff": {},
        "battle": None,
        "completed": False,
    }
    inv.set_flag(f"guest.{world_id}.visited", True)
    inv.save()
    t = data_loader.get_text
    msg = md_message(
        f"\n**{t('guest.enter_title')}**\n\n{world.get('入场', '')}",
        bot,
        mention=user_id,
    )
    await send(msg)
    await _guest_send_stage(user_id, guest_active[user_id], bot, send)
    raise FinishedException()


async def _guest_send_stage(user_id: str, state: dict, bot: Bot, send) -> None:
    """渲染当前阶段：世界标题 + 阶段描述 + 自动检定。

    纯战斗阶段（有「战斗」无「选项」）先发阶段描述再直接开战；
    普通阶段发描述 + 选项按钮。
    """
    world_id = state["world_id"]
    world = _world(world_id)
    stage = _stage(world_id, state["stage_id"])
    if not stage:
        await _guest_ending(user_id, state, bot, send, completed=True)
        return
    lines = []
    if stage.get("标题"):
        lines.append(f"**{world.get('标题')} · {stage['标题']}**")
    if stage.get("描述"):
        lines.append(stage["描述"])
    if stage.get("检定") and state["stage_id"] not in state["entered"]:
        check_line = _guest_check_block(user_id, state, stage["检定"])
        if check_line:
            lines.append(check_line)
    state["entered"].append(state["stage_id"])
    options = stage.get("选项") or []
    if stage.get("战斗") and not options:
        # 纯战斗阶段：进入即开战（奖励与推进由战斗配置控制）
        msg = md_message("\n\n".join(x for x in lines if x), bot, mention=user_id)
        await send(msg)
        await _guest_start_battle(user_id, state, bot, send)
        return
    kb = build_keyboard(_guest_stage_rows(world_id, stage))
    lines.append(f"**{data_loader.get_text('guest.option_title')}**")
    msg = md_message("\n\n".join(x for x in lines if x), bot, mention=user_id)
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def _guest_handle_stage_choice(
    user_id: str, state: dict, choice: str, bot: Bot, send
) -> None:
    """阶段选项：应用效果 → 按「下一步」推进（下一幕 / 战斗 / 结束）。

    按钮回调携带 world|stage|option（校验防旧按钮）；命令兜底仅传选项文本。
    """
    option_input = choice
    if "|" in choice:
        parts = choice.split("|", 2)
        if len(parts) < 3:
            return
        world_id, stage_id, option_input = parts[0], parts[1], parts[2]
        if world_id != state["world_id"] or stage_id != state["stage_id"]:
            return  # 旧按钮 / 已过期阶段
    stage = _stage(state["world_id"], state["stage_id"])
    option = next(
        (o for o in stage.get("选项") or [] if o.get("输入") == option_input),
        None,
    )
    if option is None:
        await send(
            md_message(
                f"\n{data_loader.get_text('guest.invalid_choice')}", bot, mention=user_id
            )
        )
        return
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        await send(need_create_message(bot, mention=user_id))
        return
    inv = Investigator(inv_model)
    effects = option.get("效果") or {}
    summary = _guest_apply_effects(inv, user_id, effects)
    buff = effects.get("临时")
    if isinstance(buff, dict):
        for k, v in buff.items():
            state["buff"][k] = state["buff"].get(k, 0) + v
    inv.save()

    reply = option.get("回复", "")
    if summary:
        reply = (
            f"{reply}\n\n"
            f"**{data_loader.get_text('adventure.event_effect_title')}**\n> {summary}"
        )
    await send(md_message(f"\n{reply}", bot, mention=user_id))

    next_step = option.get("下一步", "结束")
    if next_step == "战斗":
        await _guest_start_battle(user_id, state, bot, send)
    elif next_step == "结束":
        state["completed"] = True
        await _guest_ending(user_id, state, bot, send, completed=True)
    else:
        state["stage_id"] = next_step
        await _guest_send_stage(user_id, state, bot, send)


async def _guest_start_battle(user_id: str, state: dict, bot: Bot, send) -> None:
    """进入阶段战斗：复用 BattleService + is_gm_room 隔离，应用已积攒的临时 buff。"""
    world_id = state["world_id"]
    world = _world(world_id)
    stage = _stage(world_id, state["stage_id"])
    battle_cfg = stage.get("战斗") or {}
    monster_id = str(battle_cfg.get("怪物", "45"))
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        await send(need_create_message(bot, mention=user_id))
        return
    inv = Investigator(inv_model)
    service = BattleService(inv, Monster(monster_id))
    service.is_gm_room = True  # 战败不登记 E07 / 胜利不走掉落与门扉（隔离红线）
    service.set_guest_texts(world.get("玩家文案") or {})  # 世界主题玩家战斗文案覆盖
    if state.get("buff"):
        service.set_environment({"玩家": dict(state["buff"])})
    state["battle"] = service
    state["phase"] = "battle"
    service.roll_initiative()

    t = data_loader.get_text
    intro = battle_cfg.get("开场", "")
    if not intro:
        intro = getattr(service.monster, "出场", "") or service.monster.名字
    lines = [
        f"**{t('guest.battle_title')} · {world.get('标题')}**",
        intro,
        report_section(t("battle.monster_intro_title")),
        service.monster.名字,
        service.get_dex_compare_section(),
        service.get_status_table(),
        service.get_action_section(),
    ]
    msg = md_message("\n\n".join(x for x in lines if x), bot, mention=user_id)
    kb = _guest_battle_keyboard(service)
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def _guest_battle_input(
    user_id: str, state: dict, choice: str, bot: Bot, send, token: int | None = None
) -> None:
    """乱入战斗行动：执行动作 → 回合卡片（图片优先，md 回退，附行动按钮）→ 战斗结束分支。

    战斗结束时先补发最后一轮战报（result[:-1]，战报卡片优先/md 回退，不附行动按钮），
    再走 _guest_battle_end 发送胜利/战败收尾（对齐 GM 房间 _gm_send_round 结构）。
    """
    service = state.get("battle")
    if not service or service.fight_is_over():
        await send(
            md_message(
                f"\n{data_loader.get_text('guest.no_active')}", bot, mention=user_id
            )
        )
        return
    if token is not None and token != service.get_turn_token():
        return  # 旧按钮（令牌不匹配）
    if choice == "逃跑":
        await send(
            md_message(
                f"\n{data_loader.get_text('guest.no_exit')}", bot, mention=user_id
            )
        )
        return
    result = service.execute_action(choice)
    if service.fight_is_over():
        # 1. 最后一轮检定/交锋战报（不含结束文本；逃跑等单段结果无战报则跳过）
        if any(x for x in result[:-1]):
            img = await render_pic(battle_round_html(service, result))
            if img is None or not await send_pic(bot, img, send):
                combat_text = "\n" + "\n\n".join(str(x) for x in result[:-1] if x)
                await send(md_message(combat_text, bot, mention=user_id))
        # 2. 结束分支（胜利回复/战败归途，不附行动按钮）
        await _guest_battle_end(user_id, state, bot, send)
        return
    # 普通回合：单段信息结果（弹药不足/未知行动等）卡片无正文，直接发文本，避免空白战报
    if not any(x for x in result[:-1]):
        response = "\n" + "\n\n".join(str(x) for x in result if x)
        msg = md_message(response, bot, mention=user_id)
        kb = _guest_battle_keyboard(service)
        if kb is not None and not isinstance(msg, str):
            msg.append(kb)
        await send(msg)
        return
    img = await render_pic(battle_round_html(service, result))
    if img is not None and await send_pic(bot, img, send):
        text = service.get_action_section()
    else:
        text = "\n" + "\n\n".join(str(x) for x in result if x)
    msg = md_message(text, bot, mention=user_id)
    kb = _guest_battle_keyboard(service)
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def _guest_battle_end(user_id: str, state: dict, bot: Bot, send) -> None:
    """战斗结束：胜利 → 应用胜利效果并推进/归途；失败 → 直接归途（空手，无纪念品）。"""
    world_id = state["world_id"]
    stage = _stage(world_id, state["stage_id"])
    battle_cfg = stage.get("战斗") or {}
    service = state["battle"]
    t = data_loader.get_text
    if service.hp_record["mon"] <= 0:
        win = battle_cfg.get("胜利") or {}
        inv_model = investigator_repo.find_by_qq(user_id)
        inv = Investigator(inv_model) if inv_model else None
        reply = win.get("回复", "")
        if inv is not None:
            summary = _guest_apply_effects(inv, user_id, win.get("效果") or {})
            inv.save()
            if summary:
                reply = (
                    f"{reply}\n\n"
                    f"**{t('adventure.event_effect_title')}**\n> {summary}"
                )
        await send(md_message(f"\n{reply}", bot, mention=user_id))
        next_step = win.get("下一步", "结束")
        if next_step == "结束" or not next_step:
            state["completed"] = True
            await _guest_ending(user_id, state, bot, send, completed=True)
        else:
            state["stage_id"] = next_step
            state["phase"] = "stage"
            state["battle"] = None
            await _guest_send_stage(user_id, state, bot, send)
    else:
        lose = battle_cfg.get("战败") or {}
        reply = lose.get("回复", t("guest.defeat_title"))
        await send(
            md_message(
                f"\n**{t('guest.defeat_title')}**\n\n{reply}",
                bot,
                mention=user_id,
            )
        )
        await _guest_ending(user_id, state, bot, send, completed=False)


def _guest_grant_souvenir(user_id: str) -> str:
    """发放纪念品 310「异界纪念品」（仅未持有且完整通关时发放，防重复）。"""
    from ..models.item import Equipment

    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        return ""
    equipments, _ = Investigator(inv_model).get_equipments()
    if equipments.get(_SOUVENIR_ID, 0) > 0:
        return ""
    investigator_repo.add_item_to_inventory(inv_model, _SOUVENIR_ID, 1)
    return Equipment(_SOUVENIR_ID).name


async def _guest_ending(
    user_id: str, state: dict, bot: Bot, send, completed: bool
) -> None:
    """归途：穿过裂缝回到庄园；完整通关可能带回纪念品（310），随后统一收尾 day+1。"""
    world_id = state["world_id"]
    world = _world(world_id)
    ending = world.get("结尾") or {}
    t = data_loader.get_text
    lines = [
        f"**{t('guest.end_title')} · {world.get('名字', '')}**",
        ending.get("门", ""),
    ]
    if completed:
        souvenir = _guest_grant_souvenir(user_id)
        if souvenir:
            lines.append(t("guest.souvenir_gain", name=souvenir))
    msg = md_message("\n\n".join(x for x in lines if x), bot, mention=user_id)
    await send(msg)
    if completed and ending.get("纪念品文案"):
        await send(
            md_message(f"\n{ending['纪念品文案']}", bot, mention=user_id)
        )
    await _finish_guest(user_id, bot, send)


async def _finish_guest(user_id: str, bot: Bot, send) -> None:
    """乱入统一收尾：解除冒险态、day+1（<40 冻结）、清状态、发送归途文本。"""
    t = data_loader.get_text
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is not None:
        inv = Investigator(inv_model)
        inv.is_adventure = False
        if inv.day < 40:
            inv.day += 1
        inv.save()
    battle_manager.remove_battle(user_id)
    guest_active.pop(user_id, None)
    await send(
        md_message(
            f"\n{t('guest.exit')}\n\n{t('guest.day_passed')}",
            bot,
            mention=user_id,
        )
    )


# --- 统一输入分发：按钮回调 与 /行动 命令兜底共用 ---
async def _guest_handle_input(
    user_id: str, choice: str, bot: Bot, send, token: int | None = None
) -> None:
    state = guest_active.get(user_id)
    if not state:
        await send(
            md_message(
                f"\n{data_loader.get_text('guest.no_active')}", bot, mention=user_id
            )
        )
        return
    if state["phase"] == "stage":
        await _guest_handle_stage_choice(user_id, state, choice, bot, send)
    elif state["phase"] == "battle":
        await _guest_battle_input(user_id, state, choice, bot, send, token)
    else:
        await send(
            md_message(
                f"\n{data_loader.get_text('guest.no_active')}", bot, mention=user_id
            )
        )


async def _guest_button_handler(
    user_id: str,
    payload: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """乱入全部按钮回调（guest_stage / guest_battle 共用）。"""
    async def _send(msg) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)

    try:
        await _guest_handle_input(user_id, payload, bot, _send, token)
    except FinishedException:
        pass


def _guest_active_rule(event: Event) -> bool:
    """/行动 兜底命令规则：仅当玩家处于乱入状态时拦截（其余走原战斗命令）。"""
    try:
        return event.get_user_id() in guest_active
    except Exception:
        return False


guest_cmd = on_command("行动", rule=_guest_active_rule, priority=3, block=True)


@guest_cmd.handle()
async def handle_guest_command(event: Event, bot: Bot, msg: Message = CommandArg()) -> None:
    try:
        await _guest_handle_input(
            event.get_user_id(), msg.extract_plain_text().strip(), bot, guest_cmd.send
        )
    except FinishedException:
        pass


# --- 按钮回调注册 ---
for _kind in ("guest_stage", "guest_battle"):
    register_button_handler(_kind, _guest_button_handler)
