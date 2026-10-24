"""战斗系统扩展·批次1 测试：伤害标签 / 免疫抗性 / 骰子压制 / 怪物伤害法术修复。

对齐 .qa/plans/battle-system-extension-design.md §7 批次1 验收用例。
"""
import copy
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

from types import SimpleNamespace

from src.models.item import Equipment
from src.models.monster import Monster
from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.services.battle import BattleService
from src.services.config_validator import ConfigValidator
from src.services.damage_calculator import calculate_damage
from src.services.data_loader import data_loader
from src.services.dice_roller import SuccessLevel, roll_dice


_DATA_DIR = Path(__file__).parent.parent / "plugins" / "StoryTeller" / "data"

_DATA_FILES = (
    "monster_data.json", "goods_data.json", "event_data.json",
    "environment_data.json", "check_point.json", "reply_data.json",
    "text_data.json", "npc_data.json", "ending_data.json", "flow_data.json",
    "display_data.json", "shop_data.json", "spell_data.json",
    "mainline_config.json", "weights.json", "boss_weights.json", "loots.json", "boss/", "worlds/",
)


def _copy_data(tmp: Path) -> Path:
    """把 data/*.json 复制到临时目录（测试隔离，改副本不影响生产数据）。"""
    dst = tmp / "data"
    dst.mkdir(parents=True, exist_ok=True)
    for name in _DATA_FILES:
        if name.endswith("/"):
            sub = dst / name
            sub.mkdir(parents=True, exist_ok=True)
            src_dir = _DATA_DIR / name
            if src_dir.is_dir():
                for f in src_dir.glob("*.json"):
                    shutil.copy(f, sub / f.name)
            continue
        src = _DATA_DIR / name
        if src.exists():
            shutil.copy(src, dst / name)
    return dst


def _load(tmp_data: Path, name: str) -> dict:
    return json.loads((tmp_data / name).read_text(encoding="utf-8-sig"))


def _dump(tmp_data: Path, name: str, obj) -> None:
    (tmp_data / name).write_text(
        json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8"
    )


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


# --- 1. 物理免疫 ---
def test_physical_immune_melee_zero_spell_normal():
    bs = _make_battle("test_b1_pim", "PhysImmuneMelee")
    bs.monster.免疫 = ["物理"]
    weapon = Equipment("102")
    before = bs.hp_record["mon"]
    bs._handle_player_melee_success(
        SimpleNamespace(level1=SuccessLevel.EXTREME_SUCCESS, level2=SuccessLevel.FAILURE),
        weapon,
        bs.monster.get_action("mon"),
        "",
    )
    assert bs.hp_record["mon"] == before, "物理免疫下格斗应 0 伤"
    text = bs._apply_damage_to_monster(10, dmg_type="magic")
    assert bs.hp_record["mon"] == before - 10, "魔法标签不受物理免疫影响"
    assert "毫无效果" not in text, "魔法伤害不应触发物理免疫文案"
    _clean("test_b1_pim")


def test_physical_immune_counter_zero():
    bs = _make_battle("test_b1_pic", "PhysImmuneCounter")
    bs.monster.免疫 = ["物理"]
    before = bs.hp_record["mon"]
    bs._handle_player_counter_success(Equipment("102"))
    assert bs.hp_record["mon"] == before, "物理免疫下反击应 0 伤"
    _clean("test_b1_pic")


def test_physical_immune_apply_damage_zero():
    bs = _make_battle("test_b1_pid", "PhysImmuneDirect")
    bs.monster.免疫 = ["物理"]
    before = bs.hp_record["mon"]
    text = bs._apply_damage_to_monster(99, dmg_type="physical")
    assert bs.hp_record["mon"] == before
    assert "毫无效果" in text
    _clean("test_b1_pid")


# --- 2. 魔法免疫 ---
def test_magic_immune_spell_zero_weapon_normal():
    bs = _make_battle("test_b1_mim", "MagicImmuneSpell")
    bs.monster.免疫 = ["魔法"]
    before = bs.hp_record["mon"]
    text = bs._apply_damage_to_monster(10, dmg_type="magic")
    assert bs.hp_record["mon"] == before, "魔法免疫下法术应 0 伤"
    assert "毫无效果" in text
    weapon = Equipment("102")
    bs._handle_player_melee_success(
        SimpleNamespace(level1=SuccessLevel.EXTREME_SUCCESS, level2=SuccessLevel.FAILURE),
        weapon,
        bs.monster.get_action("mon"),
        "",
    )
    assert bs.hp_record["mon"] < before, "魔法免疫下武器（物理）正常结算"
    _clean("test_b1_mim")


def test_magic_immune_append_attack_zero():
    """猎犬之牙（603 append_attack）带魔法标签：魔法免疫怪 0 伤。"""
    from src.services.battle.trinkets import _fx_append_attack

    bs = _make_battle("test_b1_mia", "MagicImmuneAppend")
    bs.monster.免疫 = ["魔法"]
    before = bs.hp_record["mon"]
    item = Equipment("603")
    fx = {"type": "append_attack", "dice": "1d3"}
    eff = {"文案": "阴影里的獠牙骤然咬落，追加 {数值} 点伤害。"}
    ctx = {"damage": 5}
    _lines = _fx_append_attack(bs, item, eff, fx, ctx, False)
    assert bs.hp_record["mon"] == before, "魔法免疫下猎犬之牙应 0 伤"
    _clean("test_b1_mia")


def test_magic_immune_bone_whistle_zero():
    """骨哨（506 每回合助战）带魔法标签：魔法免疫怪 0 伤。"""
    bs = _make_battle("test_b1_mib", "MagicImmuneWhistle")
    bs.monster.免疫 = ["魔法"]
    before = bs.hp_record["mon"]
    bs.bone_whistle = 1
    bs.current_turn = "mon"
    calls: list[str] = []
    orig = bs._apply_damage_to_monster

    def spy(damage: int, dmg_type: str = "physical") -> str:
        calls.append(dmg_type)
        return orig(damage, dmg_type=dmg_type)

    bs._apply_damage_to_monster = spy
    # 用非防御行动触发怪物回合：只走骨哨分支，避免反击伤害混淆
    _parts = bs._execute_monster_action("格斗")
    assert "magic" in calls, "骨哨伤害应带魔法标签"
    assert bs.bone_whistle == 0
    assert bs.hp_record["mon"] == before, "魔法免疫下骨哨应 0 伤"
    _clean("test_b1_mib")


# --- 3. 抗性 ---
def test_resist_magic_half_physical_normal():
    bs = _make_battle("test_b1_rsm", "ResistMagic")
    bs.monster.抗性 = {"魔法": 0.5}
    before = bs.hp_record["mon"]
    text = bs._apply_damage_to_monster(10, dmg_type="magic")
    assert bs.hp_record["mon"] == before - 5, "魔法抗性 50%：10→5"
    assert "毫无效果" not in text
    before = bs.hp_record["mon"]
    bs._apply_damage_to_monster(10, dmg_type="physical")
    assert bs.hp_record["mon"] == before - 10, "物理不受魔法抗性影响"
    _clean("test_b1_rsm")


def test_resist_magic_floor():
    """向下取整：3 × (1-0.5) = 1.5 → 1。"""
    bs = _make_battle("test_b1_rsf", "ResistFloor")
    bs.monster.抗性 = {"魔法": 0.5}
    before = bs.hp_record["mon"]
    bs._apply_damage_to_monster(3, dmg_type="magic")
    assert bs.hp_record["mon"] == before - 1
    _clean("test_b1_rsf")


# --- 4. 免疫优先于抗性 ---
def test_immune_overrides_resist():
    bs = _make_battle("test_b1_ir", "ImmuneResist")
    bs.monster.免疫 = ["魔法"]
    bs.monster.抗性 = {"魔法": 0.5}
    before = bs.hp_record["mon"]
    text = bs._apply_damage_to_monster(10, dmg_type="magic")
    assert bs.hp_record["mon"] == before, "免疫优先于抗性"
    assert "毫无效果" in text
    bs._apply_damage_to_monster(10, dmg_type="physical")
    assert bs.hp_record["mon"] == before - 10
    _clean("test_b1_ir")


# --- 5. dice_suppress（骰子压制：仅贯穿武器 ex=1 生效） ---
def test_calc_min_roll_4d6():
    expr, val = calculate_damage("4d6", is_extreme_double=True, min_roll=True)
    assert val == 4
    assert expr == "4d6=1+1+1+1", expr


def test_calc_min_roll_3d6_plus_1():
    _expr, val = calculate_damage("3d6+1", is_extreme_double=True, min_roll=True)
    assert val == 4, "3d6+1 → 3+1=4"


def test_calc_min_roll_fixed_unaffected():
    _expr, val = calculate_damage("1d4+2", is_extreme_double=True, min_roll=True)
    assert val == 3, "固定 +2 不受压制：1+2=3"
    _expr, val2 = calculate_damage("5", is_extreme_double=True, min_roll=True)
    assert val2 == 5, "纯固定值不压制"


def test_calc_min_roll_priority_over_critical():
    _expr, val = calculate_damage(
        "2d6", SuccessLevel.CRITICAL_SUCCESS, True, min_roll=True
    )
    assert val == 2, "贯穿武器暴击下仍取最小（压制优先）"


def test_calc_no_min_zero_regression():
    _expr, val = calculate_damage("1d1")
    assert val == 1


def test_roll_min_helper():
    assert roll_dice("4d6", use_min=True) == ("1+1+1+1", 4)
    assert roll_dice("3d6+1", use_min=True)[1] == 4
    assert roll_dice("2d6+1d4+3", use_min=True)[1] == 6
    assert roll_dice("1d6-2", use_min=True)[1] == -1


def test_dice_suppress_melee_hint_and_min():
    bs = _make_battle("test_b1_dsm", "DiceSuppressMelee")
    assert bs.monster.dice_suppress is False
    bs.monster.dice_suppress = True
    weapon = Equipment("102")
    result = bs._handle_player_melee_success(
        SimpleNamespace(level1=SuccessLevel.EXTREME_SUCCESS, level2=SuccessLevel.FAILURE),
        weapon,
        bs.monster.get_action("mon"),
        "",
    )
    assert "最弱的一击" in str(result), "提示文案应展示"
    # 102 老旧雕刻刀 1d8：压制后每骰 1 → 伤害 1（无被压制仍 ≥1，精确断言 val）
    _clean("test_b1_dsm")


def test_no_dice_suppress_no_hint():
    bs = _make_battle("test_b1_nds", "NoDiceSuppress")
    assert bs.monster.dice_suppress is False
    weapon = Equipment("102")
    result = bs._handle_player_melee_success(
        SimpleNamespace(level1=SuccessLevel.SUCCESS, level2=SuccessLevel.FAILURE),
        weapon,
        bs.monster.get_action("mon"),
        "",
    )
    assert "最弱的一击" not in str(result)
    _clean("test_b1_nds")


# --- 6. 怪物 damage 型法术实际造成伤害（修复验证） ---
def test_monster_damage_spell_deals_damage():
    bs = _make_battle("test_b1_mds", "MonsterDmgSpell")
    spell = {"name": "死咒", "des": "怪物低语着禁忌的咒文",
             "effect": {"type": "damage", "dice": "1d3"}}
    data_loader.spell_data["_test_b1_dmg"] = spell
    try:
        before = bs.hp_record["inv"]
        lines = bs._execute_monster_spell(
            {"type": "spell", "spell": "_test_b1_dmg", "name": "死咒", "des": spell["des"]}
        )
        assert bs.hp_record["inv"] < before, "怪物 damage 法术应对玩家实际造成伤害"
    finally:
        data_loader.spell_data.pop("_test_b1_dmg", None)
    _clean("test_b1_mds")


# --- 7. monster 模型读取新字段 ---
def test_monster_model_reads_new_fields():
    m = Monster("1")
    assert m.dice_suppress is False
    assert m.免疫 == []
    assert m.抗性 == {}
    orig = dict(m._data)
    try:
        m._data["dice_suppress"] = True
        m._data["免疫"] = ["魔法"]
        m._data["抗性"] = {"物理": 0.5}
        m2 = Monster("1")
        assert m2.dice_suppress is True
        assert m2.免疫 == ["魔法"]
        assert m2.抗性 == {"物理": 0.5}
    finally:
        m._data.clear()
        m._data.update(orig)


# --- 8. validator 新字段 ---
def test_validator_new_fields_valid():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        mon = _load(d, "monster_data.json")
        mon["1"]["免疫"] = ["物理"]
        mon["1"]["抗性"] = {"魔法": 0.5}
        mon["1"]["dice_suppress"] = True
        _dump(d, "monster_data.json", mon)
        errors = ConfigValidator(d).validate_all()
        assert errors == [], "\n".join(errors)


def test_validator_immune_invalid_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        mon = _load(d, "monster_data.json")
        mon["1"]["免疫"] = ["毒气"]
        _dump(d, "monster_data.json", mon)
        errors = ConfigValidator(d).validate_all()
        assert any("免疫" in e and "毒气" in e for e in errors), errors


def test_validator_resist_out_of_range_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        mon = _load(d, "monster_data.json")
        mon["1"]["抗性"] = {"物理": 2.5}
        _dump(d, "monster_data.json", mon)
        errors = ConfigValidator(d).validate_all()
        assert any("抗性" in e and "0~1" in e for e in errors), errors


def test_validator_dice_suppress_nonbool_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        mon = _load(d, "monster_data.json")
        mon["1"]["dice_suppress"] = "yes"
        _dump(d, "monster_data.json", mon)
        errors = ConfigValidator(d).validate_all()
        assert any("dice_suppress" in e for e in errors), errors