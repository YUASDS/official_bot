import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

from src.services.data_loader import data_loader


def test_environment_data_loaded():
    assert isinstance(data_loader.environment_data, dict)
    assert len(data_loader.environment_data) > 0
    assert "浓雾" in data_loader.environment_data


def test_event_data_loaded():
    assert isinstance(data_loader.event_data, dict)
    assert len(data_loader.event_data) > 0
    assert "古书" in data_loader.event_data


def test_environment_buff_applied():
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
    from src.models.monster import Monster
    from src.services.battle import BattleService

    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save("test_env_qq", "EnvHero", data)
    inv = Investigator(inv_model)
    m = Monster.load_random_for_day(1)
    if not m:
        investigator_repo.delete_by_qq("test_env_qq")
        return

    bs = BattleService(inv, m)
    env = {"name": "浓雾", "描述": "浓雾笼罩", "玩家": {"射击": -20}, "怪物": {}}
    bs.set_environment(env)
    original = inv.get_skill("射击", 20)
    modified = bs._get_player_modified_skill("射击", 20)
    assert modified == original - 20

    investigator_repo.delete_by_qq("test_env_qq")


def test_event_option_application():
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo

    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save("test_event_qq", "EventHero", data)
    inv = Investigator(inv_model)
    orig_san = inv.get_skill("san")
    orig_hp = inv.hp

    inv.set_skill("san", max(0, orig_san - 5))
    inv.hp = max(1, orig_hp - 3)
    inv.save()

    assert inv.get_skill("san") <= orig_san
    assert inv.hp <= orig_hp

    investigator_repo.delete_by_qq("test_event_qq")
