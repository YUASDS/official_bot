import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.plugins.character import query_item_info


def test_query_visible_item_shows_info():
    text = query_item_info("player1", "101")
    assert "弹簧折刀" in text
    assert "武器" in text
    assert "近战" in text
    assert "未知物品" not in text


def test_query_hidden_items_hidden_for_non_gm():
    os.environ["MASTER"] = "gm_query_user"
    # 已开放查询的信物/管理员系列（设计意图：玩家可见）
    assert "幸运币" in query_item_info("player1", "400")
    assert "冒险卷" in query_item_info("player1", "401")
    assert "古圣者的遗愿" in query_item_info("player1", "501")
    assert "管理员的骰子袋" in query_item_info("player1", "308")
    assert "管理员的徽记" in query_item_info("player1", "309")
    # 仍隐藏：部分信物（防剧透 E09 收集体系；508 格拉基之泪已随纪念品框开放可见）
    assert "格拉基之泪" in query_item_info("player1", "508")
    # 仍隐藏：管理员风衣（GM 专属）
    assert query_item_info("player1", "306") == "未知物品"
    # 仍隐藏：JK 掉落（防剧透）
    assert query_item_info("player1", "510") == "未知物品"
    assert query_item_info("player1", "511") == "未知物品"
    # 仍隐藏：未实装物品（仅数据，无获取途径）
    assert query_item_info("player1", "600") == "未知物品"


def test_query_hidden_items_visible_for_gm():
    os.environ["MASTER"] = "gm_query_user"
    assert "幸运币" in query_item_info("gm_query_user", "400")
    assert "管理员风衣" in query_item_info("gm_query_user", "306")
    assert "圣剑发射器" in query_item_info("gm_query_user", "511")
    assert "守护挂坠" in query_item_info("gm_query_user", "600")


def test_query_nonexistent_item_unknown():
    text = query_item_info("player1", "99999")
    assert text == "未知物品"
