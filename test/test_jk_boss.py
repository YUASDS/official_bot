"""隐藏挑战「JK」（怪物 48）测试：AI 状态机 / 大失败自伤 / 共享触发 / 流程 / 奖励 / E09。"""

import random
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.models.item import Equipment
from src.models.monster import Monster
from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.services.battle import BattleService
from src.services.dice_roller import SuccessLevel
from src.utils.active_battles import battle_manager


# --- 辅助：构造调查员 ---
def _make_inv(qq: str, name: str = "JK"):
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(qq, name, data)
    return Investigator(inv_model)


def _make_battle(qq: str) -> BattleService:
    inv = _make_inv(qq)
    return BattleService(inv, Monster("48"))


# --- C1：自由攻击池 / 多法术 / 不闪避 ---
def test_jk_attack_pool_random_melee_ranged():
    m = Monster("48")
    types = {m.get_ai_action("mon")["type"] for _ in range(200)}
    assert types == {"melee", "ranged"}
    action = m.get_ai_action("mon")
    assert action["skill"] == 90
    assert action["damage"] in ("3d6+2+1d6", "3d6+2")
    assert action["attack_fumble_effect"] == {"自伤": 5}
    # 纯读：不推进状态
    assert m._shots_fired == 0 and m._ai_pending_spell is None


def test_jk_multi_spell_hp_low_heal():
    m = Monster("48")
    m.hp = 10  # < 31×0.5
    m.advance_ai()
    assert m._ai_pending_spell == "s002"
    assert m.get_ai_action("mon")["type"] == "spell"


def test_jk_multi_spell_probability_shield():
    from unittest.mock import patch

    m = Monster("48")
    with patch("random.random", return_value=0.1):
        m.advance_ai()
    assert m._ai_pending_spell == "s003"
    m2 = Monster("48")
    with patch("random.random", return_value=0.9):
        m2.advance_ai()
    assert m2._ai_pending_spell is None  # 未命中规则 → 正常推进不卡回合


def test_jk_multi_spell_mp_insufficient_not_blocking():
    m = Monster("48")
    m.mp = 1  # s002 需 2 / s003 需 3
    m.hp = 10
    m.advance_ai()
    assert m._ai_pending_spell is None
    assert m.get_ai_action("mon")["type"] in ("melee", "ranged")


def test_jk_spell_turn_no_dodge():
    m = Monster("48")
    m._ai_pending_spell = "s003"
    m._ai_phase = "受伤后"
    m.complete_spell_turn()
    assert m._ai_pending_spell is None
    assert m.is_dodging is False  # 不闪避：施法后仍只反击
    assert m.get_ai_action("mon")["type"] in ("melee", "ranged")


def test_jk_cast_heal_in_battle():
    bs = _make_battle("qa_jk_cast_heal")
    bs.hp_record["inv"] = 99999
    bs.monster._ai_pending_spell = "s002"
    bs.monster._ai_phase = "受伤后"
    bs.monster.hp = 20
    bs.hp_record["mon"] = 20
    bs.current_turn = "mon"
    result = bs._execute_monster_action("反击")
    out = "\n".join(str(x) for x in result)
    assert "施法·愈合术" in out
    assert "康师傅冰红茶" in out  # 法术文案.s002 原文
    assert bs.monster.hp > 20
    assert bs.monster.is_dodging is False
    assert bs.monster.mp == 10  # 12 - 2
    investigator_repo.delete_by_qq("qa_jk_cast_heal")


def test_jk_cast_shield_in_battle():
    bs = _make_battle("qa_jk_cast_shield")
    bs.hp_record["inv"] = 99999
    bs.monster._ai_pending_spell = "s003"
    bs.monster._ai_phase = "受伤后"
    bs.current_turn = "mon"
    result = bs._execute_monster_action("反击")
    out = "\n".join(str(x) for x in result)
    assert "施法·肉体守护" in out
    assert "绿光风衣" in out  # 法术文案.s003 原文
    assert bs.monster_temp_hp > 0
    assert bs.monster.mp == 9  # 12 - 3
    investigator_repo.delete_by_qq("qa_jk_cast_shield")


# --- C3：大失败自伤 5（近战/远程一致） ---
def test_jk_melee_fumble_self_damage_5():
    bs = _make_battle("qa_jk_fumble_melee")
    melee = dict(bs.monster._ai_data["攻击"][0])
    before = bs.hp_record["mon"]
    text, _ = bs._resolve_dodge(
        SimpleNamespace(level1=SuccessLevel.CRITICAL_FAILURE, level2=0), melee
    )
    assert bs.hp_record["mon"] == before - 5
    assert "巴卡那" in text
    investigator_repo.delete_by_qq("qa_jk_fumble_melee")


def test_jk_ranged_fumble_self_damage_5():
    bs = _make_battle("qa_jk_fumble_ranged")
    ranged = dict(bs.monster._ai_data["攻击"][1])
    before = bs.hp_record["mon"]
    text, _ = bs._resolve_dodge(
        SimpleNamespace(level1=SuccessLevel.CRITICAL_FAILURE, level2=0), ranged
    )
    assert bs.hp_record["mon"] == before - 5
    assert "仁慈" in text  # 远程大失败：嘴硬「这是我的仁慈」+ 自伤5
    investigator_repo.delete_by_qq("qa_jk_fumble_ranged")


def test_jk_plain_failure_no_self_damage():
    bs = _make_battle("qa_jk_fail_plain")
    melee = dict(bs.monster._ai_data["攻击"][0])
    before = bs.hp_record["mon"]
    text, _ = bs._resolve_dodge(
        SimpleNamespace(level1=SuccessLevel.FAILURE, level2=SuccessLevel.FAILURE),
        melee,
    )
    assert bs.hp_record["mon"] == before  # 普通失败无特效
    assert "有两下子" in text
    investigator_repo.delete_by_qq("qa_jk_fail_plain")


def test_jk_melee_fumble_during_player_critfail():
    """玩家大失败 + JK 大失败：JK 自伤 5 且不反击。"""
    bs = _make_battle("qa_jk_fumble_both")
    bs.investigator._equipped["近战"] = "101"
    melee = dict(bs.monster._ai_data["攻击"][0])
    before = bs.hp_record["mon"]
    bs._monster_fumble(melee, SuccessLevel.CRITICAL_FAILURE)
    assert bs.hp_record["mon"] == before - 5
    investigator_repo.delete_by_qq("qa_jk_fumble_both")


# --- C6：E09 互认替代（509/510） ---
def test_e09_relics_substitutes():
    from src.services.ending_engine import e09_relics_count

    qq = "qa_jk_e09"
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    relics = ["400", "501", "502", "503", "504", "506", "507", "508"]

    def mk(held: list[str]) -> Investigator:
        investigator_repo.delete_by_qq(qq)
        model = investigator_repo.create_and_save(qq, "E09T", data)
        for rid in held:
            investigator_repo.add_item_to_inventory(model, rid, 1)
        return Investigator(investigator_repo.find_by_qq(qq))

    assert e09_relics_count(mk(relics)) == 8
    assert e09_relics_count(mk(relics[:7] + ["509"])) == 8
    assert e09_relics_count(mk(relics[:7] + ["510"])) == 8
    assert e09_relics_count(mk(relics[:6] + ["509"])) == 7
    assert e09_relics_count(mk(relics[:7] + ["509", "510"])) == 8  # 双持有只 +1
    investigator_repo.delete_by_qq(qq)


# --- D2：武器 511 / 信物 510 ---
def test_weapon_511_promax():
    w = Equipment("511")
    assert w.is_valid
    assert w.name == "圣剑发射器·Promax"
    assert w.part == "远程" and w.identify_skill == "格斗"
    assert w.damage_dice == "3d6+2"
    assert w.bullet == 6 and w.max_bullet == 6
    assert w.is_extreme_double  # ex=1（int 真值）
    assert w.breakable is False
    assert w.skill_bonus == ["射击", "三连射", "换弹"]


def test_weapon_511_equip_actions():
    qq = "qa_jk_weapon"
    inv = _make_inv(qq)
    investigator_repo.add_item_to_inventory(inv._model, "511", 1)
    investigator_repo.equip_item(qq, "511")
    inv.update_equipment()
    actions = inv.get_available_actions()["inv"]
    assert "三连射" in actions
    assert "换弹" in actions
    assert "射击" in actions
    investigator_repo.delete_by_qq(qq)


def test_relic_510_loads():
    item = Equipment("510")
    assert item.is_valid
    assert item.name == "主角的徽记"
    assert item.type == "misc" and item.part == "misc"
    assert item.price == 0


# --- C7：共享触发（jk/qiren 同一层） ---
def test_jk_unlocked_gate():
    from src.plugins.jk import jk_unlocked

    inv = _make_inv("qa_jk_gate")
    # JK 前置：110+501 同时持有（all 模式），此处只验日门边界
    investigator_repo.add_item_to_inventory(inv._model, "110", 1)
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    inv.day = 12
    assert jk_unlocked(inv) is False
    inv.day = 13
    assert jk_unlocked(inv) is True
    inv.day = 39
    assert jk_unlocked(inv) is True
    inv.day = 40
    assert jk_unlocked(inv) is False
    investigator_repo.delete_by_qq("qa_jk_gate")


def test_hidden_should_trigger_shared_dice():
    from unittest.mock import patch

    from src.plugins.jk import hidden_should_trigger

    inv = _make_inv("qa_jk_hidden")
    inv.day = 25
    # 未掷出 1 → 不触发
    with patch("src.plugins.jk.roll_dice", return_value=("20", 5)):
        assert hidden_should_trigger(inv) is None
    # 掷出 1 但无前置物品（jk 需 110+501 / qiren 需 501/508 任一）→ 无候选
    with patch("src.plugins.jk.roll_dice", return_value=("20", 1)):
        assert hidden_should_trigger(inv) is None
    # 持 501 单件 → 仅 qiren 候选（jk all 缺 110 不满足）→ "qiren"
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    with patch("src.plugins.jk.roll_dice", return_value=("20", 1)):
        assert hidden_should_trigger(inv) == "qiren"
    # 补 110 → jk/qiren 双候选随机
    investigator_repo.add_item_to_inventory(inv._model, "110", 1)
    with patch("src.plugins.jk.roll_dice", return_value=("20", 1)):
        r = hidden_should_trigger(inv)
        assert r in ("jk", "qiren")
    # 无候选（day10）→ None
    inv.day = 10
    with patch("src.plugins.jk.roll_dice", return_value=("20", 1)):
        assert hidden_should_trigger(inv) is None
    investigator_repo.delete_by_qq("qa_jk_hidden")


def test_ai_old_monsters_zero_regression():
    """10 个 AI 怪状态机零回归：开火阶段 / 受伤施法 / 施法后闪避 均不变。"""
    for mid in ("5", "10", "15", "35", "36", "38", "39", "40", "41", "42"):
        m = Monster(mid)
        assert m._ai_data is not None
        assert m._ai_phase == "远程"
        m.advance_ai()
        # 开火阶段行动类型与数据一致（38 为 ranged，其余近战型怪为 melee）
        assert m.get_ai_action("mon")["type"] == m._ai_data["远程"].get(
            "type", "melee"
        )
        for _ in range(8):
            m.advance_ai()
            m.get_ai_action("mon")
        m.hp = 1  # 受伤
        m.advance_ai()
        if m._ai_pending_spell:
            assert m.get_ai_action("mon")["type"] == "spell"
            m.complete_spell_turn()
            assert m.is_dodging is True  # 老怪施法后仍进闪避


# --- C8：冒险流程（强制 48 / 对话 / 跳日 / 门扉防御 / 开场大喝 / 图片） ---
class FakeBot:
    type = "Console"

    async def send_msg(self, user_id=None, message=None):
        pass


def _run_jk_adventure(qq, sent, bot=None, finish_raises=True):
    import asyncio
    from unittest.mock import patch

    from src.plugins.adventure import _run_adventure

    async def _no_pic(bot, img, send):
        return False

    async def _no_render(html):
        return None

    async def send(msg):
        sent.append(str(msg))

    async def finish(msg):
        sent.append("FIN:" + str(msg))
        raise SystemExit

    with patch("src.plugins.adventure.send_pic", _no_pic), patch(
        "src.plugins.adventure.render_pic", _no_render
    ), patch(
        "src.plugins.adventure.run_sanity_and_madness",
        return_value=("", "", False, 0, False, 0),
    ):
        try:
            asyncio.run(_run_adventure(qq, bot or FakeBot(), send, finish))
        except SystemExit:
            pass


def test_adventure_jk_forced_boss_replace():
    """jk_pending 置位后 /今日冒险 强制怪物 48 + 无环境 + 开场大喝。"""
    import asyncio
    from unittest.mock import patch

    from src.plugins.adventure import _run_adventure
    from src.plugins.jk import jk_pending

    qq = "qa_jk_adv"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    jk_pending[qq] = True

    sent: list[str] = []
    _run_jk_adventure(qq, sent)

    battle = battle_manager.get_battle(qq)
    assert battle is not None, "未创建战斗"
    assert battle.monster.id == "48"
    assert battle.monster.名字 == "“主角”"
    assert battle.monster.hp == 31 and battle.monster.armor == 4
    assert not battle.environment  # 无专属环境
    text = "\n".join(sent)
    assert "六脉神剑" in text  # 开场大喝（怪物 intro 后）
    jk_pending.pop(qq, None)
    battle_manager.remove_battle(qq)
    investigator_repo.delete_by_qq(qq)


def test_jk_dialogue_trigger_no_battle():
    """共享触发命中 jk → 发送现身对话（不直接开战），等待按钮选择。"""
    import asyncio
    from unittest.mock import patch

    from src.plugins.adventure import _run_adventure
    from src.utils.buttons import BUTTON_HANDLERS

    qq = "qa_jk_dialog"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    async def finish(msg):
        sent.append("FIN:" + str(msg))

    with patch("src.plugins.adventure.hidden_should_trigger", return_value="jk"):
        asyncio.run(_run_adventure(qq, FakeBot(), send, finish))

    text = "\n".join(sent)
    assert "抢走我主角位置" in text  # JK 入场台词（怪 48 已改名「“主角”」）
    assert "jk" in BUTTON_HANDLERS  # 挑战/不挑战 按钮已注册
    assert battle_manager.get_battle(qq) is None
    investigator_repo.delete_by_qq(qq)


def test_jk_button_decline_skips_day():
    import asyncio

    from src.plugins.jk import handle_jk_button
    from util.DaylyRecord import get_data

    qq = "qa_jk_decline"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()

    asyncio.run(handle_jk_button(qq, "不挑战", FakeBot(), ""))
    after = Investigator(investigator_repo.find_by_qq(qq))
    assert after.day == 26
    assert get_data(qq, "adventure_done") is True
    investigator_repo.delete_by_qq(qq)


def test_jk_button_challenge_forces_boss():
    import asyncio
    from unittest.mock import patch

    from src.plugins.jk import handle_jk_button, jk_pending

    qq = "qa_jk_challenge"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()

    sent: list[str] = []

    async def _no_pic(bot, img, send):
        return False

    async def _no_render(html):
        return None

    async def _send(msg):
        sent.append(str(msg))

    async def _finish(msg):
        sent.append("FIN:" + str(msg))
        raise SystemExit

    with patch("src.plugins.adventure.send_pic", _no_pic), patch(
        "src.plugins.adventure.render_pic", _no_render
    ), patch(
        "src.plugins.adventure.run_sanity_and_madness",
        return_value=("", "", False, 0, False, 0),
    ):
        try:
            asyncio.run(handle_jk_button(qq, "挑战", FakeBot(), ""))
        except SystemExit:
            pass

    battle = battle_manager.get_battle(qq)
    assert battle is not None
    assert battle.monster.id == "48"
    battle_manager.remove_battle(qq)
    investigator_repo.delete_by_qq(qq)


def test_day40_jk_pending_does_not_override_gatekeeper():
    """day40 残留 JK 挑战旗标不覆盖守门人：强制怪物仍落回 day40 池（守门人 36）。"""
    from src.plugins.jk import jk_pending

    qq = "qa_jk_day40"
    inv = _make_inv(qq)
    inv.day = 40
    inv.save()
    jk_pending[qq] = True

    sent: list[str] = []
    _run_jk_adventure(qq, sent)

    battle = battle_manager.get_battle(qq)
    assert battle is not None
    assert battle.monster.id == "36", "day40 必须为守门人 36，而非 JK（48）"
    jk_pending.pop(qq, None)
    battle_manager.remove_battle(qq)
    investigator_repo.delete_by_qq(qq)


def test_jk_first_meet_image_sends_and_sets_flag():
    """首次见面：形象图发送成功 → 置位 jk.img_shown 持久化防重发。"""
    import asyncio
    from unittest.mock import patch

    from src.plugins.adventure import _run_adventure
    from src.plugins.jk import jk_pending

    qq = "qa_jk_img"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    jk_pending[qq] = True

    sent: list[str] = []
    calls: list[str] = []

    async def _pic(bot, img, send):
        calls.append("pic")
        await send("IMG")
        return True

    async def _no_render(html):
        return None

    async def send(msg):
        sent.append(str(msg))

    async def finish(msg):
        raise SystemExit

    with patch("src.plugins.adventure.send_pic", _pic), patch(
        "src.plugins.adventure.render_pic", _no_render
    ), patch(
        "src.plugins.adventure.run_sanity_and_madness",
        return_value=("", "", False, 0, False, 0),
    ):
        try:
            asyncio.run(_run_adventure(qq, FakeBot(), send, finish))
        except SystemExit:
            pass

    assert calls, "应发送 JK 形象图"
    after = Investigator(investigator_repo.find_by_qq(qq))
    assert after.get_flag("jk.img_shown") is True  # 持久化防重发
    jk_pending.pop(qq, None)
    battle_manager.remove_battle(qq)
    investigator_repo.delete_by_qq(qq)


# --- 奖励：胜利 → BOSS 档掉落 + 信物 510 首杀保留 ---
# 48 已挂 hidden_elite 手动池（loots 系统）：断言读配置而非硬编码 BOSS 档
import json as _json

_BOSS_POOL_IDS = {
    str(e["物品"])
    for e in _json.load(
        open("plugins/StoryTeller/data/loots.json", encoding="utf-8-sig")
    )["pools"]["hidden_jk"]
}


def test_jk_reward_special_case():
    from database.db import get_info

    qq = "qa_jk_reward"
    inv = _make_inv(qq)
    gold_before = get_info(qq).gold
    bs = BattleService(inv, Monster("48"))
    bs.hp_record["mon"] = 0
    _header, detail = bs._victory_parts()
    equip, _ = inv.get_equipments()
    assert any(equip.get(i, 0) == 1 for i in _BOSS_POOL_IDS)  # BOSS 档池 1 件
    assert equip.get("510") == 1  # 信物段首杀保留
    assert gold_before + 30 <= get_info(qq).gold <= gold_before + 60  # 乌帕随档位
    assert "主角的徽记" in detail
    assert "饿啊！怎么会？" in _header  # 战败CG
    investigator_repo.delete_by_qq(qq)


def test_jk_reward_510_not_duplicated():
    from database.db import get_info

    qq = "qa_jk_reward2"
    inv = _make_inv(qq)
    gold_before = get_info(qq).gold
    bs = BattleService(inv, Monster("48"))
    bs.hp_record["mon"] = 0
    bs._victory_parts()
    # 第二次胜利：BOSS 档池物品可重复掉落，510 首杀不重复
    inv2 = Investigator(investigator_repo.find_by_qq(qq))
    bs2 = BattleService(inv2, Monster("48"))
    bs2.hp_record["mon"] = 0
    bs2._victory_parts()
    equip, _ = inv2.get_equipments()
    assert equip.get("510") == 1
    assert gold_before + 60 <= get_info(qq).gold <= gold_before + 120
    investigator_repo.delete_by_qq(qq)


def test_jk_defeat_normal_death_no_weapon():
    """战败 = 普通死亡链（E07 登记），不赠武器/信物。"""
    qq = "qa_jk_death"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("48"))
    bs.hp_record["inv"] = 0
    out = bs._check_combat_over()
    assert out is not None
    assert "死亡" in str(out)
    equip, _ = inv.get_equipments()
    assert equip.get("511", 0) == 0
    assert equip.get("510", 0) == 0
    assert inv.is_survive is False
    investigator_repo.delete_by_qq(qq)
