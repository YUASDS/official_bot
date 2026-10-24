import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.plugins.gm import gift_all_gold, gift_all_scrolls, give_item, is_master


def test_is_master_reads_env():
    os.environ["MASTER"] = "gm_1, gm_2"
    assert is_master("gm_1")
    assert is_master("gm_2")
    assert not is_master("nobody")


def test_gift_all_gold_adds_to_all_users():
    import asyncio

    from database.db import get_info, init_user

    os.environ["MASTER"] = "gm_gold_test"
    init_user("gm_gold_test")
    init_user("gm_gold_other")
    before = get_info("gm_gold_other").gold

    count = asyncio.run(gift_all_gold(50))
    assert count >= 2
    assert get_info("gm_gold_other").gold == before + 50


def test_gift_all_scrolls_adds_401_to_investigators():
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo

    investigator_repo.delete_by_qq("gm_scroll_user")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save("gm_scroll_user", "GmHero", data)

    count = gift_all_scrolls(2)
    assert count >= 1

    inv = Investigator(inv_model)
    equipments, _ = inv.get_equipments()
    assert equipments.get("401") == 2
    investigator_repo.delete_by_qq("gm_scroll_user")


def test_give_item_adds_item():
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo

    investigator_repo.delete_by_qq("gm_give_user")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save("gm_give_user", "GiveHero", data)

    ok, text = give_item("gm_give_user", "101", 2)
    assert ok
    assert "弹簧折刀" in text
    inv = Investigator(inv_model)
    equipments, _ = inv.get_equipments()
    assert equipments.get("101") == 3  # 初始 1 + 发放 2
    investigator_repo.delete_by_qq("gm_give_user")


def test_give_item_default_quantity_is_one():
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo

    investigator_repo.delete_by_qq("gm_give_qty_user")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save("gm_give_qty_user", "QtyHero", data)

    ok, text = give_item("gm_give_qty_user", "302")
    assert ok
    assert "锁子甲" in text
    inv = Investigator(inv_model)
    equipments, _ = inv.get_equipments()
    assert equipments.get("302") == 1
    investigator_repo.delete_by_qq("gm_give_qty_user")


def test_give_item_player_missing():
    ok, text = give_item("gm_nobody_exists", "101")
    assert not ok
    assert "调查员" in text


def test_give_item_item_missing():
    from src.models.player import InvestigatorGenerator, investigator_repo

    investigator_repo.delete_by_qq("gm_give_miss_user")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    investigator_repo.create_and_save("gm_give_miss_user", "MissHero", data)

    ok, text = give_item("gm_give_miss_user", "99999")
    assert not ok
    assert "不存在" in text
    investigator_repo.delete_by_qq("gm_give_miss_user")
