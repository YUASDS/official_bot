import random
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

from src.models.item import Equipment
from src.models.monster import Monster
from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.services.battle import BattleService
from src.services.dice_roller import ConfrontationRoll, SuccessLevel


def _fixed_monster_action(monster, damage: str = "1d4", skill: int = 90) -> dict:
    """@description 固定怪物为单选项行动（穿透 ex:1），返回行动 dict。

    不调用 get_action（避免触发 _battle_action 缓存，保持种子复刻一致）。
    """
    monster._data["攻击"] = {
        "a": {
            "skill": skill,
            "damage": damage,
            "ex": 1,
            "counterattack": "怪物反击",
            "attack_succ": "命中 $骰子=$伤害",
            "attack_false": "怪物未中",
        }
    }
    monster._data["装甲"] = 0
    return monster._data["攻击"]["a"]


def test_monster_counterattack_damage_never_critical() -> None:
    """critical=False：反击伤害恒为普通伤害（穿透双骰被禁用）。"""
    investigator_repo.delete_by_qq("test_counter_crit_unit")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_counter_crit_unit", "CounterCrit", data
    )
    inv = Investigator(inv_model)
    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq("test_counter_crit_unit")
        return
    action = _fixed_monster_action(m, damage="4")  # 平值伤害，无随机
    bs = BattleService(inv, m)
    stub = SimpleNamespace(level1=SuccessLevel.CRITICAL_SUCCESS)

    # 默认 critical=True：暴击穿透双骰（4+4=8）
    hp_before = bs.hp_record["inv"]
    bs._handle_monster_attack_success(action, stub)
    assert hp_before - bs.hp_record["inv"] == 8

    # critical=False：普通伤害（4）
    bs.hp_record["inv"] = hp_before
    bs._handle_monster_attack_success(action, stub, critical=False)
    assert hp_before - bs.hp_record["inv"] == 4
    investigator_repo.delete_by_qq("test_counter_crit_unit")


def test_melee_fumble_monster_crit_counter_no_extra_dice() -> None:
    """玩家大失败 + 怪物反击极难/大成功：反击伤害仍为单骰（无第二份伤害骰）。"""
    investigator_repo.delete_by_qq("test_counter_crit_fumble")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_counter_crit_fumble", "FumbleCrit", data
    )
    inv = Investigator(inv_model)
    # 装备可损坏武器，避免弹簧折刀大失败自伤路径
    investigator_repo.add_item_to_inventory(inv_model, "102", 1)
    investigator_repo.equip_item("test_counter_crit_fumble", "102")
    inv.update_equipment()

    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq("test_counter_crit_fumble")
        return
    _fixed_monster_action(m, damage="1d4")  # 穿透怪，反击大成功时会触发双骰

    player_skill = inv.get_skill(Equipment("102").identify_skill, 25)
    monster_skill = 90

    # 战斗中 get_action 命中 _battle_action 缓存（不消耗随机），搜索须保持一致
    hit = None
    for seed in range(5000):
        random.seed(seed)
        c = ConfrontationRoll(player_skill, monster_skill)
        if (
            c.level1 == SuccessLevel.CRITICAL_FAILURE
            and c.level2 >= SuccessLevel.EXTREME_SUCCESS
        ):
            hit = (seed, c)
            break
    assert hit is not None, "未找到玩家大失败+怪物极难/大成功的种子"

    seed, c = hit
    random.seed(seed)
    bs = BattleService(inv, m)
    hp_before = bs.hp_record["inv"]
    result = bs._melee_attack()
    loss = hp_before - bs.hp_record["inv"]

    assert c.level2 >= SuccessLevel.EXTREME_SUCCESS  # 前置：怪物反击极难/大成功
    assert 1 <= loss <= 4  # 单骰 1d4 上限 4；穿透双骰可达 8
    assert "1d4" in str(result)  # 战报含伤害骰
    investigator_repo.delete_by_qq("test_counter_crit_fumble")
