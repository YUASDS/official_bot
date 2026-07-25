import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.models.monster import Monster
from src.services.battle import BattleService


def test_battle_init():
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save("test_battle_init_v2", "BattleInit", data)
    inv = Investigator(inv_model)
    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq("test_battle_init_v2")
        return
    bs = BattleService(inv, m)
    assert bs.fight_is_over() is False
    investigator_repo.delete_by_qq("test_battle_init_v2")


def test_battle_melee():
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save("test_battle_melee", "BattleMelee", data)
    inv = Investigator(inv_model)
    m = Monster.load_random_for_day(1)
    if not m:
        return
    bs = BattleService(inv, m)
    result = bs._melee_attack()
    assert isinstance(result, tuple)
    assert len(result) > 0
    investigator_repo.delete_by_qq("test_battle_melee")


def test_battle_ranged_no_gun():
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save("test_battle_range", "BattleRange", data)
    inv = Investigator(inv_model)
    m = Monster.load_random_for_day(1)
    if not m:
        return
    bs = BattleService(inv, m)
    result = bs._ranged_attack(1)
    assert "没有装备" in result[0]
    investigator_repo.delete_by_qq("test_battle_range")
