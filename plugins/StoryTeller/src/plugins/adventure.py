from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.exception import FinishedException
from nonebot.params import CommandArg
from loguru import logger
import random
from pathlib import Path
from typing import Any, Callable

from ..services.battle import BattleService
from ..models.player import Investigator, investigator_repo
from ..models.monster import Monster, monster_repo
from ..services.data_loader import data_loader
from ..services.sanity import perform_sanity_check
from ..utils.active_battles import battle_manager
from ..utils.buttons import (
    _send_to_user,
    register_button_handler,
    setup_button_callback,
)
from ..utils.md_format import (
    build_keyboard,
    cmd_tag,
    md_message,
    md_to_html,
    report_check_table,
    report_quote,
    report_section,
)
from ..services.dice_roller import (
    get_success_description,
    get_success_icon,
    roll_dice,
)
from database.db import add_gold

# State for active random events (user_id -> event context)
_event_states: dict[str, dict] = {}

# 复活道具 ID（预留接口：在 goods_data.json 中加入该 ID 商品后自动生效）
RESURRECT_ITEM_ID = "501"

_CARD_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "battle_card.html"
_END_CARD_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "end_card.html"
_OPEN_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "battle_open.html"
_ROUND_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "battle_round.html"


def _do_resurrect(user_id: str) -> str:
    """使用复活道具（命令/按钮共用）。

    预留接口：背包中拥有 RESURRECT_ITEM_ID 时消耗 1 个并复活；
    无道具或未死亡时返回对应提示。
    """
    t = data_loader.get_text
    inv = Investigator.load(user_id)
    if inv.is_survive:
        return t("adventure.resurrect_alive")

    equipments, _ = inv.get_equipments()
    if RESURRECT_ITEM_ID not in equipments:
        return t("adventure.resurrect_none")

    investigator_repo.remove_item_from_inventory(user_id, RESURRECT_ITEM_ID, 1)
    inv.is_survive = True
    inv.restore_hp()
    max_san = inv.get_skill("意志") or 0
    inv.set_skill("san", max(0, max_san))
    inv.save()
    return t("adventure.resurrect_ok")


async def _render_pic(html: str):
    """渲染 HTML 卡片为图片 BytesIO；失败返回 None。"""
    try:
        from util.html2pic import html_to_pic

        return await html_to_pic(html, selector=".card", wait=0.8)
    except Exception:
        return None


async def _send_pic(bot: Bot, img: Any, send: Callable) -> bool:
    """按适配器发送图片消息；成功返回 True，失败返回 False（调用方回退 md）。"""
    bt = getattr(bot, "type", "")
    try:
        if bt == "QQ":
            from nonebot.adapters.qq.message import Message as QQMessage
            from nonebot.adapters.qq.message import MessageSegment

            await send(QQMessage(MessageSegment.file_image(img)))
            return True
        if bt == "Console":
            from nonebot.adapters.console.message import ConsoleMessage, Image

            await send(ConsoleMessage(Image(img)))
            return True
        if bt == "Mirai":
            from nonebot.adapters.mirai.message import Image, MessageChain

            await send(MessageChain(Image(img)))
            return True
    except Exception as e:  # noqa: BLE001 - 图片发送失败应回退 md
        logger.debug(f"send image failed on {bt}: {e}")
    return False


def _battle_title(service: BattleService) -> str:
    """战报卡片标题（环境名，无环境用默认）。"""
    t = data_loader.get_text
    env_name = service.environment.get("name", "") if service.environment else ""
    title = (
        t("battle.report_title", env=env_name)
        if env_name
        else t("battle.report_title_default")
    )
    return title.replace("# 🕯️ ", "").replace(" · 实时战报", "")


def _round_status_html(service: BattleService) -> str:
    """回合卡片状态条（HP/SAN/弹药 chips）。"""
    inv = service.investigator
    max_san = inv.get_skill("意志") or inv.get_skill("san", 0)
    chips = [
        f'<div class="chip"><span class="k">🧑‍🎤 {inv.name}</span> '
        f'<span class="v">HP {service.hp_record["inv"]}/{inv.get_max_hp()}</span>'
        f"</div>",
        f'<div class="chip"><span class="k">🧠 SAN</span> '
        f'<span class="v">{inv.get_skill("san", 0)}/{max_san}</span></div>',
        f'<div class="chip"><span class="k">👾 {service.monster.名字}</span> '
        f'<span class="v">HP {service.hp_record["mon"]}/{service.monster.max_hp}</span>'
        f"</div>",
    ]
    if service.gun:
        chips.append(
            f'<div class="chip"><span class="k">🔫 弹药</span> '
            f'<span class="v">{service.bullet}/{service.max_bullet}</span></div>'
        )
    return "".join(chips)


def _battle_round_html(service: BattleService, result: tuple) -> str:
    """回合战报卡片 HTML（检定/交锋/疯狂等 md 渲染进卡片，末段回合提示作脚注）。"""
    t = data_loader.get_text
    body = md_to_html("\n\n".join(str(x) for x in result[:-1] if x))
    owner_key = (
        "battle.your_turn" if service.current_turn == "inv" else "battle.monster_turn"
    )
    hint = t("battle.turn_line", owner=t(owner_key)).replace("**", "")
    return (
        _ROUND_TEMPLATE.read_text(encoding="utf-8")
        .replace("__ROUND__", str(service.get_turn_token()))
        .replace("__TITLE__", t(owner_key))
        .replace("__BODY__", body)
        .replace("__STATUS__", _round_status_html(service))
        .replace("__HINT__", hint)
    )


def _battle_open_html(service: BattleService, body_md: str) -> str:
    """开场战报卡片 HTML（md 渲染进卡片，首个 # 标题行由印章区承担）。"""
    parts = body_md.split("\n\n", 1)
    if parts[0].strip().startswith("# "):
        body_md = parts[1] if len(parts) > 1 else ""
    return (
        _OPEN_TEMPLATE.read_text(encoding="utf-8")
        .replace("__TITLE__", _battle_title(service))
        .replace("__DAY__", str(service.investigator.day))
        .replace("__BODY__", md_to_html(body_md))
    )


def _fmt_bonus(v) -> str:
    """数值显示带符号（如 -20 / +10），骰子表达式原样（如 +1d4）。"""
    if isinstance(v, int):
        return f"{v:+d}"
    return str(v)


def _env_effects_lines(env: dict) -> list[str]:
    """环境效果摘要行（如「🧑‍🎤 射击 -20」「👾 敏捷 -10」）。"""
    if not env:
        return []
    lines = []
    player = env.get("玩家", {})
    monster = env.get("怪物", {})
    if player:
        lines.append(
            "🧑‍🎤 " + " ｜ ".join(f"{k} {_fmt_bonus(v)}" for k, v in player.items())
        )
    if monster:
        lines.append(
            "👾 " + " ｜ ".join(f"{k} {_fmt_bonus(v)}" for k, v in monster.items())
        )
    return lines


def _battle_card_html(
    service: BattleService,
    day_event: str = "",
    event_desc: str = "",
) -> str:
    """入场 CG 卡片 HTML（环境氛围图，怪物出场在 md 中展示）。

    event_desc 非空时（奇遇场景）：异象区 = 环境描述 + 奇遇描述。
    """
    t = data_loader.get_text
    env_name = service.environment.get("name", "")
    title = (
        t("battle.report_title", env=env_name)
        if env_name
        else t("battle.report_title_default")
    )
    title = title.replace("# 🕯️ ", "").replace(" · 实时战报", "")
    inv = service.investigator

    anomaly_lines = []
    env_desc = service.environment.get("描述", "") if service.environment else ""
    if env_desc:
        anomaly_lines.append(f"<p>{env_desc}</p>")
    if event_desc:
        anomaly_lines.append(f"<p>{event_desc}</p>")
    elif day_event:
        anomaly_lines.append(f"<p>{day_event}</p>")

    # 环境修正区块
    effects = ""
    env_lines = _env_effects_lines(service.environment)
    if env_lines:
        t2 = data_loader.get_text
        rows = "".join(f'<div class="e-row">{l}</div>' for l in env_lines)
        effects = (
            f'<div class="effects"><div class="e-title">⚙️ {t2("adventure.env_effect_title")}</div>'
            f"{rows}</div>"
        )

    html = _CARD_TEMPLATE.read_text(encoding="utf-8")
    return (
        html.replace("__TITLE__", title)
        .replace("__DAY__", str(inv.day))
        .replace("__ANOMALY__", "\n".join(anomaly_lines))
        .replace("__EFFECTS__", effects)
    )


def _end_card_html(service: BattleService) -> str:
    """结算卡片 HTML。"""
    t = data_loader.get_text
    d = service.get_end_card_data()
    if d["fled"]:
        icon, cls, title, hint = (
            "🏃",
            "win",
            t("battle.fled_title"),
            t("battle.fled_hint"),
        )
        ending = t("battle.fled_ending")
    elif d["victory"]:
        icon, cls, title, hint = (
            "🏆",
            "win",
            t("battle.victory_title").replace("## ", ""),
            "明日可继续冒险",
        )
        ending = d["ending"]
    else:
        icon, cls, title, hint = (
            "💀",
            "dead",
            t("battle.death_text", name=service.player_name).replace("## ", ""),
            "重新创建调查员继续冒险",
        )
        ending = d["ending"]

    # 胜利明细（侦查检定/战利品/成长）渲染进卡片
    detail = ""
    ext = service.end_card_ext
    if d["victory"] and ext:
        search = ext.get("search", {})
        level = search.get("level", 0)
        icon_s = get_success_icon(level)
        desc = get_success_description(level)
        detail = (
            f'<div class="detail">'
            f'<div class="rowline"><span class="k">🔍 侦查检定</span>'
            f'<span class="v">{icon_s} {desc}（{search.get("dice", "?")}/{search.get("target", "?")}）</span></div>'
            f'<div class="rowline"><span class="k">🎁 战利品</span>'
            f'<span class="v">{ext.get("bonus", "")}</span></div>'
        )
        growth = ext.get("growth", [])
        if growth:
            g = "".join(x.strip() for x in growth)
            detail += (
                f'<div class="rowline"><span class="k">📈 成长鉴定：</span>'
                f'<span class="v">{g}</span></div>'
            )
        detail += "</div>"

    html = _END_CARD_TEMPLATE.read_text(encoding="utf-8")
    return (
        html.replace("__ICON__", icon)
        .replace("__CLS__", cls)
        .replace("__TITLE__", title)
        .replace("__ENDING__", ending)
        .replace("__HP__", f"{d['hp']}/{d['max_hp']}")
        .replace("__SAN__", f"{d['san']}/{d['max_san']}")
        .replace("__DAY__", str(d["day"]))
        .replace("__DETAIL__", detail)
        .replace("__HINT__", hint)
    )


def _send_turn(battle: BattleService, bot: Bot, text: str) -> Message:
    """构造战斗回合消息（MD 文本 + 行动按钮）。

    按钮回调数据携带回合令牌（token），点击后旧按钮自动失效。
    战斗结束后不再附加行动按钮；角色死亡时附加「创建调查员」按钮。
    """
    msg = md_message(text, bot)
    if isinstance(msg, str):
        return msg
    if battle.fight_is_over():
        if battle.hp_record["inv"] <= 0:
            kb = build_keyboard(
                [
                    [
                        (
                            data_loader.get_text("character.resurrect_button"),
                            "resurrect",
                        ),
                        (data_loader.get_text("character.create_button"), "create"),
                    ]
                ]
            )
            if kb is not None:
                msg.append(kb)
        return msg
    actions = battle.get_available_actions_for_turn()
    if actions:
        token = battle.get_turn_token()
        rows = [[(a, f"action:{a}:{token}") for a in actions[:4]]]
        if len(actions) > 4:
            rows.append([(a, f"action:{a}:{token}") for a in actions[4:]])
        kb = build_keyboard(rows)
        if kb is not None:
            msg.append(kb)
    return msg


async def _send_combat_result(
    battle: BattleService,
    bot: Bot,
    result: tuple,
    send: Callable,
) -> None:
    """发送战斗回合结果。

    全平台优先渲染 HTML 图片卡片（回合战报 + 结算卡片），失败回退 md 文本；
    卡片后附行动按钮消息（QQ 键盘 / 纯文本指令）。
    """
    if battle.fight_is_over():
        # 1. 本回合战报卡片（不含结束文本；逃跑等单段结果无战报则跳过）
        if any(x for x in result[:-1]):
            img = await _render_pic(_battle_round_html(battle, result))
            if img is not None:
                if not await _send_pic(bot, img, send):
                    combat_text = "\n" + "\n\n".join(str(x) for x in result[:-1] if x)
                    await send(md_message(combat_text, bot))

        # 2. 结算卡片
        img = await _render_pic(_end_card_html(battle))
        if img is not None and await _send_pic(bot, img, send):
            if battle.hp_record["inv"] <= 0:
                t = data_loader.get_text
                await send(
                    md_message(
                        f"\n**{t('character.resurrect_button')}**\n{cmd_tag('/复活')}\n\n"
                        f"**{t('character.create_button')}**\n{cmd_tag('/创建调查员')}",
                        bot,
                    )
                )
            return

        # 结算图片失败 → md 回退
        if result[-1]:
            await send(md_message(str(result[-1]), bot))
        if battle.hp_record["inv"] <= 0:
            t = data_loader.get_text
            await send(
                md_message(
                    f"\n**{t('character.resurrect_button')}**\n{cmd_tag('/复活')}\n\n"
                    f"**{t('character.create_button')}**\n{cmd_tag('/创建调查员')}",
                    bot,
                )
            )
        return

    # 普通回合：战报卡片 + 行动按钮消息
    img = await _render_pic(_battle_round_html(battle, result))
    if img is not None and await _send_pic(bot, img, send):
        await send(_send_turn(battle, bot, str(result[-1])))
        return
    await send(_send_turn(battle, bot, "\n" + "\n\n".join([str(x) for x in result if x])))


adventure_cmd = on_command(
    "今日冒险", aliases={"daily_adventure", "开始冒险"}, priority=10, block=True
)


@adventure_cmd.handle()
async def handle_adventure(event: Event, bot: Bot):
    user_id = event.get_user_id()
    try:
        inv = Investigator.load(user_id)
        if not inv.is_survive:
            await adventure_cmd.finish(
                md_message(f"\n{data_loader.get_text('adventure.player_dead')}", bot)
            )
        if battle_manager.get_battle(user_id):
            await adventure_cmd.finish(
                md_message(f"\n{data_loader.get_text('adventure.in_battle')}", bot)
            )

        inv.restore_hp()
        inv.save()

        monster_id = monster_repo.find_random_id_for_day(inv.day)
        if not monster_id:
            await adventure_cmd.finish(
                md_message(
                    f"\n{data_loader.get_text('adventure.no_monster_config')}", bot
                )
            )
        monster = Monster(monster_id)
        monster_intro = getattr(
            monster,
            "出场",
            data_loader.get_text("adventure.monster_intro_default", name=monster.name),
        )
        day_event = data_loader.get_event(inv.day)

        # --- Environment ---
        env = {}
        env_desc = ""
        if data_loader.environment_data:
            env_key = random.choice(list(data_loader.environment_data.keys()))
            env = data_loader.environment_data[env_key].copy()
            env["name"] = env_key
            env_desc = f"【{env_key}】{env.get('描述', '')}"

        # --- Battle service ---
        service = BattleService(inv, monster)
        if env:
            service.set_environment(env)

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
        anomaly = f"{report_section(anomaly_title)}\n" f"{report_quote(anomaly_lines)}"

        header = f"\n{env_desc}\n{day_event}" if env_desc else f"\n{day_event}"

        # --- Random event (40% chance)：奇遇在理智检定之前展示 ---
        if random.random() < 0.4 and data_loader.event_data:
            event_key = random.choice(list(data_loader.event_data.keys()))
            event_data = data_loader.event_data[event_key]

            battle_manager.add_battle(user_id, service)
            _event_states[user_id] = {
                "event": event_data,
            }

            # 事件选项按钮
            event_kb = build_keyboard(
                [[(opt["输入"], f"event:{opt['输入']}") for opt in event_data["选项"]]]
            )

            # 奇遇 CG 卡片（全平台）+ 选项按钮；图片失败回退文本
            card_html = _battle_card_html(service, event_desc=event_data["描述"])
            img = await _render_pic(card_html)
            if img is not None and await _send_pic(bot, img, adventure_cmd.send):
                options = "\n".join(
                    f" {data_loader.get_text('adventure.event_choice', input=opt['输入'])}"
                    for opt in event_data["选项"]
                )
                event_msg = md_message(
                    f"\n**{data_loader.get_text('adventure.event_title')}**\n\n"
                    f"{options}",
                    bot,
                )
            else:
                event_text = (
                    f"\n\n{data_loader.get_text('adventure.event_title')}\n"
                    f"{report_quote([event_data['描述']])}\n"
                )
                for opt in event_data["选项"]:
                    event_text += f" {data_loader.get_text('adventure.event_choice', input=opt['输入'])}\n"
                event_msg = md_message(f"{header}{event_text}", bot)
            if event_kb is not None and not isinstance(event_msg, str):
                event_msg.append(event_kb)
            await adventure_cmd.send(event_msg)
            return

        # --- 无奇遇：理智检定 → 战斗开始 ---
        san_desc, madness_desc, is_mad, madness_duration, san_zero = (
            _run_sanity_and_madness(inv, monster)
        )
        if san_zero:
            inv.is_survive = False
            inv.save()
            await adventure_cmd.finish(
                md_message(
                    f"{san_desc}\n\n"
                    f"{data_loader.get_text('adventure.sanity_zero')}",
                    bot,
                )
            )
        if is_mad:
            service.set_madness(True, madness_duration)

        battle_manager.add_battle(user_id, service)
        service.roll_initiative()

        # 环境修正小节
        env_effects = ""
        env_lines = _env_effects_lines(env)
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
        img = await _render_pic(_battle_card_html(service, day_event))
        if img is not None:
            cg_shown = await _send_pic(bot, img, adventure_cmd.send)

        # 开场战报卡片：CG 已展示异象/环境修正，战斗卡片不再重复
        battle_reply = (
            f"{report_section(data_loader.get_text('battle.monster_intro_title'))}\n"
            f"{monster_intro}\n\n"
            f"{san_desc}{madness_desc}\n\n"
            f"{service.get_dex_compare_section()}\n\n"
            f"{service.get_status_table()}\n\n"
            f"{service.get_danger_section()}\n\n"
            f"{service.get_action_section()}"
        )
        img = await _render_pic(_battle_open_html(service, battle_reply))
        if img is not None and await _send_pic(bot, img, adventure_cmd.send):
            await adventure_cmd.send(
                _send_turn(service, bot, service._get_next_turn_prompt())
            )
            return

        # 图片失败回退 md（含完整开场内容）
        if not cg_shown:
            await adventure_cmd.send(_send_turn(service, bot, reply))
            return
        await adventure_cmd.send(_send_turn(service, bot, battle_reply))

    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"Error starting adventure for {user_id}: {e}")
        await adventure_cmd.finish(
            md_message(f"\n{data_loader.get_text('adventure.start_error')}", bot)
        )


resurrect_cmd = on_command(
    "复活", aliases={"use_resurrect", "复活道具"}, priority=10, block=True
)


@resurrect_cmd.handle()
async def handle_resurrect(event: Event, bot: Bot) -> None:
    """使用复活道具（死亡后）。"""
    user_id = event.get_user_id()
    await resurrect_cmd.finish(md_message(f"\n{_do_resurrect(user_id)}", bot))


async def handle_resurrect_button(
    user_id: str,
    payload: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """死亡消息「使用复活道具」按钮回调。"""
    await _send_to_user(
        bot, user_id, md_message(f"\n{_do_resurrect(user_id)}", bot), group_openid
    )


def _run_sanity_and_madness(
    inv: Investigator, monster: Monster
) -> tuple[str, str, bool, int, bool]:
    """理智检定 + （理智损失≥5 时）智力检定。

    返回 (san_desc, madness_desc, is_mad, madness_duration, san_zero)。
    san_zero 为 True 表示 SAN 归零（永久疯狂），由调用方结束游戏。
    """
    san_passed, san_desc, san_loss = perform_sanity_check(inv, monster)

    madness_desc = ""
    is_mad = False
    madness_duration = 5
    if not san_passed:
        current_san = inv.get_skill("san")
        if current_san <= 0:
            inv.save()
            return san_desc, madness_desc, is_mad, madness_duration, True

        # 理智损失 ≥5 才进行智力检定：成功则陷入临时疯狂
        if san_loss >= 5:
            int_val = inv.get_skill("智力")
            _, int_check_res = roll_dice("1d100")
            if int_check_res <= int_val:
                _, madness_duration = roll_dice("1d10")
                is_mad = True
                level_icon = get_success_icon(1)
                level_text = data_loader.get_text("dice.success")
                quote = data_loader.get_text(
                    "adventure.int_check_success",
                    duration=madness_duration,
                )
            else:
                level_icon = get_success_icon(0)
                level_text = data_loader.get_text("dice.failure")
                quote = data_loader.get_text("adventure.int_check_fail")

            row = data_loader.get_text(
                "adventure.int_check_row",
                icon=data_loader.get_text("report.icon_brains"),
                dice=int_check_res,
                target=int_val,
                result=data_loader.get_text(
                    "report.check_result",
                    icon=level_icon,
                    level=level_text,
                ),
            )
            madness_desc = (
                f"\n{report_section(data_loader.get_text('adventure.int_check_title'))}\n"
                f"{report_check_table([row])}\n"
                f"{quote}"
            )
    inv.save()  # 持久化 SAN 扣减
    return san_desc, madness_desc, is_mad, madness_duration, False


def _apply_event_effects(inv: Investigator, user_id: str, effects: dict) -> str:
    """应用奇遇事件效果并返回变更摘要（如「🧠 SAN -10 ｜ 💪 意志 +5」）。"""
    from ..models.item import Equipment as _Equipment

    changes: list[str] = []
    if "san" in effects:
        san = inv.get_skill("san") + effects["san"]
        inv.set_skill("san", max(0, san))
        changes.append(f"🧠 SAN {effects['san']:+d}")
    if "hp" in effects:
        inv.hp = max(1, inv.hp + effects["hp"])
        changes.append(f"❤️ HP {effects['hp']:+d}")
    if "金币" in effects:
        add_gold(user_id, effects["金币"])
        changes.append(f"🪙 金币 {effects['金币']:+d}")
    if "物品" in effects:
        item = _Equipment(effects["物品"])
        inv.add_item_to_inventory(effects["物品"], 1)
        changes.append(f"🎒 获得 {item.name}")
    if "技能" in effects:
        for sk_name, sk_delta in effects["技能"].items():
            sk_val = inv.get_skill(sk_name, 0) + sk_delta
            inv.set_skill(sk_name, max(0, sk_val))
            changes.append(f"💪 {sk_name} {sk_delta:+d}")
    inv.save()
    return " ｜ ".join(changes)


def _event_reply_with_effects(event_reply: str, summary: str) -> str:
    """事件回复 + 效果摘要小节。"""
    t = data_loader.get_text
    if not summary:
        return event_reply
    return (
        f"{event_reply}\n\n" f"**{t('adventure.event_effect_title')}**\n" f"> {summary}"
    )


combat_cmd = on_command("行动", aliases={"combat_action"}, priority=5, block=True)


@combat_cmd.handle()
async def handle_combat(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    action = msg.extract_plain_text().strip()

    # Check for pending event choice first
    ev_state = _event_states.pop(user_id, None)
    if ev_state:
        event_data = ev_state["event"]
        battle = battle_manager.get_battle(user_id)
        if not battle:
            await combat_cmd.finish(
                md_message(
                    f"\n{data_loader.get_text('adventure.battle_state_error')}", bot
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
            effects = matched.get("效果", {})
            event_reply = matched["回复"]
            inv = battle.investigator
            summary = _apply_event_effects(inv, user_id, effects)
            event_reply = _event_reply_with_effects(event_reply, summary)
        else:
            event_reply = data_loader.get_text("adventure.event_default")

        # 奇遇完成后：怪物出场 → 理智检定（+智力检定/疯狂）→ 敏捷对比 → 战斗开始
        san_desc, madness_desc, is_mad, madness_duration, san_zero = (
            _run_sanity_and_madness(battle.investigator, battle.monster)
        )
        if san_zero:
            battle.investigator.is_survive = False
            battle.investigator.save()
            await combat_cmd.finish(
                md_message(
                    f"{san_desc}\n\n"
                    f"{data_loader.get_text('adventure.sanity_zero')}",
                    bot,
                )
            )
        if is_mad:
            battle.set_madness(True, madness_duration)

        monster_intro = getattr(
            battle.monster,
            "出场",
            data_loader.get_text(
                "adventure.monster_intro_default", name=battle.monster.name
            ),
        )
        reply = (
            f"{event_reply}\n\n"
            f"{report_section(data_loader.get_text('battle.monster_intro_title'))}\n"
            f"{monster_intro}\n\n"
            f"{san_desc}{madness_desc}\n\n"
            f"{battle.get_dex_compare_section()}\n\n"
            f"{battle.start_turn()}"
        )
        img = await _render_pic(_battle_open_html(battle, reply))
        if img is not None and await _send_pic(bot, img, combat_cmd.send):
            await combat_cmd.send(
                _send_turn(battle, bot, battle._get_next_turn_prompt())
            )
            return
        await combat_cmd.send(_send_turn(battle, bot, reply))
        return

    # Normal combat flow
    battle = battle_manager.get_battle(user_id)
    if not battle:
        await combat_cmd.finish(
            md_message(f"\n{data_loader.get_text('adventure.no_active_battle')}", bot)
        )

    if not action:
        await combat_cmd.finish(
            md_message(f"\n{data_loader.get_text('adventure.need_action')}", bot)
        )

    if action.startswith(data_loader.get_text("adventure.use_item")):
        item_id = action.split()[1]
        ok, msg_text = investigator_repo.equip_item(user_id, item_id)
        if ok:
            battle.investigator.update_equipment()
            if battle.current_turn == "inv":
                battle._update_gun_status()
            prompt = battle._get_next_turn_prompt()
            await combat_cmd.send(_send_turn(battle, bot, f"{msg_text}\n\n{prompt}"))
        else:
            await combat_cmd.send(md_message(msg_text, bot))
        return

    result = battle.execute_action(action)
    await _send_combat_result(battle, bot, result, send=combat_cmd.send)

    if battle.fight_is_over():
        inv = battle.investigator
        inv.is_adventure = False
        inv.save()
        battle_manager.remove_battle(user_id)


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
            md_message(f"\n{data_loader.get_text('adventure.no_active_battle')}", bot),
            group_openid,
        )
        return

    # 旧按钮点击（令牌不匹配）直接忽略
    if token is not None and token != battle.get_turn_token():
        logger.debug(
            f"Stale button click ignored: token={token}, current={battle.get_turn_token()}"
        )
        return

    result = battle.execute_action(action)

    async def _send(msg):
        await _send_to_user(bot, user_id, msg, group_openid)

    await _send_combat_result(battle, bot, result, send=_send)

    if battle.fight_is_over():
        inv = battle.investigator
        inv.is_adventure = False
        inv.save()
        battle_manager.remove_battle(user_id)


async def handle_event_choice(
    user_id: str,
    choice: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """按钮触发奇遇事件选择。"""
    ev_state = _event_states.pop(user_id, None)
    battle = battle_manager.get_battle(user_id)
    if not ev_state or not battle:
        await _send_to_user(
            bot,
            user_id,
            md_message(
                f"\n{data_loader.get_text('adventure.battle_state_error')}", bot
            ),
            group_openid,
        )
        return

    event_data = ev_state["event"]
    matched = next((o for o in event_data["选项"] if o["输入"] == choice), None)

    if matched:
        effects = matched.get("效果", {})
        event_reply = matched["回复"]
        inv = battle.investigator
        summary = _apply_event_effects(inv, user_id, effects)
        event_reply = _event_reply_with_effects(event_reply, summary)
    else:
        event_reply = data_loader.get_text("adventure.event_default")

    # 奇遇完成后：怪物出场 → 理智检定（+智力检定/疯狂）→ 敏捷对比 → 战斗开始
    san_desc, madness_desc, is_mad, madness_duration, san_zero = (
        _run_sanity_and_madness(battle.investigator, battle.monster)
    )
    if san_zero:
        battle.investigator.is_survive = False
        battle.investigator.save()
        await _send_to_user(
            bot,
            user_id,
            md_message(
                f"{san_desc}\n\n" f"{data_loader.get_text('adventure.sanity_zero')}",
                bot,
            ),
            group_openid,
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
    reply = (
        f"{event_reply}\n\n"
        f"{report_section(data_loader.get_text('battle.monster_intro_title'))}\n"
        f"{monster_intro}\n\n"
        f"{san_desc}{madness_desc}\n\n"
        f"{battle.get_dex_compare_section()}\n\n"
        f"{battle.start_turn()}"
    )

    async def _send(msg: Any) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)

    img = await _render_pic(_battle_open_html(battle, reply))
    if img is not None and await _send_pic(bot, img, _send):
        await _send(_send_turn(battle, bot, battle._get_next_turn_prompt()))
        return
    await _send(_send_turn(battle, bot, reply))


# --- 按钮回调注册 ---
register_button_handler("action", handle_combat_action)
register_button_handler("event", handle_event_choice)
register_button_handler("resurrect", handle_resurrect_button)
setup_button_callback()
