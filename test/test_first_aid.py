"""战斗行动「急救」：检定与效果测试（成功/大成功/大失败/失败、HP 上下限、可用性）。"""

import sys
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.models.monster import Monster
from src.services.battle import BattleService
from src.services.dice_roller import DiceRoll, SuccessLevel, roll_dice


def _setup(qq: str, skill_val: int = 50):
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(qq, "FirstAid", data)
    inv = Investigator(inv_model)
    inv.set_skill("急救", skill_val)
    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq(qq)
        return None, None
    return inv, m


def _find_seed(skill: int, level: int, limit: int = 20000) -> int:
    """找到使 DiceRoll(skill) 达到指定等级的随机种子。"""
    for seed in range(limit):
        random.seed(seed)
        if DiceRoll(skill).level == level:
            return seed
    raise AssertionError(f"no seed for skill={skill} level={level}")


def test_first_aid_success_heals_1():
    qq = "test_first_aid_success"
    inv, m = _setup(qq)
    if not m:
        return
    seed = _find_seed(50, SuccessLevel.SUCCESS)
    bs = BattleService(inv, m)
    bs.hp_record["inv"] = 1
    random.seed(seed)
    result = bs._first_aid()
    out = "\n".join(str(x) for x in result if x)
    assert "检定·急救" in out
    assert bs.hp_record["inv"] == 2
    assert bs.current_turn == "mon"  # 使用后结束回合
    assert "急救" in bs.succeded_skill  # 成功记录成长技能
    investigator_repo.delete_by_qq(qq)


def test_first_aid_crit_success_heals_1d3():
    qq = "test_first_aid_crit"
    inv, m = _setup(qq)
    if not m:
        return
    seed = _find_seed(50, SuccessLevel.CRITICAL_SUCCESS)
    random.seed(seed)
    DiceRoll(50)
    _expr, val = roll_dice("1d3")
    bs = BattleService(inv, m)
    bs.hp_record["inv"] = 1
    random.seed(seed)
    bs._first_aid()
    assert bs.hp_record["inv"] == 1 + val
    investigator_repo.delete_by_qq(qq)


def test_first_aid_extreme_success_heals_1d3():
    qq = "test_first_aid_extreme"
    inv, m = _setup(qq)
    if not m:
        return
    seed = _find_seed(50, SuccessLevel.EXTREME_SUCCESS)
    random.seed(seed)
    DiceRoll(50)
    _expr, val = roll_dice("1d3")
    bs = BattleService(inv, m)
    bs.hp_record["inv"] = 1
    random.seed(seed)
    bs._first_aid()
    assert bs.hp_record["inv"] == 1 + val
    investigator_repo.delete_by_qq(qq)


def test_first_aid_hard_success_heals_1():
    qq = "test_first_aid_hard"
    inv, m = _setup(qq)
    if not m:
        return
    seed = _find_seed(50, SuccessLevel.HARD_SUCCESS)
    bs = BattleService(inv, m)
    bs.hp_record["inv"] = 1
    random.seed(seed)
    bs._first_aid()
    assert bs.hp_record["inv"] == 2
    investigator_repo.delete_by_qq(qq)


def test_first_aid_success_capped_at_max_hp():
    qq = "test_first_aid_cap"
    inv, m = _setup(qq)
    if not m:
        return
    seed = _find_seed(50, SuccessLevel.SUCCESS)
    bs = BattleService(inv, m)
    max_hp = inv.get_max_hp()
    bs.hp_record["inv"] = max_hp
    random.seed(seed)
    bs._first_aid()
    assert bs.hp_record["inv"] == max_hp
    investigator_repo.delete_by_qq(qq)


def test_first_aid_crit_failure_damages_1d3():
    qq = "test_first_aid_crit_fail"
    inv, m = _setup(qq)
    if not m:
        return
    seed = _find_seed(50, SuccessLevel.CRITICAL_FAILURE)
    random.seed(seed)
    DiceRoll(50)
    _expr, val = roll_dice("1d3")
    bs = BattleService(inv, m)
    bs.hp_record["inv"] = 10
    random.seed(seed)
    bs._first_aid()
    assert bs.hp_record["inv"] == 10 - val
    investigator_repo.delete_by_qq(qq)


def test_first_aid_crit_failure_floor_at_zero():
    qq = "test_first_aid_floor"
    inv, m = _setup(qq)
    if not m:
        return
    seed = _find_seed(50, SuccessLevel.CRITICAL_FAILURE)
    bs = BattleService(inv, m)
    bs.hp_record["inv"] = 1
    bs.hp_record["mon"] = 99999  # 防误判怪物死亡
    random.seed(seed)
    result = bs._first_aid()
    assert bs.hp_record["inv"] == 0
    assert "倒下了" in "\n".join(str(x) for x in result if x)  # 死亡判定接管
    investigator_repo.delete_by_qq(qq)


def test_first_aid_normal_failure_no_effect():
    qq = "test_first_aid_fail"
    inv, m = _setup(qq)
    if not m:
        return
    seed = _find_seed(50, SuccessLevel.FAILURE)
    bs = BattleService(inv, m)
    hp_before = bs.hp_record["inv"]
    random.seed(seed)
    result = bs._first_aid()
    assert bs.hp_record["inv"] == hp_before  # 不回复也不扣血
    assert "急救" not in bs.succeded_skill  # 失败不记录成长
    investigator_repo.delete_by_qq(qq)


def test_first_aid_in_available_actions():
    qq = "test_first_aid_avail"
    inv, m = _setup(qq)
    if not m:
        return
    bs = BattleService(inv, m)
    assert "急救" in bs._player_actions()["inv"]
    assert "急救" in bs.get_available_actions_for_turn()
    investigator_repo.delete_by_qq(qq)


def test_first_aid_execute_on_inv_turn():
    qq = "test_first_aid_execute"
    inv, m = _setup(qq)
    if not m:
        return
    seed = _find_seed(50, SuccessLevel.SUCCESS)
    bs = BattleService(inv, m)
    bs.hp_record["inv"] = 1
    random.seed(seed)
    result = bs.execute_action("急救")
    out = "\n".join(str(x) for x in result if x)
    assert "检定·急救" in out
    assert bs.hp_record["inv"] == 2
    investigator_repo.delete_by_qq(qq)


def test_first_aid_not_available_on_monster_turn():
    qq = "test_first_aid_mon_turn"
    inv, m = _setup(qq)
    if not m:
        return
    bs = BattleService(inv, m)
    bs.monster._ai_data = None  # 固定普通怪物行动路径，避免 AI 施法分支
    bs.current_turn = "mon"
    result = bs.execute_action("急救")
    out = "\n".join(str(x) for x in result if x)
    assert "未知的防御行动" in out
    investigator_repo.delete_by_qq(qq)
