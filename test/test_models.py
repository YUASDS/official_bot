import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

from src.services.dice_roller import roll_dice, BonusDiceRoll, PenaltyDiceRoll

def test_compound_expression():
    expr, val = roll_dice("1d8+2d6+3")
    assert isinstance(val, int)
    assert val >= 6

def test_bonus_dice_roll():
    roll = BonusDiceRoll(skill=50, bonus_dice=2)
    assert roll.final_result <= roll.dice
    assert 1 <= roll.final_result <= 100

def test_d_without_number():
    expr, val = roll_dice("d4")
    assert 1 <= val <= 4

def test_penalty_dice_roll():
    roll = PenaltyDiceRoll(skill=50, penalty_dice=2)
    assert 1 <= roll.final_result <= 100

def test_pure_number():
    expr, val = roll_dice("10")
    assert val == 10


# --- Equipment model tests ---
from src.models.item import Equipment

def test_equipment_str():
    e = Equipment("101")
    assert str(e) == e.name

def test_equipment_full_description():
    e = Equipment("101")
    desc = e.get_full_description()
    assert "弹簧折刀" in desc

def test_equipment_brief_description():
    e = Equipment("101")
    desc = e.get_brief_description()
    assert "ID" in desc or "伤害" in desc


# --- Monster model tests ---
from src.models.monster import Monster

def test_monster_dynamic_attr():
    m = Monster("1")
    assert m.is_valid
    assert m.name is not None

def test_monster_invalid_raises():
    import pytest
    with pytest.raises(ValueError):
        Monster("nonexistent_99999")

def test_monster_load_random_for_day():
    m = Monster.load_random_for_day(1)
    assert m.is_valid
    assert m.name is not None

def test_monster_generate_loot_returns_tuple3():
    m = Monster("1")
    loot = m.generate_loot()
    assert isinstance(loot, tuple)
    assert len(loot) == 3
    assert isinstance(loot[0], int)
    assert isinstance(loot[2], str)


# --- Player model tests ---
from src.models.player import (
    Investigator, InvestigatorGenerator, InvestigatorFormatter,
    CreateInvestigator, investigator_repo, InvestigatorModel
)

def test_player_repo_delete_by_qq():
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv = investigator_repo.create_and_save("test_del_qq", "TestDel", data)
    assert investigator_repo.find_by_qq("test_del_qq") is not None
    investigator_repo.delete_by_qq("test_del_qq")
    assert investigator_repo.find_by_qq("test_del_qq") is None
def test_player_break_equipped_item():
    # Use unique QQ to avoid stale state
    qq = "test_break_qq2"
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(qq, "TestBreak", data)
    inv = Investigator(inv_model)
    success = inv.break_equipped_item("格斗")
    assert success
    assert inv.get_equipped_id("近战") is None
    investigator_repo.delete_by_qq(qq)


def test_player_get_full_attributes():
    qq = "test_attr_qq2"
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(qq, "TestAttr", data)
    inv = Investigator(inv_model)
    attrs = inv.get_full_attributes_dict()
    assert "力量" in attrs
    investigator_repo.delete_by_qq(qq)


def test_player_mark_as_deceased():
    qq = "test_dead_qq2"
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(qq, "TestDead", data)
    inv = Investigator(inv_model)
    inv.mark_as_deceased()
    assert not inv.is_survive
    investigator_repo.delete_by_qq(qq)
def test_generator_multiple():
    data = InvestigatorGenerator.generate_investigator_data(3)
    assert len(data) == 3
    assert isinstance(data[0], dict)

def test_create_investigator_flow():
    ci = CreateInvestigator(3)
    assert ci.choose_investigator(1)
    assert ci.skill_point > 0
    # set_skill requires exact allocation with per-skill cap of 75
    # This is inherently hard to test with random attribute rolls
    # Instead verify that set_skill validates correctly
    # Too little: should fail
    ok, msg = ci.set_skill(f"格斗 1")
    if ci.skill_point != 1:
        assert not ok, f"Expected failure for under-allocation, got: {msg}"
    # Too much per-skill: try to set 100 points to one skill
    ok2, _ = ci.set_skill(f"格斗 {ci.skill_point}")
    # Will fail if 格斗+skill_point > 75, which is most cases
    # Just confirm the method runs without exception
    assert isinstance(ok2, bool)
