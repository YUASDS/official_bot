"""BOSS 通用框架（批次4）测试。

覆盖：
1. JK 配置完整性（boss_data.json schema 字段 / 文本键 / 怪物引用 / 奖励元数据一致性）；
2. 触发（boss_unlocked 日门 / boss_pick_daily 共享组掷骰 / 候选随机）；
3. 对话与按钮（send_boss_dialogue 标题+台词+按钮 kind/payload、BUTTON_HANDLERS 自动注册）；
4. 挑战/不挑战（boss_pending 置位 / 重入强制怪物 / 跳过今日 day+1 / day40 防御）；
5. 图片 / 开场大喝 / 强制环境（boss_image_for_monster / boss_battle_cry / boss_forced_environment）；
6. 奖励结算一致性（settlement 专属奖励分支 vs boss_data 元数据）；
7. qiren 收编后行为不变（unlocked / should_trigger / 对话 / 跳过）；
8. 新 BOSS 纯配置扩展（register_boss 微型 BOSS：触发/对话/按钮/挑战强制/跳过）；
9. flow 复用（对话.流程 走 flow_engine）。

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


def _make_inv(qq: str, name: str = "Boss") -> Investigator:
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


def _drive_send(boss_id: str, qq: str, bot=None):
    """调用 send_boss_dialogue，返回发送文本列表。"""
    from src.services.boss_framework import send_boss_dialogue

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(send_boss_dialogue(boss_id, qq, bot or ConsoleBot(), send))
    return sent


# --- 测试专用微型 BOSS 配置（验证 schema 扩展性；仅代码注册，不落生产数据） ---
MINI_BOSS = {
    "id": "test_champion",
    "怪物id": "45",
    "触发": {
        "条件": {"日": {"min": 13, "max": 39}},
        "前置物品": [],
        "概率": {"骰": "d20", "值": 1},
        "共享组": "test_group",
    },
    "对话": {
        "标题": "boss.test_champion.title",
        "台词": "boss.test_champion.appear",
        "挑战动作": "挑战",
        "不挑战动作": "不挑战",
        "按钮": [
            {"输入": "boss.test_champion.challenge", "动作": "挑战"},
            {"输入": "boss.test_champion.decline", "动作": "不挑战"},
        ],
        "挑战后": "boss.test_champion.challenge_pending",
        "跳过": "boss.test_champion.skip_done",
    },
    "战斗": {"开场大喝": None, "强制环境": None, "隔离": True},
    "奖励": {"胜利": {"乌帕": 30, "物品": [], "信物": []}, "战败": {}},
    "图片": {},
    "卡片": {},
}


def _register_mini():
    from src.services.boss_framework import register_boss

    register_boss("test_champion", MINI_BOSS)


def _unregister_mini():
    from src.services.boss_framework import unregister_boss

    unregister_boss("test_champion")


# --- C1：JK 配置完整性（schema 字段 / 文本键 / 怪物引用 / 奖励元数据） ---
def test_jk_boss_config_complete():
    from src.services.boss_framework import get_boss

    cfg = get_boss("jk")
    assert cfg["怪物id"] == "48"
    trig = cfg["触发"]
    assert trig["条件"]["日"] == {"min": 13, "max": 39}
    assert trig["共享组"] == "hidden"
    assert trig["概率"] == {"骰": "d20", "值": 1}
    dlg = cfg["对话"]
    for key in ("标题", "台词", "挑战后", "跳过"):
        assert dlg[key]
    assert [b["动作"] for b in dlg["按钮"]] == ["挑战", "不挑战"]
    # 文本键全部存在于 text_data
    for key in (dlg["标题"], dlg["台词"], dlg["挑战后"], dlg["跳过"], dlg["按钮"][0]["输入"], dlg["按钮"][1]["输入"]):
        assert data_loader.get_text(key, default="") != "", f"缺文本键 {key}"
    # 战斗绑定：开场大喝键存在；强制环境为 null（JK 无专属环境）
    assert cfg["战斗"]["开场大喝"] == "jk.battle_cry"
    assert cfg["战斗"]["强制环境"] is None
    assert cfg["图片"] == {"路径": "resources/images/jk.jpg", "flag": "jk.img_shown"}
    assert cfg["卡片"]["白色主题"] is True
    assert cfg["奖励"]["胜利"]["乌帕"] == 50
    assert cfg["奖励"]["胜利"]["信物"] == ["510"]
    assert "物品" not in cfg["奖励"]["胜利"]  # 手写掉落废弃（走 loots BOSS 档）
    # 怪物 48 存在（复用 monster_data AI 战斗）
    assert data_loader.monster_data.get("48") is not None


def test_qiren_boss_config_complete():
    from src.services.boss_framework import get_boss

    cfg = get_boss("qiren")
    assert cfg["怪物id"] == "38"
    trig = cfg["触发"]
    assert trig["条件"]["日"] == {"min": 20, "max": 39}
    assert trig["条件"]["物品"] == ["501", "508"]
    assert trig["共享组"] == "hidden"
    assert cfg["战斗"]["强制环境"] == "庄园.黑色满月"
    assert cfg["奖励"]["胜利"]["乌帕"] == 100


# --- C2：触发（unlocked 日门 / 共享组掷骰 / 候选随机） ---
def test_boss_unlocked_jk_day_gate():
    from src.services.boss_framework import boss_unlocked

    inv = _make_inv("qa_bf_gate")
    investigator_repo.add_item_to_inventory(inv._model, "110", 1)
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    for day, expect in ((12, False), (13, True), (39, True), (40, False)):
        inv.day = day
        assert boss_unlocked("jk", inv) is expect, f"jk day{day}"
    _cleanup("qa_bf_gate")


def test_boss_trigger_cfg_normalizes_mode():
    """归一化输出含前置模式（缺省 any）；jk=all + 110/501，qiren=any缺省 + 501/508。"""
    from src.services.boss_framework import boss_trigger_cfg

    jk = boss_trigger_cfg("jk")
    assert jk["前置模式"] == "all"
    assert jk["物品"] == ["110", "501"]
    qiren = boss_trigger_cfg("qiren")
    assert qiren["前置模式"] == "any"  # 缺省
    assert qiren["物品"] == ["501", "508"]


def test_boss_unlocked_jk_requires_both_items():
    """JK 前置模式 all：110+501 同时持有才解锁；仅 110 / 仅 501 / 无物品均不触发。"""
    from src.services.boss_framework import boss_unlocked

    inv = _make_inv("qa_bf_jk_and")
    inv.day = 25
    assert boss_unlocked("jk", inv) is False  # 无物品
    investigator_repo.add_item_to_inventory(inv._model, "110", 1)
    assert boss_unlocked("jk", inv) is False  # 仅 110
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    assert boss_unlocked("jk", inv) is True  # 110+501 同时持有
    investigator_repo.remove_item_from_inventory(inv.qq, "110", 1)
    assert boss_unlocked("jk", inv) is False  # 缺 110
    _cleanup("qa_bf_jk_and")


def test_boss_unlocked_qiren_any_mode_zero_change():
    """qiren 零变化：缺省 any，仅持 501 或仅持 508 均解锁；无物品不触发。"""
    from src.services.boss_framework import boss_unlocked

    inv = _make_inv("qa_bf_qiren_any")
    inv.day = 25
    assert boss_unlocked("qiren", inv) is False  # 无物品
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    assert boss_unlocked("qiren", inv) is True  # 仅 501
    investigator_repo.remove_item_from_inventory(inv.qq, "501", 1)
    investigator_repo.add_item_to_inventory(inv._model, "508", 1)
    assert boss_unlocked("qiren", inv) is True  # 仅 508
    _cleanup("qa_bf_qiren_any")


def test_boss_pick_daily_shared_group():
    from unittest.mock import patch

    from src.plugins.jk import hidden_should_trigger

    inv = _make_inv("qa_bf_hidden")
    inv.day = 25
    # 持 110+501 → jk/qiren 同解锁（hidden 共享组）
    investigator_repo.add_item_to_inventory(inv._model, "110", 1)
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    # 未掷出触发值 → 不触发
    with patch("src.plugins.jk.roll_dice", return_value=("20", 5)):
        assert hidden_should_trigger(inv) is None
    # 掷出触发值 → jk/qiren 双候选随机
    with patch("src.plugins.jk.roll_dice", return_value=("20", 1)):
        assert hidden_should_trigger(inv) in ("jk", "qiren")
    # 无候选（未持 501/508 单件 → 候选中 qiren 仍解锁）——
    # 仅持 110（缺 501）→ 双 BOSS 均不满足前置（jk all / qiren any）→ 无候选
    investigator_repo.remove_item_from_inventory(inv.qq, "501", 1)
    with patch("src.plugins.jk.roll_dice", return_value=("20", 1)):
        assert hidden_should_trigger(inv) is None
    # 无候选（day10 日门）→ None
    inv.day = 10
    with patch("src.plugins.jk.roll_dice", return_value=("20", 1)):
        assert hidden_should_trigger(inv) is None
    _cleanup("qa_bf_hidden")


def test_boss_pick_daily_evaluates_group_once():
    """共享组只掷一次骰：hidden 组命中后不再逐个独立掷骰。"""
    from unittest.mock import patch

    from src.services.boss_framework import boss_pick_daily

    inv = _make_inv("qa_bf_once")
    inv.day = 25
    investigator_repo.add_item_to_inventory(inv._model, "110", 1)
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    calls: list[str] = []

    def fake_roll(expr):
        calls.append(expr)
        return ("20", 1)

    assert boss_pick_daily(inv, roll_fn=fake_roll) in ("jk", "qiren")
    assert calls == ["d20"]  # 共享组一次掷骰
    _cleanup("qa_bf_once")


# --- C3：对话与按钮 ---
def test_jk_send_dialogue_title_appear_and_buttons():
    from src.utils.buttons import BUTTON_HANDLERS

    qq = "qa_bf_dlg"
    _make_inv(qq)
    out = _drive_send("jk", qq)
    text = "\n".join(out)
    assert "主角" in text  # jk.title
    assert "抢走我主角位置" in text  # jk.appear
    assert "jk" in BUTTON_HANDLERS  # boss_framework 导入时自动注册
    assert "qiren" in BUTTON_HANDLERS
    _cleanup(qq)


def test_jk_button_payload_routes_to_handler():
    """按钮 payload「挑战」经 BUTTON_HANDLERS["jk"] → handle_boss_button → boss_pending 置位。"""
    from unittest.mock import patch

    from src.services.boss_framework import boss_pending

    qq = "qa_bf_route"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()

    async def fake_run(uid, bot, send, finish):
        pass

    from src.utils.buttons import BUTTON_HANDLERS

    with patch("src.plugins.adventure._run_adventure", fake_run):
        asyncio.run(BUTTON_HANDLERS["jk"](qq, "挑战", ConsoleBot(), ""))
    assert boss_pending.pop(qq, None) == "jk"
    _cleanup(qq)


# --- C4：挑战 / 不挑战 / day40 防御 ---
def test_boss_button_decline_skips_day():
    import asyncio

    from src.services.boss_framework import handle_boss_button
    from util.DaylyRecord import get_data

    qq = "qa_bf_decline"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    asyncio.run(handle_boss_button("jk", qq, "不挑战", ConsoleBot(), ""))
    after = Investigator(investigator_repo.find_by_qq(qq))
    assert after.day == 26
    assert get_data(qq, "adventure_done") is True
    _cleanup(qq)


def test_boss_button_challenge_forces_boss():
    import asyncio
    from unittest.mock import patch

    from src.services.boss_framework import boss_pending, boss_resolve_forced, handle_boss_button

    qq = "qa_bf_challenge"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()

    async def fake_run(uid, bot, send, finish):
        pass

    with patch("src.plugins.adventure._run_adventure", fake_run):
        asyncio.run(handle_boss_button("jk", qq, "挑战", ConsoleBot(), ""))
    assert boss_pending.get(qq) == "jk"
    # 模拟下一次 /今日冒险：boss_resolve_forced 弹出旗标并返回 boss id
    assert boss_resolve_forced(qq, Investigator(investigator_repo.find_by_qq(qq))) == "jk"
    assert qq not in boss_pending
    _cleanup(qq)


def test_boss_resolve_forced_day40_guard():
    from src.services.boss_framework import boss_pending, boss_resolve_forced

    qq = "qa_bf_day40"
    inv = _make_inv(qq)
    inv.day = 40
    inv.save()
    boss_pending[qq] = "jk"
    assert boss_resolve_forced(qq, inv) is None  # day40 归守门人
    assert qq not in boss_pending  # 旗标已弹出
    _cleanup(qq)


def test_boss_resolve_forced_legacy_jk_pending():
    """兼容旧旗标：直接写 jk_pending/qiren_pending 仍被调度器消费。"""
    from src.plugins.jk import jk_pending
    from src.plugins.qiren import qiren_pending
    from src.services.boss_framework import boss_resolve_forced

    qq = "qa_bf_legacy"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    jk_pending[qq] = True
    assert boss_resolve_forced(qq, inv) == "jk"
    assert qq not in jk_pending
    qiren_pending[qq] = True
    assert boss_resolve_forced(qq, inv) == "qiren"
    _cleanup(qq)


# --- C5：图片 / 开场大喝 / 强制环境 ---
def test_boss_image_for_monster():
    from src.services.boss_framework import boss_image_for_monster

    assert boss_image_for_monster("48") == ("resources/images/jk.jpg", "jk.img_shown")
    assert boss_image_for_monster("38") is None
    assert boss_image_for_monster("1") is None


def test_boss_battle_cry():
    from src.services.boss_framework import boss_battle_cry

    assert "六脉神剑" in boss_battle_cry("48")
    assert boss_battle_cry("38") == ""
    assert boss_battle_cry("1") == ""


def test_boss_forced_environment():
    from src.services.boss_framework import boss_forced_environment

    assert boss_forced_environment("jk") == {}  # 无专属环境
    env = boss_forced_environment("qiren")
    assert env.get("name") == "庄园.黑色满月"
    assert env["怪物"]["射击"] == -30


# --- C6：奖励结算一致性（settlement BOSS 档掉落 + 信物首杀保留） ---
# 奖励结算一致性（settlement 掉落 + 信物首杀保留）：48→hidden_jk、38→hidden_qi 手动池
import json as _json

_BOSS_POOL_IDS = set()
for _pname in ("hidden_jk", "hidden_qi"):
    _BOSS_POOL_IDS |= {
        str(e["物品"])
        for e in _json.load(
            open("plugins/StoryTeller/data/loots.json", encoding="utf-8-sig")
        )["pools"][_pname]
    }


def test_jk_victory_reward_matches_config():
    from database.db import get_info

    from src.models.monster import Monster
    from src.services.battle import BattleService

    qq = "qa_bf_jkreward"
    inv = _make_inv(qq)
    gold_before = get_info(qq).gold
    bs = BattleService(inv, Monster("48"))
    bs.hp_record["mon"] = 0
    _header, detail = bs._victory_parts()
    equip, _ = inv.get_equipments()
    assert any(equip.get(i, 0) == 1 for i in _BOSS_POOL_IDS)  # BOSS 档池 1 件
    assert equip.get("510") == 1  # 信物段首杀保留
    assert gold_before + 30 <= get_info(qq).gold <= gold_before + 60  # 乌帕随档位
    assert "战利品" in detail
    _cleanup(qq)


def test_qiren_victory_reward_matches_config():
    from database.db import get_info

    from src.models.monster import Monster
    from src.services.battle import BattleService

    qq = "qa_bf_qireward"
    inv = _make_inv(qq)
    gold_before = get_info(qq).gold
    bs = BattleService(inv, Monster("38"))
    bs.hp_record["mon"] = 0
    _header, detail = bs._victory_parts()
    equip, _ = inv.get_equipments()
    assert any(equip.get(i, 0) == 1 for i in _BOSS_POOL_IDS)  # BOSS 档池 1 件
    assert equip.get("509") == 1  # 信物段首杀保留
    assert gold_before + 30 <= get_info(qq).gold <= gold_before + 60  # 乌帕随档位
    _cleanup(qq)


# --- C7：qiren 收编后行为不变 ---
def test_qiren_unlocked_and_should_trigger():
    from unittest.mock import patch

    from src.plugins.qiren import qiren_should_trigger, qiren_unlocked

    inv = _make_inv("qa_bf_qiren")
    inv.day = 19
    assert qiren_unlocked(inv) is False
    inv.day = 25
    assert qiren_unlocked(inv) is False  # 未持有 501/508
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    assert qiren_unlocked(inv) is True
    inv.day = 40
    assert qiren_unlocked(inv) is False
    inv.day = 25
    with patch("src.plugins.qiren.roll_dice", return_value=("20", 1)):
        assert qiren_should_trigger(inv) is True
    with patch("src.plugins.qiren.roll_dice", return_value=("20", 5)):
        assert qiren_should_trigger(inv) is False
    _cleanup("qa_bf_qiren")


def test_qiren_send_dialogue_and_decline():
    import asyncio

    from src.plugins.qiren import qiren_send_dialogue
    from src.services.boss_framework import handle_boss_button
    from util.DaylyRecord import get_data

    qq = "qa_bf_qiren_dlg"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(qiren_send_dialogue(qq, ConsoleBot(), send))
    text = "\n".join(sent)
    assert "满月下的身影" in text
    assert "第 41 天" in text

    asyncio.run(handle_boss_button("qiren", qq, "不挑战", ConsoleBot(), ""))
    after = Investigator(investigator_repo.find_by_qq(qq))
    assert after.day == 26
    assert get_data(qq, "adventure_done") is True
    _cleanup(qq)


# --- C8：新 BOSS 纯配置扩展（微型 BOSS 端到端） ---
def test_mini_boss_unlock_and_pick():
    from unittest.mock import patch

    from src.services.boss_framework import boss_pick_daily, boss_unlocked

    _register_mini()
    try:
        inv = _make_inv("qa_bf_mini")
        inv.day = 12
        assert boss_unlocked("test_champion", inv) is False
        inv.day = 20
        assert boss_unlocked("test_champion", inv) is True

        # 组间依次掷骰：hidden 组（jk/qiren）miss 后轮到 test_group（微型 BOSS）
        calls = {"n": 0}

        def fake_roll(expr):
            calls["n"] += 1
            return ("20", 5) if calls["n"] == 1 else ("20", 1)

        assert boss_pick_daily(inv, roll_fn=fake_roll) == "test_champion"
        assert calls["n"] == 2  # 每组一次掷骰（hidden miss → test_group hit）
        _cleanup("qa_bf_mini")
    finally:
        _unregister_mini()


def test_mini_boss_dialogue_and_buttons():
    from src.utils.buttons import BUTTON_HANDLERS

    _register_mini()
    try:
        qq = "qa_bf_mini_dlg"
        _make_inv(qq)
        out = _drive_send("test_champion", qq)
        text = "\n".join(out)
        assert "试炼幻象" in text  # boss.test_champion.title
        assert "由配置驱动的试炼幻象" in text  # boss.test_champion.appear
        assert "test_champion" in BUTTON_HANDLERS  # register_boss 自动注册按钮
        _cleanup(qq)
    finally:
        _unregister_mini()


def test_mini_boss_challenge_forces_monster_and_decline_skips():
    import asyncio
    from unittest.mock import patch

    from src.services.boss_framework import (
        boss_pending,
        boss_resolve_forced,
        handle_boss_button,
    )

    _register_mini()
    try:
        qq = "qa_bf_mini_btn"
        inv = _make_inv(qq)
        inv.day = 20
        inv.save()
        # 挑战：置位 boss_pending（_run_adventure 打桩避免真实开战）
        async def fake_run(uid, bot, send, finish):
            pass

        with patch("src.plugins.adventure._run_adventure", fake_run):
            asyncio.run(handle_boss_button("test_champion", qq, "挑战", ConsoleBot(), ""))
        assert boss_pending.get(qq) == "test_champion"
        assert boss_resolve_forced(qq, inv) == "test_champion"
        # 不挑战：day+1
        asyncio.run(handle_boss_button("test_champion", qq, "不挑战", ConsoleBot(), ""))
        after = Investigator(investigator_repo.find_by_qq(qq))
        assert after.day == 21
        _cleanup(qq)
    finally:
        _unregister_mini()


# --- C9：flow 复用（对话.流程 走 flow_engine） ---
def test_boss_dialogue_flow_reuse():
    from src.services.boss_framework import register_boss, unregister_boss
    from src.services.flow_engine import flow_states

    flow_boss = dict(MINI_BOSS)
    flow_boss["对话"] = {"流程": "lane_tale"}

    register_boss("flow_boss", flow_boss)
    try:
        qq = "qa_bf_flow"
        inv = _make_inv(qq)
        inv.day = 10
        inv.save()
        out = _drive_send("flow_boss", qq)
        text = "\n".join(out)
        assert "入夜时分" in text  # lane_tale 首节点文案
        assert qq in flow_states  # flow 已启动
        assert flow_states[qq]["flow_id"] == "lane_tale"
        flow_states.pop(qq, None)
        _cleanup(qq)
    finally:
        unregister_boss("flow_boss")


def test_unknown_boss_safe_noop():
    """未知 BOSS：send/handle 均静默安全（不崩溃）。"""
    import asyncio

    from src.services.boss_framework import handle_boss_button, send_boss_dialogue

    qq = "qa_bf_unknown"
    _make_inv(qq)

    async def send(msg):
        pass

    asyncio.run(send_boss_dialogue("no_such_boss", qq, ConsoleBot(), send))
    asyncio.run(handle_boss_button("no_such_boss", qq, "挑战", ConsoleBot(), ""))
    _cleanup(qq)
