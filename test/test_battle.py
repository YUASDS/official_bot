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


def test_battle_madness_auto_resolve() -> None:
    """失控期间：一次点击自动结算至疯狂结束，随机行动次数等于剩余回合数。"""
    import random

    random.seed(7)
    investigator_repo.delete_by_qq("test_battle_madness")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_battle_madness", "Madness", data
    )
    inv = Investigator(inv_model)
    inv.set_skill("格斗", 80)
    inv.set_skill("闪避", 80)
    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq("test_battle_madness")
        return
    bs = BattleService(inv, m)
    bs.hp_record["mon"] = 99999  # 防早胜，保证疯狂完整结算
    bs.hp_record["inv"] = 99999  # 防早亡
    bs.current_turn = "inv"
    # 固定疯狂期间的随机行动为格斗，避免逃跑提前结束战斗
    bs.investigator.get_available_actions = lambda: {
        "inv": ["格斗"],
        "mon": ["反击", "闪避"],
    }
    madness_duration = 3
    bs.set_madness(is_madness=True, duration=madness_duration)
    result = bs.execute_action("格斗")
    out = "\n".join(str(x) for x in result if x)
    assert out.count("疯狂·失控") == madness_duration
    assert bs.madness_duration == 0
    assert bs.is_madness is False
    investigator_repo.delete_by_qq("test_battle_madness")


def _fixed_monster_action(bs, damage: str = "5") -> dict:
    """固定怪物为单选项行动，返回行动 dict。"""
    bs.monster._data["攻击"] = {
        "a": {
            "skill": 40,
            "damage": damage,
            "counterattack": "怪物反击",
            "attack_succ": "怪物命中",
            "attack_false": "怪物未中",
        }
    }
    bs.monster._data["装甲"] = 0
    return bs.monster.get_action("mon")


def test_monster_damage_armor_applied_once() -> None:
    """护甲仅在结算时应用一次（投掷结果不再预扣）。"""
    from types import SimpleNamespace

    from src.services.dice_roller import SuccessLevel

    investigator_repo.delete_by_qq("test_battle_armor")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_battle_armor", "ArmorHero", data
    )
    inv = Investigator(inv_model)
    investigator_repo.add_item_to_inventory(inv_model, "301", 1)  # 皮夹克 护甲1
    investigator_repo.equip_item("test_battle_armor", "301")
    inv.update_equipment()
    assert inv.get_armor_value() == 1

    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq("test_battle_armor")
        return
    bs = BattleService(inv, m)
    action = _fixed_monster_action(bs, damage="5")

    hp_before = bs.hp_record["inv"]
    bs._handle_monster_attack_success(
        action, SimpleNamespace(level1=SuccessLevel.SUCCESS)
    )
    assert hp_before - bs.hp_record["inv"] == 4  # 5 - 1（仅扣一次）
    investigator_repo.delete_by_qq("test_battle_armor")


def test_monster_env_damage_buff_applied() -> None:
    """环境「怪物·伤害」加成先加后结算，实际生效。"""
    from types import SimpleNamespace

    from src.services.dice_roller import SuccessLevel

    investigator_repo.delete_by_qq("test_battle_env_dmg")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_battle_env_dmg", "EnvDmgHero", data
    )
    inv = Investigator(inv_model)
    investigator_repo.add_item_to_inventory(inv_model, "301", 1)
    investigator_repo.equip_item("test_battle_env_dmg", "301")
    inv.update_equipment()

    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq("test_battle_env_dmg")
        return
    bs = BattleService(inv, m)
    bs.set_environment({"怪物": {"伤害": "3"}})
    action = _fixed_monster_action(bs, damage="5")

    hp_before = bs.hp_record["inv"]
    bs._handle_monster_attack_success(
        action, SimpleNamespace(level1=SuccessLevel.SUCCESS)
    )
    assert hp_before - bs.hp_record["inv"] == 7  # (5+3) - 1
    investigator_repo.delete_by_qq("test_battle_env_dmg")


def test_melee_fumble_monster_counter_hits_only_on_monster_success() -> None:
    """玩家大失败：怪物仅自身检定成功才造成伤害，失败/大失败不命中。"""
    import random

    from src.models.item import Equipment
    from src.services.dice_roller import ConfrontationRoll, SuccessLevel

    investigator_repo.delete_by_qq("test_battle_fumble")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_battle_fumble", "Fumble", data
    )
    inv = Investigator(inv_model)
    # 装备可损坏武器（102 老旧雕刻刀），避免弹簧折刀的大失败自伤路径
    investigator_repo.add_item_to_inventory(inv_model, "102", 1)
    investigator_repo.equip_item("test_battle_fumble", "102")
    inv.update_equipment()

    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq("test_battle_fumble")
        return
    # 固定怪物行动：单选项（避免 get_action 随机），伤害 3 平值
    m._data["攻击"] = {
        "a": {
            "skill": 40,
            "damage": "3",
            "counterattack": "怪物反击",
            "attack_succ": "怪物命中",
            "attack_false": "怪物未中",
        }
    }
    m._data["装甲"] = 0

    player_skill = inv.get_skill(Equipment("102").identify_skill, 25)
    monster_skill = 40

    fumble: tuple | None = None
    for seed in range(2000):
        random.seed(seed)
        random.choice(["a"])  # 复刻 get_action 的选项随机消耗
        c = ConfrontationRoll(player_skill, monster_skill)
        if c.level1 == SuccessLevel.CRITICAL_FAILURE:
            fumble = (seed, c)
            break
    assert fumble is not None, "未能找到玩家大失败的种子"

    seed, c = fumble
    random.seed(seed)
    bs = BattleService(inv, m)
    hp_before = bs.hp_record["inv"]
    bs._melee_attack()
    if c.level2 > SuccessLevel.FAILURE:
        assert bs.hp_record["inv"] == hp_before - 3  # 怪物成功 → 命中
    else:
        assert bs.hp_record["inv"] == hp_before  # 怪物失败/大失败 → 不命中
    investigator_repo.delete_by_qq("test_battle_fumble")
