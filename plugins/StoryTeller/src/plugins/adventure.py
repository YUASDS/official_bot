from loguru import logger
from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.params import CommandArg

from ..models.monster import Monster, monster_repo
from ..models.player import Investigator, investigator_repo
from ..services.battle import BattleService
from ..services.data_loader import data_loader
from ..utils.active_battles import battle_manager

adventure_cmd = on_command("今日冒险", aliases={"daily_adventure", "开始冒险"}, priority=10, block=True)

@adventure_cmd.handle()
async def handle_adventure(event: Event) -> None:
    user_id = event.get_user_id()
    try:
        inv = Investigator.load(user_id)
        if not inv.is_survive:
            await adventure_cmd.finish("当前调查员已死亡。请使用复活道具。")

        if battle_manager.get_battle(user_id):
            await adventure_cmd.finish("你正在战斗中！请继续战斗。")

        if inv.is_adventure and not battle_manager.get_battle(user_id):
            await adventure_cmd.finish("你今天已经尝试过冒险了。明天再来吧。")

        monster_id = monster_repo.find_random_id_for_day(inv.day)
        if not monster_id:
            await adventure_cmd.finish("找不到今天的怪物配置。")

        monster = Monster(monster_id)

        # Monster intro scene
        monster_intro = getattr(monster, "出场", f"一只{monster.name}出现了！")
        day_event = data_loader.get_event(inv.day)

        # Sanity Check
        from ..services.sanity import perform_sanity_check
        san_passed, san_desc, _san_loss = perform_sanity_check(inv, monster)

        madness_desc = ""
        is_mad = False
        if not san_passed:
            current_san = inv.get_skill("san")
            if current_san <= 0:
                inv.is_survive = False
                inv.save()
                await adventure_cmd.finish(f"{san_desc}\n你的理智归零，陷入了永久的疯狂。游戏结束。")

            from ..services.dice_roller import roll_dice
            int_val = inv.get_skill("智力")
            _int_check_str, int_check_res = roll_dice("1d100")

            if int_check_res <= int_val:
                _madness_duration_str, madness_duration = roll_dice("1d10")
                is_mad = True
                madness_desc = f"\n智力检定({int_check_res}/{int_val})成功。你理解了这一恐怖的存在，陷入了临时疯狂({madness_duration}回合)！"
            else:
                madness_desc = f"\n智力检定({int_check_res}/{int_val})失败。你的大脑拒绝理解这一恐怖，避开了疯狂。"

        service = BattleService(inv, monster)
        if is_mad:
            service.set_madness(True, madness_duration)

        battle_manager.add_battle(user_id, service)
        inv.is_adventure = True
        inv.save()

        reply = f"{day_event}\n{monster_intro}\n\n{san_desc}{madness_desc}\n\n{service.start_turn()}"
        await adventure_cmd.send(reply)

    except Exception as e:
        logger.exception(f"Error starting adventure for {user_id}: {e}")
        await adventure_cmd.finish("冒险启动时发生错误。")

combat_cmd = on_command("行动", aliases={"combat_action"}, priority=5, block=True)

@combat_cmd.handle()
async def handle_combat(event: Event, msg: Message = CommandArg()) -> None:
    user_id = event.get_user_id()
    battle = battle_manager.get_battle(user_id)

    if not battle:
        await combat_cmd.finish("当前没有进行中的战斗。")

    action = msg.extract_plain_text().strip()
    if not action:
        await combat_cmd.finish("请输入具体的行动指令。")

    # Mid-combat equipment swap: /行动 使用 <item_id>
    if action.startswith("使用 "):
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
    response = "\n".join([str(x) for x in result if x])

    await combat_cmd.send(response)

    if battle.fight_is_over():
        inv = battle.investigator
        inv.is_adventure = False
        inv.save()
        battle_manager.remove_battle(user_id)
