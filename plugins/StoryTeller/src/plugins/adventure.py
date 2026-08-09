from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.exception import FinishedException
from nonebot.params import CommandArg
from loguru import logger
import random

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


# --- 按钮回调注册 ---
register_button_handler("action", handle_combat_action)
register_button_handler("event", handle_event_choice)
setup_button_callback()
