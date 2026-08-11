"""战斗消息发送流：编排「卡片图片 + md 回退 + 行动按钮」的发送顺序与战斗结束引导。"""

from typing import Callable

from nonebot.adapters import Bot, Message

from ..models.player import Investigator
from ..utils.image_sender import render_pic, send_pic
from ..utils.md_format import (
    build_keyboard,
    cmd_tag,
    md_message,
    report_quote,
    report_section,
)
from .battle import BattleService
from .battle_cards import (
    battle_card_html,
    battle_open_html,
    battle_round_html,
    end_card_html,
    sanity_zero_card_html,
)
from .data_loader import data_loader


def send_turn(battle: BattleService, bot: Bot, text: str) -> Message:
    """构造战斗回合消息（MD 文本 + 行动按钮，开头 @ 战斗所属玩家）。

    按钮回调数据携带回合令牌（token），点击后旧按钮自动失效。
    战斗结束后不再附加行动按钮；角色死亡时附加「创建调查员」按钮。
    """
    msg = md_message(text, bot, mention=battle.investigator.qq)
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


async def send_end_buttons(battle: BattleService, bot: Bot, send: Callable) -> None:
    """战斗结束引导按钮：阵亡→复活/创建；胜利/逃跑→调查员信息 + 购买冒险卷。"""
    t = data_loader.get_text
    mention = battle.investigator.qq
    if battle.hp_record["inv"] <= 0:
        await send(
            md_message(
                f"\n**{t('character.resurrect_button')}**\n{cmd_tag('/复活')}\n\n"
                f"**{t('character.create_button')}**\n{cmd_tag('/创建调查员')}",
                bot,
                mention=mention,
            )
        )
        return

    kb = build_keyboard(
        [
            [
                (t("character.info_button"), "info"),
                (t("adventure.scroll_buy_button"), "buy:401"),
            ]
        ]
    )
    msg = md_message(
        f"\n**{t('character.info_button')}**\n{cmd_tag('/调查员信息')}\n\n"
        f"**{t('adventure.scroll_buy_button')}**\n{cmd_tag('/购买 401')}",
        bot,
        mention=mention,
    )
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def send_event_skip_battle(
    battle: BattleService,
    event_reply: str,
    bot: Bot,
    send: Callable,
    finish: Callable,
) -> None:
    """@description 奇遇检定成功跳过战斗：图片卡片优先，渲染/发送失败回退 md 文本。"""
    t = data_loader.get_text
    reply = f"{event_reply}\n\n{t('adventure.event_skip_battle')}"
    img = await render_pic(battle_open_html(battle, reply))
    if img is not None and await send_pic(bot, img, send):
        return
    await finish(md_message(reply, bot, mention=battle.investigator.qq))


async def send_combat_result(
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
            img = await render_pic(battle_round_html(battle, result))
            if img is not None and not await send_pic(bot, img, send):
                combat_text = "\n" + "\n\n".join(str(x) for x in result[:-1] if x)
                await send(
                    md_message(
                        combat_text, bot, mention=battle.investigator.qq
                    )
                )

        # 2. 结算卡片 + 结束引导按钮
        img = await render_pic(end_card_html(battle))
        if img is not None and await send_pic(bot, img, send):
            await send_end_buttons(battle, bot, send)
            return

        # 结算图片失败 → md 回退
        if result[-1]:
            await send(
                md_message(
                    str(result[-1]), bot, mention=battle.investigator.qq
                )
            )
        await send_end_buttons(battle, bot, send)
        return

    # 普通回合：战报卡片 + 行动按钮消息（仅行动抉择）
    img = await render_pic(battle_round_html(battle, result))
    if img is not None and await send_pic(bot, img, send):
        await send(send_turn(battle, bot, battle.get_action_section()))
        return
    response = "\n" + "\n\n".join(str(x) for x in result if x)
    await send(send_turn(battle, bot, response))


async def send_sanity_zero(
    service: BattleService,
    inv: Investigator,
    san_desc: str,
    san_loss: int,
    bot: Bot,
    send: Callable,
    show_cg: bool = True,
) -> None:
    """SAN 归零（永久疯狂）流程：入场CG → 遭遇登场+鉴定 → 心智崩塌结算 → 复活/创建引导。

    每张图片渲染失败独立回退 md；奇遇路径（show_cg=False）跳过入场CG（事件CG已展示）。
    """
    t = data_loader.get_text
    cg_shown = False
    if show_cg:
        img = await render_pic(
            battle_card_html(service, data_loader.get_event(inv.day))
        )
        if img is not None:
            cg_shown = await send_pic(bot, img, send)

    monster_intro = getattr(
        service.monster,
        "出场",
        t("adventure.monster_intro_default", name=service.monster.name),
    )
    anomaly = ""
    if not cg_shown and service.environment:
        anomaly = report_quote([service.environment.get("描述", "")])
    encounter = (f"{anomaly}\n\n" if anomaly else "") + (
        f"{report_section(t('battle.monster_intro_title'))}\n"
        f"{monster_intro}\n\n"
        f"{san_desc}"
    )
    img = await render_pic(battle_open_html(service, encounter))
    if img is None or not await send_pic(bot, img, send):
        await send(md_message(encounter, bot, mention=inv.qq))

    img = await render_pic(sanity_zero_card_html(inv, san_loss))
    if img is None or not await send_pic(bot, img, send):
        await send(
            md_message(
                f"{san_desc}\n\n{data_loader.get_text('adventure.sanity_zero')}",
                bot,
                mention=inv.qq,
            )
        )

    await send(
        md_message(
            f"\n**{t('character.resurrect_button')}**\n{cmd_tag('/复活')}\n\n"
            f"**{t('character.create_button')}**\n{cmd_tag('/创建调查员')}",
            bot,
            mention=inv.qq,
        )
    )
