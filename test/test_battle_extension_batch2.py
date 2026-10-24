"""战斗系统扩展·批次2 测试：攻击吸收（hp / 护盾 / 属性）+ 战斗内临时快照恢复。

对齐 .qa/plans/battle-system-extension-design.md §7 批次2 验收用例
（用例 9~12）+ 命中失败不吸收 + validator 非法用例。
"""
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import pytest

from src.models.item import Equipment
from src.models.monster import Monster
from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.services.battle import damage as damage_mod
from src.services.battle import BattleService
from src.services.config_validator import ConfigValidator
from src.services.dice_roller import SuccessLevel

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


@pytest.fixture(autouse=True)
def _cleanup_after_test():
    """每用例结束清理测试调查员（隔离 DB 残留）。"""
    yield
    _clean_all()


def _clean_all() -> None:
    for qq in (
        "test_b2_ahp", "test_b2_ash", "test_b2_asm", "test_b2_aat",
        "test_b2_aap", "test_b2_amiss", "test_b2_avic",
    ):
        investigator_repo.delete_by_qq(qq)


def _hit_level():
    return SimpleNamespace(level1=SuccessLevel.SUCCESS, level2=SuccessLevel.FAILURE)


# --- 吸收 hp：怪回血=骰值、玩家扣血（护甲不防） ---
def test_absorb_hp(monkeypatch):
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("6", 6))
    bs = _make_battle("test_b2_ahp", "AbsorbHp")
    bs.hp_record["mon"] = 5
    bs.monster.hp = 5
    action = {"skill": 50, "damage": "0", "吸收": {"类型": "hp", "骰子": "1d6"}}
    inv_before = bs.hp_record["inv"]
    mon_before = bs.hp_record["mon"]
    monster_text, _player_text = bs._handle_monster_attack_success(action, _hit_level())
    assert bs.hp_record["inv"] == inv_before - 6, "玩家扣血 = 骰值 6"
    assert bs.hp_record["mon"] == mon_before + 6, "怪物回血 = 骰值 6"
    assert "生命力" in monster_text, "缺省模板 battle.absorb_hp 应展示"
    _clean("test_b2_ahp")


def test_absorb_hp_armor_not_applied():
    """吸血扣血不走护甲减伤：装甲不减免血抽。"""
    bs = _make_battle("test_b2_ahp", "AbsorbHpArmor")
    # 直接替换 damage 模块的 roll_dice（此用例不依赖 monkeypatch 顺序）
    original = damage_mod.roll_dice
    damage_mod.roll_dice = lambda *a, **k: ("5", 5)
    try:
        inv_before = bs.hp_record["inv"]
        action = {"skill": 50, "damage": "0", "吸收": {"类型": "hp", "骰子": "1d6"}}
        bs._handle_monster_attack_success(action, _hit_level())
        assert bs.hp_record["inv"] == inv_before - 5, "护甲不应减免吸血"
    finally:
        damage_mod.roll_dice = original
    _clean("test_b2_ahp")


def test_absorb_hp_heal_capped_at_max(monkeypatch):
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("20", 20))
    bs = _make_battle("test_b2_ahp", "AbsorbHpCap")
    bs.hp_record["mon"] = bs.monster.max_hp - 3
    bs.monster.hp = bs.hp_record["mon"]
    action = {"skill": 50, "damage": "0", "吸收": {"类型": "hp", "骰子": "1d20"}}
    bs._handle_monster_attack_success(action, _hit_level())
    assert bs.hp_record["mon"] == bs.monster.max_hp, "怪物回血不应超过 max_hp"
    _clean("test_b2_ahp")


# --- 吸收护盾：玩家 temp_hp → 怪 monster_temp_hp ---
def test_absorb_shield_transfer(monkeypatch):
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("4", 4))
    bs = _make_battle("test_b2_ash", "AbsorbShield")
    bs.temp_hp = 10
    action = {"skill": 50, "damage": "0", "吸收": {"类型": "护盾", "骰子": "1d4"}}
    inv_before = bs.hp_record["inv"]
    monster_text, _ = bs._handle_monster_attack_success(action, _hit_level())
    assert bs.temp_hp == 6, "玩家护盾转移 4 点"
    assert bs.monster_temp_hp == 4, "怪物获得对应 monster_temp_hp"
    assert bs.hp_record["inv"] == inv_before, "护盾吸收不扣 HP"
    assert "护盾" in monster_text
    _clean("test_b2_ash")


def test_absorb_shield_min_of_dice_and_shield(monkeypatch):
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("6", 6))
    bs = _make_battle("test_b2_ash", "AbsorbShieldMin")
    bs.temp_hp = 2
    action = {"skill": 50, "damage": "0", "吸收": {"类型": "护盾", "骰子": "1d6"}}
    bs._handle_monster_attack_success(action, _hit_level())
    assert bs.temp_hp == 0, "转移量 = min(6, 2) = 2"
    assert bs.monster_temp_hp == 2
    _clean("test_b2_ash")


def test_absorb_shield_miss_no_penalty(monkeypatch):
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("4", 4))
    bs = _make_battle("test_b2_asm", "AbsorbShieldMiss")
    bs.temp_hp = 0
    action = {"skill": 50, "damage": "0", "吸收": {"类型": "护盾", "骰子": "1d4"}}
    inv_before = bs.hp_record["inv"]
    monster_text, player_text = bs._handle_monster_attack_success(action, _hit_level())
    assert bs.temp_hp == 0
    assert bs.monster_temp_hp == 0
    assert bs.hp_record["inv"] == inv_before, "无护盾吸收落空，伤害 0 无额外惩罚"
    assert "捞了个空" in monster_text or "护盾" in monster_text
    _clean("test_b2_asm")


# --- 吸收属性（临时）：战斗内扣减生效、战斗结束恢复原值 ---
def test_absorb_attr_temp_deduct(monkeypatch):
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("3", 3))
    bs = _make_battle("test_b2_aat", "AbsorbAttrTemp")
    str_before = bs.investigator.get_skill("力量")
    action = {
        "skill": 50, "damage": "0",
        "吸收": {"类型": "属性", "目标": "力量", "骰子": "1d3", "持久": False},
    }
    monster_text, _ = bs._handle_monster_attack_success(action, _hit_level())
    assert bs.investigator.get_skill("力量") == str_before - 3, "战斗内扣减生效"
    assert "力量" in monster_text, "属性名应出现在文案"
    _clean("test_b2_aat")


def test_absorb_attr_temp_restore_on_restore(monkeypatch):
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("3", 3))
    bs = _make_battle("test_b2_aat", "AbsorbAttrRestore")
    str_before = bs.investigator.get_skill("力量")
    action = {
        "skill": 50, "damage": "0",
        "吸收": {"类型": "属性", "目标": "力量", "骰子": "1d3", "持久": False},
    }
    bs._handle_monster_attack_success(action, _hit_level())
    assert bs.investigator.get_skill("力量") == str_before - 3
    bs._restore_absorb_snapshot()
    assert bs.investigator.get_skill("力量") == str_before, "战斗结束恢复原值"
    _clean("test_b2_aat")


def test_absorb_attr_temp_restore_on_victory(monkeypatch):
    """走胜利结算路径（settlement 对齐 hp/san 时机）后属性恢复原值。"""
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("3", 3))
    bs = _make_battle("test_b2_aat", "AbsorbAttrVictory")
    str_before = bs.investigator.get_skill("力量")
    action = {
        "skill": 50, "damage": "0",
        "吸收": {"类型": "属性", "目标": "力量", "骰子": "1d3", "持久": False},
    }
    bs._handle_monster_attack_success(action, _hit_level())
    assert bs.investigator.get_skill("力量") == str_before - 3
    bs.hp_record["mon"] = 0
    bs._handle_victory()
    assert bs.investigator.get_skill("力量") == str_before, "胜利结算后恢复原值"
    _clean("test_b2_aat")


def test_absorb_attr_temp_restore_on_death(monkeypatch):
    """走死亡结算路径后属性恢复原值（不随战死角色残留在 update_data）。"""
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("3", 3))
    bs = _make_battle("test_b2_aat", "AbsorbAttrDeath")
    str_before = bs.investigator.get_skill("力量")
    action = {
        "skill": 50, "damage": "0",
        "吸收": {"类型": "属性", "目标": "力量", "骰子": "1d3", "持久": False},
    }
    bs._handle_monster_attack_success(action, _hit_level())
    bs.hp_record["inv"] = 0
    bs._check_combat_over()
    assert bs.investigator.get_skill("力量") == str_before, "死亡结算后恢复原值"
    _clean("test_b2_aat")


def test_absorb_attr_temp_no_persist_model(monkeypatch):
    """临时吸收不落 player model 持久字段（save 后 model 值仍为原值）。"""
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("3", 3))
    bs = _make_battle("test_b2_aat", "AbsorbAttrNoPersist")
    str_before = bs.investigator.get_skill("力量")
    action = {
        "skill": 50, "damage": "0",
        "吸收": {"类型": "属性", "目标": "力量", "骰子": "1d3", "持久": False},
    }
    bs._handle_monster_attack_success(action, _hit_level())
    bs._restore_absorb_snapshot()
    bs.investigator.save()
    model = investigator_repo.find_by_qq("test_b2_aat")
    assert model.力量 == str_before, "DB 中力量应为原值"
    _clean("test_b2_aat")


# --- 吸收属性（持久）：永久扣减 + 警示文案 ---
def test_absorb_attr_permanent_deduct(monkeypatch):
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("2", 2))
    bs = _make_battle("test_b2_aap", "AbsorbAttrPerm")
    str_before = bs.investigator.get_skill("力量")
    action = {
        "skill": 50, "damage": "0",
        "吸收": {"类型": "属性", "目标": "力量", "骰子": "1d3", "持久": True},
    }
    monster_text, _ = bs._handle_monster_attack_success(action, _hit_level())
    assert bs.investigator.get_skill("力量") == str_before - 2, "持久吸收扣减生效"
    assert "力量" in monster_text
    assert "永久" in monster_text, "持久警示文案应展示"
    # 恢复快照不应回滚持久吸收
    bs._restore_absorb_snapshot()
    assert bs.investigator.get_skill("力量") == str_before - 2, "持久吸收不因恢复而回滚"
    _clean("test_b2_aap")


def test_absorb_attr_permanent_persists_on_save(monkeypatch):
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("2", 2))
    bs = _make_battle("test_b2_aap", "AbsorbAttrPermSave")
    str_before = bs.investigator.get_skill("力量")
    action = {
        "skill": 50, "damage": "0",
        "吸收": {"类型": "属性", "目标": "力量", "骰子": "1d3", "持久": True},
    }
    bs._handle_monster_attack_success(action, _hit_level())
    bs.investigator.save()
    model = investigator_repo.find_by_qq("test_b2_aap")
    assert model.力量 == str_before - 2, "持久吸收应随 save 落库"
    _clean("test_b2_aap")


def test_absorb_attr_floor_at_zero(monkeypatch):
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("999", 999))
    bs = _make_battle("test_b2_aap", "AbsorbAttrFloor")
    action = {
        "skill": 50, "damage": "0",
        "吸收": {"类型": "属性", "目标": "力量", "骰子": "1d6", "持久": True},
    }
    bs._handle_monster_attack_success(action, _hit_level())
    assert bs.investigator.get_skill("力量") == 0, "属性扣减下限为 0"
    _clean("test_b2_aap")


def test_absorb_attr_perm_plus_temp_restore_keeps_perm(monkeypatch):
    """同一技能先持久后临时：恢复临时后持久扣减保留。"""
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("2", 2))
    bs = _make_battle("test_b2_aap", "AbsorbAttrMixed")
    str_before = bs.investigator.get_skill("力量")
    perm_act = {
        "skill": 50, "damage": "0",
        "吸收": {"类型": "属性", "目标": "力量", "骰子": "1d3", "持久": True},
    }
    temp_act = {
        "skill": 50, "damage": "0",
        "吸收": {"类型": "属性", "目标": "力量", "骰子": "1d3", "持久": False},
    }
    bs._handle_monster_attack_success(perm_act, _hit_level())
    bs._handle_monster_attack_success(temp_act, _hit_level())
    assert bs.investigator.get_skill("力量") == str_before - 4
    bs._restore_absorb_snapshot()
    assert bs.investigator.get_skill("力量") == str_before - 2, "恢复仅撤销临时，保留持久扣减"
    _clean("test_b2_aap")


# --- 吸收行动命中失败不吸收 ---
def test_absorb_miss_on_player_dodge(monkeypatch):
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("6", 6))
    bs = _make_battle("test_b2_amiss", "AbsorbMissDodge")
    action = {"skill": 50, "damage": "0", "吸收": {"类型": "hp", "骰子": "1d6"}}
    inv_before = bs.hp_record["inv"]
    mon_before = bs.hp_record["mon"]
    # 玩家闪避成功 → 怪物未命中 → 不触发吸收
    bs._resolve_dodge(
        SimpleNamespace(level1=SuccessLevel.FAILURE, level2=SuccessLevel.SUCCESS),
        action,
    )
    assert bs.hp_record["inv"] == inv_before, "命中失败不扣血"
    assert bs.hp_record["mon"] == mon_before, "命中失败不回血"
    _clean("test_b2_amiss")


def test_absorb_miss_on_monster_fumble(monkeypatch):
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("6", 6))
    bs = _make_battle("test_b2_amiss", "AbsorbMissFumble")
    action = {"skill": 50, "damage": "0", "吸收": {"类型": "hp", "骰子": "1d6"}}
    inv_before = bs.hp_record["inv"]
    mon_before = bs.hp_record["mon"]
    bs._resolve_dodge(
        SimpleNamespace(level1=SuccessLevel.CRITICAL_FAILURE, level2=SuccessLevel.FAILURE),
        action,
    )
    assert bs.hp_record["inv"] == inv_before, "怪物大失败不触发吸收"
    assert bs.hp_record["mon"] == mon_before
    _clean("test_b2_amiss")


def test_absorb_custom_text(monkeypatch):
    monkeypatch.setattr(damage_mod, "roll_dice", lambda *a, **k: ("3", 3))
    bs = _make_battle("test_b2_ahp", "AbsorbCustomText")
    action = {
        "skill": 50, "damage": "0",
        "吸收": {"类型": "属性", "目标": "敏捷", "骰子": "1d3", "持久": False,
                 "文案": "专属文本：敏捷被抽干。"},
    }
    monster_text, _ = bs._handle_monster_attack_success(action, _hit_level())
    assert "专属文本" in monster_text, "攻击项吸收.文案 应覆盖缺省模板"
    _clean("test_b2_ahp")


# --- validator：吸收字段合法 / 非法用例 ---
def _validator_absorb(mon_absorb: dict) -> list[str]:
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
        mon = _load(d, "monster_data.json")
        mon["1"]["攻击"] = {
            "吸收测试": {
                "skill": 50, "damage": "0",
                "吸收": mon_absorb,
                "attack_succ": "命中", "attack_false": "未中",
            }
        }
        _dump(d, "monster_data.json", mon)
        return ConfigValidator(d).validate_all()


def test_validator_absorb_all_types_valid():
    for absorb in (
        {"类型": "hp", "骰子": "1d6"},
        {"类型": "护盾", "骰子": "1d4"},
        {"类型": "属性", "目标": "力量", "骰子": "1d3", "持久": False},
        {"类型": "属性", "目标": "意志", "骰子": "1d3", "持久": True, "文案": "专属"},
    ):
        errors = _validator_absorb(absorb)
        assert errors == [], f"{absorb}\n{errors}"


def test_validator_absorb_illegal_type_reported():
    errors = _validator_absorb({"类型": "吸蓝", "骰子": "1d6"})
    assert any("吸收" in e and "类型" in e for e in errors), errors


def test_validator_absorb_attr_missing_target_reported():
    errors = _validator_absorb({"类型": "属性", "骰子": "1d3"})
    assert any("目标" in e for e in errors), errors


def test_validator_absorb_attr_unknown_target_reported():
    errors = _validator_absorb({"类型": "属性", "目标": "查克拉", "骰子": "1d3"})
    assert any("目标" in e and "查克拉" in e for e in errors), errors


def test_validator_absorb_bad_dice_reported():
    errors = _validator_absorb({"类型": "hp", "骰子": "abc"})
    assert any("骰子" in e for e in errors), errors


def test_validator_absorb_nonbool_persist_reported():
    errors = _validator_absorb({"类型": "属性", "目标": "力量", "骰子": "1d3", "持久": "yes"})
    assert any("持久" in e for e in errors), errors


def test_validator_absorb_not_dict_reported():
    errors = _validator_absorb("hp")
    assert any("吸收" in e for e in errors), errors
