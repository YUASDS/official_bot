"""批次 2「零行为收编」专项测试：周目联动配置 / 触发参数集中化 / 互斥链调度 / _skip_daily。

覆盖：
1. 触发配置读取（triggers 段各彩蛋参数正确、插件 unlocked 门按配置求值）；
2. 互斥链调度器（priority 排序、hidden 组共享一次判定、一天至多一个、after_monster 最低优先级）；
3. _skip_daily（day+1 / day40 冻结 / 解除冒险态）；
4. 周目联动魔法数字配置化（44 遭遇 / 32 迟疑 / 权重 / 黑色满月环境名 / jk 图片路径 flag）。

只测新增行为，不修改 src/。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import asyncio

import nonebot

nonebot.init()

from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    Investigator,
    InvestigatorGenerator,
    investigator_repo,
)
from src.services.data_loader import data_loader


def _make_inv(qq: str, name: str = "Cfg") -> Investigator:
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    data["san"] = 60
    data["意志"] = 60
    inv_model = investigator_repo.create_and_save(qq, name, data)
    return Investigator(inv_model)


def _cleanup(qq: str) -> None:
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)


class ConsoleBot:
    type = "Console"

    async def send_msg(self, user_id=None, message=None):
        pass


# --- C1：触发配置读取（triggers 段参数正确） ---
def test_trigger_config_params():
    jk = data_loader.get_trigger("jk")
    assert jk["id"] == "jk" and jk["group"] == "hidden" and jk["kind"] == "hidden"
    assert jk["priority"] == 1
    assert jk["day_min"] == 13 and jk["day_max"] == 39
    assert jk["dice"] == "d20" and jk["value"] == 1

    qiren = data_loader.get_trigger("qiren")
    assert qiren["priority"] == 1 and qiren["group"] == "hidden"
    assert qiren["day_min"] == 20 and qiren["day_max"] == 39
    assert qiren["items"] == ["501", "508"]

    guest = data_loader.get_trigger("guest")
    assert guest["priority"] == 2
    assert guest["day_min"] == 5 and guest["day_max"] == 39
    assert guest["dice"] == "d100" and guest["value"] == 3 and guest["relation"] == "lte"
    assert guest["souvenir_id"] == "310"

    npc = data_loader.get_trigger("npc")
    assert npc["priority"] == 3

    gm = data_loader.get_trigger("gm_room")
    assert gm["priority"] == 4
    assert gm["day_min"] == 10 and gm["day_max"] == 39
    assert gm["dice"] == "d100" and gm["value"] == 1 and gm["relation"] == "lte"
    assert gm["after_monster"] is True

    assert data_loader.get_trigger("no_such_trigger") == {}


def test_unlocked_gates_follow_config():
    """各彩蛋 unlocked 门按 triggers 配置 day_min/day_max 求值（边界与现状一致）。"""
    from src.plugins.jk import jk_unlocked
    from src.plugins.guest import guest_unlocked
    from src.plugins.gm_room import gm_room_unlocked
    from src.plugins.qiren import qiren_unlocked

    inv = _make_inv("qa_cfg_gates")
    # jk: 13-39（前置 110+501 all 模式，此处只验日门）
    investigator_repo.add_item_to_inventory(inv._model, "110", 1)
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    for day, expect in ((12, False), (13, True), (39, True), (40, False)):
        inv.day = day
        assert jk_unlocked(inv) is expect, f"jk day{day}"
    investigator_repo.remove_item_from_inventory(inv.qq, "110", 1)
    investigator_repo.remove_item_from_inventory(inv.qq, "501", 1)
    # guest: 5-39
    for day, expect in ((4, False), (5, True), (39, True), (40, False)):
        inv.day = day
        assert guest_unlocked(inv) is expect, f"guest day{day}"
    # gm_room: 10-39
    for day, expect in ((9, False), (10, True), (39, True), (40, False)):
        inv.day = day
        assert gm_room_unlocked(inv) is expect, f"gm day{day}"
    # qiren: 20-39 + 持 501/508 任一
    inv.day = 19
    assert qiren_unlocked(inv) is False
    inv.day = 25
    assert qiren_unlocked(inv) is False  # 未持有物品
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    assert qiren_unlocked(inv) is True
    inv.day = 40
    assert qiren_unlocked(inv) is False
    _cleanup("qa_cfg_gates")


def test_souvenir_id_from_config():
    """迁移后纪念品 id 由 flow_data 承载（flow.纪念品 / souvenir 节点 物品），与 triggers 一致。"""
    from src.services.flow_engine import get_flow

    assert get_flow("sword_magic").get("纪念品") == "310"
    assert get_flow("sword_magic")["节点"]["souvenir"]["物品"] == "310"
    assert data_loader.get_trigger("guest")["souvenir_id"] == "310"


def test_loop_linkage_config_reads():
    """周目联动魔法数字收编：44 遭遇 / 32 迟疑 / 权重 / 黑色满月环境池排除。"""
    from unittest.mock import patch

    from src.plugins.adventure import (
        _apply_hound_hesitation,
        _hound_weight,
        _linkage_encounter,
    )
    from src.services.battle import BattleService
    from src.models.monster import Monster

    # 环境池归属：黑色满月由「入池:false」标记排除，不再是 loop_linkage 键
    from src.plugins.adventure import pick_random_environment
    from src.services.data_loader import data_loader

    with patch("random.choice", return_value="浓雾"):
        assert pick_random_environment() == "浓雾"  # 普通环境可被选中
    pool_keys = [k for k, v in data_loader.environment_data.items() if v.get("入池", True) is not False]
    assert "庄园.黑色满月" not in pool_keys

    inv = _make_inv("qa_cfg_link")
    inv.day = 12
    with patch("src.plugins.adventure.random.randint", return_value=50):
        assert _linkage_encounter(inv) is None  # 无 past.hound 旗标
    inv.set_flag("past.hound", "killed")
    with patch("src.plugins.adventure.random.randint", return_value=50):
        assert _linkage_encounter(inv) == "44"  # day12 + killed → 44
    with patch("src.plugins.adventure.random.randint", return_value=51):
        assert _linkage_encounter(inv) is None  # 概率未命中
    inv.day = 13
    with patch("src.plugins.adventure.random.randint", return_value=50):
        assert _linkage_encounter(inv) is None  # 非 12/20 天不遭遇

    inv.set_flag("past.hound", "killed_by")
    assert _hound_weight(inv) == {"32": 2}
    inv.set_flag("past.hound", "killed")
    assert _hound_weight(inv) is None

    inv.set_flag("past.hound", "killed_by")
    service = BattleService(inv, Monster("32"))
    _apply_hound_hesitation(service, inv, "32")
    assert service.hound_hesitates is True
    _cleanup("qa_cfg_link")


# --- C2：互斥链调度器（priority 排序 / hidden 组共享 / 一天至多一个 / after_monster） ---
def test_triggers_sorted_priority_order():
    from src.plugins.adventure import _triggers_sorted

    order = [t["id"] for t in _triggers_sorted()]
    assert order == ["jk", "qiren", "guest", "npc", "gm_room"]
    assert [t["priority"] for t in _triggers_sorted()] == [1, 1, 2, 3, 4]


def test_dispatch_hidden_first_returns_before_others():
    """hidden 命中即接管当日：guest/npc 不再判定（互斥：一天至多一个）。"""
    from unittest.mock import patch

    from src.plugins.adventure import _dispatch_daily_triggers

    qq = "qa_disp_hidden"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    called: list[str] = []
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    def fake_hidden(i):
        called.append("hidden")
        return "jk"

    def fake_guest(i):
        called.append("guest")
        return "sword_magic"

    def fake_npc(i):
        called.append("npc")
        return None

    def fake_gm(i):
        called.append("gm_room")
        return False

    async def run():
        with patch("src.plugins.adventure.hidden_should_trigger", fake_hidden), patch(
            "src.plugins.adventure.guest_should_trigger", fake_guest
        ), patch("src.plugins.adventure.npc_should_trigger", fake_npc), patch(
            "src.plugins.adventure.gm_room_should_trigger", fake_gm
        ):
            return await _dispatch_daily_triggers(
                qq, inv, ConsoleBot(), send, after_monster=False
            )

    assert asyncio.run(run()) is True
    assert called == ["hidden"]  # guest/npc/gm_room 未判定
    assert any("主角" in s for s in sent)  # JK 现身对话已发送
    _cleanup(qq)


def test_dispatch_hidden_group_single_roll_then_guest():
    """jk/qiren 同 hidden 组：共享一次判定（hidden 只调一次），miss 后按序轮到 guest。"""
    from unittest.mock import patch

    from src.plugins.adventure import _dispatch_daily_triggers
    from src.plugins.guest import guest_active

    qq = "qa_disp_guest"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    called: list[str] = []
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    def fake_hidden(i):
        called.append("hidden")
        return None

    def fake_guest(i):
        called.append("guest")
        return "sword_magic"

    def fake_npc(i):
        called.append("npc")
        return None

    async def run():
        with patch("src.plugins.adventure.hidden_should_trigger", fake_hidden), patch(
            "src.plugins.adventure.guest_should_trigger", fake_guest
        ), patch("src.plugins.adventure.npc_should_trigger", fake_npc):
            return await _dispatch_daily_triggers(
                qq, inv, ConsoleBot(), send, after_monster=False
            )

    try:
        result = asyncio.run(run())
    except BaseException:
        # guest_enter 触发即以 FinishedException 中断当日冒险（adventure 内正常出口）；
        # 全量收集期 nonebot.exception 会被 mock，用 BaseException 保证两种环境均健壮。
        result = True
    assert result is True
    assert called == ["hidden", "guest"]  # hidden 仅一次（共享组），guest 命中
    assert any("空间裂缝" in s or "王都" in s for s in sent)  # 乱入入场文案
    guest_active.pop(qq, None)
    _cleanup(qq)


def test_dispatch_npc_after_hidden_guest_miss():
    """hidden/guest miss 后轮到 npc。"""
    from unittest.mock import patch

    from src.plugins.adventure import _dispatch_daily_triggers

    qq = "qa_disp_npc"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    called: list[str] = []

    async def send(msg):
        pass

    def fake_hidden(i):
        called.append("hidden")
        return None

    def fake_guest(i):
        called.append("guest")
        return None

    def fake_npc(i):
        called.append("npc")
        return "guardian"

    async def run():
        with patch("src.plugins.adventure.hidden_should_trigger", fake_hidden), patch(
            "src.plugins.adventure.guest_should_trigger", fake_guest
        ), patch("src.plugins.adventure.npc_should_trigger", fake_npc):
            return await _dispatch_daily_triggers(
                qq, inv, ConsoleBot(), send, after_monster=False
            )

    assert asyncio.run(run()) is True
    assert called == ["hidden", "guest", "npc"]
    _cleanup(qq)


def test_dispatch_after_monster_only_gm_room():
    """after_monster=True 只判定 gm_room（互斥链最低优先级，选怪后触发）。"""
    from unittest.mock import patch

    from src.plugins.adventure import _dispatch_daily_triggers

    qq = "qa_disp_gm"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    called: list[str] = []

    async def send(msg):
        pass

    async def fake_enter(uid, inv_, bot, send_):
        called.append("enter")

    async def run():
        with patch("src.plugins.adventure.gm_room_should_trigger", return_value=True), patch(
            "src.plugins.adventure.gm_room_enter", fake_enter
        ):
            return await _dispatch_daily_triggers(
                qq, inv, ConsoleBot(), send, after_monster=True
            )

    assert asyncio.run(run()) is True
    assert called == ["enter"]
    _cleanup(qq)


def test_dispatch_none_on_day40():
    """day40：hidden/guest/npc/gm_room 全部不触发（门扉归守门人，互斥链空转）。"""
    from unittest.mock import patch

    from src.plugins.adventure import _dispatch_daily_triggers

    qq = "qa_disp_day40"
    inv = _make_inv(qq)
    inv.day = 40
    inv.save()
    called: list[str] = []

    async def send(msg):
        pass

    def fake_none(i):
        called.append("checked")
        return None

    async def run():
        with patch("src.plugins.adventure.hidden_should_trigger", fake_none), patch(
            "src.plugins.adventure.guest_should_trigger", fake_none
        ), patch("src.plugins.adventure.npc_should_trigger", fake_none), patch(
            "src.plugins.adventure.gm_room_should_trigger", lambda i: False
        ):
            return await _dispatch_daily_triggers(
                qq, inv, ConsoleBot(), send, after_monster=False
            )

    assert asyncio.run(run()) is False
    assert called == ["checked", "checked", "checked"]  # hidden/guest/npc 依次判定未命中
    _cleanup(qq)


# --- C3：_skip_daily（day+1 / day40 冻结 / 解除冒险态） ---
def test_skip_daily_advances_day():
    from src.plugins.adventure import _skip_daily

    qq = "qa_skip_adv"
    inv = _make_inv(qq)
    inv.day = 25
    inv.is_adventure = True
    inv.save()
    _skip_daily(qq, inv)
    after = Investigator(investigator_repo.find_by_qq(qq))
    assert after.day == 26
    assert after.is_adventure is False
    _cleanup(qq)


def test_skip_daily_day40_freeze():
    from src.plugins.adventure import _skip_daily

    qq = "qa_skip_40"
    inv = _make_inv(qq)
    inv.day = 40
    inv.is_adventure = True
    inv.save()
    _skip_daily(qq, inv)
    after = Investigator(investigator_repo.find_by_qq(qq))
    assert after.day == 40  # day40 冻结：终局由守门人战斗结算
    assert after.is_adventure is False
    _cleanup(qq)
