from database.db import get_info
from .Fight import start_combat
from .Investigator import Investigator

# from .Equipment import Equipment
# from .dice import DiceRoll
# from .GlobalData import data_manager


def check(inv: Investigator):
    """开始冒险"""
    if not inv.is_survive:
        return (
            False,
            "当前调查员已经死亡，无法进行冒险哦~\n可以使用复活道具让调查员复活~",
        )
    elif inv.is_adventure:
        return False, "今天已经参加了冒险哦~\n等明天再来吧~"
    elif inv.hp == 0:
        return False, "当前调查员还没创建完成哦~"
    else:
        return True, ""


def check_issurvive(inv: Investigator):
    if inv.hp == 0:
        return False, "当前调查员还没创建完成哦~"
    elif not inv.is_survive:
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
        first_turn_replies = combat.start_turn()
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


def get_qq_equipment(qq: str):

    inv = Investigator.load(qq)

    info = get_info(qq)
    wupa = f"\n当前乌帕数量：{info.gold}\n"
    return f"{wupa}已有装备：\n{inv.str_equipments()}"


if __name__ == "__main__":
    reply = "开始冒险"

    # 查看是否存在数据
