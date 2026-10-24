"""配置全量校验器（config_validator）测试（批次 7）。

覆盖：
1. 现有 15 个 JSON 全部通过（schema + 跨文件引用，全绿基线）；
2. 逐文件 schema 校验（monster 必填/攻击 damage、goods type/part/段位、饰品 effect、
   event 选项结构、check_point 40 天、spell 必填……）；
3. 跨文件引用（monster→物品/法术、event→物品/技能、boss/flow→文案键/怪物、
   shop→物品、首杀表→怪物、check_point→怪物、door→结局/物品）；
4. 故意构造坏引用 → 校验器报出对应错误（fail-fast 抛错 / warn 告警）；
5. ID 段位断言（config-id-naming：饰品 ≥600、残卷 700~799、远程 1~99/511~599 等）。

只测新增行为，不修改 src/。
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import pytest

from src.services.config_validator import (
    ConfigValidator,
    ConfigValidationError,
    DATA_FILES,
    run_config_validation,
)

_DATA_DIR = Path(__file__).parent.parent / "plugins" / "StoryTeller" / "data"


def _validator(data_dir=None) -> ConfigValidator:
    return ConfigValidator(data_dir or _DATA_DIR)


def _copy_data(tmp: Path) -> Path:
    """把 data/*.json 复制到临时目录（测试隔离，改副本不影响生产数据）。

    目录化（第2+3期）：boss/ 与 worlds/ 目录连同其下 *.json 一并复制。
    """
    dst = tmp / "data"
    dst.mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
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


# --- 1. 全量通过（基线） ---
def test_all_data_passes():
    errors = _validator().validate_all()
    assert errors == [], "\n".join(errors)


def test_all_files_covered():
    """数据文件全部在校验清单内（目录化第2+3期：boss_data.json 拆为 boss/ 目录，
    flow_data.json 仅 lane_tale，三世界迁 data/worlds/）。"""
    expected = {
        "monster_data.json", "goods_data.json", "event_data.json",
        "environment_data.json", "check_point.json", "reply_data.json",
        "text_data.json", "npc_data.json",
        "ending_data.json", "flow_data.json",
        "display_data.json", "shop_data.json", "spell_data.json",
        "mainline_config.json", "weights.json", "boss_weights.json",
        "loots.json", "boss/", "worlds/",
        }
    assert set(DATA_FILES) == expected


def test_run_validation_passes_and_returns_zero():
    n = run_config_validation(_DATA_DIR)
    assert n == 0


# --- 2. 逐文件 schema 校验 ---
def test_monster_missing_damage_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        mon = _load(d, "monster_data.json")
        mon["1"]["攻击"]["爪撕"]["damage"] = ""
        _dump(d, "monster_data.json", mon)
        errors = _validator(d).validate_all()
        assert any("1.攻击.爪撕.damage" in e and "damage" in e for e in errors), errors


def test_monster_bad_reward_item_type_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        mon = _load(d, "monster_data.json")
        mon["1"]["奖励"] = {"乌帕": "abc", "物品": []}  # 手写奖励已废弃，注入构造坏值
        _dump(d, "monster_data.json", mon)
        errors = _validator(d).validate_all()
        assert any("1.奖励.乌帕" in e for e in errors), errors


def test_goods_trinket_segment_rule():
    """config-id-naming：饰品 ID 必须 ≥600（避开信物 400-508）。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        goods = _load(d, "goods_data.json")
        goods["599"] = {"name": "越界饰品", "type": "饰品", "part": "饰品",
                        "des": "x", "effect": {"触发": "受击", "条件": {"概率": 1},
                                                "效果": {"type": "san", "值": 1}}}
        _dump(d, "goods_data.json", goods)
        errors = _validator(d).validate_all()
        assert any("599" in e and "饰品" in e for e in errors), errors


def test_goods_spell_scroll_segment_rule():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        goods = _load(d, "goods_data.json")
        goods["600"] = {"name": "越界残卷", "type": "spell_scroll", "part": "法术",
                        "des": "x", "spell": "s001"}
        goods["600"].pop("effect", None)
        _dump(d, "goods_data.json", goods)
        errors = _validator(d).validate_all()
        assert any("600" in e and "700~799" in e for e in errors), errors


def test_goods_trinket_missing_effect_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        goods = _load(d, "goods_data.json")
        goods["605"] = {"name": "缺效果饰品", "type": "饰品", "part": "饰品", "des": "x"}
        _dump(d, "goods_data.json", goods)
        errors = _validator(d).validate_all()
        assert any("605" in e and "effect" in e for e in errors), errors


def test_checkpoint_missing_day_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        cp = _load(d, "check_point.json")
        del cp["40"]
        _dump(d, "check_point.json", cp)
        errors = _validator(d).validate_all()
        assert any("40" in e for e in errors), errors


def test_event_option_missing_input_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        ev = _load(d, "event_data.json")
        ev["古书"]["选项"][0]["输入"] = ""
        _dump(d, "event_data.json", ev)
        errors = _validator(d).validate_all()
        assert any("古书.选项[0].输入" in e for e in errors), errors


def test_spell_missing_required_field_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        sp = _load(d, "spell_data.json")
        del sp["s001"]["name"]
        _dump(d, "spell_data.json", sp)
        errors = _validator(d).validate_all()
        assert any("s001" in e and "name" in e for e in errors), errors


# --- 3. 跨文件引用 ---
def test_monster_item_ref_broken_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        mon = _load(d, "monster_data.json")
        mon["1"]["奖励"] = {"乌帕": 5, "物品": ["99999"]}  # 手写奖励已废弃，注入构造坏引用
        _dump(d, "monster_data.json", mon)
        errors = _validator(d).validate_all()
        assert any("99999" in e and "goods_data" in e for e in errors), errors


def test_monster_spell_ref_broken_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        mon = _load(d, "monster_data.json")
        mon["36"]["ai"]["受伤后"]["法术"] = ["s999"]
        _dump(d, "monster_data.json", mon)
        errors = _validator(d).validate_all()
        assert any("s999" in e and "spell_data" in e for e in errors), errors


def test_event_item_ref_broken_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        ev = _load(d, "event_data.json")
        ev["地窖"]["选项"][0]["效果"]["物品"] = "88888"
        _dump(d, "event_data.json", ev)
        errors = _validator(d).validate_all()
        assert any("88888" in e and "goods_data" in e for e in errors), errors


def test_event_skill_unknown_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        ev = _load(d, "event_data.json")
        ev["古书"]["选项"][0]["效果"]["技能"] = {"不存在技能": 10}
        _dump(d, "event_data.json", ev)
        errors = _validator(d).validate_all()
        assert any("不存在技能" in e for e in errors), errors


def test_checkpoint_monster_ref_broken_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        cp = _load(d, "check_point.json")
        cp["1"] = ["9999"]
        _dump(d, "check_point.json", cp)
        errors = _validator(d).validate_all()
        assert any("9999" in e for e in errors), errors


def test_shop_item_ref_broken_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        shop = _load(d, "shop_data.json")
        shop["40"].append("77777")
        _dump(d, "shop_data.json", shop)
        errors = _validator(d).validate_all()
        assert any("77777" in e and "goods_data" in e for e in errors), errors


def test_boss_monster_ref_broken_reported():
    """目录化：boss/jk.json（data/boss/ 目录文件）怪物引用破坏 → 报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        boss = _load(d, "boss/jk.json")
        boss["怪物id"] = "66666"
        _dump(d, "boss/jk.json", boss)
        errors = _validator(d).validate_all()
        assert any("66666" in e and "monster_data" in e for e in errors), errors


def test_boss_text_key_broken_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        boss = _load(d, "boss/jk.json")
        boss["对话"]["台词"] = "jk.no_such_key"
        _dump(d, "boss/jk.json", boss)
        errors = _validator(d).validate_all()
        assert any("jk.no_such_key" in e for e in errors), errors


def test_flow_monster_ref_broken_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        flow = _load(d, "flow_data.json")
        flow["lane_tale"]["节点"]["n1"]["战斗"] = {"怪物": "55555"}
        _dump(d, "flow_data.json", flow)
        errors = _validator(d).validate_all()
        assert any("55555" in e and "monster_data" in e for e in errors), errors


def test_flow_next_ref_broken_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        flow = _load(d, "flow_data.json")
        flow["lane_tale"]["节点"]["n1"]["选项"][0]["下一步"] = "nobody"
        _dump(d, "flow_data.json", flow)
        errors = _validator(d).validate_all()
        assert any("nobody" in e for e in errors), errors


def test_flow_monster_ref_broken_reported():
    """目录化：worlds/sword_magic.json 季.s1 战斗节点怪物引用破坏 → 报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        flow = _load(d, "worlds/sword_magic.json")
        flow["季"]["s1"]["节点"]["s4"]["战斗"]["怪物"] = "44444"
        _dump(d, "worlds/sword_magic.json", flow)
        errors = _validator(d).validate_all()
        assert any("44444" in e and "monster_data" in e for e in errors), errors


def test_flow_invalid_node_type_reported():
    """未知节点类型（含纪念品之外的非法值）→ 报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        flow = _load(d, "worlds/sword_magic.json")
        flow["季"]["s1"]["节点"]["s1"]["类型"] = "事件"
        _dump(d, "worlds/sword_magic.json", flow)
        errors = _validator(d).validate_all()
        assert any("非法节点类型" in e and "s1" in e for e in errors), errors


def test_flow_souvenir_item_ref_broken_reported():
    """纪念品节点物品引用破坏 → 报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        flow = _load(d, "worlds/sword_magic.json")
        flow["季"]["s1"]["节点"]["souvenir"]["物品"] = "99999"
        _dump(d, "worlds/sword_magic.json", flow)
        errors = _validator(d).validate_all()
        assert any("99999" in e and "goods_data" in e for e in errors), errors


def test_flow_tail_text_key_broken_reported():
    """收尾 exit_text_key 文案键不存在 → 报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        flow = _load(d, "worlds/sword_magic.json")
        flow["季"]["s1"]["收尾"]["exit_text_key"] = "guest.no_such_key"
        _dump(d, "worlds/sword_magic.json", flow)
        errors = _validator(d).validate_all()
        assert any("guest.no_such_key" in e for e in errors), errors


def test_flow_entry_condition_bad_operator_reported():
    """入口条件未知算子 → 报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        flow = _load(d, "worlds/sword_magic.json")
        flow["季"]["s1"]["入口条件"] = {"星数": 99}
        _dump(d, "worlds/sword_magic.json", flow)
        errors = _validator(d).validate_all()
        assert any("星数" in e for e in errors), errors


def test_world_relay_flag_invalid_reported():
    """世界季「接力」标记非 bool → 报错（可选字段，缺省 false）。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        world = _load(d, "worlds/anime_academy.json")
        world["季"]["s2"]["接力"] = "yes"
        _dump(d, "worlds/anime_academy.json", world)
        errors = _validator(d).validate_all()
        assert any("接力" in e and "布尔值" in e for e in errors), errors


def test_world_relay_flag_bool_passes():
    """世界季「接力」bool（true/false）通过校验；缺省（其余季无该字段）不报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        world = _load(d, "worlds/anime_academy.json")
        world["季"]["s2"]["接力"] = True
        world["季"]["s1"]["接力"] = False
        _dump(d, "worlds/anime_academy.json", world)
        errors = _validator(d).validate_all()
        assert errors == [], "\n".join(errors)


def test_boss_trigger_mode_enum_invalid_reported():
    """BOSS「前置模式」非法枚举 → 报错（可选字段，合法 any/all）。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        boss = _load(d, "boss/jk.json")
        boss["触发"]["前置模式"] = "xor"
        _dump(d, "boss/jk.json", boss)
        errors = _validator(d).validate_all()
        assert any("前置模式" in e and "any/all" in e for e in errors), errors


def test_boss_trigger_mode_enum_valid_passes():
    """BOSS「前置模式」合法枚举（any/all）通过；缺省不报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        boss = _load(d, "boss/jk.json")
        boss["触发"]["前置模式"] = "any"
        _dump(d, "boss/jk.json", boss)
        errors = _validator(d).validate_all()
        assert errors == [], "\n".join(errors)


def test_first_kill_ref_broken_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        ending = _load(d, "ending_data.json")
        ending["relics"]["first_kill"]["9999"] = "88888"
        _dump(d, "ending_data.json", ending)
        errors = _validator(d).validate_all()
        assert any("9999" in e for e in errors), errors


def test_door_end_ref_broken_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        ending = _load(d, "ending_data.json")
        ending["door"]["main_choices"]["A"]["end"] = "E99"
        _dump(d, "ending_data.json", ending)
        errors = _validator(d).validate_all()
        assert any("E99" in e for e in errors), errors


def test_linkage_env_ref_broken_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        reply = _load(d, "reply_data.json")
        reply["loop_linkage"]["black_moon_environment"] = "不存在的环境"
        _dump(d, "reply_data.json", reply)
        errors = _validator(d).validate_all()
        assert not any("不存在的环境" in e for e in errors), errors


def test_environment_pool_flag_invalid_reported():
    """环境「入池」标记非 bool 时报错（入池标记承载普通池归属，缺省视为 true）。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        env = _load(d, "environment_data.json")
        env["浓雾"]["入池"] = "yes"  # 非法类型
        _dump(d, "environment_data.json", env)
        errors = _validator(d).validate_all()
        assert any("入池" in e and "浓雾" in e for e in errors), errors


# --- 4. fail-fast / warn 双模式 ---
def test_fail_mode_raises_config_error(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        mon = _load(d, "monster_data.json")
        mon["1"]["奖励"] = {"乌帕": 5, "物品": ["99999"]}
        _dump(d, "monster_data.json", mon)
        monkeypatch.setenv("CONFIG_VALIDATE_MODE", "fail")
        with pytest.raises(ConfigValidationError) as ei:
            run_config_validation(d)
        assert "99999" in str(ei.value)


def test_warn_mode_returns_error_count(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        d = _copy_data(Path(tmp))
        mon = _load(d, "monster_data.json")
        mon["1"]["奖励"] = {"乌帕": 5, "物品": ["99999"]}
        _dump(d, "monster_data.json", mon)
        monkeypatch.setenv("CONFIG_VALIDATE_MODE", "warn")
        assert run_config_validation(d) == 1


def test_default_mode_is_fail():
    os.environ.pop("CONFIG_VALIDATE_MODE", None)
    import src.services.config_validator as cv

    assert cv.CONFIG_VALIDATE_MODE == "fail"


# --- 主线配置（mainline_config.json）schema 校验（批1b） ---
def test_mainline_config_passes():
    """生产 mainline_config.json 全字段通过（positive 基线）。"""
    errors = _validator().validate_all()
    assert errors == [], "\n".join(errors)


def _mainline_broken(bad_key: str, bad_val):
    """构造一个 mainline_config 坏字段的临时数据目录并跑校验。"""
    def run() -> list:
        with tempfile.TemporaryDirectory() as tmp:
            d = _copy_data(Path(tmp))
            cfg = _load(d, "mainline_config.json")
            target = cfg
            keys = bad_key.split(".")
            for k in keys[:-1]:
                target = target[k]
            target[keys[-1]] = bad_val
            _dump(d, "mainline_config.json", cfg)
            return _validator(d).validate_all()
    return run


def test_mainline_bad_ngplus_min_reported():
    assert any("二周目解锁.ng_plus_min" in e
               for e in _mainline_broken("二周目解锁.ng_plus_min", 0)())


def test_mainline_bad_divergence_day_reported():
    assert any("分歧窗口.day" in e
               for e in _mainline_broken("分歧窗口.day", "x")())


def test_mainline_bad_opening_days_reported():
    assert any("开幕插曲.days" in e
               for e in _mainline_broken("开幕插曲.days", "oops")())


def test_mainline_bad_interval_reported():
    assert any("分支节奏.间隔" in e
               for e in _mainline_broken("分支节奏.间隔", 0)())


def test_mainline_bad_tiebreak_reported():
    assert any("平局策略" in e
               for e in _mainline_broken("平局策略", "random_lane")())


def test_mainline_tiebreak_courage_passes():
    """courage=勇气线别名 合法（positive）。"""
    cfg = _load(_DATA_DIR, "mainline_config.json")
    assert cfg["平局策略"] == "courage"
