from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.exception import FinishedException
from nonebot.params import CommandArg
from loguru import logger
import random
import ujson
from pathlib import Path
from typing import Any, Callable

from ..services.battle import BattleService
from ..models.player import Investigator, ending_repo, investigator_repo
from ..models.monster import Monster, monster_repo
from ..services.battle_cards import (
    battle_card_html,
    battle_open_html,
    ending_card_html,
    env_effects_lines,
)
from ..services.combat_messaging import (
    send_combat_result,
    send_event_skip_battle,
    send_sanity_zero,
    send_turn,
)
from ..services.data_loader import data_loader
from ..services.ending_engine import (
    check_daily,
    check_san_zero,
    ending_card_payload,
    judge_door_choice,
    pending_door_choice,
    render_door_choice,
)
from ..services.event_service import (
    apply_event_choice,
    event_option_label,
    event_option_locked,
    event_option_rows,
    event_states,
    pick_random_event,
)
from ..services.boss_framework import (
    boss_battle_cry,
    boss_image_for_monster,
    boss_forced_environment,
    boss_resolve_forced,
    get_boss,
    register_adventure_runner,
    send_boss_dialogue,
)
from ..services.resurrect import do_resurrect
from ..services.sanity import run_sanity_and_madness
from .jk import hidden_should_trigger
from .guest import guest_enter, guest_should_trigger
from .gm_room import gm_afterglow, gm_room_enter, gm_room_should_trigger
from .npc import (
    inject_companion,
    npc_handle_command,
    npc_should_trigger,
    npc_send_dialogue,
    npc_states,
)
from ..utils.active_battles import battle_manager
from ..utils.buttons import (
    _send_to_user,
    register_button_handler,
    setup_button_callback,
)
from ..utils.image_sender import render_pic, send_pic
from ..utils.md_format import (
    build_keyboard,
    cmd_tag,
    md_message,
    need_create_message,
    report_quote,
    report_section,
)
from ..services.daily_service import (
    _adventure_done_today,
    _mark_adventure_done,
    _skip_daily,
    door_states,
)

# 周目联动配置段（reply_data.json `loop_linkage`）：天数/怪物 id/概率/权重/环境名全部数据驱动，
# 缺省逐项回退现状魔法数字（零行为）。
def _loop_linkage(key: str) -> dict:
    """读取 loop_linkage 子段（缺省空 dict）。"""
    return (data_loader.reply_data.get("loop_linkage") or {}).get(key) or {}


def _black_moon_environment() -> str | None:
    """启专属环境名：已由 environment_data.json 的「入池:false」标记承载（普通池排除）。

    此函数保留仅为语义查询；实际过滤逻辑见 pick_random_environment（按入池标记）。
    """
    return None


def _hound_weight(inv: Investigator) -> dict | None:
    """周目联动权重：被猎犬杀死过 → 猎犬在每日池权重 ×2（"它来找你了"）。

    权重值读 loop_linkage.hound_weight（缺省 {"32": 2}）。
    """
    cfg = _loop_linkage("hound_weight")
    if inv.get_flag(cfg.get("flag", "past.hound")) != cfg.get("value", "killed_by"):
        return None
    return {str(cfg.get("monster_id", "32")): int(cfg.get("weight", 2))}


def pick_random_environment() -> str | None:
    """普通日随机环境：从「入池」标记为 true（缺省视为 true）的环境池中随机选取；池空返回 None。"""
    pool = [
        k
        for k, v in data_loader.environment_data.items()
        if v.get("入池", True) is not False
    ]
    return random.choice(pool) if pool else None


def _linkage_encounter(inv: Investigator) -> str | None:
    """周目联动·特定天遭遇：击杀过廷达洛斯之猎犬 → D12/D20 概率替换为「猎犬·复仇」(44)。

    纯遭遇层：只替换当日怪，不改结局判定；44 不掉 400~508 信物（奖励为消耗品 505）。
    天数/怪物 id/概率/flag 均读 loop_linkage.hound_vengeance（缺省现状 12/20/44/50）。
    """
    cfg = _loop_linkage("hound_vengeance")
    days = cfg.get("days") or [12, 20]
    monster_id = str(cfg.get("monster_id", "44"))
    chance = int(cfg.get("chance", 50))
    flag = cfg.get("flag", "past.hound")
    value = cfg.get("value", "killed")
    if inv.day not in days:
        return None
    if inv.get_flag(flag) != value:
        return None
    if random.randint(1, 100) <= chance:
        return monster_id
    return None


def _apply_hound_hesitation(
    service: BattleService, inv: Investigator, monster_id: str
) -> None:
    """周目联动：被猎犬杀死过的调查员，本局首次遭遇猎犬 32 时它迟疑一回合（不攻击）。

    单局旗标 past.hound_hesitated 在迟疑真正发生时置位（engine 消费），确保每局仅首遇生效。
    怪物 id/flag/守卫 flag 读 loop_linkage.hound_hesitate（缺省现状 32/killed_by）。
    """
    cfg = _loop_linkage("hound_hesitate")
    mid = str(cfg.get("monster_id", "32"))
    flag = cfg.get("flag", "past.hound")
    value = cfg.get("value", "killed_by")
    guard_flag = cfg.get("guard_flag", "past.hound_hesitated")
    if monster_id == mid and inv.get_flag(flag) == value and not inv.get_flag(guard_flag):
        service.hound_hesitates = True


def _resurrect_price() -> int:
    """复活道具（501）售价：扫描 shop_data 价格档位（默认 200）。"""
    for price_key, items in data_loader.shop_data.items():
        if isinstance(items, list) and "501" in items:
            return int(price_key)
    return 200


async def _send_frozen_door(
    user_id: str,
    inv: Investigator,
    block: str,
    bot: Bot,
    send: Callable,
    finish: Callable,
) -> None:
    """门扉冻结拦截：若有未完成抉择则重渲染门扉按钮（防 bot 重启后卡死），否则直接拦截。"""
    if pending_door_choice(inv):
        door_render = render_door_choice(inv)
        door_states[user_id] = {"choices": door_render["choices"]}
        choices = door_render["choices"]
        rows = [
            [(c["label"], f"door:{c['key']}") for c in choices[i : i + 3]]
            for i in range(0, len(choices), 3)
        ]
        kb = build_keyboard(rows)
        msg = md_message(f"\n{block}\n\n{door_render['text']}", bot, mention=user_id)
        if kb is not None and not isinstance(msg, str):
            msg.append(kb)
        await finish(msg)
    await finish(md_message(f"\n{block}", bot, mention=user_id))


def _danger_warning(day: int) -> str:
    """高难日预警：当日怪物池高危占比≥2/3（必战级），或池内含拉卡德/修格斯（Boss 叙事怪）时返回提示行。

    把"必死"变"可决策"：玩家可提前备防具/武器，或进场后果断逃跑。
    """
    pool = monster_repo._checkpoint_data.get(str(day)) or []
    monsters = data_loader.monster_data
    risky = [mid for mid in pool if monsters.get(mid, {}).get("高危")]
    boss_ids = {"10", "5"}  # 拉卡德 / 修格斯
    if risky and (len(risky) * 3 >= len(pool) * 2 or boss_ids & set(pool)):
        return data_loader.get_text("adventure.danger_warning")
    return ""


def _scroll_price() -> int:
    """冒险卷（401）售价：shop_data 0 档固定价（默认 10）。"""
    items = data_loader.shop_data.get("0") or {}
    return int(items.get("401", 10))


def _daily_done_msg(user_id: str, bot: Bot):
    """@description 今日冒险已完成的消息：含购买冒险卷并开始冒险的按钮与指令。"""
    t = data_loader.get_text
    price = _scroll_price()
    kb = build_keyboard([[(t("adventure.scroll_buy_button"), "scroll_adventure")]])
    msg = md_message(
        f"\n{t('adventure.daily_done')}\n\n"
        f"{t('adventure.daily_done_buy', price=price)}\n"
        f"{cmd_tag('/购买 401', show=t('adventure.scroll_buy_button'))}",
        bot,
        mention=user_id,
    )
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    return msg


def _advance_day_after_event(inv: Investigator) -> None:
    """事件检定成功跳过战斗后推进 day：与胜利结算一致（settlement.py，day40 冻结不推进）。

    圣泉洗礼等「跳过战斗」事件此前不推进 day，导致 day5 反复触发/主线卡死（B1）。
    day40 保持冻结（终局由守门人战斗结算），不再额外 +1。
    """
    if inv.day < 40:
        inv.day += 1
    inv.save()


# --- 每日彩蛋互斥链调度器（triggers 配置驱动）---
# 缺省触发器顺序 = 现状链（hidden[jk/qiren] → guest → npc → gm_room）；
# 唯一数据定义在 data_loader._DEFAULT_TRIGGERS，本插件只读不定义。
def _triggers_sorted() -> list[dict]:
    """按 priority 升序的触发器配置（reply_data `triggers` 段；缺省回退现状顺序）。"""
    return sorted(data_loader.get_triggers(), key=lambda t: int(t.get("priority", 99)))


def _trigger_by_id(tid: str) -> dict:
    """按 id 取触发器配置（缺省回退空 dict）。"""
    for t in _triggers_sorted():
        if t.get("id") == tid:
            return t
    return {}


async def _dispatch_daily_triggers(
    user_id: str, inv: Investigator, bot: Bot, send: Callable, after_monster: bool
) -> bool:
    """每日彩蛋互斥链调度：按 triggers 配置 priority 排序遍历，第一个命中即触发（一天至多一个）。

    after_monster=False：hidden → guest → npc（命中即接管当日并返回 True）；
    after_monster=True：gm_room（选怪后判定，保持随机消费时序不变，优先级最低）。
    同 group 触发器共享一次判定：jk/qiren 同属 hidden 组，一次 d20 由 hidden_should_trigger 裁决。
    """
    seen_groups: set[str] = set()
    for t in _triggers_sorted():
        if bool(t.get("after_monster")) != after_monster:
            continue
        gid = str(t.get("group") or t.get("id") or "")
        if gid in seen_groups:
            continue
        seen_groups.add(gid)
        kind = t.get("kind") or t.get("id")
        if kind == "hidden":
            # 隐藏挑战 BOSS：遍历 boss 配置表（boss_data.json），共享组一次掷骰，
            # 命中后发送对应 BOSS 对话（新 BOSS 纯数据入链，无需改代码）
            boss_id = hidden_should_trigger(inv)
            if boss_id is not None:
                await send_boss_dialogue(boss_id, user_id, bot, send)
                return True
        elif kind == "guest":
            guest_id = guest_should_trigger(inv)
            if guest_id is not None:
                await guest_enter(user_id, inv, bot, send, guest_id)
                return True
        elif kind == "npc":
            npc_id = npc_should_trigger(inv)
            if npc_id is not None:
                await npc_send_dialogue(user_id, bot, send, npc_id)
                return True
        elif kind == "gm_room":
            if gm_room_should_trigger(inv):
                await gm_room_enter(user_id, inv, bot, send)
                gm_afterglow[user_id] = True
                return True
    return False


def _jk_image() -> dict:
    """JK 形象图配置（boss_data「jk」图片段，缺省回退 reply_data boss_image，零行为）。

    保留供旧调用方/测试读取配置；实际发送由 boss_framework.boss_image_for_monster 统一消费。
    """
    img = (get_boss("jk") or {}).get("图片") or {}
    if img.get("路径"):
        return {"path": img["路径"], "flag": img.get("flag") or "jk.img_shown"}
    return (data_loader.reply_data.get("boss_image") or {}).get("jk") or {}


def _jk_image_path() -> str:
    return str(_jk_image().get("path", "resources/images/jk.jpg"))


def _jk_image_flag() -> str:
    return str(_jk_image().get("flag", "jk.img_shown"))


adventure_cmd = on_command(
    "今日冒险", aliases={"daily_adventure", "开始冒险"}, priority=10, block=True
)


async def _run_adventure(
    user_id: str, bot: Bot, send: Callable, finish: Callable
) -> None:
    """今日冒险完整流程（命令与按钮共用）。finish 发送后须抛出 FinishedException 中断流程。"""
    try:
        inv_model = investigator_repo.find_by_qq(user_id)
        if inv_model is None:
            await finish(need_create_message(bot, mention=user_id))
        inv = Investigator(inv_model)
        if not inv.is_survive:
            t = data_loader.get_text
            from database.db import get_info

            gold = get_info(user_id).gold
            price = _resurrect_price()
            enough = (
                t("adventure.resurrect_price_enough")
                if gold >= price
                else t("adventure.resurrect_price_not_enough")
            )
            await finish(
                md_message(
                    f"\n{t('adventure.player_dead_price', price=price, gold=gold, enough=enough)}\n\n"
                    f"{cmd_tag('/复活', show=t('character.resurrect_button'))}\n"
                    f"{cmd_tag('/今日商店', show=t('shop.shop_button'))}\n"
                    f"{cmd_tag('/创建调查员', show=t('character.create_button'))}",
                    bot,
                    mention=user_id,
                )
            )
        if battle_manager.get_battle(user_id):
            await finish(
                md_message(
                    f"\n{data_loader.get_text('adventure.in_battle')}",
                    bot,
                    mention=user_id,
                )
            )
        if _adventure_done_today(user_id):
            # 冒险卷（401）：背包持有则自动使用，获得额外冒险次数
            if investigator_repo.remove_item_from_inventory(user_id, "401", 1):
                await send(
                    md_message(
                        f"\n{data_loader.get_text('adventure.scroll_used')}",
                        bot,
                        mention=user_id,
                    )
                )
            else:
                await finish(_daily_done_msg(user_id, bot))

        inv.restore_hp()
        inv.save()

        # 每日推进出口（④）：inactive_days 清零、day 同步、信物里程碑、
        # day==40 守卫、结局/门扉冻结拦截
        _logs, _block = check_daily(inv)
        if _block:
            await _send_frozen_door(user_id, inv, _block, bot, send, finish)
        for _log in _logs:
            await send(md_message(f"\n{_log}", bot, mention=user_id))

        # 隐藏挑战 BOSS（jk/qiren 及未来新 BOSS）：挑战旗标 → 强制绑定怪物（boss 配置表）；
        # 否则每日共享概率触发对话（挑战/不挑战）。day40 守卫内置 boss_framework（门扉归守门人）。
        forced_boss = boss_resolve_forced(user_id, inv)
        forced = bool(forced_boss)
        if forced:
            monster_id = str((get_boss(forced_boss) or {}).get("怪物id") or forced_boss)
        else:
            monster_id = None
        if not monster_id:
            if await _dispatch_daily_triggers(
                user_id, inv, bot, send, after_monster=False
            ):
                # 每日彩蛋互斥链：按 triggers 配置 priority 排序，第一个命中即触发（一天至多一个）
                return
            # 周目联动：被猎犬杀死过 → 猎犬在每日池权重 ×2（"它来找你了"）
            weights = _hound_weight(inv)
            monster_id = monster_repo.find_random_id_for_day(inv.day, weights=weights)
        # 周目联动：击杀过猎犬 → D12/D20 概率替换为强化版「猎犬·复仇」(44)
        if not forced:
            linked = _linkage_encounter(inv)
            if linked:
                monster_id = linked
        # 走到战斗/事件/GM 房间流程前清理残留 NPC 对话状态（防 /行动 误拦截）
        npc_states.pop(user_id, None)
        if not monster_id:
            await finish(
                md_message(
                    f"\n{data_loader.get_text('adventure.no_monster_config')}",
                    bot,
                    mention=user_id,
                )
            )
        monster = Monster(monster_id)
        monster_intro = getattr(
            monster,
            "出场",
            data_loader.get_text("adventure.monster_intro_default", name=monster.name),
        )
        # 开场大喝：BOSS 配置表驱动（战斗.开场大喝），怪物 intro 后大喝一声
        battle_cry = boss_battle_cry(monster_id)
        if battle_cry:
            monster_intro = f"{monster_intro}\n\n{battle_cry}"
        day_event = data_loader.get_event(inv.day, flags=inv.get_all_flags())

        # GM 房间彩蛋（梦之碎片）：after_monster 触发器（选怪后判定，保持随机消费时序不变）；
        # day40/隐藏挑战不参与（互斥链优先级最低）
        if not forced and await _dispatch_daily_triggers(
            user_id, inv, bot, send, after_monster=True
        ):
            return

        # --- Environment ---
        env = {}
        env_desc = ""
        if forced:
            # 隐藏挑战强制环境（boss_data 战斗.强制环境；JK 无专属环境 → env 保持空）
            env = boss_forced_environment(forced_boss)
            if env:
                env_desc = f"【{env.get('name')}】{env.get('描述', '')}"
        elif data_loader.environment_data:
            env_key = pick_random_environment()
            env = {}
            if env_key:
                env = data_loader.environment_data[env_key].copy()
                env["name"] = env_key
                env_desc = f"【{env_key}】{env.get('描述', '')}"

        # --- Battle service ---
        service = BattleService(inv, monster)
        if env:
            service.set_environment(env)
        # 周目联动：被猎犬杀死过 → 首次遭遇猎犬它迟疑一回合（首回合跳过攻击）
        _apply_hound_hesitation(service, inv, monster_id)
        # 伙伴助战：好感度满解锁的 NPC 伙伴注入（泛化 506 骨哨助战，纯战斗内状态）
        inject_companion(service, inv)
        # 梦醒前的余韵：GM 房间彩蛋当日自动注入战斗强化（全技能+30/伤害翻倍/+25临时生命）
        if gm_afterglow.pop(user_id, False):
            service.apply_dream_buff()

        inv.is_adventure = True
        inv.save()

        env_name = env.get("name", "") if env else ""
        title = (
            data_loader.get_text("battle.report_title", env=env_name)
            if env_name
            else data_loader.get_text("battle.report_title_default")
        )
        anomaly_title = (
            data_loader.get_text("battle.anomaly_title", env=env_name)
            if env_name
            else data_loader.get_text("battle.anomaly_title_default")
        )
        anomaly_lines = [
            env.get("描述", "") if env else "",
            day_event,
            monster_intro,
        ]
        warning = _danger_warning(inv.day)
        if warning:
            anomaly_lines.insert(1, warning)
        anomaly = f"{report_section(anomaly_title)}\n" f"{report_quote(anomaly_lines)}"

        header = f"\n{env_desc}\n{day_event}" if env_desc else f"\n{day_event}"
        if warning:
            header += f"\n{warning}"

        # --- 奇遇：固定日期事件当天必触发；否则 40% 随机（按条件过滤）---
        # 隐藏挑战强制怪物时跳过奇遇（替换今日遭遇）
        event_data = None if forced else pick_random_event(inv)
        if event_data:
            battle_manager.add_battle(user_id, service)
            event_states[user_id] = {
                "event": event_data,
            }

            from database.db import get_info

            # 展示层与执行层（apply_event_choice）条件求值入参一致：传真实 progress，
            # 避免选项含「进度」算子时展示层恒置灰而执行层放行的打架（P3 一致性）
            gold = get_info(user_id).gold
            progress = ending_repo.ensure_progress(inv.qq, inv.day)
            labels = [
                event_option_label(opt, gold, inv, progress)
                for opt in event_data["选项"]
            ]

            # 事件选项按钮（商品选项显示价格 / 乌帕不足；条件未满足置灰锁定；每行 3 个）
            options = event_data["选项"]
            locks = [event_option_locked(opt, inv, progress) for opt in options]
            event_kb = build_keyboard(event_option_rows(labels, options, locks))

            # 奇遇 CG 卡片（全平台）+ 选项按钮；图片失败回退文本
            card_html = battle_card_html(service, event_desc=event_data["描述"])
            img = await render_pic(card_html)
            if img is not None and await send_pic(bot, img, send):
                options = "\n".join(
                    f" {data_loader.get_text('adventure.event_choice', input=label)}"
                    for label in labels
                )
                event_msg = md_message(
                    f"\n**{data_loader.get_text('adventure.event_title')}**\n\n"
                    f"{options}",
                    bot,
                    mention=user_id,
                )
            else:
                event_text = (
                    f"\n\n{data_loader.get_text('adventure.event_title')}\n"
                    f"{report_quote([event_data['描述']])}\n"
                )
                for label in labels:
                    event_text += f" {data_loader.get_text('adventure.event_choice', input=label)}\n"
                event_msg = md_message(f"{header}{event_text}", bot, mention=user_id)
            if event_kb is not None and not isinstance(event_msg, str):
                event_msg.append(event_kb)
            await send(event_msg)
            return

        # --- 无奇遇：理智检定 → 战斗开始 ---
        san_desc, madness_desc, is_mad, madness_duration, san_zero, san_loss = (
            run_sanity_and_madness(inv, monster)
        )
        if san_zero:
            # 出口②：SAN 归零（永久疯狂）→ 登记 E06 + 清理战场残留（防 /行动 命中遗留 battle）
            e06_result = check_san_zero(inv)
            battle_manager.remove_battle(user_id)
            inv.is_survive = False
            inv.save()
            _mark_adventure_done(user_id)
            await send_sanity_zero(
                service, inv, san_desc, san_loss, bot, send
            )
            # E06 结局卡片（卡片优先，渲染失败回退 md 结局文案）
            if not await _try_send_ending_card(
                bot,
                user_id,
                "",
                "E06",
                note=str(e06_result.get("note") or ""),
                inv=inv,
                send=send,
            ):
                await send(
                    md_message(f"\n{e06_result['message']}", bot, mention=user_id)
                )
            return
        if is_mad:
            service.set_madness(True, madness_duration)

        battle_manager.add_battle(user_id, service)
        service.roll_initiative()

        # BOSS 首次见面：发送形象图（boss_data 图片，持久化防重发，失败静默）
        boss_img = boss_image_for_monster(monster_id)
        if boss_img and not inv.get_flag(boss_img[1]):
            img_path = Path(__file__).resolve().parent.parent.parent / boss_img[0]
            try:
                if img_path.exists():
                    with img_path.open("rb") as f:
                        if await send_pic(bot, f, send):
                            inv.set_flag(boss_img[1], True)
                            inv.save()
            except Exception:  # noqa: BLE001 - 图片发送失败静默（零影响战斗）
                logger.debug(f"BOSS image send failed for {user_id}")

        # 环境修正小节
        env_effects = ""
        env_lines = env_effects_lines(env)
        if env_lines:
            env_effects = (
                f"{report_section(data_loader.get_text('adventure.env_effect_title'))}\n"
                f"{report_quote(env_lines)}"
            )

        reply = (
            f"{title}\n\n"
            f"{data_loader.get_text('battle.day_line', day=inv.day)}\n\n"
            f"{anomaly}\n\n"
            f"{env_effects}\n\n"
            f"{report_section(data_loader.get_text('battle.monster_intro_title'))}\n"
            f"{monster_intro}\n\n"
            f"{san_desc}{madness_desc}\n\n"
            f"{service.get_dex_compare_section()}\n\n"
            f"{service.get_status_table()}\n\n"
            f"{service.get_danger_section()}\n\n"
            f"{service.get_action_section()}"
        )

        # 入场 CG 卡片（环境氛围，独立一张图；渲染失败则异象/环境修正保留在战斗卡片）
        cg_shown = False
        img = await render_pic(battle_card_html(service, day_event))
        if img is not None:
            cg_shown = await send_pic(bot, img, send)

        # 开场战报卡片：CG 已展示异象/环境修正，战斗卡片不再重复
        battle_reply = (
            f"{warning}\n\n"
            f"{report_section(data_loader.get_text('battle.monster_intro_title'))}\n"
            f"{monster_intro}\n\n"
            f"{san_desc}{madness_desc}\n\n"
            f"{service.get_dex_compare_section()}\n\n"
            f"{service.get_status_table()}\n\n"
            f"{service.get_danger_section()}\n\n"
            f"{service.get_action_section()}"
        )
        img = await render_pic(battle_open_html(service, battle_reply))
        if img is not None and await send_pic(bot, img, send):
            await send(
                send_turn(service, bot, service.get_action_section())
            )
            return

        # 图片失败回退 md（含完整开场内容）
        if not cg_shown:
            await send(send_turn(service, bot, reply))
            return
        await send(send_turn(service, bot, battle_reply))

    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"Error starting adventure for {user_id}: {e}")
        await finish(
            md_message(
                f"\n{data_loader.get_text('adventure.start_error')}",
                bot,
                mention=user_id,
            )
        )


# BOSS 挑战「重入今日冒险」回调注册：boss_framework 依赖注入（解循环①——services 不再
# import plugins/adventure）。委托在调用时经模块全局解析 `_run_adventure`，故对
# `mock.patch("src.plugins.adventure._run_adventure")` 的测试桩同样生效。
async def _reenter_adventure_runner(
    user_id: str, bot: Bot, send: Callable, finish: Callable
) -> None:
    await _run_adventure(user_id, bot, send, finish)


register_adventure_runner(_reenter_adventure_runner)


@adventure_cmd.handle()
async def handle_adventure(event: Event, bot: Bot):
    await _run_adventure(
        event.get_user_id(), bot, adventure_cmd.send, adventure_cmd.finish
    )


async def handle_adventure_button(
    user_id: str,
    payload: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """「今日冒险」按钮回调：与命令共用同一流程。"""
    async def _send(msg: Any) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)

    async def _finish(msg: Any) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)
        raise FinishedException

    try:
        await _run_adventure(user_id, bot, _send, _finish)
    except FinishedException:
        pass


async def handle_scroll_adventure_button(
    user_id: str,
    payload: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """「购买冒险卷并冒险」按钮回调：购买 401 后自动开始今日冒险。"""
    from ..services.shop_service import shop_service

    items = shop_service.get_todays_shop("seed")
    ok, res = shop_service.buy_item(user_id, "401", 1, items)
    await _send_to_user(
        bot,
        user_id,
        md_message(f"\n{res}", bot, mention=user_id),
        group_openid,
    )
    if ok:
        await handle_adventure_button(user_id, "", bot, group_openid, token)


resurrect_cmd = on_command(
    "复活", aliases={"use_resurrect", "复活道具"}, priority=10, block=True
)


@resurrect_cmd.handle()
async def handle_resurrect(event: Event, bot: Bot) -> None:
    """使用复活道具（死亡后）。"""
    user_id = event.get_user_id()
    await resurrect_cmd.finish(
        md_message(f"\n{do_resurrect(user_id)}", bot, mention=user_id)
    )


async def handle_resurrect_button(
    user_id: str,
    payload: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """死亡消息「使用复活道具」按钮回调。"""
    await _send_to_user(
        bot,
        user_id,
        md_message(f"\n{do_resurrect(user_id)}", bot, mention=user_id),
        group_openid,
    )


combat_cmd = on_command("行动", aliases={"combat_action"}, priority=5, block=True)


@combat_cmd.handle()
async def handle_combat(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    if investigator_repo.find_by_qq(user_id) is None:
        await combat_cmd.finish(need_create_message(bot, mention=user_id))
    action = msg.extract_plain_text().strip()

    # 门扉抉择命令通道（door 按钮/命令同通道）
    door_state = door_states.get(user_id)
    if door_state:
        choice = action.removeprefix("/行动 ").removeprefix("/行动").strip()
        door_key = _resolve_door_key(door_state, choice)
        if not door_key:
            await combat_cmd.finish(
                md_message(
                    f"\n{data_loader.get_text('door.invalid_choice', default='请选择门扉选项。')}",
                    bot,
                    mention=user_id,
                )
            )
        door_states.pop(user_id, None)
        inv_model = investigator_repo.find_by_qq(user_id)
        if inv_model is None:
            await combat_cmd.finish(need_create_message(bot, mention=user_id))
        inv = Investigator(inv_model)
        result = judge_door_choice(inv, door_key)
        if result.get("error"):
            await combat_cmd.finish(
                md_message(f"\n{result['message']}", bot, mention=user_id)
            )
        if result.get("refight"):
            await _start_day40_refight(user_id, bot, combat_cmd.send)
            return
        # 结局卡片优先；渲染失败回退 md 结局文案
        if await _try_send_ending_card(
            bot,
            user_id,
            "",
            str(result["ending"]),
            variant=str(result.get("variant") or ""),
            note=str(result.get("note") or ""),
            inv=inv,
            send=combat_cmd.send,
        ):
            return
        await combat_cmd.finish(
            md_message(f"\n{result['message']}", bot, mention=user_id)
        )

    # NPC 对话命令通道（npc_states 未决时 `/行动 <选项输入>` 完成选择，对齐事件/门扉模式）
    npc_state = npc_states.pop(user_id, None)
    if npc_state:
        choice = action.removeprefix("/行动 ").removeprefix("/行动").strip()
        await npc_handle_command(user_id, npc_state["npc_id"], choice, bot, combat_cmd.send)
        return

    # Check for pending event choice first
    ev_state = event_states.pop(user_id, None)
    if ev_state:
        event_data = ev_state["event"]
        battle = battle_manager.get_battle(user_id)
        if not battle:
            await combat_cmd.finish(
                md_message(
                    f"\n{data_loader.get_text('adventure.battle_state_error')}",
                    bot,
                    mention=user_id,
                )
            )
        if not battle.investigator.is_survive:
            # P0-1 防线：死亡角色不进入事件流程，清理战场残留
            battle_manager.remove_battle(user_id)
            await combat_cmd.finish(
                md_message(
                    f"\n{data_loader.get_text('adventure.player_dead')}",
                    bot,
                    mention=user_id,
                )
            )

        # Strip /行动 prefix
        choice = action.removeprefix("/行动 ").removeprefix("/行动").strip()
        matched = (
            next((o for o in event_data["选项"] if o["输入"] == choice), None)
            if choice
            else None
        )

        if not action:
            matched = None  # No choice typed

        if matched:
            event_reply, skip_battle = apply_event_choice(
                battle.investigator, user_id, matched
            )
            if skip_battle:
                # 检定成功：跳过今日战斗（图片卡片优先，失败回退 md）
                battle.investigator.is_adventure = False
                _advance_day_after_event(battle.investigator)  # day +1（day40 冻结），与胜利结算一致
                battle_manager.remove_battle(user_id)
                _mark_adventure_done(user_id)
                await send_event_skip_battle(
                    battle, event_reply, bot, combat_cmd.send, combat_cmd.finish
                )
                return
        else:
            event_reply = data_loader.get_text("adventure.event_default")
            skip_battle = False
        if skip_battle:
            return

        # 奇遇完成后：怪物出场 → 理智检定（+智力检定/疯狂）→ 敏捷对比 → 战斗开始
        san_desc, madness_desc, is_mad, madness_duration, san_zero, san_loss = (
            run_sanity_and_madness(battle.investigator, battle.monster)
        )
        if san_zero:
            # 出口②：SAN 归零（永久疯狂）→ 登记 E06 + 清理战场残留（P0-1 防线）
            e06_result = check_san_zero(battle.investigator)
            battle_manager.remove_battle(user_id)
            battle.investigator.is_survive = False
            battle.investigator.save()
            _mark_adventure_done(user_id)
            await send_sanity_zero(
                battle,
                battle.investigator,
                san_desc,
                san_loss,
                bot,
                combat_cmd.send,
                show_cg=False,
            )
            # E06 结局卡片（卡片优先，渲染失败回退 md 结局文案）
            if not await _try_send_ending_card(
                bot,
                user_id,
                "",
                "E06",
                note=str(e06_result.get("note") or ""),
                inv=battle.investigator,
                send=combat_cmd.send,
            ):
                await combat_cmd.send(
                    md_message(f"\n{e06_result['message']}", bot, mention=user_id)
                )
            return
        if is_mad:
            battle.set_madness(True, madness_duration)

        monster_intro = getattr(
            battle.monster,
            "出场",
            data_loader.get_text(
                "adventure.monster_intro_default", name=battle.monster.name
            ),
        )
        warning = _danger_warning(battle.investigator.day)
        event_block = f"{event_reply}\n\n{warning}" if warning else event_reply
        reply = (
            f"{event_block}\n\n"
            f"{report_section(data_loader.get_text('battle.monster_intro_title'))}\n"
            f"{monster_intro}\n\n"
            f"{san_desc}{madness_desc}\n\n"
            f"{battle.get_dex_compare_section()}\n\n"
            f"{battle.start_turn()}"
        )
        img = await render_pic(battle_open_html(battle, reply))
        if img is not None and await send_pic(bot, img, combat_cmd.send):
            await combat_cmd.send(send_turn(battle, bot, battle.get_action_section()))
            return
        await combat_cmd.send(send_turn(battle, bot, reply))
        return

    # Normal combat flow
    battle = battle_manager.get_battle(user_id)
    if not battle:
        await combat_cmd.finish(
            md_message(
                f"\n{data_loader.get_text('adventure.no_active_battle')}",
                bot,
                mention=user_id,
            )
        )
    if not battle.investigator.is_survive:
        # P0-1 防线：死亡角色不能继续战斗，清理战场残留（防 SAN 归零后 /行动 打完整场）
        battle_manager.remove_battle(user_id)
        await combat_cmd.finish(
            md_message(
                f"\n{data_loader.get_text('adventure.player_dead')}",
                bot,
                mention=user_id,
            )
        )

    if not action:
        await combat_cmd.finish(
            md_message(
                f"\n{data_loader.get_text('adventure.need_action')}",
                bot,
                mention=user_id,
            )
        )

    if action.startswith(data_loader.get_text("adventure.use_item")):
        tokens = action.split()
        if len(tokens) < 2:
            await combat_cmd.finish(
                md_message(
                    f"\n{data_loader.get_text('adventure.need_item_id', default='请输入物品ID。')}",
                    bot,
                    mention=user_id,
                )
            )
        item_id = tokens[1]
        if item_id in ("505", "506"):
            # 消耗品：走战斗动作（回复 HP / 骨哨助战）
            before = _ending_ids_snapshot(user_id)
            result = battle.execute_action(f"使用{item_id}")
            await send_combat_result(battle, bot, result, send=combat_cmd.send)
            if battle.fight_is_over():
                await _send_new_battle_endings(
                    bot, user_id, "", battle, before,
                    _ending_ids_snapshot(user_id), combat_cmd.send,
                )
                await _cleanup_battle(user_id, battle)
                await _present_door_choice(user_id, battle, bot, combat_cmd.send)
            return
        ok, msg_text = investigator_repo.equip_item(user_id, item_id)
        if ok:
            battle.investigator.update_equipment()
            if battle.current_turn == "inv":
                battle._update_gun_status()
            prompt = battle._get_next_turn_prompt()
            await combat_cmd.send(send_turn(battle, bot, f"{msg_text}\n\n{prompt}"))
        else:
            await combat_cmd.send(md_message(msg_text, bot, mention=user_id))
        return

    before = _ending_ids_snapshot(user_id)
    result = battle.execute_action(action)
    await send_combat_result(battle, bot, result, send=combat_cmd.send)

    if battle.fight_is_over():
        await _send_new_battle_endings(
            bot, user_id, "", battle, before,
            _ending_ids_snapshot(user_id), combat_cmd.send,
        )
        await _cleanup_battle(user_id, battle)
        await _present_door_choice(user_id, battle, bot, combat_cmd.send)


async def handle_combat_action(
    user_id: str,
    action: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """按钮触发战斗行动。token 用于防重复点击（旧按钮失效）。"""
    battle = battle_manager.get_battle(user_id)
    if not battle:
        await _send_to_user(
            bot,
            user_id,
            md_message(
                f"\n{data_loader.get_text('adventure.no_active_battle')}",
                bot,
                mention=user_id,
            ),
            group_openid,
        )
        return
    if not battle.investigator.is_survive:
        # P0-1 防线：死亡角色不能通过按钮继续战斗，清理战场残留
        battle_manager.remove_battle(user_id)
        await _send_to_user(
            bot,
            user_id,
            md_message(
                f"\n{data_loader.get_text('adventure.player_dead')}",
                bot,
                mention=user_id,
            ),
            group_openid,
        )
        return

    # 旧按钮点击（令牌不匹配）直接忽略
    if token is not None and token != battle.get_turn_token():
        logger.debug(
            f"Stale button click ignored: token={token}, current={battle.get_turn_token()}"
        )
        return

    before = _ending_ids_snapshot(user_id)
    result = battle.execute_action(action)

    async def _send(msg):
        await _send_to_user(bot, user_id, msg, group_openid)

    await send_combat_result(battle, bot, result, send=_send)

    if battle.fight_is_over():
        await _send_new_battle_endings(
            bot, user_id, group_openid, battle, before,
            _ending_ids_snapshot(user_id), _send,
        )
        await _cleanup_battle(user_id, battle)
        await _present_door_choice(user_id, battle, bot, _send, group_openid)


async def _cleanup_battle(user_id: str, battle: Any) -> None:
    """战斗结束清理：解除冒险态、移除战斗、记录今日完成。"""
    inv = battle.investigator
    inv.is_adventure = False
    inv.save()
    battle_manager.remove_battle(user_id)
    _mark_adventure_done(user_id)


async def _present_door_choice(
    user_id: str,
    battle: Any,
    bot: Bot,
    send: Callable,
    group_openid: str = "",
) -> None:
    """第 40 天门扉抉择按钮：战报后追加可点击的门扉选项。"""
    door = getattr(battle, "door_choice", None)
    if not door or not door.get("door_choice"):
        return
    door_states[user_id] = {"choices": door.get("choices") or [], "ended": False}
    choices = door_states[user_id]["choices"]
    rows = [
        [(c["label"], f"door:{c['key']}") for c in choices[i : i + 3]]
        for i in range(0, len(choices), 3)
    ]
    kb = build_keyboard(rows)
    msg = md_message(
        f"\n{data_loader.get_text('door.title', default='🚪 门扉抉择')}",
        bot,
        mention=user_id,
    )
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


def _resolve_door_key(door_state: dict, text: str) -> str:
    """命令文本 → 门扉选项 key（支持 key 或中文标签）。"""
    if not text:
        return ""
    for c in door_state.get("choices") or []:
        if text == c["key"] or text == c["label"]:
            return c["key"]
    return ""


def _ending_ids_snapshot(user_id: str) -> dict[str, str]:
    """表 B 结局快照（id → 最新变体）；用于战斗动作后检测新登记的结局（E05/E07）。"""
    coll = ending_repo.get_collection(user_id)
    if coll is None:
        return {}
    try:
        recs = ujson.loads(coll.endings or "[]")
    except (ValueError, TypeError):
        return {}
    out: dict[str, str] = {}
    if isinstance(recs, list):
        for r in recs:
            if isinstance(r, dict) and r.get("id"):
                out[r["id"]] = str(r.get("variant") or "")
    return out


async def _try_send_ending_card(
    bot: Bot,
    user_id: str,
    group_openid: str,
    end_id: str,
    variant: str = "",
    note: str = "",
    inv: Any = None,
    record: dict | None = None,
    send: Callable | None = None,
) -> bool:
    """结局卡片优先发送（渲染+发送都成功返回 True；失败由调用方回退 md）。

    参考 send_combat_result 模式：卡片优先、md 回退。
    """
    payload = ending_card_payload(
        end_id, variant=variant or None, note=note, inv=inv, record=record
    )
    img = await render_pic(ending_card_html(**payload))
    if img is None:
        return False
    _send = send or (lambda m: _send_to_user(bot, user_id, m, group_openid))
    return await send_pic(bot, img, _send)


async def _send_new_battle_endings(
    bot: Bot,
    user_id: str,
    group_openid: str,
    battle: Any,
    before: dict,
    after: dict,
    send: Callable,
) -> None:
    """战斗动作后新登记的终局卡片（E05 day40 战败 / E07 非 day40 死亡）。

    纯展示增强：卡片作为战报 md 之上的结局宣告；渲染失败不回退重复文本
    （结局文案已含在战斗战报 md 内），避免重复刷屏。
    """
    new_ids = sorted({eid for eid in after if eid not in before})
    door = getattr(battle, "door_choice", None) or {}
    door_end = str(door.get("ending") or "") if door else ""
    for eid in new_ids:
        if eid not in ("E05", "E07"):
            continue
        note = str(door.get("note") or "") if door_end == eid else ""
        await _try_send_ending_card(
            bot,
            user_id,
            group_openid,
            eid,
            variant=after.get(eid, ""),
            note=note,
            inv=battle.investigator,
            send=send,
        )


async def handle_door_choice(
    user_id: str,
    choice: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """门扉抉择按钮回调：判定分支并发送结局 / 重赴守门人。"""
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        await _send_to_user(
            bot,
            user_id,
            need_create_message(bot, mention=user_id),
            group_openid,
        )
        return
    inv = Investigator(inv_model)
    result = judge_door_choice(inv, choice)

    async def _send(msg) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)

    if result.get("error"):
        await _send(md_message(f"\n{result['message']}", bot, mention=user_id))
        return
    if result.get("refight"):
        door_states.pop(user_id, None)
        await _start_day40_refight(user_id, bot, _send)
        return
    door_states.pop(user_id, None)
    # 结局卡片优先（含结局编号/名称/正文/变体/达成信息）；渲染失败回退 md 结局文案
    if not await _try_send_ending_card(
        bot,
        user_id,
        group_openid,
        str(result["ending"]),
        variant=str(result.get("variant") or ""),
        note=str(result.get("note") or ""),
        inv=inv,
        send=_send,
    ):
        await _send(md_message(f"\n{result['message']}", bot, mention=user_id))


async def _start_day40_refight(
    user_id: str, bot: Bot, send: Callable
) -> None:
    """「重赴门前」：以守门人 36 重开 day40 战斗（跳过事件与每日守卫）。"""
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        await send(need_create_message(bot, mention=user_id))
        return
    inv = Investigator(inv_model)
    inv.restore_hp()
    inv.is_adventure = True
    inv.day = 40
    inv.save()
    monster = Monster("36")
    service = BattleService(inv, monster)
    inject_companion(service, inv)
    battle_manager.add_battle(user_id, service)
    service.roll_initiative()

    monster_intro = getattr(
        monster,
        "出场",
        data_loader.get_text(
            "adventure.monster_intro_default", name=monster.name
        ),
    )
    reply = (
        f"🚪 **你再次站到门前。**\n\n"
        f"{data_loader.get_text('battle.day_line', day=inv.day)}\n\n"
        f"{report_section(data_loader.get_text('battle.monster_intro_title'))}\n"
        f"{monster_intro}\n\n"
        f"{service.get_dex_compare_section()}\n\n"
        f"{service.get_status_table()}\n\n"
        f"{service.get_danger_section()}\n\n"
        f"{service.get_action_section()}"
    )
    img = await render_pic(battle_open_html(service, reply))
    if img is not None and await send_pic(bot, img, send):
        await send(send_turn(service, bot, service.get_action_section()))
        return
    await send(send_turn(service, bot, reply))


async def handle_event_locked(
    user_id: str,
    choice: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """条件未满足的事件选项：禁点提示（不消费 event_states，可另选其他选项）。"""
    await _send_to_user(
        bot,
        user_id,
        md_message(
            f"\n{data_loader.get_text('adventure.event_locked', default='条件未满足')}，无法选择此选项。",
            bot,
            mention=user_id,
        ),
        group_openid,
    )


async def handle_event_choice(
    user_id: str,
    choice: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """按钮触发奇遇事件选择。"""
    ev_state = event_states.pop(user_id, None)
    battle = battle_manager.get_battle(user_id)
    if not ev_state or not battle:
        await _send_to_user(
            bot,
            user_id,
            md_message(
                f"\n{data_loader.get_text('adventure.battle_state_error')}",
                bot,
                mention=user_id,
            ),
            group_openid,
        )
        return
    if not battle.investigator.is_survive:
        # P0-1 防线：死亡角色不能通过按钮继续事件流程，清理战场残留
        battle_manager.remove_battle(user_id)
        await _send_to_user(
            bot,
            user_id,
            md_message(
                f"\n{data_loader.get_text('adventure.player_dead')}",
                bot,
                mention=user_id,
            ),
            group_openid,
        )
        return

    event_data = ev_state["event"]
    matched = next((o for o in event_data["选项"] if o["输入"] == choice), None)

    async def _send(msg) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)

    if matched:
        event_reply, skip_battle = apply_event_choice(
            battle.investigator, user_id, matched
        )
        if skip_battle:
            # 检定成功：跳过今日战斗（图片卡片优先，失败回退 md）
            battle.investigator.is_adventure = False
            _advance_day_after_event(battle.investigator)  # day +1（day40 冻结），与胜利结算一致
            battle_manager.remove_battle(user_id)
            _mark_adventure_done(user_id)
            await send_event_skip_battle(battle, event_reply, bot, _send, _send)
            return
    else:
        event_reply = data_loader.get_text("adventure.event_default")
        skip_battle = False
    if skip_battle:
        return

    # 奇遇完成后：怪物出场 → 理智检定（+智力检定/疯狂）→ 敏捷对比 → 战斗开始
    san_desc, madness_desc, is_mad, madness_duration, san_zero, san_loss = (
        run_sanity_and_madness(battle.investigator, battle.monster)
    )
    if san_zero:
        # 出口②：SAN 归零（永久疯狂）→ 登记 E06 + 清理战场残留（P0-1 防线）
        e06_result = check_san_zero(battle.investigator)
        battle_manager.remove_battle(user_id)
        battle.investigator.is_survive = False
        battle.investigator.save()
        _mark_adventure_done(user_id)

        await send_sanity_zero(
            battle,
            battle.investigator,
            san_desc,
            san_loss,
            bot,
            _send,
            show_cg=False,
        )
        # E06 结局卡片（卡片优先，渲染失败回退 md 结局文案）
        if not await _try_send_ending_card(
            bot,
            user_id,
            group_openid,
            "E06",
            note=str(e06_result.get("note") or ""),
            inv=battle.investigator,
            send=_send,
        ):
            await _send(
                md_message(f"\n{e06_result['message']}", bot, mention=user_id)
            )
        return
    if is_mad:
        battle.set_madness(True, madness_duration)

    monster_intro = getattr(
        battle.monster,
        "出场",
        data_loader.get_text(
            "adventure.monster_intro_default", name=battle.monster.name
        ),
    )
    warning = _danger_warning(battle.investigator.day)
    event_block = f"{event_reply}\n\n{warning}" if warning else event_reply
    reply = (
        f"{event_block}\n\n"
        f"{report_section(data_loader.get_text('battle.monster_intro_title'))}\n"
        f"{monster_intro}\n\n"
        f"{san_desc}{madness_desc}\n\n"
        f"{battle.get_dex_compare_section()}\n\n"
        f"{battle.start_turn()}"
    )

    img = await render_pic(battle_open_html(battle, reply))
    if img is not None and await send_pic(bot, img, _send):
        await _send(send_turn(battle, bot, battle.get_action_section()))
        return
    await _send(send_turn(battle, bot, reply))


# --- 按钮回调注册 ---
register_button_handler("action", handle_combat_action)
register_button_handler("event", handle_event_choice)
register_button_handler("event_locked", handle_event_locked)
register_button_handler("door", handle_door_choice)
register_button_handler("resurrect", handle_resurrect_button)
register_button_handler("adventure", handle_adventure_button)
register_button_handler("scroll_adventure", handle_scroll_adventure_button)
setup_button_callback()
