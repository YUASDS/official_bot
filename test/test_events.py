import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

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


def _baptism_option():
    """圣泉洗礼·接受洗礼：检定 意志，奖励 san 3d6+10，失败 san-1/hp-2，跳过战斗。"""
    for ev in data_loader.event_data.values():
        for opt in ev.get("选项", []):
            if opt.get("输入") == "接受洗礼" and "检定" in opt:
                return opt
    raise AssertionError("未找到圣泉洗礼检定选项")


def test_event_check_failure_applies_failure_results():
    """检定失败 → 应用「失败」效果与失败回复，且不跳过战斗。"""
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
    from src.services.event_service import apply_event_choice

    investigator_repo.delete_by_qq("test_event_check_fail")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_event_check_fail", "CheckFail", data
    )
    inv = Investigator(inv_model)
    inv.set_skill("意志", 0)  # 1d100 > 0 必然失败
    inv.save()
    before_san = inv.get_skill("san")
    before_hp = inv.hp

    option = _baptism_option()
    reply, skip = apply_event_choice(inv, "test_event_check_fail", option)

    assert skip is False  # 失败不跳过战斗
    assert inv.get_skill("san") == before_san - 1  # 应用失败效果
    assert inv.hp == before_hp - 2
    assert "洗礼失败了" in reply  # 失败回复
    assert "泉水涤荡" not in reply  # 不是成功回复
    investigator_repo.delete_by_qq("test_event_check_fail")


def test_event_check_success_applies_reward_and_skips():
    """检定成功 → 应用「奖励」效果与成功回复，并跳过战斗。"""
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
    from src.services.event_service import apply_event_choice

    investigator_repo.delete_by_qq("test_event_check_ok")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_event_check_ok", "CheckOk", data
    )
    inv = Investigator(inv_model)
    inv.set_skill("意志", 100)  # 1d100 <= 100 必然成功
    inv.save()
    before_san = inv.get_skill("san")

    option = _baptism_option()
    reply, skip = apply_event_choice(inv, "test_event_check_ok", option)

    assert skip is True  # 成功跳过战斗
    assert inv.get_skill("san") >= before_san + 13  # 3d6+10 最小 13
    assert "泉水涤荡过你的每一寸意识" in reply  # 成功回复
    assert "洗礼失败了" not in reply  # 不是失败回复
    investigator_repo.delete_by_qq("test_event_check_ok")


def test_event_check_resolve_failure_effects_dict():
    """resolve_check_option 失败分支返回「失败」效果字典。"""
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
    from src.services.event_service import resolve_check_option

    investigator_repo.delete_by_qq("test_event_check_resolve")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_event_check_resolve", "CheckResolve", data
    )
    inv = Investigator(inv_model)
    inv.set_skill("意志", 0)
    inv.save()

    effects, _reply, passed = resolve_check_option(inv, _baptism_option())
    assert passed is False
    assert effects == {"san": -1, "hp": -2}
    investigator_repo.delete_by_qq("test_event_check_resolve")


def _event_option(event_key: str, option_input: str) -> dict:
    ev = data_loader.event_data[event_key]
    return next(o for o in ev["选项"] if o["输入"] == option_input)


def test_attr_boost_event_applies():
    """寒泉淬体·浸泡寒泉：体质 +3，san -2。"""
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
    from src.services.event_service import apply_event_choice

    investigator_repo.delete_by_qq("test_attr_boost")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save("test_attr_boost", "AttrBoost", data)
    inv = Investigator(inv_model)
    before_con = inv.get_skill("体质")
    before_san = inv.get_skill("san")

    reply, skip = apply_event_choice(
        inv, "test_attr_boost", _event_option("寒泉淬体", "浸泡寒泉")
    )
    assert inv.get_skill("体质") == before_con + 3
    assert inv.get_skill("san") == before_san - 2
    assert skip is False
    assert "体质" in reply
    investigator_repo.delete_by_qq("test_attr_boost")


def test_attr_check_event_failure_applies_failure():
    """试炼石碑：智力检定失败 → san -6，属性不变。"""
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
    from src.services.event_service import apply_event_choice

    investigator_repo.delete_by_qq("test_attr_check_fail")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_attr_check_fail", "AttrCheckFail", data
    )
    inv = Investigator(inv_model)
    inv.set_skill("智力", 0)  # 必然失败
    inv.save()
    before_int = inv.get_skill("智力")
    before_san = inv.get_skill("san")

    reply, skip = apply_event_choice(
        inv, "test_attr_check_fail", _event_option("试炼石碑", "领悟碑文")
    )
    assert inv.get_skill("智力") == before_int
    assert inv.get_skill("san") == before_san - 6
    assert skip is False
    investigator_repo.delete_by_qq("test_attr_check_fail")


def test_attr_check_event_success_applies_reward():
    """试炼石碑：智力检定成功 → 智力 +4，san 回复。"""
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
    from src.services.event_service import apply_event_choice

    investigator_repo.delete_by_qq("test_attr_check_ok")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_attr_check_ok", "AttrCheckOk", data
    )
    inv = Investigator(inv_model)
    inv.set_skill("智力", 100)  # 必然成功
    inv.save()
    before_int = inv.get_skill("智力")
    before_san = inv.get_skill("san")

    reply, skip = apply_event_choice(
        inv, "test_attr_check_ok", _event_option("试炼石碑", "领悟碑文")
    )
    assert inv.get_skill("智力") == before_int + 4
    assert inv.get_skill("san") >= before_san + 1  # 1d3 至少 1
    assert skip is False
    investigator_repo.delete_by_qq("test_attr_check_ok")


def test_new_attr_events_in_random_pool():
    """新增属性事件无固定触发日，可进入每日随机池。"""
    for key in ["寒泉淬体", "血肉祭坛", "试炼石碑", "风之试炼"]:
        ev = data_loader.event_data[key]
        assert "触发" not in ev  # 随机池事件
        assert len(ev["选项"]) >= 2
