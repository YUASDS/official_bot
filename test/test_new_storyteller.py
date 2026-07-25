import sys
import unittest
import os
from pathlib import Path
from unittest.mock import MagicMock

# Add project root to sys.path to allow imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# MOCK NONEBOT ENVIRONMENT BEFORE IMPORTS
sys.modules['nonebot'] = MagicMock()
mock_plugin = MagicMock()
mock_plugin.on = MagicMock()
sys.modules['nonebot.plugin'] = mock_plugin
sys.modules['nonebot.adapters'] = MagicMock()
sys.modules['nonebot.adapters.onebot'] = MagicMock()
sys.modules['nonebot.adapters.onebot.v11'] = MagicMock()
sys.modules['nonebot.params'] = MagicMock()
sys.modules['nonebot.typing'] = MagicMock()
sys.modules['nonebot.rule'] = MagicMock()
sys.modules['nonebot.log'] = MagicMock()

mock_alconna = MagicMock()
sys.modules['nonebot_plugin_alconna'] = mock_alconna
mock_alconna.on_alconna = MagicMock()
mock_alconna.Alconna = MagicMock()
mock_alconna.Args = MagicMock()
mock_alconna.Arparma = MagicMock()

mock_waiter = MagicMock()
sys.modules['nonebot_plugin_waiter'] = mock_waiter
mock_waiter.waiter = MagicMock()

sys.modules['nonebot'].on_command = MagicMock()
sys.modules['nonebot'].get_driver = MagicMock()

from plugins.StoryTeller.src.models.player import Investigator, investigator_repo, InvestigatorModel, InventoryItemModel
from plugins.StoryTeller.src.models.monster import Monster, monster_repo
from plugins.StoryTeller.src.models.item import Equipment, equipment_repo
from plugins.StoryTeller.src.services.battle import BattleService
from plugins.StoryTeller.src.services.sanity import perform_sanity_check
from plugins.StoryTeller.src.services.shop_service import shop_service
from plugins.StoryTeller.src.services.dice_roller import roll_dice
from peewee import SqliteDatabase
from unittest.mock import patch


def _get_any_monster():
    return Monster.load_random_for_day(1)


class TestStoryTellerIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            db = InvestigatorModel._meta.database
            if db.is_closed():
                db.connect()
            db.create_tables([InvestigatorModel, InventoryItemModel], safe=True)
        except Exception:
            pass

    def setUp(self):
        self.qq = "123456"
        self.inv = Investigator.load(self.qq, "TestInvestigator")
        self.inv.set_skill("hp", 20)
        self.inv.set_skill("san", 50)
        self.inv.set_skill("智力", 50)
        self.inv.set_skill("敏捷", 80)
        self.inv.set_skill("格斗", 80)
        self.inv.save()

        investigator_repo.add_item_to_inventory(self.inv._model, "101")
        import ujson
        self.inv._equipped = {"近战": "101"}
        self.inv.save()

    def test_dice_roller(self):
        expr, val = roll_dice("1d1+5")
        self.assertEqual(val, 6)

    def test_monster_loading(self):
        m = _get_any_monster()
        if m and m.is_valid:
            self.assertGreater(m.hp, 0)
            self.assertIsNotNone(m.name)
        else:
            print("Warning: No valid monsters found to test.")

    def test_sanity_check(self):
        m = _get_any_monster()
        if not m:
            return
        passed, desc, loss = perform_sanity_check(self.inv, m)
        self.assertIsInstance(passed, bool)
        self.assertIsInstance(desc, str)
        self.assertIsInstance(loss, int)

    def test_battle_flow_player_turn(self):
        m = _get_any_monster()
        if not m:
            return
        m.dex = 10
        m.hp = 20

        battle = BattleService(self.inv, m)
        start_msg = battle.start_turn()
        self.assertIn("敏捷鉴定", start_msg)
        self.assertEqual(battle.current_turn, "inv")

        res = battle.execute_action("格斗")
        res_str = "\n".join([str(x) for x in res])
        print(f"Battle Log (Player Attack): {res_str}")
        valid_keywords = ["发起进攻", "进行反击", "命中", "伤害", "攻击未命中", "失败", "成功", "进行格斗", "反击"]
        found = any(k in res_str for k in valid_keywords)
        self.assertTrue(found, f"Combat log missing keywords: {res_str}")

    def test_battle_flow_monster_turn(self):
        m = _get_any_monster()
        if not m:
            return
        m.dex = 999
        self.inv.set_skill("敏捷", 10)
        self.inv.save()

        battle = BattleService(self.inv, m)
        start_msg = battle.start_turn()
        self.assertEqual(battle.current_turn, "mon")

        res = battle.execute_action("闪避")
        res_str = "\n".join([str(x) for x in res])
        print(f"Battle Log (Player Dodge): {res_str}")

    @patch('plugins.StoryTeller.src.services.shop_service.reduce_gold')
    def test_shop_service_buy(self, mock_reduce_gold):
        mock_reduce_gold.return_value = True
        shop = shop_service.get_todays_shop("seed")
        self.assertIsInstance(shop, dict)

    def test_investigator_creation(self):
        new_qq = "99999"
        inv = Investigator.load(new_qq, "Newbie")
        self.assertEqual(inv.name, "Newbie")
        self.assertEqual(inv.hp, (inv.get_skill("体质") + inv.get_skill("体型")) // 10)
        self.assertTrue(inv.is_survive)
        str_val = inv.get_skill("力量")
        self.assertTrue(15 <= str_val <= 90, f"Strength {str_val} out of range")
        self.assertEqual(inv.get_skill("手枪"), 20)


if __name__ == "__main__":
    unittest.main()
