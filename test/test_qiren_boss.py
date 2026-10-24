import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from types import SimpleNamespace

from src.models.item import Equipment
from src.models.monster import Monster
from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.services.battle import BattleService
from src.services.dice_roller import SuccessLevel
from src.utils.active_battles import battle_manager


# --- 辅助：构造调查员 + 启（38）战斗 ---
# 38 已挂 hidden_elite 手动池（loots 系统）：断言读配置而非硬编码 BOSS 档
import json as _json

_BOSS_POOL_IDS = {
    str(e["物品"])
    for e in _json.load(
        open("plugins/StoryTeller/data/loots.json", encoding="utf-8-sig")
    )["pools"]["hidden_qi"]
}


def _make_inv(qq: str, name: str = "Qiren"):
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(qq, name, data)
    return Investigator(inv_model)


def _make_boss(qq: str, inv: Investigator = None) -> BattleService:
    m = Monster("38")
    if inv is None:
        inv = _make_inv(qq)
    bs = BattleService(inv, m)
    bs.set_environment(
        {"玩家": {"射击": -30, "闪避": 10}, "怪物": {"射击": -30, "敏捷": 10, "闪避": 10}}
    )
    return bs


# --- C1：AI 状态机 ---
def test_ai_phase_open_fire_twice_then_melee():
    m = Monster("38")
    assert m.get_ai_action("mon")["type"] == "ranged"
    m.advance_ai()
    assert m._shots_fired == 1 and m._ai_phase == "远程"
    m.advance_ai()
    assert m._shots_fired == 2 and m._ai_phase == "近战"
    assert m.get_ai_action("mon")["type"] == "melee"


def test_ai_hurt_interrupts_and_casts_spell():
    m = Monster("38")
    m.advance_ai()  # 1 发
    m.hp = 20  # 受伤
    m.advance_ai()
    assert m._ai_pending_spell in ("s002", "s003")
    assert m.get_ai_action("mon")["type"] == "spell"
    # 纯读：再取仍为法术，且不推进状态
    assert m.get_ai_action("mon")["type"] == "spell"
    assert m._ai_pending_spell in ("s002", "s003")
    m.complete_spell_turn()
    assert m.is_dodging and m._ai_pending_spell is None
    assert m.get_ai_action("mon")["type"] == "melee"


def test_ai_get_action_pure_read():
    m = Monster("38")
    shots = m._shots_fired
    m.get_ai_action("mon")
    m.get_ai_action("mon")
    assert m._shots_fired == shots
    assert m._ai_pending_spell is None


def test_old_monster_ai_absent():
    m = Monster("1")
    assert m._ai_data is None
    assert m.mp == 0 and m.max_mp == 0
    assert m.is_dodging is False
    # get_action 行为不变（整场缓存）
    a1 = m.get_action("mon")
    assert a1.get("skill") is not None


def test_monster_mp_from_will():
    m = Monster("38")
    assert m.max_mp == 14  # 意志 70 // 5


# --- C2/C4：环境与技能 ---
def test_env_ranged_shot_modifier():
    bs = _make_boss("qa_env_shot")
    bs.current_turn = "inv"
    ranged = bs.monster.get_ai_action("mon")  # 开火
    assert bs._get_monster_attack_skill(ranged) == 50  # 80 - 30
    melee = bs.monster._ai_melee_action()
    assert bs._get_monster_attack_skill(melee) == 104  # 不受射击修正
    investigator_repo.delete_by_qq("qa_env_shot")


def test_env_dodge_mode_skill():
    bs = _make_boss("qa_env_dodge")
    bs.monster._ai_dodging = True
    bs.monster._ai_phase = "受伤后"
    melee = bs.monster._ai_melee_action()
    bs.current_turn = "inv"
    assert bs._get_monster_attack_skill(melee) == 109  # 99 + 10
    # 怪物自身回合攻击仍用近战技能
    bs.current_turn = "mon"
    assert bs._get_monster_attack_skill(melee) == 104
    bs.monster._ai_dodging = False
    investigator_repo.delete_by_qq("qa_env_dodge")


# --- C3：文案分档与闪避不反击 ---
def test_attack_crit_uses_attack_crit_text():
    bs = _make_boss("qa_text_crit")
    melee = bs.monster._ai_melee_action()
    monster_text, _ = bs._handle_monster_attack_success(
        melee, SimpleNamespace(level1=SuccessLevel.CRITICAL_SUCCESS)
    )
    assert "漆黑" in monster_text  # 攻击大成功原文
    assert "$骰子=$伤害" not in monster_text  # 伤害占位被填充
    investigator_repo.delete_by_qq("qa_text_crit")


def test_attack_succ_text_and_ranged_player_texts():
    bs = _make_boss("qa_text_succ")
    melee = bs.monster._ai_melee_action()
    monster_text, _ = bs._handle_monster_attack_success(
        melee, SimpleNamespace(level1=SuccessLevel.SUCCESS)
    )
    assert "砸碎了一切" in monster_text
    ranged = bs.monster._ai_ranged_action()
    m2, _ = bs._handle_monster_attack_success(
        ranged, SimpleNamespace(level1=SuccessLevel.SUCCESS)
    )
    assert "胸膛正中" in m2  # 玩家文案.射击成功
    m3, _ = bs._handle_monster_attack_success(
        ranged, SimpleNamespace(level1=SuccessLevel.CRITICAL_SUCCESS)
    )
    assert "锁骨下方" in m3  # 玩家文案.射击大成功
    investigator_repo.delete_by_qq("qa_text_succ")


def test_monster_fumble_texts():
    bs = _make_boss("qa_text_fumble")
    melee = bs.monster._ai_melee_action()
    mt, _ = bs._resolve_dodge(
        SimpleNamespace(level1=SuccessLevel.CRITICAL_FAILURE, level2=SuccessLevel.CRITICAL_SUCCESS),
        melee,
    )
    assert "松开剑柄" in mt  # 近战大失败 attack_false 原文
    ranged = bs.monster._ai_ranged_action()
    mt2, _ = bs._resolve_dodge(
        SimpleNamespace(level1=SuccessLevel.CRITICAL_FAILURE, level2=0),
        ranged,
    )
    assert "炸" in mt2  # 远程大失败 attack_fumble（枪炸）
    investigator_repo.delete_by_qq("qa_text_fumble")


def test_dodge_mode_melee_no_counter():
    bs = _make_boss("qa_text_dodge")
    bs.investigator._equipped["近战"] = "101"
    bs.monster._ai_dodging = True
    bs.monster._ai_phase = "受伤后"
    melee = bs.monster._ai_melee_action()
    weapon = Equipment("101")
    hp_before = bs.hp_record["inv"]
    _roll, exchange, _end = bs._handle_player_melee_failure(
        SimpleNamespace(level1=SuccessLevel.FAILURE, level2=SuccessLevel.SUCCESS),
        weapon,
        melee,
        "roll",
    )
    assert bs.hp_record["inv"] == hp_before  # 闪避不反击伤害
    assert "侧身" in exchange
    bs.monster._ai_dodging = False
    investigator_repo.delete_by_qq("qa_text_dodge")


# --- C6：怪物施法 ---
def test_monster_cast_heal():
    bs = _make_boss("qa_cast_heal")
    bs.hp_record["inv"] = 99999
    bs.monster._ai_pending_spell = "s002"
    bs.monster._ai_phase = "受伤后"
    bs.monster.hp = 20
    bs.hp_record["mon"] = 20
    bs.current_turn = "mon"
    result = bs._execute_monster_action("反击")
    out = "\n".join(str(x) for x in result)
    assert "施法·愈合术" in out
    assert "月光倾泻在他身上" in out  # 法术文案.s002 原文
    assert bs.monster.hp > 20  # 回血
    assert bs.monster.is_dodging
    assert bs.monster.mp == 12  # 14 - 2
    investigator_repo.delete_by_qq("qa_cast_heal")


def test_monster_cast_shield():
    bs = _make_boss("qa_cast_shield")
    bs.hp_record["inv"] = 99999
    bs.monster._ai_pending_spell = "s003"
    bs.monster._ai_phase = "受伤后"
    bs.current_turn = "mon"
    result = bs._execute_monster_action("反击")
    out = "\n".join(str(x) for x in result)
    assert "施法·肉体守护" in out
    assert "铠甲" in out  # 法术文案.s003 原文
    assert bs.monster_temp_hp > 0  # 上盾
    assert bs.monster.mp == 11  # 14 - 3
    investigator_repo.delete_by_qq("qa_cast_shield")


# --- C7：触发与替换 ---
def test_qiren_unlocked_gate():
    from src.plugins.qiren import qiren_unlocked

    inv = _make_inv("qa_qiren_gate")
    inv.day = 19
    assert qiren_unlocked(inv) is False  # day < 20
    inv.day = 25
    assert qiren_unlocked(inv) is False  # 未持有 501/508
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    assert qiren_unlocked(inv) is True
    investigator_repo.delete_by_qq("qa_qiren_gate")


def test_qiren_should_trigger_probability():
    from unittest.mock import patch

    from src.plugins.qiren import qiren_should_trigger

    inv = _make_inv("qa_qiren_trig")
    inv.day = 25
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    with patch("src.plugins.qiren.roll_dice", return_value=("20", 1)):
        assert qiren_should_trigger(inv) is True
    with patch("src.plugins.qiren.roll_dice", return_value=("20", 5)):
        assert qiren_should_trigger(inv) is False
    investigator_repo.delete_by_qq("qa_qiren_trig")


def test_adventure_qiren_forced_boss_replace():
    """qiren_pending 置位后 /今日冒险 强制怪物 38 + 环境「庄园.黑色满月」。"""
    from src.plugins.adventure import _run_adventure
    from src.plugins.qiren import qiren_pending

    qq = "qa_qiren_adv"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    qiren_pending[qq] = True

    sent: list[str] = []

    class FakeBot:
        type = "Console"

    async def send(msg) -> None:
        sent.append(str(msg))

    async def finish(msg) -> None:
        sent.append("FIN:" + str(msg))
        raise SystemExit

    import asyncio

    try:
        asyncio.run(_run_adventure(qq, FakeBot(), send, finish))
    except SystemExit:
        pass

    battle = battle_manager.get_battle(qq)
    assert battle is not None, "未创建战斗"
    assert battle.monster.id == "38"
    assert battle.monster.名字 == "启"
    assert battle.environment.get("name") == "庄园.黑色满月"
    assert battle.environment["怪物"]["射击"] == -30
    qiren_pending.pop(qq, None)
    battle_manager.remove_battle(qq)
    investigator_repo.delete_by_qq(qq)


def test_qiren_dialogue_trigger_no_battle():
    """概率触发命中 → 发送现身对话（不挑战则无战斗），等待按钮选择。"""
    import asyncio
    from unittest.mock import patch

    from src.plugins.adventure import _run_adventure
    from src.utils.buttons import BUTTON_HANDLERS

    qq = "qa_qiren_dialog"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)

    class FakeBot:
        type = "Console"

        async def send_msg(self, user_id=None, message=None):
            pass

    sent: list[str] = []

    async def send(msg) -> None:
        sent.append(str(msg))

    async def finish(msg) -> None:
        sent.append("FIN:" + str(msg))

    # adventure 模块内绑定的名字需在其命名空间 patch（共享触发层：hidden_should_trigger 返回 qiren）
    with patch("src.plugins.adventure.hidden_should_trigger", return_value="qiren"):
        asyncio.run(_run_adventure(qq, FakeBot(), send, finish))

    text = "\n".join(sent)
    assert "第 41 天" in text  # 启的现身台词
    assert battle_manager.get_battle(qq) is None  # 触发对话不直接开战
    assert "qiren" in BUTTON_HANDLERS  # 挑战/不挑战 按钮已注册
    investigator_repo.delete_by_qq(qq)


def test_qiren_button_decline_skips_day():
    """不挑战：day+1（day40 冻结）+ 标记今日冒险完成。"""
    import asyncio

    from src.plugins.qiren import handle_qiren_button
    from util.DaylyRecord import get_data

    qq = "qa_qiren_decline"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)

    class FakeBot:
        type = "Console"

        async def send_msg(self, user_id=None, message=None):
            pass

    asyncio.run(handle_qiren_button(qq, "不挑战", FakeBot(), ""))
    after = Investigator(investigator_repo.find_by_qq(qq))
    assert after.day == 26
    assert get_data(qq, "adventure_done") is True
    investigator_repo.delete_by_qq(qq)


def test_qiren_button_challenge_forces_boss():
    """挑战：置位旗标 → 重入今日冒险 → 强制怪物 38 + 环境。"""
    import asyncio

    from src.plugins.qiren import handle_qiren_button
    from util.DaylyRecord import add_data, write_json

    qq = "qa_qiren_challenge"
    inv = _make_inv(qq)
    inv.day = 25
    inv.save()
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    add_data(qq, "adventure_done", False)
    write_json()

    class FakeBot:
        type = "Console"

        async def send_msg(self, user_id=None, message=None):
            pass

    asyncio.run(handle_qiren_button(qq, "挑战", FakeBot(), ""))
    battle = battle_manager.get_battle(qq)
    assert battle is not None
    assert battle.monster.id == "38"
    assert battle.environment.get("name") == "庄园.黑色满月"
    battle_manager.remove_battle(qq)
    investigator_repo.delete_by_qq(qq)


def test_qiren_reward_special_case():
    """胜利结算：BOSS 档池 1 件 + 信物 509（独立于侦查检定，乌帕随档位）。"""
    from database.db import get_info

    qq = "qa_qiren_reward"
    inv = _make_inv(qq)
    gold_before = get_info(qq).gold
    bs = BattleService(inv, Monster("38"))
    bs.hp_record["mon"] = 0
    _header, detail = bs._victory_parts()
    equip, _ = inv.get_equipments()
    assert any(equip.get(i, 0) == 1 for i in _BOSS_POOL_IDS)
    assert equip.get("509") == 1  # 信物段首杀保留
    assert gold_before + 30 <= get_info(qq).gold <= gold_before + 60
    assert "启的信物" in detail
    investigator_repo.delete_by_qq(qq)


def test_qiren_unlocked_excludes_day40():
    """day40 门扉归守门人，启不参与：day>=40 即使持有信物也不解锁。"""
    from src.plugins.qiren import qiren_unlocked

    inv = _make_inv("qa_qiren_day40")
    inv.day = 39
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    assert qiren_unlocked(inv) is True  # day39 解锁
    inv.day = 40
    assert qiren_unlocked(inv) is False  # day40 归守门人
    inv.day = 41
    assert qiren_unlocked(inv) is False  # 结局后亦不参与
    investigator_repo.delete_by_qq("qa_qiren_day40")


def test_qiren_should_trigger_not_on_day40():
    """day40 概率触发被排除：即使掷出触发值也不触发对话。"""
    from unittest.mock import patch

    from src.plugins.qiren import qiren_should_trigger

    inv = _make_inv("qa_qiren_trig40")
    inv.day = 40
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    with patch("src.plugins.qiren.roll_dice", return_value=("20", 1)):
        assert qiren_should_trigger(inv) is False
    investigator_repo.delete_by_qq("qa_qiren_trig40")


def test_day40_qiren_pending_does_not_override_gatekeeper():
    """day40 残留挑战旗标不覆盖守门人：强制怪物仍落回 day40 池（守门人 36）。"""
    import asyncio
    from unittest.mock import patch

    from src.plugins.adventure import _run_adventure
    from src.plugins.qiren import qiren_pending

    qq = "qa_qiren_day40_force"
    inv = _make_inv(qq)
    inv.day = 40
    inv.save()
    investigator_repo.add_item_to_inventory(inv._model, "501", 1)
    qiren_pending[qq] = True

    class FakeBot:
        type = "Console"

        async def send_msg(self, user_id=None, message=None):
            pass

    async def send(msg) -> None:
        pass

    async def finish(msg) -> None:
        raise SystemExit

    with patch("src.plugins.adventure.pick_random_event", return_value=None), patch(
        "src.plugins.adventure.run_sanity_and_madness",
        return_value=("", "", False, 0, False, 0),
    ):
        try:
            asyncio.run(_run_adventure(qq, FakeBot(), send, finish))
        except SystemExit:
            pass

    battle = battle_manager.get_battle(qq)
    assert battle is not None, "day40 应正常开战"
    assert battle.monster.id == "36", "day40 必须为守门人 36，而非启（38）"
    qiren_pending.pop(qq, None)
    battle_manager.remove_battle(qq)
    investigator_repo.delete_by_qq(qq)


# --- C8：环境隔离回归（黑色满月仅走 qiren 强制路径）---
def test_random_environment_never_black_moon():
    """普通日随机环境池永不返回「庄园.黑色满月」。"""
    from src.plugins.adventure import pick_random_environment
    from src.services.data_loader import data_loader

    keys = {pick_random_environment() for _ in range(200)}
    assert keys, "随机环境池不应为空"
    assert "庄园.黑色满月" not in keys
    assert keys <= set(data_loader.environment_data.keys())


def test_qiren_forced_environment_is_black_moon():
    """qiren 挑战强制环境仍为「庄园.黑色满月」（不受随机池排除影响）。"""
    from src.services.data_loader import data_loader

    assert "庄园.黑色满月" in data_loader.environment_data
    env = data_loader.environment_data["庄园.黑色满月"]
    assert env["玩家"] == {"射击": -30, "闪避": 10}
    assert env["怪物"] == {"射击": -30, "敏捷": 10, "闪避": 10}


def test_monster38_cg_and_environment_pure():
    """怪物 38 出场为完整 CG；黑色满月环境描述为纯氛围（不含启专属词）。"""
    from src.services.data_loader import data_loader

    cg = data_loader.monster_data["38"]["出场"]
    env_desc = data_loader.environment_data["庄园.黑色满月"]["描述"]

    assert cg.startswith("月光泼下来的瞬间")
    assert cg.endswith("恰好是你藏身的阴影。")
    assert "战斗/逃跑" not in cg and "（战斗/逃跑按钮）" not in cg
    assert "第 41 天" not in cg  # 完整 CG 不再用旧版台词

    for word in ("身影", "圣剑", "霰弹枪"):
        assert word not in env_desc, f"环境描述不应包含启专属词: {word}"
    assert "黑色的满月" in env_desc
