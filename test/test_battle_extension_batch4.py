"""战斗系统扩展·批次4 测试：法术效果类型扩展（dot / 属性增减 / san）+ AI 回合数触发。

对齐 .qa/plans/battle-system-extension-design.md §7 批次4 验收用例
（用例 15~18）+ 无新效果法术零回归 + validator 非法用例。
"""
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import pytest

from src.models.monster import Monster
from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.services.battle import BattleService
from src.services.battle import engine as engine_mod
from src.services.battle import spells as spells_mod
from src.services.config_validator import ConfigValidator
from src.services.data_loader import data_loader

_DATA_DIR = Path(__file__).parent.parent / "plugins" / "StoryTeller" / "data"

_DATA_FILES = (
    "monster_data.json", "goods_data.json", "event_data.json",
    "environment_data.json", "check_point.json", "reply_data.json",
    "text_data.json", "npc_data.json", "ending_data.json", "flow_data.json",
    "display_data.json", "shop_data.json", "spell_data.json",
    "mainline_config.json", "weights.json", "boss_weights.json", "loots.json", "boss/", "worlds/",
)

# 测试专用法术（注入 data_loader.spell_data，用后恢复）
_TEST_SPELLS = {
    "t_dot": {
        "name": "测试·持续侵蚀", "des": "凋零的祷言缠上目标。",
        "learn_skill": "智力", "mp_cost": 2, "san_cost": 0, "对抗": False,
        "effect": {"type": "dot", "dice": "1d3", "回合": 3},
        "回复": "诅咒缠上怪物，血肉开始持续凋零。",
    },
    "t_dot_tick": {
        "name": "测试·持续侵蚀", "des": "专属 tick 文案。",
        "learn_skill": "智力", "mp_cost": 2, "san_cost": 0, "对抗": False,
        "effect": {"type": "dot", "dice": "1d3", "回合": 2,
                   "tick_text": "专属tick：造成 {damage} 点持续伤害。"},
        "回复": "回退文案不应出现。",
    },
    "t_attr_buff": {
        "name": "测试·力量涌动", "des": "禁忌的祷词点燃血液。",
        "learn_skill": "智力", "mp_cost": 1, "san_cost": 0, "对抗": False,
        "effect": {"type": "属性增减", "目标": "自身", "属性": "格斗",
                   "骰子": "1d3", "持续": "战斗"},
        "回复": "你的力量被点燃。",
    },
    "t_attr_debuff_mon": {
        "name": "测试·诅咒弱化", "des": "弱化的咒文。",
        "learn_skill": "智力", "mp_cost": 1, "san_cost": 0, "对抗": False,
        "effect": {"type": "属性增减", "目标": "怪物", "属性": "格斗",
                   "骰子": "1d3", "持续": "战斗"},
        "回复": "怪物的力量被削弱。",
    },
    "t_attr_debuff_inv": {
        "name": "测试·迟缓咒", "des": "迟缓的咒文。",
        "learn_skill": "智力", "mp_cost": 2, "san_cost": 0, "对抗": False,
        "effect": {"type": "属性增减", "目标": "玩家", "属性": "力量",
                   "骰子": "1d3", "持续": "战斗"},
        "回复": "你的力量被压制。",
    },
    "t_san": {
        "name": "测试·理智侵蚀", "des": "夺走理智的咒文。",
        "learn_skill": "智力", "mp_cost": 2, "san_cost": 0, "对抗": False,
        "effect": {"type": "san", "dice": "1d3"},
        "回复": "诅咒攫走你的理智。",
    },
}


def _make_battle(qq: str, name: str, mid: str = "1") -> BattleService:
    """构造可控战斗：怪物数据深拷贝隔离（不污染全局 data_loader）。"""
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(qq, name, data)
    inv = Investigator(inv_model)
    m = Monster(mid)
    m._data = copy.deepcopy(m._data)
    m._data["攻击"] = {
        "a": {
            "skill": 40,
            "damage": "5",
            "counterattack": "怪物反击",
            "attack_succ": "怪物命中",
            "attack_false": "怪物未中",
        }
    }
    m._data["装甲"] = 0
    m.armor = 0
    return BattleService(inv, m)


def _clean(qq: str) -> None:
    investigator_repo.delete_by_qq(qq)


@pytest.fixture(autouse=True)
def _spell_fixture():
    """注入测试法术到 data_loader.spell_data（每个用例后恢复）。"""
    backup: dict[str, object] = {}
    for sid, sp in _TEST_SPELLS.items():
        backup[sid] = data_loader.spell_data.get(sid)
        data_loader.spell_data[sid] = copy.deepcopy(sp)
    yield
    for sid, orig in backup.items():
        if orig is None:
            data_loader.spell_data.pop(sid, None)
        else:
            data_loader.spell_data[sid] = orig


@pytest.fixture(autouse=True)
def _cleanup_after_test():
    """每用例结束清理测试调查员（隔离 DB 残留）。"""
    yield
    for qq in (
        "test_b4_dot", "test_b4_dotm", "test_b4_dotcast", "test_b4_attr",
        "test_b4_attrb", "test_b4_attrp", "test_b4_san", "test_b4_sancast",
        "test_b4_zero", "test_b4_monatk", "test_b4_aiturn",
    ):
        investigator_repo.delete_by_qq(qq)


# --- 玩家对怪 dot：3 回合持续伤害 ---
def test_player_dot_on_monster_ticks_three_rounds(monkeypatch):
    monkeypatch.setattr(engine_mod, "roll_dice", lambda *a, **k: ("1d3", 2))
    bs = _make_battle("test_b4_dot", "DotMon")
    bs.hp_record["mon"] = 20
    bs.monster.hp = 20
    text, monster_text = bs._apply_spell_effect(
        {"effect": {"type": "dot", "dice": "1d3", "回合": 3},
         "回复": "诅咒缠上怪物，血肉开始持续凋零。"}
    )
    assert bs._dot_on_monster == {"dice": "1d3", "剩余": 3,
                                  "tick_text": "诅咒缠上怪物，血肉开始持续凋零。"}
    assert "3" in text, "效果行应展示持续回合数"
    assert monster_text == ""

    bs.current_turn = "mon"
    bs._apply_dot_ticks()
    assert bs.hp_record["mon"] == 18, "回合1 tick 伤害 2"
    assert bs._dot_on_monster["剩余"] == 2
    bs._apply_dot_ticks()
    assert bs.hp_record["mon"] == 16, "回合2 tick 伤害 2"
    assert bs._dot_on_monster["剩余"] == 1
    bs._apply_dot_ticks()
    assert bs.hp_record["mon"] == 14, "回合3 tick 伤害 2"
    assert bs._dot_on_monster is None, "3 回合耗尽清除"
    bs._apply_dot_ticks()
    assert bs.hp_record["mon"] == 14, "清除后不再 tick"
    _clean("test_b4_dot")


def test_player_dot_tick_custom_text(monkeypatch):
    """dot tick 文案：tick_text 覆盖法术回复（缺省回退链）。"""
    monkeypatch.setattr(engine_mod, "roll_dice", lambda *a, **k: ("1d3", 1))
    bs = _make_battle("test_b4_dot", "DotCustom")
    bs.hp_record["mon"] = 20
    bs.monster.hp = 20
    bs._apply_spell_effect(
        {"effect": {"type": "dot", "dice": "1d3", "回合": 2,
                    "tick_text": "专属tick：造成 {damage} 点持续伤害。"},
         "回复": "回退文案不应出现。"}
    )
    bs.current_turn = "mon"
    lines = bs._apply_dot_ticks()
    assert any("专属tick：造成 1 点持续伤害。" in x for x in lines)
    assert not any("回退文案" in x for x in lines)
    _clean("test_b4_dot")


def test_player_cast_dot_via_cmd(monkeypatch):
    """完整施法路径：/行动 施法t_dot → dot 登记 + 回合初 tick。"""
    monkeypatch.setattr(spells_mod, "roll_dice", lambda *a, **k: ("1d3", 2))
    monkeypatch.setattr(engine_mod, "roll_dice", lambda *a, **k: ("1d3", 2))
    bs = _make_battle("test_b4_dotcast", "DotCast")
    bs.investigator.add_spell("t_dot")
    bs.hp_record["mon"] = 20
    bs.monster.hp = 20
    result = bs._cast_spell("t_dot")
    out = "\n".join(str(x) for x in result)
    assert bs._dot_on_monster is not None, "施法后 dot 应登记"
    assert bs.hp_record["mon"] == 18, "施法回合结束转向怪物回合初即 tick 1"
    assert bs._dot_on_monster["剩余"] == 2
    assert "持续" in out or "3" in out
    _clean("test_b4_dotcast")


# --- 怪对玩家 dot ---
def test_monster_dot_on_player_ticks(monkeypatch):
    monkeypatch.setattr(engine_mod, "roll_dice", lambda *a, **k: ("1d3", 2))
    bs = _make_battle("test_b4_dotm", "DotPlayer")
    bs.hp_record["inv"] = 20
    lines = bs._execute_monster_spell({"spell": "t_dot", "type": "spell"})
    joined = "\n".join(lines)
    assert bs._dot_on_player == {"dice": "1d3", "剩余": 3,
                                 "tick_text": "诅咒缠上怪物，血肉开始持续凋零。"}
    assert "持续" in joined
    bs.current_turn = "inv"
    bs._apply_dot_ticks()
    assert bs.hp_record["inv"] == 18, "玩家回合初 tick 伤害 2"
    assert bs._dot_on_player["剩余"] == 2
    _clean("test_b4_dotm")


# --- 属性增减：玩家自身 buff（战斗内生效 + 结束恢复） ---
def test_attr_buff_self_apply_and_restore(monkeypatch):
    monkeypatch.setattr(spells_mod, "roll_dice", lambda *a, **k: ("1d3", 3))
    bs = _make_battle("test_b4_attrb", "AttrBuff")
    fight_before = bs.investigator.get_skill("格斗")
    text, _ = bs._apply_spell_effect(
        {"effect": {"type": "属性增减", "目标": "自身", "属性": "格斗",
                    "骰子": "1d3", "持续": "战斗"},
         "回复": ""}
    )
    assert bs.investigator.get_skill("格斗") == fight_before + 3, "战斗内 buff 生效"
    assert bs._get_player_modified_skill("格斗") == fight_before + 3
    assert "格斗" in text, "缺省模板 battle.attr_buff 应展示属性名"
    bs._restore_absorb_snapshot()
    assert bs.investigator.get_skill("格斗") == fight_before, "战斗结束恢复原值"
    _clean("test_b4_attrb")


def test_attr_buff_restore_on_victory(monkeypatch):
    monkeypatch.setattr(spells_mod, "roll_dice", lambda *a, **k: ("1d3", 2))
    bs = _make_battle("test_b4_attrb", "AttrBuffVictory")
    fight_before = bs.investigator.get_skill("格斗")
    bs._apply_spell_effect(
        {"effect": {"type": "属性增减", "目标": "自身", "属性": "格斗",
                    "骰子": "1d3", "持续": "战斗"},
         "回复": "你的力量被点燃。"}
    )
    assert bs.investigator.get_skill("格斗") == fight_before + 2
    bs.hp_record["mon"] = 0
    bs._handle_victory()
    assert bs.investigator.get_skill("格斗") == fight_before, "胜利结算后恢复"
    _clean("test_b4_attrb")


# --- 属性增减：玩家对怪 debuff（战斗内生效 + 恢复） ---
def test_attr_debuff_monster_apply_and_restore(monkeypatch):
    monkeypatch.setattr(spells_mod, "roll_dice", lambda *a, **k: ("1d3", 3))
    bs = _make_battle("test_b4_attr", "AttrDebuffMon")
    action = {"skill": 40, "damage": "5"}
    skill_before = bs._get_monster_attack_skill(action)
    text, _ = bs._apply_spell_effect(
        {"effect": {"type": "属性增减", "目标": "怪物", "属性": "格斗",
                    "骰子": "1d3", "持续": "战斗"},
         "回复": ""}
    )
    assert bs._monster_attr_delta.get("格斗") == -3, "对怪 debuff 净修正量"
    assert bs._get_monster_attack_skill(action) == skill_before - 3, "攻击技能被削弱"
    assert "格斗" in text, "缺省模板 battle.attr_debuff_mon 应展示属性名"
    bs._restore_absorb_snapshot()
    assert bs._get_monster_attack_skill(action) == skill_before, "战斗结束恢复"
    assert bs._monster_attr_delta == {}, "净修正量清空"
    _clean("test_b4_attr")


def test_attr_debuff_monster_affects_dex(monkeypatch):
    """对怪 debuff 敏捷 → 先手判定（_get_monster_modified dex）同步降低。"""
    monkeypatch.setattr(spells_mod, "roll_dice", lambda *a, **k: ("1d3", 2))
    bs = _make_battle("test_b4_attr", "AttrDebuffDex")
    dex_before = bs._get_monster_modified("dex", 50)
    bs._apply_spell_effect(
        {"effect": {"type": "属性增减", "目标": "怪物", "属性": "敏捷",
                    "骰子": "1d3", "持续": "战斗"},
         "回复": "怪物的敏捷被削弱。"}
    )
    assert bs._get_monster_modified("dex", 50) == dex_before - 2
    bs._restore_absorb_snapshot()
    assert bs._get_monster_modified("dex", 50) == dex_before
    _clean("test_b4_attr")


# --- 属性增减：怪对玩家 debuff ---
def test_monster_attr_debuff_player(monkeypatch):
    monkeypatch.setattr(engine_mod, "roll_dice", lambda *a, **k: ("1d3", 3))
    bs = _make_battle("test_b4_attrp", "AttrDebuffPlayer")
    str_before = bs.investigator.get_skill("力量")
    lines = bs._execute_monster_spell({"spell": "t_attr_debuff_inv", "type": "spell"})
    joined = "\n".join(lines)
    assert bs.investigator.get_skill("力量") == str_before - 3, "怪 debuff 玩家生效"
    assert "力量" in joined
    bs._restore_absorb_snapshot()
    assert bs.investigator.get_skill("力量") == str_before, "战斗结束恢复"
    _clean("test_b4_attrp")


# --- san：怪对玩家扣理智 ---
def test_monster_san_spell_deducts_sanity(monkeypatch):
    monkeypatch.setattr(engine_mod, "roll_dice", lambda *a, **k: ("1d3", 3))
    bs = _make_battle("test_b4_san", "SanSpell")
    bs.investigator.set_skill("san", 50)
    san_before = bs.investigator.get_skill("san")
    lines = bs._execute_monster_spell({"spell": "t_san", "type": "spell"})
    joined = "\n".join(lines)
    assert bs.investigator.get_skill("san") == san_before - 3, "san 伤害扣理智"
    assert "理智" in joined or "san" in joined
    _clean("test_b4_san")


def test_monster_san_spell_floor_at_zero(monkeypatch):
    monkeypatch.setattr(engine_mod, "roll_dice", lambda *a, **k: ("1d3", 999))
    bs = _make_battle("test_b4_san", "SanFloor")
    bs.investigator.set_skill("san", 1)
    bs._execute_monster_spell({"spell": "t_san", "type": "spell"})
    assert bs.investigator.get_skill("san") == 0, "san 扣减下限 0"
    _clean("test_b4_san")


def test_player_cast_san_no_self_damage(monkeypatch):
    """玩家对怪施放 san 型法术：文案兜底，不做玩家 san 自伤。"""
    bs = _make_battle("test_b4_sancast", "SanSelf")
    bs.investigator.add_spell("t_san")
    bs.investigator.set_skill("san", 50)
    text, monster_text = bs._apply_spell_effect(
        {"effect": {"type": "san", "dice": "1d3"}, "回复": "诅咒攫走你的理智。"}
    )
    assert bs.investigator.get_skill("san") == 50, "玩家施放 san 型法术不扣自身理智"
    assert text, "应返回兜底文案"
    assert monster_text == ""
    _clean("test_b4_sancast")


# --- 无新效果法术零回归 ---
def test_existing_spells_zero_regression(monkeypatch):
    """现有 s001(damage)/s002(heal)/s003(temp_hp) 行为逐字节不变。"""
    monkeypatch.setattr(spells_mod, "roll_dice", lambda *a, **k: ("2d6", 5))
    bs = _make_battle("test_b4_zero", "ZeroReg")
    bs.hp_record["mon"] = 20
    text, monster_text = bs._apply_spell_effect(
        {"effect": {"type": "damage", "dice": "2d6"}, "回复": "x"}
    )
    assert text == "2d6=5，造成 5 点伤害"
    assert bs.hp_record["mon"] == 15

    monkeypatch.setattr(spells_mod, "roll_dice", lambda *a, **k: ("1d6+2", 4))
    hp_before = bs.hp_record["inv"]
    text2, _ = bs._apply_spell_effect(
        {"effect": {"type": "heal", "dice": "1d6+2"}, "回复": "x"}
    )
    assert bs.hp_record["inv"] == min(
        bs.investigator.get_max_hp(), hp_before + 4
    ), "heal 行为不变"

    monkeypatch.setattr(spells_mod, "roll_dice", lambda *a, **k: ("1d6+2", 4))
    temp_before = bs.temp_hp
    text3, _ = bs._apply_spell_effect(
        {"effect": {"type": "temp_hp", "dice": "1d6+2"}, "回复": "x"}
    )
    assert bs.temp_hp == temp_before + 4, "temp_hp 行为不变"
    _clean("test_b4_zero")


def test_monster_damage_spell_zero_regression(monkeypatch):
    """怪物 damage 型法术（批次1 修复）回归：仍实际造成魔法伤害。"""
    monkeypatch.setattr(engine_mod, "roll_dice", lambda *a, **k: ("2d6", 5))
    bs = _make_battle("test_b4_monatk", "MonDamageSpell")
    bs.hp_record["inv"] = 30
    inv_before = bs.hp_record["inv"]
    lines = bs._execute_monster_spell({"spell": "s001", "type": "spell"})
    joined = "\n".join(lines)
    assert bs.hp_record["inv"] < inv_before, "怪物 damage 法术实际造成伤害"
    assert joined, "怪物施法文案应展示"
    _clean("test_b4_monatk")


# --- AI 回合数触发：第 N 个怪物回合施法一次 ---
def _custom_ai_monster(rule: dict) -> Monster:
    m = Monster("1")
    m._data = copy.deepcopy(m._data)
    m._data["意志"] = 60
    m._data["ai"] = {
        "近战": {
            "skill": 40, "damage": "1d3", "attack": "a",
            "counterattack": "c", "attack_succ": "s", "attack_false": "f",
        },
        "受伤后": {"多法术": {"t_dot": rule}},
    }
    m._ai_data = m._data["ai"]
    m._ai_dodge = 99
    # max_mp 在 __init__ 时按原始 _data（无 ai）结算为 0，此处按 ai 配置重算
    m.max_mp = int(m._data.get("意志", 0)) // 5
    m.mp = m.max_mp
    return m


def test_ai_turn_count_trigger_at_nth_turn():
    m = _custom_ai_monster({"触发": "回合数", "值": 3})
    m.advance_ai()
    assert m._ai_pending_spell is None, "第1回合不触发"
    m.advance_ai()
    assert m._ai_pending_spell is None, "第2回合不触发"
    m.advance_ai()
    assert m._ai_pending_spell == "t_dot", "第3个怪物回合施法"
    assert m.get_ai_action("mon")["type"] == "spell"


def test_ai_turn_count_fires_once():
    m = _custom_ai_monster({"触发": "回合数", "值": 2})
    m.advance_ai()
    assert m._ai_pending_spell is None
    m.advance_ai()
    assert m._ai_pending_spell == "t_dot", "第2回合触发"
    m.complete_spell_turn()
    m.advance_ai()
    assert m._ai_pending_spell is None, "仅第 N 回合施法一次，不重复触发"
    assert m._ai_turn_count == 3


def test_ai_turn_count_mp_gate():
    m = _custom_ai_monster({"触发": "回合数", "值": 2})
    m.mp = 1  # t_dot 需 2 MP
    m.advance_ai()
    m.advance_ai()
    assert m._ai_pending_spell is None, "MP 不足不触发，不卡回合"


def test_ai_turn_count_with_other_rules():
    """回合数与 hp低/概率 并存：规则各自独立求值。"""
    from unittest.mock import patch

    m = _custom_ai_monster({"触发": "回合数", "值": 2})
    m._data["ai"]["受伤后"]["多法术"]["t_attr_buff"] = {"触发": "概率", "值": 0.4}
    m._ai_data = m._data["ai"]
    with patch("random.random", return_value=0.9):
        m.advance_ai()  # 回合1：概率未命中、回合数未到
        assert m._ai_pending_spell is None
        m.advance_ai()  # 回合2：回合数命中 → 施法 t_dot
    assert m._ai_pending_spell == "t_dot"


def test_ai_turn_count_zero_value_never_fires():
    m = _custom_ai_monster({"触发": "回合数", "值": 0})
    for _ in range(5):
        m.advance_ai()
    assert m._ai_pending_spell is None, "值 ≤ 0 不触发"


def test_ai_turn_count_battle_integration():
    """端到端：AI 怪第 N 个怪物回合在真实战斗流程中实际施法。"""
    bs = _make_battle("test_b4_aiturn", "AiTurnBattle")
    m = bs.monster
    m._data["意志"] = 60
    m._data["ai"] = {
        "近战": {
            "skill": 40, "damage": "0", "attack": "a",
            "counterattack": "c", "attack_succ": "s", "attack_false": "f",
        },
        "受伤后": {"多法术": {"t_dot": {"触发": "回合数", "值": 2}}},
    }
    m._ai_data = m._data["ai"]
    m.max_mp = int(m._data.get("意志", 0)) // 5
    m.mp = m.max_mp
    bs.current_turn = "mon"
    bs.hp_record["inv"] = 50
    bs.hp_record["mon"] = 50
    m.hp = 50

    out1 = "\n".join(str(x) for x in bs._execute_monster_action("反击"))
    assert "施法·" not in out1, "第1个怪物回合不施法"
    assert bs._dot_on_player is None
    out2 = "\n".join(str(x) for x in bs._execute_monster_action("反击"))
    assert "施法·" in out2, "第2个怪物回合应施法"
    assert bs._dot_on_player is not None, "怪物 dot 施加到玩家"
    _clean("test_b4_aiturn")


# --- validator：spell effect 新类型合法/非法用例 ---
def _validator_spell(effect) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "data"
        d.mkdir(parents=True, exist_ok=True)
        for name in _DATA_FILES:
            if name.endswith("/"):
                sub = d / name
                sub.mkdir(parents=True, exist_ok=True)
                src_dir = _DATA_DIR / name
                if src_dir.is_dir():
                    for f in src_dir.glob("*.json"):
                        shutil.copy(f, sub / f.name)
                continue
            src = _DATA_DIR / name
            if src.exists():
                shutil.copy(src, d / name)
        spells = _load(d, "spell_data.json")
        spells["t_validator"] = {
            "name": "校验测试", "des": "d", "回复": "r",
            "learn_skill": "智力", "effect": effect,
        }
        _dump(d, "spell_data.json", spells)
        return ConfigValidator(d).validate_all()


def _load(tmp_data: Path, name: str) -> dict:
    return json.loads((tmp_data / name).read_text(encoding="utf-8-sig"))


def _dump(tmp_data: Path, name: str, obj) -> None:
    (tmp_data / name).write_text(
        json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def test_validator_new_types_valid():
    for fx in (
        {"type": "dot", "dice": "1d3", "回合": 3},
        {"type": "dot", "dice": "2d4", "回合": 2, "tick_text": "专属"},
        {"type": "属性增减", "目标": "自身", "属性": "力量", "骰子": "1d3", "持续": "战斗"},
        {"type": "属性增减", "目标": "怪物", "属性": "格斗", "骰子": "1d3", "持续": "战斗"},
        {"type": "san", "dice": "1d3"},
    ):
        errors = _validator_spell(fx)
        assert errors == [], f"{fx}\n{errors}"


def test_validator_dot_bad_dice_reported():
    errors = _validator_spell({"type": "dot", "dice": "abc", "回合": 3})
    assert any("骰子" in e and "effect" in e for e in errors), errors


def test_validator_dot_non_positive_turns_reported():
    for bad in (0, -1, "x", True):
        errors = _validator_spell({"type": "dot", "dice": "1d3", "回合": bad})
        assert any("回合" in e for e in errors), (bad, errors)


def test_validator_dot_missing_turns_reported():
    errors = _validator_spell({"type": "dot", "dice": "1d3"})
    assert any("回合" in e for e in errors), errors


def test_validator_dot_bad_tick_text_reported():
    errors = _validator_spell({"type": "dot", "dice": "1d3", "回合": 3, "tick_text": ""})
    assert any("tick_text" in e for e in errors), errors


def test_validator_attr_illegal_target_reported():
    errors = _validator_spell(
        {"type": "属性增减", "目标": "空气", "属性": "力量", "骰子": "1d3", "持续": "战斗"}
    )
    assert any("目标" in e for e in errors), errors


def test_validator_attr_unknown_attr_reported():
    errors = _validator_spell(
        {"type": "属性增减", "目标": "自身", "属性": "查克拉", "骰子": "1d3", "持续": "战斗"}
    )
    assert any("属性" in e and "查克拉" in e for e in errors), errors


def test_validator_attr_missing_dice_reported():
    errors = _validator_spell(
        {"type": "属性增减", "目标": "自身", "属性": "力量", "持续": "战斗"}
    )
    assert any("骰子" in e for e in errors), errors


def test_validator_attr_bad_duration_reported():
    errors = _validator_spell(
        {"type": "属性增减", "目标": "自身", "属性": "力量", "骰子": "1d3", "持续": "永久"}
    )
    assert any("持续" in e for e in errors), errors


def test_validator_san_bad_dice_reported():
    errors = _validator_spell({"type": "san", "dice": "xyz"})
    assert any("骰子" in e and "effect" in e for e in errors), errors


def test_validator_illegal_type_reported():
    errors = _validator_spell({"type": "吸血", "dice": "1d3"})
    assert any("效果" in e and "type" in e for e in errors), errors
