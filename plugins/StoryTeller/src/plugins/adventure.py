from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.exception import FinishedException
from nonebot.params import CommandArg
from loguru import logger
import random
from typing import Any

from ..services.battle import BattleService
from ..models.player import Investigator, investigator_repo
from ..models.monster import Monster, monster_repo
from ..services.data_loader import data_loader
from ..utils.active_battles import battle_manager
from ..utils.md_format import (
    build_keyboard,
    md_message,
    report_check_table,
    report_quote,
    report_section,
)
from ..services.dice_roller import get_success_icon, roll_dice
from database.db import add_gold

# State for active random events (user_id -> event context)
_event_states: dict[str, dict] = {}


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
                [[(data_loader.get_text("character.create_button"), "create")]]
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

adventure_cmd = on_command("今日冒险", aliases={"daily_adventure", "开始冒险"}, priority=10, block=True)

@adventure_cmd.handle()
async def handle_adventure(event: Event, bot: Bot):
    user_id = event.get_user_id()
    try:
        inv = Investigator.load(user_id)
        if not inv.is_survive:
            await adventure_cmd.finish(md_message(f"\n{data_loader.get_text('adventure.player_dead')}", bot))
        if battle_manager.get_battle(user_id):
            await adventure_cmd.finish(md_message(f"\n{data_loader.get_text('adventure.in_battle')}", bot))

        inv.restore_hp()
        inv.save()

        monster_id = monster_repo.find_random_id_for_day(inv.day)
        if not monster_id:
            await adventure_cmd.finish(md_message(f"\n{data_loader.get_text('adventure.no_monster_config')}", bot))
        monster = Monster(monster_id)
        monster_intro = getattr(monster, "出场", data_loader.get_text("adventure.monster_intro_default", name=monster.name))
        day_event = data_loader.get_event(inv.day)

        # --- Environment ---
        env = {}
        env_desc = ""
        if data_loader.environment_data:
            env_key = random.choice(list(data_loader.environment_data.keys()))
            env = data_loader.environment_data[env_key].copy()
            env["name"] = env_key
            env_desc = f"【{env_key}】{env.get('描述', '')}"

        # --- Sanity check ---
        from ..services.sanity import perform_sanity_check
        san_passed, san_desc, _ = perform_sanity_check(inv, monster)

        madness_desc = ""
        is_mad = False
        madness_duration = 5
        if not san_passed:
            current_san = inv.get_skill("san")
            if current_san <= 0:
                inv.is_survive = False
                inv.save()
                await adventure_cmd.finish(
                    md_message(
                        f"{san_desc}\n\n"
                        f"{data_loader.get_text('adventure.sanity_zero')}",
                        bot,
                    )
                )

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

        # --- Battle service ---
        service = BattleService(inv, monster)
        if env:
            service.set_environment(env)
        if is_mad:
            service.set_madness(True, madness_duration)

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
        anomaly = (
            f"{report_section(anomaly_title)}\n"
            f"{report_quote(anomaly_lines)}"
        )

        header = f"\n{env_desc}\n{day_event}" if env_desc else f"\n{day_event}"

        # --- Random event (40% chance) ---
        if random.random() < 0.4 and data_loader.event_data:
            event_key = random.choice(list(data_loader.event_data.keys()))
            event_data = data_loader.event_data[event_key]
            event_text = (
                f"\n\n{data_loader.get_text('adventure.event_title')}\n"
                f"{report_quote([event_data['描述']])}\n"
            )
            for opt in event_data["选项"]:
                event_text += f" {data_loader.get_text('adventure.event_choice', input=opt['输入'])}\n"

            battle_manager.add_battle(user_id, service)
            _event_states[user_id] = {
                "event": event_data,
            }

            # 事件选项按钮
            event_kb = build_keyboard(
                [[(opt["输入"], f"event:{opt['输入']}") for opt in event_data["选项"]]]
            )
            event_msg = md_message(
                f"{header}{event_text}\n"
                f"{monster_intro}\n"
                f"{san_desc}{madness_desc}",
                bot,
            )
            if event_kb is not None and not isinstance(event_msg, str):
                event_msg.append(event_kb)
            await adventure_cmd.send(event_msg)
            return
        battle_manager.add_battle(user_id, service)
        service.roll_initiative()
        reply = (
            f"{title}\n\n"
            f"{data_loader.get_text('battle.day_line', day=inv.day)}\n\n"
            f"{service.get_status_table()}\n\n"
            f"{data_loader.get_text('report.rule')}\n\n"
            f"{anomaly}\n\n"
            f"{san_desc}{madness_desc}\n\n"
            f"{service.get_danger_section()}\n\n"
            f"{service.get_action_section()}"
        )
        await adventure_cmd.send(_send_turn(service, bot, reply))

    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"Error starting adventure for {user_id}: {e}")
        await adventure_cmd.finish(md_message(f"\n{data_loader.get_text('adventure.start_error')}", bot))

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
            await combat_cmd.finish(md_message(f"\n{data_loader.get_text('adventure.battle_state_error')}", bot))

        # Strip /行动 prefix
        choice = action.removeprefix("/行动 ").removeprefix("/行动").strip()
        matched = next(
            (o for o in event_data["选项"] if o["输入"] == choice), None
        ) if choice else None

        if not action:
            matched = None  # No choice typed

        if matched:
            effects = matched.get("效果", {})
            event_reply = matched["回复"]
            inv = battle.investigator
            if "san" in effects:
                san = inv.get_skill("san") + effects["san"]
                inv.set_skill("san", max(0, san))
            if "hp" in effects:
                inv.hp = max(1, inv.hp + effects["hp"])
            if "金币" in effects:
                add_gold(user_id, effects["金币"])
            if "物品" in effects:
                inv.add_item_to_inventory(effects["物品"], 1)
            if "技能" in effects:
                for sk_name, sk_delta in effects["技能"].items():
                    sk_val = inv.get_skill(sk_name, 0) + sk_delta
                    inv.set_skill(sk_name, max(0, sk_val))
            inv.save()
        else:
            event_reply = data_loader.get_text("adventure.event_default")

        reply = f"{event_reply}\n\n{battle.start_turn()}"
        await combat_cmd.send(_send_turn(battle, bot, reply))
        return

    # Normal combat flow
    battle = battle_manager.get_battle(user_id)
    if not battle:
        await combat_cmd.finish(md_message(f"\n{data_loader.get_text('adventure.no_active_battle')}", bot))

    if not action:
        await combat_cmd.finish(md_message(f"\n{data_loader.get_text('adventure.need_action')}", bot))

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
    response = "\n" + "\n\n".join([str(x) for x in result if x])
    await combat_cmd.send(_send_turn(battle, bot, response))

    if battle.fight_is_over():
        inv = battle.investigator
        inv.is_adventure = False
        inv.save()
        battle_manager.remove_battle(user_id)


# --- QQ 按钮回调 ---
try:
    from nonebot import on_type
    from nonebot.adapters.qq.event import InteractionCreateEvent

    button_callback = on_type(
        InteractionCreateEvent, priority=1, block=False
    )

    @button_callback.handle()
    async def handle_button_callback(event: InteractionCreateEvent, bot: Bot):
        """处理 QQ 交互回调（InteractionCreateEvent）。"""
        # 事件本身继承 ButtonInteraction，interaction 数据直接在 event 上
        # 先响应交互，避免 QQ 平台 3 秒超时
        try:
            await bot.put_interaction(interaction_id=event.id, code=0)
        except Exception as e:
            logger.debug(f"Failed to ack interaction: {e}")

        button_data = ""
        user_id = ""
        group_openid = ""
        try:
            button_data = event.data.resolved.button_data or ""
            user_id = event.get_user_id()
            group_openid = event.group_openid or ""
        except Exception as e:
            logger.debug(f"Failed to parse button interaction: {e}")
            return

        if not button_data or not user_id:
            return

        # 回调数据格式: "action:格斗:回合令牌" / "event:进入"
        parts = button_data.split(":")
        kind = parts[0]
        payload = parts[1] if len(parts) > 1 else ""
        token = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None

        if kind == "action":
            await handle_combat_action(user_id, payload, bot, group_openid, token)
        elif kind == "event":
            await handle_event_choice(user_id, payload, bot, group_openid)
        elif kind == "equip":
            await handle_equip_button(user_id, payload, bot, group_openid)
        elif kind == "choose":
            await handle_choose_button(user_id, payload, bot, group_openid)
        elif kind == "buy":
            await handle_buy_button(user_id, payload, bot, group_openid)
        elif kind == "info":
            await handle_info_button(user_id, bot, group_openid)
        elif kind == "create":
            await handle_create_button(user_id, bot, group_openid)
        else:
            logger.debug(f"Unknown button callback: {button_data}")

except ImportError:
    # 非 QQ 适配器环境不注册回调
    pass

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
        await _send_to_user(bot, user_id, md_message(f"\n{data_loader.get_text('adventure.no_active_battle')}", bot), group_openid)
        return

    # 旧按钮点击（令牌不匹配）直接忽略
    if token is not None and token != battle.get_turn_token():
        logger.debug(f"Stale button click ignored: token={token}, current={battle.get_turn_token()}")
        return

    result = battle.execute_action(action)
    response = "\n" + "\n\n".join([str(x) for x in result if x])
    await _send_to_user(bot, user_id, _send_turn(battle, bot, response), group_openid)

    if battle.fight_is_over():
        inv = battle.investigator
        inv.is_adventure = False
        inv.save()
        battle_manager.remove_battle(user_id)


async def handle_equip_button(
    user_id: str,
    item_id: str,
    bot: Bot,
    group_openid: str = "",
) -> None:
    """背包「使用」按钮回调：直接装备物品。"""
    ok, res = investigator_repo.equip_item(user_id, item_id)
    if ok:
        battle = battle_manager.get_battle(user_id)
        if battle:
            battle.investigator.update_equipment()
            if battle.current_turn == "inv":
                battle._update_gun_status()
    await _send_to_user(bot, user_id, md_message(f"\n{res}", bot), group_openid)


async def handle_choose_button(
    user_id: str,
    idx: str,
    bot: Bot,
    group_openid: str = "",
) -> None:
    """候选「选择」按钮回调。"""
    from ..plugins.character import choose_reply

    reply = choose_reply(user_id, int(idx)) if idx.isdigit() else None
    if reply is None:
        reply = f"\n{data_loader.get_text('character.need_create')}"
    await _send_to_user(bot, user_id, md_message(reply, bot), group_openid)


async def handle_create_button(
    user_id: str,
    bot: Bot,
    group_openid: str = "",
) -> None:
    """死亡后「创建调查员」按钮回调。"""
    from ..plugins.character import build_create_reply

    reply = build_create_reply(user_id, "调查员")
    msg = md_message(reply, bot)
    kb = build_keyboard(
        [
            [
                (
                    data_loader.get_text("character.choose_button", index=i),
                    f"choose:{i}",
                )
                for i in range(1, 4)
            ]
        ]
    )
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await _send_to_user(bot, user_id, msg, group_openid)


async def handle_buy_button(
    user_id: str,
    item_id: str,
    bot: Bot,
    group_openid: str = "",
) -> None:
    """商店「购买」按钮回调（默认数量 1）。"""
    from ..services.shop_service import shop_service

    items = shop_service.get_todays_shop("seed")
    ok, res = shop_service.buy_item(user_id, item_id, 1, items)
    msg = md_message(f"\n{res}", bot)

    if ok:
        kb = build_keyboard(
            [[(data_loader.get_text("character.info_button"), "info")]]
        )
        if kb is not None and not isinstance(msg, str):
            msg.append(kb)
    await _send_to_user(bot, user_id, msg, group_openid)


async def handle_info_button(
    user_id: str,
    bot: Bot,
    group_openid: str = "",
) -> None:
    """「调查员信息」按钮回调。"""
    from ..plugins.character import build_info_message

    await _send_to_user(bot, user_id, build_info_message(user_id, bot), group_openid)


async def handle_event_choice(
    user_id: str, choice: str, bot: Bot, group_openid: str = ""
) -> None:
    """按钮触发奇遇事件选择。"""
    ev_state = _event_states.pop(user_id, None)
    battle = battle_manager.get_battle(user_id)
    if not ev_state or not battle:
        await _send_to_user(bot, user_id, md_message(f"\n{data_loader.get_text('adventure.battle_state_error')}", bot), group_openid)
        return

    event_data = ev_state["event"]
    matched = next(
        (o for o in event_data["选项"] if o["输入"] == choice), None
    )

    if matched:
        effects = matched.get("效果", {})
        event_reply = matched["回复"]
        inv = battle.investigator
        if "san" in effects:
            san = inv.get_skill("san") + effects["san"]
            inv.set_skill("san", max(0, san))
        if "hp" in effects:
            inv.hp = max(1, inv.hp + effects["hp"])
        if "金币" in effects:
            add_gold(user_id, effects["金币"])
        if "物品" in effects:
            inv.add_item_to_inventory(effects["物品"], 1)
        if "技能" in effects:
            for sk_name, sk_delta in effects["技能"].items():
                sk_val = inv.get_skill(sk_name, 0) + sk_delta
                inv.set_skill(sk_name, max(0, sk_val))
        inv.save()
    else:
        event_reply = data_loader.get_text("adventure.event_default")

    reply = f"{event_reply}\n\n{battle.start_turn()}"
    await _send_to_user(bot, user_id, _send_turn(battle, bot, reply), group_openid)


async def _send_to_user(
    bot: Bot,
    user_id: str,
    message: Any,
    group_openid: str = "",
) -> None:
    """按 QQ 会话类型发送消息给用户。

    群聊需 group_openid（post_group_messages），私聊用 user_openid（send_to_c2c）。
    按钮回调来自群聊时，group_openid 从事件中获取。
    """
    try:
        from nonebot.adapters.qq import Bot as QQBot
        from nonebot.adapters.qq.message import Message as QQMessage

        if isinstance(bot, QQBot):
            if isinstance(message, QQMessage):
                msg_segments = message
            else:
                msg_segments = QQMessage(message)
            # 群聊：post_group_messages
            if group_openid:
                try:
                    await bot.post_group_messages(
                        group_openid=group_openid,
                        msg_type=2,
                        markdown=msg_segments.get("markdown")[-1].data["markdown"]
                        if msg_segments.get("markdown")
                        else None,
                        content=msg_segments.extract_content()
                        if not msg_segments.get("markdown")
                        else None,
                        keyboard=msg_segments.get("keyboard")[-1].data["keyboard"]
                        if msg_segments.get("keyboard")
                        else None,
                    )
                    return
                except Exception as e:
                    logger.warning(f"post_group_messages failed: {e}")
            # 私聊：send_to_c2c
            try:
                await bot.send_to_c2c(openid=user_id, message=msg_segments)
                return
            except Exception as e:
                logger.warning(f"send_to_c2c failed: {e}")
    except Exception as e:
        logger.warning(f"Failed to send via QQ bot: {e}")
    await bot.send_msg(user_id=user_id, message=message)
