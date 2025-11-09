from .Fight import start_combat
from .Investigator import investigator_service
from .Equipment import LootService
from .dice import DiceRoll
from .GlobalData import data_manager


def check(qq: str):
    """开始冒险"""
    user = investigator_service.get_investigator(qq)
    if not user:
        return False, "当前还没有存活角色，先使用\n/创建调查员\n开始调查员的创建吧~"
    elif not user.issurvive:
        return (
            False,
            "当前调查员已经死亡，无法进行冒险哦~\n可以使用复活道具让调查员复活~",
        )
    elif user.isadventure:
        return False, "今天已经参加了冒险哦~\n等明天再来吧~"
    elif user.hp == 0:
        return False, "当前调查员还没创建完成哦~"
    else:
        return True, ""


def check_issurvive(qq):
    user = investigator_service.get_investigator(qq)
    if not user:
        return False, "当前还没有存活角色，先使用\n/创建调查员\n开始调查员的创建吧~"
    elif user.hp == 0:
        return False, "当前调查员还没创建完成哦~"
    elif not user.issurvive:
        return False, "当前调查员已经死亡~"

    return True, ""


def set_attr():
    """初始化属性"""


class Adventure:
    def __init__(self, qq: str, name: str):
        self.qq = qq
        self.name = name

    def StartAdventure(self):
        """开始冒险"""
        combat = start_combat(self.qq, self.name)
        first_turn_replies = combat.first_turn()
        monster_start = combat.monster.出场
        self.combat = combat
        return monster_start, first_turn_replies

    def run_adventure(self, command):
        """运行冒险流程"""
        combat = self.combat

        # command = input("输入指令：")
        result = combat.execute_action(command)

        if not result:
            return False, "无效指令或无法执行"
        state = combat.fight_is_over()
        return state, result

    def end_adventure(self):
        """结束冒险"""
        combat = self.combat
        investigator_service.mark_as_adventured(self.qq)
        if combat.player_info["hp"] <= 0:
            investigator_service.mark_as_deceased(self.qq)
            return "疲倦席卷了你的身躯，你就此永远的沉睡了...."
        else:
            search = DiceRoll(combat.player_info.get("侦查", 25))
            if search.level > 0:
                reply = LootService.get_loot_reward(
                    self.qq, combat.monster.data.get("id", "")
                )
                investigator_service.update_investigator(
                    self.qq, combat.player_info.get("day", 1) + 1
                )
            else:
                reply = data_manager.reply_data.get("搜索失败")
            return reply


if __name__ == "__main__":
    reply = "开始冒险"

    # 查看是否存在数据
