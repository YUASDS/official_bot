"""回归测试：战斗结束后（胜利/逃跑）战场残留清理 + 购买冒险卷后 /今日冒险 链路。

Bug：战斗结束调用 `_cleanup_battle`（async）未 await，协程被丢弃，战斗从未从
battle_manager 移除 → 购买冒险卷后 /今日冒险 误报「你正在战斗中！」。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from database.db import add_gold
from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.plugins.adventure import _run_adventure, handle_combat_action
from src.services.shop_service import shop_service
from src.utils.active_battles import battle_manager
from util.DaylyRecord import add_data, get_data, write_json


class FakeBot:
    type = "Console"

    async def send_msg(self, user_id=None, message=None):
        pass


def _make_inv(qq: str, name: str = "Residue"):
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(qq, name, data)
    return Investigator(inv_model)


async def _start_adventure(qq: str) -> None:
    """确定性开局：跳过奇遇、GM 房间彩蛋与理智检定，确保直接建立战斗。"""
    async def send(msg) -> None:
        pass

    async def finish(msg) -> None:
        raise SystemExit

    with patch("src.plugins.adventure.pick_random_event", return_value=None), patch(
        "src.plugins.adventure.run_sanity_and_madness",
        return_value=("", "", False, 0, False, 0),
    ), patch("src.plugins.adventure.npc_should_trigger", return_value=None), patch(
        "src.plugins.adventure.gm_room_should_trigger", return_value=False
    ), patch(
        "src.plugins.adventure.hidden_should_trigger", return_value=None
    ):
        try:
            await _run_adventure(qq, FakeBot(), send, finish)
        except SystemExit:
            pass


def test_victory_cleanup_removes_battle():
    """战斗胜利（按钮流）后战场残留必须清理：battle_manager 不再持有该用户战斗。"""
    qq = "qa_residue_victory"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    add_data(qq, "adventure_done", False)
    write_json()

    asyncio.run(_start_adventure(qq))
    battle = battle_manager.get_battle(qq)
    assert battle is not None, "初始冒险未建立战斗"

    battle.current_turn = "inv"
    battle.hp_record["mon"] = 0  # 怪物血量归零 → 战斗结束
    with patch.object(battle, "execute_action", return_value=("胜利",)), patch(
        "src.services.combat_messaging.render_pic", return_value=None
    ):
        asyncio.run(handle_combat_action(qq, "格斗", FakeBot(), ""))

    assert battle_manager.get_battle(qq) is None, "胜利后战场残留未清理"
    assert get_data(qq, "adventure_done") is True, "胜利后未标记今日冒险完成"

    battle_manager.remove_battle(qq)
    investigator_repo.delete_by_qq(qq)


def test_flee_cleanup_removes_battle():
    """逃跑成功后战场残留必须清理：battle_manager 不再持有该用户战斗。"""
    qq = "qa_residue_flee"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    add_data(qq, "adventure_done", False)
    write_json()

    asyncio.run(_start_adventure(qq))
    battle = battle_manager.get_battle(qq)
    assert battle is not None, "初始冒险未建立战斗"

    battle.current_turn = "inv"
    battle.fled = True  # 逃跑成功
    with patch.object(battle, "execute_action", return_value=("逃跑成功",)), patch(
        "src.services.combat_messaging.render_pic", return_value=None
    ):
        asyncio.run(handle_combat_action(qq, "逃跑", FakeBot(), ""))

    assert battle_manager.get_battle(qq) is None, "逃跑后战场残留未清理"
    assert get_data(qq, "adventure_done") is True, "逃跑后未标记今日冒险完成"

    battle_manager.remove_battle(qq)
    investigator_repo.delete_by_qq(qq)


def test_scroll_buy_then_adventure_not_in_battle():
    """胜利清理后：购买冒险卷 401 → /今日冒险 不应误报「还在战斗中」，并开启新冒险。"""
    qq = "qa_residue_chain"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    add_data(qq, "adventure_done", False)
    write_json()

    # 1. 今日冒险 → 建立战斗
    asyncio.run(_start_adventure(qq))
    assert battle_manager.get_battle(qq) is not None

    # 2. 战斗胜利（按钮流）
    battle = battle_manager.get_battle(qq)
    battle.current_turn = "inv"
    battle.hp_record["mon"] = 0
    with patch.object(battle, "execute_action", return_value=("胜利",)), patch(
        "src.services.combat_messaging.render_pic", return_value=None
    ):
        asyncio.run(handle_combat_action(qq, "格斗", FakeBot(), ""))
    assert battle_manager.get_battle(qq) is None, "胜利后战场残留未清理"

    # 3. 购买冒险卷 401
    add_gold(qq, 100000)
    items = shop_service.get_todays_shop("seed")
    ok, res = shop_service.buy_item(qq, "401", 1, items)
    assert ok, f"购买冒险卷失败: {res}"

    # 4. /今日冒险 → 消费冒险卷并开启新冒险，不得误报「还在战斗中」
    sent: list[str] = []

    async def send(msg) -> None:
        sent.append(str(msg))

    async def finish(msg) -> None:
        sent.append("FIN:" + str(msg))
        raise SystemExit

    with patch("src.plugins.adventure.pick_random_event", return_value=None), patch(
        "src.plugins.adventure.run_sanity_and_madness",
        return_value=("", "", False, 0, False, 0),
    ), patch("src.plugins.adventure.npc_should_trigger", return_value=None), patch(
        "src.plugins.adventure.gm_room_should_trigger", return_value=False
    ), patch(
        "src.plugins.adventure.hidden_should_trigger", return_value=None
    ):
        try:
            asyncio.run(_run_adventure(qq, FakeBot(), send, finish))
        except SystemExit:
            pass

    text = "\n".join(sent)
    assert "你正在战斗中" not in text, f"误报还在战斗中: {text[:300]}"
    assert battle_manager.get_battle(qq) is not None, "冒险卷应开启新的今日冒险"

    battle_manager.remove_battle(qq)
    investigator_repo.delete_by_qq(qq)


def test_command_flow_victory_cleanup_removes_battle():
    """/行动 命令流（handle_combat）战斗胜利后战场残留必须清理。"""
    from types import SimpleNamespace

    import src.plugins.adventure as adv_mod
    from src.models.monster import Monster

    qq = "qa_residue_cmd"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    add_data(qq, "adventure_done", False)
    write_json()

    asyncio.run(_start_adventure(qq))
    battle = battle_manager.get_battle(qq)
    assert battle is not None

    class FakeEvent:
        def get_user_id(self):
            return qq

    class FakeMsg:
        def __init__(self, text):
            self._text = text

        def extract_plain_text(self):
            return self._text

    cmd_captured: list[str] = []

    async def cmd_send(msg) -> None:
        cmd_captured.append(str(msg))

    async def cmd_finish(msg) -> None:
        cmd_captured.append("FIN:" + str(msg))

    battle.current_turn = "inv"
    battle.hp_record["mon"] = 0
    mock_cmd = SimpleNamespace(send=cmd_send, finish=cmd_finish)
    with patch.object(battle, "execute_action", return_value=("胜利",)), patch(
        "src.services.combat_messaging.render_pic", return_value=None
    ), patch.object(adv_mod, "combat_cmd", mock_cmd):
        asyncio.run(adv_mod.handle_combat(FakeEvent(), FakeBot(), FakeMsg("/行动 格斗")))

    assert battle_manager.get_battle(qq) is None, "命令流胜利后战场残留未清理"

    battle_manager.remove_battle(qq)
    investigator_repo.delete_by_qq(qq)
