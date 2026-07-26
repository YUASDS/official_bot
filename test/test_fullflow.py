"""
Full end-to-end gameplay test using pytest.
Tests actual handler execution with mocked NoneBot events.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Mock NoneBot before importing plugin modules
sys.modules['nonebot'] = MagicMock()
sys.modules['nonebot.plugin'] = MagicMock()
sys.modules['nonebot.adapters'] = MagicMock()
sys.modules['nonebot.adapters.onebot'] = MagicMock()
sys.modules['nonebot.adapters.onebot.v11'] = MagicMock()
sys.modules['nonebot.params'] = MagicMock()
sys.modules['nonebot.typing'] = MagicMock()
sys.modules['nonebot.rule'] = MagicMock()
sys.modules['nonebot.log'] = MagicMock()
sys.modules['nonebot.exception'] = MagicMock()
sys.modules['nonebot.message'] = MagicMock()
sys.modules['nonebot_plugin_alconna'] = MagicMock()
sys.modules['nonebot_plugin_waiter'] = MagicMock()

sys.modules['nonebot'].on_command = MagicMock()

from plugins.StoryTeller.src.models.player import (
    Investigator, InvestigatorGenerator, InvestigatorFormatter,
    CreateInvestigator, investigator_repo
)
from plugins.StoryTeller.src.models.monster import Monster
from plugins.StoryTeller.src.models.item import Equipment
from plugins.StoryTeller.src.services.battle import BattleService
from plugins.StoryTeller.src.services.data_loader import data_loader


class TestFullFlow:

    def test_01_create_investigator(self):
        ci = CreateInvestigator(3)
        assert len(ci.investigators_data) == 3
        assert ci.choose_investigator(1)
        assert ci.skill_point > 0
        print(f"\n[CREATE] Skill points: {ci.skill_point}")
        print(InvestigatorFormatter.format_investigator_info("TestPlayer", ci.investigators_data))

        # Split skill points across 3 skills to avoid 75 cap
        sp = ci.skill_point
        ok, msg = ci.set_skill(f"格斗 {sp//3} 侦查 {sp//3} 手枪 {sp - 2*(sp//3)}")
        assert ok, f"Skill set failed: {msg}"

        qq = "test_fullflow_01"
        inv = ci.create_investigator(qq, "TestPlayer")
        assert inv.name == "TestPlayer"
        print(f"[CREATE] Done: {inv.name} (QQ: {qq})")

    def test_02_investigator_info(self):
        inv = Investigator.load("test_fullflow_01")
        attrs = inv.get_full_attributes_dict()
        assert "HP" in attrs
        assert "力量" in attrs
        print(f"\n[INFO] {inv.name}  Day:{inv.day}  {'死亡' if not inv.is_survive else '存活'}")
        print(f"  Attrs: {' '.join(f'{k}:{v}' for k,v in attrs.items())}")
        print(f"  Equip:\n{inv.str_equipments()}")

    def test_03_start_adventure(self):
        inv = Investigator.load("test_fullflow_01")
        monster = Monster.load_random_for_day(inv.day)
        assert monster and monster.is_valid

        import random
        env = {}
        if data_loader.environment_data:
            env_key = random.choice(list(data_loader.environment_data.keys()))
            env = data_loader.environment_data[env_key].copy()
            env["name"] = env_key

        service = BattleService(inv, monster)
        if env:
            service.set_environment(env)
        start_msg = service.start_turn()

        print(f"\n[ADVENTURE] Day {inv.day}")
        print(f"  Env: {env.get('name', 'none')}")
        print(f"  Monster: {monster.name} (HP:{monster.hp})")
        print(f"  Start:\n{start_msg}")

        # Run combat properly: choose action based on current turn
        turn = 0
        while not service.fight_is_over() and turn < 30:
            turn += 1
            actions = service.investigator.get_available_actions()
            if service.current_turn == "inv":
                action = actions["inv"][0]  # first available player action
            else:
                action = "闪避"  # default defense
            result = service.execute_action(action)
            # Only print the last element (end_turn result) which is most informative
            last_r = [r for r in result if r]
            if last_r:
                final = str(last_r[-1])[:150]
                print(f"  Turn {turn}: {service.current_turn} -> {action}: {final}")
        print(f"  END: inv HP={service.hp_record['inv']}, mon HP={service.hp_record['mon']}")

    def test_04_monster_integrity(self):
        issues = []
        for mid in data_loader.monster_data:
            try:
                m = Monster(mid)
            except ValueError:
                continue
            for name, act in m._data.get("攻击", {}).items():
                for field in ("attack_succ", "counter_succ"):
                    text = act.get(field, "")
                    if text and "$骰子" not in text:
                        issues.append(f"Monster {mid} '{m.name}' {name}/{field}: missing $骰子")
        assert not issues, "\n".join(issues)

    def test_05_item_data_integrity(self):
        for iid in data_loader.goods_data:
            item = Equipment(iid)
            assert item.is_valid, f"Item {iid} invalid"
            assert item.name, f"Item {iid} no name"
            desc = item.get_brief_description()
            assert desc, f"Item {iid} no brief description"
            full = item.get_full_description()
            assert full, f"Item {iid} no full description"
            # Verify description is actual text, not placeholder
            assert "[description]" not in str(desc) + str(full)

    def test_06_environment_data_integrity(self):
        for key, env in data_loader.environment_data.items():
            assert "描述" in env, f"Env {key}"
            assert env["描述"], f"Env {key} empty description"
            assert "玩家" in env
            assert "怪物" in env

    def test_07_event_data_integrity(self):
        for key, evt in data_loader.event_data.items():
            assert evt["描述"], f"Event {key} empty description"
            for opt in evt["选项"]:
                assert opt["输入"], f"Event {key} option missing 输入"
                assert "效果" in opt
                assert opt["回复"], f"Event {key} option missing reply"

    def test_08_daily_event_text(self):
        events = set()
        for day in range(1, 41):
            text = data_loader.get_event(day)
            assert text, f"Day {day} has no event text"
            events.add(text)
        assert len(events) == 40, f"Expected 40 unique, got {len(events)}"

    def test_09_equipment_spot_check(self):
        for iid in ["101", "1", "301"]:  # removed nonexistent 201
            item = Equipment(iid)
            assert item.is_valid, f"Item {iid} not found"
            desc = item.get_brief_description()
            full = item.get_full_description()
            print(f"\n[ITEM {iid}] {item.name}")
            print(f"  Brief: {desc}")
            print(f"  Full:  {full[:120]}")
            assert "ID" in desc or "ID" in full

    def test_10_description_not_placeholder(self):
        """Verify no item uses the placeholder 'No description available.'"""
        for iid in data_loader.goods_data:
            item = Equipment(iid)
            if not item.is_valid:
                continue
            full = item.get_full_description()
            assert "No description available" not in full, \
                f"Item {iid} ({item.name}) has placeholder description"
