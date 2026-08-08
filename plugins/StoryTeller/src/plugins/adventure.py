from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.exception import FinishedException
from nonebot.params import CommandArg
from loguru import logger
import random

from ..services.battle import BattleService
from ..models.player import Investigator, investigator_repo
from ..models.monster import Monster, monster_repo
from ..services.data_loader import data_loader
from ..utils.active_battles import battle_manager
from database.db import add_gold

# State for active random events (user_id -> event context)
_event_states: dict[str, dict] = {}

adventure_cmd = on_command("今日冒险", aliases={"daily_adventure", "开始冒险"}, priority=10, block=True)

@adventure_cmd.handle()
async def handle_adventure(event: Event):
    user_id = event.get_user_id()
    try:
        inv = Investigator.load(user_id)
        if not inv.is_survive:
            await adventure_cmd.finish(f"\n{data_loader.get_text('adventure.player_dead')}")
        if battle_manager.get_battle(user_id):
            await adventure_cmd.finish(f"\n{data_loader.get_text('adventure.in_battle')}")

        inv.restore_hp()
        inv.save()

        monster_id = monster_repo.find_random_id_for_day(inv.day)
        if not monster_id:
            await adventure_cmd.finish(f"\n{data_loader.get_text('adventure.no_monster_config')}")
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
                    f"{san_desc}\n\n"
                    f"{data_loader.get_text('adventure.sanity_zero')}"
                )

            from ..services.dice_roller import roll_dice
            int_val = inv.get_skill("智力")
            _, int_check_res = roll_dice("1d100")
            if int_check_res <= int_val:
                _, madness_duration = roll_dice("1d10")
                is_mad = True
                madness_desc = data_loader.get_text(
                    "adventure.int_check_success",
                    dice=int_check_res,
                    target=int_val,
                    duration=madness_duration,
                )
            else:
                madness_desc = data_loader.get_text(
                    "adventure.int_check_fail",
                    dice=int_check_res,
                    target=int_val,
                )

        # --- Battle service ---
        service = BattleService(inv, monster)
        if env:
            service.set_environment(env)
        if is_mad:
            service.set_madness(True, madness_duration)

        inv.is_adventure = True
        inv.save()

        header = f"\n{env_desc}\n{day_event}" if env_desc else f"\n{day_event}"

        # --- Random event (40% chance) ---
        if random.random() < 0.4 and data_loader.event_data:
            event_key = random.choice(list(data_loader.event_data.keys()))
            event_data = data_loader.event_data[event_key]
            event_text = f"\n\n{data_loader.get_text('adventure.event_title')}\n{event_data['描述']}\n"
            for opt in event_data["选项"]:
                event_text += f" {data_loader.get_text('adventure.event_choice', input=opt['输入'])}\n"

            battle_manager.add_battle(user_id, service)
            _event_states[user_id] = {
                "event": event_data,
                "header": header,
                "monster_intro": monster_intro,
            }

            reply = (
                f"{header}{event_text}\n"
                f"{monster_intro}\n"
                f"{san_desc}{madness_desc}"
            )
            await adventure_cmd.send(reply)
            return

        battle_manager.add_battle(user_id, service)
        reply = (
            f"{header}\n\n"
            f"{monster_intro}\n"
            f"{san_desc}{madness_desc}\n\n"
            f"{service.start_turn()}"
        )
        await adventure_cmd.send(reply)

    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"Error starting adventure for {user_id}: {e}")
        await adventure_cmd.finish(f"\n{data_loader.get_text('adventure.start_error')}")

combat_cmd = on_command("行动", aliases={"combat_action"}, priority=5, block=True)

@combat_cmd.handle()
async def handle_combat(event: Event, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    action = msg.extract_plain_text().strip()

    # Check for pending event choice first
    ev_state = _event_states.pop(user_id, None)
    if ev_state:
        event_data = ev_state["event"]
        header = ev_state["header"]
        monster_intro = ev_state["monster_intro"]
        battle = battle_manager.get_battle(user_id)
        if not battle:
            await combat_cmd.finish(f"\n{data_loader.get_text('adventure.battle_state_error')}")

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

        reply = (
            f"{header}\n"
            f"{event_reply}\n\n"
            f"{monster_intro}\n\n"
            f"{battle.start_turn()}"
        )
        await combat_cmd.send(reply)
        return

    # Normal combat flow
    battle = battle_manager.get_battle(user_id)
    if not battle:
        await combat_cmd.finish(f"\n{data_loader.get_text('adventure.no_active_battle')}")

    if not action:
        await combat_cmd.finish(f"\n{data_loader.get_text('adventure.need_action')}")

    if action.startswith(data_loader.get_text("adventure.use_item")):
        item_id = action.split()[1]
        ok, msg_text = investigator_repo.equip_item(user_id, item_id)
        if ok:
            battle.investigator.update_equipment()
            if battle.current_turn == "inv":
                battle._update_gun_status()
            prompt = battle._get_next_turn_prompt()
            await combat_cmd.send(f"{msg_text}\n\n{prompt}")
        else:
            await combat_cmd.send(msg_text)
        return

    result = battle.execute_action(action)
    response = "\n" + "\n".join([str(x) for x in result if x])
    await combat_cmd.send(response)

    if battle.fight_is_over():
        inv = battle.investigator
        inv.is_adventure = False
        inv.save()
        battle_manager.remove_battle(user_id)
