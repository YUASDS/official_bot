"""GM 房间彩蛋 V2：对话 → 试炼 → 奖励 状态机。

每日冒险（/今日冒险）1% 概率（d100 ≤ 1）且 10 ≤ day < 40 触发「空间扭曲」：
玩家被拉入 GM 的房间，与神秘管理员完成两轮对话，随后对抗三选一的高危守卫
（哈斯塔 40 / 奈亚拉托提普 41 / 克苏鲁 42，均为 GM 房间专属，已移出每日池）。

守卫战状态机（本轮战败 → GM 复活 + 临时助力 → 二轮再战同一守卫）：
- 一轮胜   → 护甲 306「管理员风衣」+ 随机属性 +30 + 梦之碎片 → 正常收尾
- 一轮败   → GM 复活（_gm_revive，不走 do_resurrect、不耗 501）
             + 临时助力（全技能 +25 / 伤害 +1d6 / 临时生命 +10，仅当轮有效）→ 二轮
- 二轮胜   → 三轮奖励选择（武器/道具/属性→120，各含「我不想要」）
             - 任一选择  → 正常结束 CG
             - 三次全拒  → 特殊结束 CG（GM 无奈 → 随机属性 +30 + 梦之碎片）
- 二轮败   → 空手退出（不给碎片、不给任何奖励，仅叙事记忆）

隔离设计：房间战斗走 engine.is_gm_room 短路——战败不落 is_survive、不登记 E07、
SAN 永不归零（不触发 E06）；胜利不走掉落/成长/day+1/门扉；奖励不含 400~508 信物，
属性奖励不含 意志（SAN 污染）/克苏鲁神话（E01 知识度污染）。梦之碎片仅胜利出口发放，
两种用途（战斗强化 / 门扉重掷）与账号级存储一字不改。

交互：按钮（build_keyboard 回调）为主，/行动 命令为兜底。
"""

from __future__ import annotations

import random
from typing import Any

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.exception import FinishedException
from nonebot.params import CommandArg

from ..models.item import Equipment
from ..models.monster import Monster
from ..models.player import Investigator, ending_repo, investigator_repo
from ..services.battle import BattleService
from ..services.battle_cards import battle_round_html
from ..services.data_loader import data_loader
from ..services.dice_roller import roll_dice
from ..services.ending_engine import (
    pending_door_choice,
    render_door_choice,
    reroll_door_choice,
)
from ..utils.active_battles import battle_manager
from ..utils.buttons import _send_to_user, register_button_handler
from ..utils.image_sender import render_pic, send_pic
from ..utils.md_format import (
    build_keyboard,
    md_message,
    need_create_message,
    report_section,
)

# 触发配置（保留现状）
_GM_MIN_DAY = 10          # day >= 10（新手期不触发）
_GM_MAX_DAY = 40          # day40 门扉冻结，不参与
_GM_DICE = "d100"         # 概率骰
_GM_TRIGGER_VALUE = 1     # d100 ≤ 1 触发（1%）

# 守卫池（GM 房间专属，三选一随机抽取）
_GUARD_IDS = ["40", "41", "42"]

# GM 助力（二轮）：按 80% 胜率目标实测校准（balance sim）
_GM_BOOST_SKILL = 25      # 全技能 +25
_GM_BOOST_DAMAGE = "1d6"  # 伤害 +1d6
_GM_BOOST_TEMP_HP = 10    # 临时生命 +10

# 随机 +30 属性池：排除 幸运/教育/智力（用户拍板）→ 排除 意志（SAN 上限连锁，
# 污染 E02/E03/E06 判定）→ 排除 克苏鲁神话（E01 知识度成分）。保留 力量/体质/
# 敏捷/体型/外貌（无任何 E01~E10 判定算子读取，安全且战斗/彩蛋价值明确）。
_RANDOM_ATTR_POOL = ["力量", "体质", "敏捷", "体型", "外貌"]

# 属性 →120 池（第三轮选择）：可突破上限（set_skill 无硬性上限）；排除 意志/体型
# （SAN 连锁）/ 克苏鲁神话（E01 知识度）。
_STAT_120_POOL = ["力量", "体质", "敏捷", "智力"]

# 三轮选择选项（每轮 = 1 件管理员系列独有物品 + 2 件现有稀有物品）
_DLG1_OPTIONS = [
    {"key": "who", "label": "「你是谁？」"},
    {"key": "where", "label": "「这是什么地方？」"},
    {"key": "silent", "label": "沉默"},
]
_DLG2_OPTIONS = [
    {"key": "ready", "label": "「来吧。」"},
    {"key": "hesitate", "label": "「……我要是不想玩呢？」"},
]
_WEAPON_CHOICES = [
    ("307", "近战·管理员的长柄匙"),
    ("9", "步枪·12号泵动式霰弹枪"),
    ("15", "手枪·沙漠之鹰"),
]
_ITEM_CHOICES = [
    ("308", "管理员·骰子袋"),
    ("703", "肉体守护残卷"),
    ("702", "愈合术残卷"),
]

# 对话/选择阶段（命令兜底只在这些阶段拦截 /行动）

# 当次冒险「梦醒前的余韵」旗标：V2 不再使用，保留定义以兼容旧引用（adventure 导入）
gm_afterglow: dict[str, bool] = {}

# GM 房间状态机：user_id -> 状态字典
# {
#   "phase": "dlg1"|"dlg2"|"battle"|"choice_weapon"|"choice_item"|"choice_stat"|"done",
#   "round": 1|2, "revive_used": bool, "guard_id": "40"|"41"|"42",
#   "declined": list[str], "received": list[str], "battle": BattleService|None,
# }
gm_room_active: dict[str, dict] = {}


def gm_room_unlocked(inv: Investigator) -> bool:
    """触发条件门：10 ≤ day < 40（新手期 / 门扉冻结不触发）。"""
    return _GM_MIN_DAY <= inv.day < _GM_MAX_DAY


def gm_room_should_trigger(inv: Investigator) -> bool:
    """每日冒险开局概率触发：解锁后掷 d100 ≤ 1 则 True。"""
    if not gm_room_unlocked(inv):
        return False
    _expr, val = roll_dice(_GM_DICE)
    return val <= _GM_TRIGGER_VALUE


def _gm_mark_done(user_id: str) -> None:
    """懒加载冒险完成记录（避免循环导入）。"""
    from .adventure import _mark_adventure_done

    _mark_adventure_done(user_id)


def _gm_battle_keyboard(service: BattleService):
    """GM 房间战斗按钮：gm_battle 回调（携带回合令牌防旧按钮）。"""
    actions = service.get_available_actions_for_turn()
    if not actions:
        return None
    token = service.get_turn_token()
    rows = [[(a, f"gm_battle:{a}:{token}") for a in actions[:4]]]
    if len(actions) > 4:
        rows.append([(a, f"gm_battle:{a}:{token}") for a in actions[4:]])
    return build_keyboard(rows)


def _gm_strip(text: str) -> str:
    """命令兜底输入归一：去「」与常见标点/空白。"""
    return text.strip().strip("「」?？。…．,， ") or text.strip()


def _gm_match_key(candidates, choice: str):
    """按钮回调（key）与命令兜底（key/去引标签/「流派·名称」任意段）通用解析。

    返回匹配 key 或 None。
    """
    choice = _gm_strip(choice)
    for key, label in candidates:
        if choice == _gm_strip(key) or choice == _gm_strip(label):
            return key
        for seg in str(label).split("·"):
            if seg and choice == _gm_strip(seg):
                return key
    return None


async def gm_room_enter(user_id: str, inv: Investigator, bot: Bot, send) -> None:
    """进入 GM 房间（V2）：标记当日完成 → 置状态 → 发第一轮对话按钮。

    触发即替代当日冒险；房间结束后 day+1。发送对话后抛 FinishedException，
    中断 adventure._run_adventure 的后续选怪/环境/开战流程。
    """
    if user_id in gm_room_active:
        return
    _gm_mark_done(user_id)
    gm_room_active[user_id] = {
        "phase": "dlg1",
        "round": 1,
        "revive_used": False,
        "guard_id": None,
        "declined": [],
        "received": [],
        "battle": None,
    }
    t = data_loader.get_text
    kb = build_keyboard([[(o["label"], f"gm_dlg1:{o['key']}") for o in _DLG1_OPTIONS]])
    msg = md_message(
        f"\n**{t('gm_room_v2.title')}**\n\n{t('gm_room_v2.enter')}\n\n"
        f"{t('gm_room_v2.dlg1_text')}",
        bot,
        mention=user_id,
    )
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)
    raise FinishedException()


async def _gm_advance_dlg1(user_id: str, state: dict, choice: str, bot: Bot, send) -> None:
    t = data_loader.get_text
    key = _gm_match_key([(o["key"], o["label"]) for o in _DLG1_OPTIONS], choice)
    if not key:
        await send(
            md_message(f"\n{t('gm_room_v2.invalid_choice')}", bot, mention=user_id)
        )
        return
    state["phase"] = "dlg2"
    reply = t(f"gm_room_v2.dlg1_reply_{key}")
    kb = build_keyboard(
        [[(o["label"], f"gm_dlg2:{o['key']}") for o in _DLG2_OPTIONS]]
    )
    msg = md_message(f"\n{reply}\n\n{t('gm_room_v2.dlg2_text')}", bot, mention=user_id)
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def _gm_advance_dlg2(user_id: str, state: dict, choice: str, bot: Bot, send) -> None:
    t = data_loader.get_text
    key = _gm_match_key([(o["key"], o["label"]) for o in _DLG2_OPTIONS], choice)
    if not key:
        await send(
            md_message(f"\n{t('gm_room_v2.invalid_choice')}", bot, mention=user_id)
        )
        return
    state["phase"] = "battle"
    reply = t(f"gm_room_v2.dlg2_reply_{key}")
    # 三选一随机抽取守卫
    state["guard_id"] = random.choice(_GUARD_IDS)
    guard = Monster(state["guard_id"])
    inv_model = investigator_repo.find_by_qq(user_id)
    inv = Investigator(inv_model) if inv_model else None
    if inv is None:
        await send(need_create_message(bot, mention=user_id))
        gm_room_active.pop(user_id, None)
        return
    service = BattleService(inv, guard)
    service.is_gm_room = True
    state["battle"] = service
    service.roll_initiative()

    lines = [
        reply,
        f"**{t('gm_room_v2.trial_title')}**",
        t("gm_room_v2.trial_start"),
        report_section(t("battle.monster_intro_title")),
        guard.出场,
        service.get_dex_compare_section(),
        service.get_status_table(),
        service.get_action_section(),
    ]
    msg = md_message("\n" + "\n\n".join(lines), bot, mention=user_id)
    kb = _gm_battle_keyboard(service)
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def _gm_send_round(
    user_id: str, state: dict, service: BattleService, result: tuple, bot: Bot, send
) -> None:
    """发送 GM 房间战斗回合结果（战报卡片优先，md 回退；附 gm 行动按钮）。

    战斗结束时只发结束文本，分支叙事由 _gm_handle_battle_end 继续。
    """
    if service.fight_is_over():
        if result and result[-1]:
            await send(md_message(f"\n{result[-1]}", bot, mention=user_id))
        return
    img = await render_pic(battle_round_html(service, result))
    if img is not None and await send_pic(bot, img, send):
        text = service.get_action_section()
    else:
        text = "\n" + "\n\n".join(str(x) for x in result if x)
    msg = md_message(text, bot, mention=user_id)
    kb = _gm_battle_keyboard(service)
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def _gm_handle_battle_input(
    user_id: str, state: dict, choice: str, bot: Bot, send, token: int | None = None
) -> None:
    service = state.get("battle")
    if not service or service.fight_is_over():
        await send(
            md_message(f"\n{data_loader.get_text('gm_room_v2.battle_defeat')}", bot, mention=user_id)
        )
        return
    if token is not None and token != service.get_turn_token():
        return  # 旧按钮（令牌不匹配）
    if choice == "逃跑":
        # 房间无出口：逃跑禁用
        await send(
            md_message(f"\n{data_loader.get_text('gm_room_v2.no_exit')}", bot, mention=user_id)
        )
        return
    result = service.execute_action(choice)
    await _gm_send_round(user_id, state, service, result, bot, send)
    if service.fight_is_over():
        await _gm_handle_battle_end(user_id, state, service, bot, send)


async def _gm_handle_battle_end(
    user_id: str, state: dict, service: BattleService, bot: Bot, send
) -> None:
    inv = service.investigator
    if service.hp_record["mon"] <= 0:
        if state["round"] == 1:
            await _gm_win1(user_id, state, inv, bot, send)
        else:
            await _gm_win2(user_id, state, inv, bot, send)
    else:
        # 战败：未复活 → GM 发力二轮；已复活 → 空手退出
        if not state["revive_used"]:
            await _gm_revive_flow(user_id, state, inv, bot, send)
        else:
            await _gm_lose2(user_id, state, inv, bot, send)


async def _gm_win1(user_id: str, state: dict, inv: Investigator, bot: Bot, send) -> None:
    """分支 B：第一轮就胜利 → 护甲 306 + 随机属性 +30 + 梦之碎片。"""
    t = data_loader.get_text
    armor = Equipment("306")
    inv.add_item_to_inventory("306", 1)
    attr = random.choice(_RANDOM_ATTR_POOL)
    inv.set_skill(attr, inv.get_skill(attr, 0) + 30)
    ending_repo.add_dream_fragment(user_id, 1)
    inv.save()
    lines = [
        f"**{t('gm_room_v2.win1_title')}**",
        t("gm_room_v2.win1_text"),
        t("gm_room_v2.reward_armor", name=armor.name),
        t("gm_room_v2.reward_stat_random", attr=attr),
        t("gm_room_v2.reward_fragment"),
    ]
    await send(md_message("\n" + "\n\n".join(lines), bot, mention=user_id))
    await _finish_gm_room(user_id, inv, bot, send)


async def _gm_win2(user_id: str, state: dict, inv: Investigator, bot: Bot, send) -> None:
    """分支 A·二轮胜 → 进入三轮奖励选择（武器轮）。"""
    t = data_loader.get_text
    state["phase"] = "choice_weapon"
    kb = build_keyboard(
        [
            [(f"武器「{label}」", f"gm_choice_weapon:{iid}") for iid, label in _WEAPON_CHOICES],
            [(t("gm_room_v2.choice_refuse_label"), "gm_choice_weapon:none")],
        ]
    )
    lines = [
        f"**{t('gm_room_v2.win2_title')}**",
        t("gm_room_v2.win2_text"),
        t("gm_room_v2.choice_weapon"),
    ]
    msg = md_message("\n" + "\n\n".join(lines), bot, mention=user_id)
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def _gm_lose2(user_id: str, state: dict, inv: Investigator, bot: Bot, send) -> None:
    """分支 A·二轮败 → 空手退出（不给碎片、不给任何奖励，仅叙事记忆）。"""
    t = data_loader.get_text
    lines = [
        f"**{t('gm_room_v2.lose2_title')}**",
        t("gm_room_v2.lose2_text"),
    ]
    await send(md_message("\n" + "\n\n".join(lines), bot, mention=user_id))
    await _finish_gm_room(user_id, inv, bot, send)


async def _gm_revive_flow(user_id: str, state: dict, inv: Investigator, bot: Bot, send) -> None:
    """GM 复活 + 临时助力 → 二轮再战同一守卫。"""
    t = data_loader.get_text
    state["revive_used"] = True
    state["round"] = 2
    # 房间内专用复活：不走 do_resurrect、不耗 501；引擎短路下 inv 未被置死，保险复原
    inv.is_survive = True
    inv.restore_hp()
    inv.save()
    guard = Monster(state["guard_id"])
    service = BattleService(inv, guard)
    service.is_gm_room = True
    # 临时助力：环境修正（全技能 +25 / 伤害 +1d6）+ 临时生命 +10，仅当轮实例生效
    boost_env = {
        "玩家": {
            "格斗": _GM_BOOST_SKILL,
            "闪避": _GM_BOOST_SKILL,
            "射击": _GM_BOOST_SKILL,
            "侦查": _GM_BOOST_SKILL,
            "急救": _GM_BOOST_SKILL,
            "敏捷": _GM_BOOST_SKILL,
            "伤害": _GM_BOOST_DAMAGE,
        }
    }
    service.set_environment(boost_env)
    service.temp_hp += _GM_BOOST_TEMP_HP
    state["battle"] = service
    service.roll_initiative()

    lines = [
        f"**{t('gm_room_v2.revive_title')}**",
        t("gm_room_v2.revive_text"),
        f"> {t('gm_room_v2.revive_boost')}",
        f"**{t('gm_room_v2.trial_title')}**",
        t("gm_room_v2.trial_again"),
        report_section(t("battle.monster_intro_title")),
        guard.出场,
        service.get_dex_compare_section(),
        service.get_status_table(),
        service.get_action_section(),
    ]
    msg = md_message("\n" + "\n\n".join(lines), bot, mention=user_id)
    kb = _gm_battle_keyboard(service)
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def _gm_handle_choice(user_id: str, state: dict, choice: str, bot: Bot, send) -> None:
    """三轮奖励选择：武器/道具/属性（各含「我不想要」）。"""
    t = data_loader.get_text
    phase = state["phase"]
    inv_model = investigator_repo.find_by_qq(user_id)
    inv = Investigator(inv_model) if inv_model else None
    if inv is None:
        await send(need_create_message(bot, mention=user_id))
        return
    if phase == "choice_weapon":
        if _gm_strip(choice) in ("none", "我不想要"):
            choice = "none"
        if choice == "none":
            state["declined"].append("weapon")
        else:
            key = _gm_match_key(_WEAPON_CHOICES, choice)
            if not key:
                await send(
                    md_message(f"\n{t('gm_room_v2.invalid_choice')}", bot, mention=user_id)
                )
                return
            choice = key
            name = Equipment(choice).name
            inv.add_item_to_inventory(choice, 1)
            inv.save()
            state["received"].append(name)
            await send(
                md_message(f"\n{t('gm_room_v2.reward_weapon', name=name)}", bot, mention=user_id)
            )
        await _gm_send_next_choice(user_id, state, bot, send)
    elif phase == "choice_item":
        if _gm_strip(choice) in ("none", "我不想要"):
            choice = "none"
        if choice == "none":
            state["declined"].append("item")
        else:
            key = _gm_match_key(_ITEM_CHOICES, choice)
            if not key:
                await send(
                    md_message(f"\n{t('gm_room_v2.invalid_choice')}", bot, mention=user_id)
                )
                return
            choice = key
            name = Equipment(choice).name
            inv.add_item_to_inventory(choice, 1)
            lines = [t("gm_room_v2.reward_item", name=name)]
            if choice == "308":
                # 管理员的骰子袋：掷一枚幸运骰 → 随机属性 +10（安全池）
                attr = random.choice(_RANDOM_ATTR_POOL)
                inv.set_skill(attr, inv.get_skill(attr, 0) + 10)
                lines.append(t("gm_room_v2.reward_stat_small", attr=attr))
            inv.save()
            state["received"].append(name)
            await send(md_message("\n" + "\n".join(lines), bot, mention=user_id))
        await _gm_send_next_choice(user_id, state, bot, send)
    elif phase == "choice_stat":
        if _gm_strip(choice) in ("none", "我不想要"):
            choice = "none"
        if choice == "none":
            state["declined"].append("stat")
            await _gm_finalize_choices(user_id, state, bot, send)
        else:
            key = _gm_match_key(
                [(a, f"{a} → 120") for a in _STAT_120_POOL], choice
            )
            if not key:
                await send(
                    md_message(f"\n{t('gm_room_v2.invalid_choice')}", bot, mention=user_id)
                )
                return
            choice = key
            inv.set_skill(choice, 120)  # 可突破上限
            inv.add_item_to_inventory("309", 1)  # 管理员的徽记（纪念）
            inv.save()
            state["received"].append(f"{choice}→120")
            await send(
                md_message(f"\n{t('gm_room_v2.reward_stat', attr=choice)}", bot, mention=user_id)
            )
            await _gm_finalize_choices(user_id, state, bot, send)


async def _gm_send_next_choice(user_id: str, state: dict, bot: Bot, send) -> None:
    """推进到下一轮选择：武器 → 道具 → 属性。"""
    t = data_loader.get_text
    if state["phase"] == "choice_weapon":
        state["phase"] = "choice_item"
        kb = build_keyboard(
            [
                [(f"道具「{label}」", f"gm_choice_item:{iid}") for iid, label in _ITEM_CHOICES],
                [(t("gm_room_v2.choice_refuse_label"), "gm_choice_item:none")],
            ]
        )
        lines = [t("gm_room_v2.choice_item")]
    elif state["phase"] == "choice_item":
        state["phase"] = "choice_stat"
        rows = [[(f"{attr} → 120", f"gm_choice_stat:{attr}") for attr in _STAT_120_POOL[:2]]]
        rows.append([(f"{attr} → 120", f"gm_choice_stat:{attr}") for attr in _STAT_120_POOL[2:]])
        rows.append([(t("gm_room_v2.choice_refuse_label"), "gm_choice_stat:none")])
        kb = build_keyboard(rows)
        lines = [t("gm_room_v2.choice_stat")]
    else:
        return
    msg = md_message("\n" + "\n\n".join(lines), bot, mention=user_id)
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def _gm_finalize_choices(user_id: str, state: dict, bot: Bot, send) -> None:
    """三轮选择收尾：三次全拒 → 特殊结束 CG；否则正常收尾。"""
    t = data_loader.get_text
    inv_model = investigator_repo.find_by_qq(user_id)
    inv = Investigator(inv_model) if inv_model else None
    if len(state["declined"]) >= 3 and inv is not None:
        # 特殊结束 CG：GM 无奈 → 随机属性 +30 + 梦之碎片
        attr = random.choice(_RANDOM_ATTR_POOL)
        inv.set_skill(attr, inv.get_skill(attr, 0) + 30)
        ending_repo.add_dream_fragment(user_id, 1)
        inv.save()
        lines = [
            f"**{t('gm_room_v2.choice_refuse_hint')}**",
            t("gm_room_v2.reward_stat_random", attr=attr),
            t("gm_room_v2.reward_fragment"),
        ]
        await send(md_message("\n" + "\n\n".join(lines), bot, mention=user_id))
    await _finish_gm_room(user_id, inv, bot, send)


async def _finish_gm_room(user_id: str, inv: Investigator | None, bot: Bot, send) -> None:
    """房间统一收尾：解除冒险态、HP 刷新、day+1（<40 冻结）、清状态、发送关闭文本。"""
    t = data_loader.get_text
    if inv is not None:
        inv.is_adventure = False
        inv.restore_hp()
        if inv.day < 40:
            inv.day += 1
        inv.save()
    battle_manager.remove_battle(user_id)
    gm_room_active.pop(user_id, None)
    lines = [f"**{t('gm_room_v2.title')}**", t("gm_room_v2.exit_text")]
    await send(md_message("\n" + "\n\n".join(lines), bot, mention=user_id))


# --- 统一输入分发：按钮回调 与 /行动 命令兜底共用 ---
async def _gm_handle_input(user_id: str, choice: str, bot: Bot, send, token: int | None = None) -> None:
    state = gm_room_active.get(user_id)
    if not state:
        await send(
            md_message(f"\n{data_loader.get_text('gm_room_v2.no_active')}", bot, mention=user_id)
        )
        return
    phase = state["phase"]
    if phase == "dlg1":
        await _gm_advance_dlg1(user_id, state, choice, bot, send)
    elif phase == "dlg2":
        await _gm_advance_dlg2(user_id, state, choice, bot, send)
    elif phase == "battle":
        await _gm_handle_battle_input(user_id, state, choice, bot, send, token)
    elif phase in ("choice_weapon", "choice_item", "choice_stat"):
        await _gm_handle_choice(user_id, state, choice, bot, send)
    else:
        await send(
            md_message(f"\n{data_loader.get_text('gm_room_v2.no_active')}", bot, mention=user_id)
        )


async def _gm_button_handler(
    user_id: str,
    choice: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """GM 房间全部按钮回调（gm_dlg1/gm_dlg2/gm_battle/gm_choice_* 共用）。"""
    async def _send(msg) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)

    try:
        await _gm_handle_input(user_id, choice, bot, _send, token)
    except FinishedException:
        pass


def _gm_active_rule(event: Event) -> bool:
    """/行动 兜底命令规则：仅当玩家处于 GM 房间状态时拦截（其余走原战斗命令）。"""
    try:
        return event.get_user_id() in gm_room_active
    except Exception:
        return False


gm_room_cmd = on_command("行动", rule=_gm_active_rule, priority=4, block=True)


@gm_room_cmd.handle()
async def handle_gm_room_command(event: Event, bot: Bot, msg: Message = CommandArg()) -> None:
    try:
        await _gm_handle_input(
            event.get_user_id(), msg.extract_plain_text().strip(), bot, gm_room_cmd.send
        )
    except FinishedException:
        pass


# --- 按钮回调注册 ---
for _kind in ("gm_dlg1", "gm_dlg2", "gm_battle", "gm_choice_weapon", "gm_choice_item", "gm_choice_stat"):
    register_button_handler(_kind, _gm_button_handler)


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
