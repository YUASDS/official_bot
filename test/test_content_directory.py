"""内容目录化（第2+3期）新增行为测试：BOSS 目录加载 / 世界季结构 / flow_id 兼容映射。

覆盖：
1. boss_registry 从 data/boss/*.json 目录加载（每 BOSS 一文件），字段与原 boss_data 逐字一致；
   boss/ 目录放非 BOSS 文件（如 boss_weights.json 误放）被安全跳过；
2. flow 注册表：data/worlds/*.json 季结构展开注册（单季世界规范键 = 世界 id）；
   flow_id 兼容别名：flow.<世界id>.s1 → 世界 id；世界名字解析；季级触发概率读取；
3. 多季世界（模拟）：每季注册 flow.<世界id>.<季>，裸世界 id → 首季别名；
4. config_validator：世界文件 schema（季/触发概率/旗标算子）与 BOSS 目录校验；
5. 兼容回归：resolve_flow_ref / guest_registry / 战败不再遇语义保持。

注意：本文件为「新测试不入库」验证专用，不随生产提交。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.services import flow_engine as fe
from src.services.boss_framework import boss_registry, get_boss
from src.services.config_validator import ConfigValidator

_DATA = Path(__file__).parent.parent / "plugins" / "StoryTeller" / "data"
_WORLDS = ("sword_magic", "dnd_dungeon", "anime_academy")
# anime_academy 为多季世界（S1+S2）：每季注册 flow.<世界id>.<季>
_ANIME_SEASONS = ("flow.anime_academy.s1", "flow.anime_academy.s2")
_WORLD_FLOWS = ("sword_magic", "dnd_dungeon", *_ANIME_SEASONS)


# --- C1：BOSS 目录加载 ---
def test_boss_registry_loaded_from_dir():
    reg = boss_registry()
    assert set(reg.keys()) == {"jk", "qiren"}
    assert reg["jk"]["怪物id"] == "48"
    assert reg["qiren"]["战斗"]["强制环境"] == "庄园.黑色满月"
    # 与原 boss_data.json 归档条目字段级逐字一致（战利品系统已废弃手写 奖励.胜利.物品，
    # 掉落走 loots.json 档位池——从归档快照剔除该字段后再比较）
    old = json.loads(
        (Path(__file__).parent.parent / "test" / "archive" / "content-directory"
         / "boss_data.json").read_text(encoding="utf-8-sig"))
    for bid in ("jk", "qiren"):
        expected = dict(old[bid])
        win = (expected.get("奖励") or {}).get("胜利")
        if isinstance(win, dict):
            win.pop("物品", None)
        assert get_boss(bid) == expected, bid


def test_boss_dir_ignores_non_boss_files():
    """boss/ 目录误放非 BOSS 文件（如 boss_weights.json 副本）→ 注册表安全跳过。"""
    stray = _DATA / "boss" / "_stray_test.json"
    stray.write_text(json.dumps({"dice": "d20", "value": 1}), encoding="utf-8")
    try:
        from src.services.boss_framework import _load_boss_files
        reg = _load_boss_files()
        assert set(reg.keys()) == {"jk", "qiren"}
    finally:
        stray.unlink()


# --- C2：世界季结构 / flow_id 兼容映射 ---
def test_flow_registry_worlds_and_seasons():
    reg = fe.flow_registry()
    assert set(reg) >= {"lane_tale", *_WORLD_FLOWS}
    for fid in _WORLD_FLOWS:
        f = reg[fid]
        assert f["入口"] == "enter"
        assert f.get("名字"), fid
        assert (f.get("收尾") or {}).get("skip_daily") is True
        assert f["节点"]["end"]["类型"] == "结束"
        assert f["节点"]["end_lose"]["完成标记"] is False
        assert f.get("触发概率") == {"dice": "d100", "value": (10 if fid == "flow.anime_academy.s2" else 3), "relation": "lte"}  # s2 用户配置 3→10，其余世界 3
    # 多季世界：S2 需 S1 完成解锁（引擎写入键 flow.flow.anime_academy.s1.done）
    s2_cond = reg["flow.anime_academy.s2"]["入口条件"]
    assert "日" in s2_cond and "any" in s2_cond
    assert "flow.flow.anime_academy.s1.done" in str(s2_cond)


def test_resolve_flow_ref_compat_aliases():
    """单季世界：规范键 = 世界 id；别名 flow.<世界id>.s1 → 世界 id；世界名字可解析。
    多季世界（anime_academy S1+S2）：每季 flow.<世界id>.<季>，裸世界 id 别名 → 首季。"""
    assert fe.resolve_flow_ref("sword_magic") == "sword_magic"
    assert fe.resolve_flow_ref("flow.sword_magic.s1") == "sword_magic"
    assert fe.resolve_flow_ref("anime_academy") == "flow.anime_academy.s1"
    assert fe.resolve_flow_ref("flow.anime_academy.s1") == "flow.anime_academy.s1"
    assert fe.resolve_flow_ref("flow.anime_academy.s2") == "flow.anime_academy.s2"
    assert fe.resolve_flow_ref("王都黄昏") == "sword_magic"
    assert fe.resolve_flow_ref("巷口异闻") == "lane_tale"
    assert fe.resolve_flow_ref("no_such") is None


def test_guest_registry_worlds_from_world_files():
    from src.plugins.guest import guest_registry
    reg = guest_registry()
    assert set(reg.keys()) >= set(_WORLD_FLOWS)
    for fid in _WORLD_FLOWS:
        assert (reg[fid].get("触发概率") or {}).get("value") == (10 if fid == "flow.anime_academy.s2" else 3)  # s2 用户配置 3→10


def test_multi_season_world_expansion():
    """多季世界（模拟）：每季注册 flow.<世界id>.<季>；裸世界 id 别名 → 首季。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "multi_world.json"
        p.write_text(json.dumps({
            "id": "multi_world",
            "名字": "多季试炼",
            "季": {
                "s1": {"标题": "S1", "入口": "enter",
                       "节点": {"enter": {"类型": "文本", "下一步": "end"},
                                "end": {"类型": "结束"}}},
                "s2": {"标题": "S2", "入口": "enter",
                       "入口条件": {"旗标": "flow.multi_world.s1_done"},
                       "节点": {"enter": {"类型": "文本", "下一步": "end"},
                                "end": {"类型": "结束"}}},
            },
        }), encoding="utf-8")
        flows, aliases = fe._expand_world_file(p)
        assert set(flows) == {"flow.multi_world.s1", "flow.multi_world.s2"}
        assert aliases == {"multi_world": "flow.multi_world.s1"}
        assert flows["flow.multi_world.s1"]["名字"] == "多季试炼"
        assert flows["flow.multi_world.s2"]["入口条件"]["旗标"] == "flow.multi_world.s1_done"


# --- C3：config_validator 世界/BOSS 目录校验 ---
def test_validator_world_bad_season_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "data"
        (d / "worlds").mkdir(parents=True)
        (d / "boss").mkdir()
        for name in ("monster_data.json", "goods_data.json", "text_data.json",
                     "environment_data.json", "flow_data.json"):
            import shutil
            shutil.copy(_DATA / name, d / name)
        (d / "worlds" / "bad_world.json").write_text(json.dumps(
            {"id": "bad_world", "名字": "坏世界", "季": {"s1": "not-a-dict"}},
            ensure_ascii=False), encoding="utf-8")
        errors = ConfigValidator(d).validate_all()
        assert any("季.s1" in e and "dict" in e for e in errors), errors


def test_validator_world_trigger_prob_checked():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "data"
        (d / "worlds").mkdir(parents=True)
        (d / "boss").mkdir()
        import shutil
        for name in ("monster_data.json", "goods_data.json", "text_data.json",
                     "environment_data.json", "flow_data.json"):
            shutil.copy(_DATA / name, d / name)
        (d / "worlds" / "bad_prob.json").write_text(json.dumps(
            {"id": "bad_prob", "名字": "坏概率", "季": {"s1": {
                "入口": "enter",
                "触发概率": {"dice": "d100", "value": "abc"},
                "节点": {"enter": {"类型": "文本", "下一步": "end"},
                         "end": {"类型": "结束"}},
            }}},
            ensure_ascii=False), encoding="utf-8")
        errors = ConfigValidator(d).validate_all()
        assert any("触发概率.value" in e for e in errors), errors


def test_validator_flag_operator_allowed():
    """旗标 算子（目录化第1期新增，第3期校验收口）在世界入口条件中合法。"""
    from src.services.config_validator import CONDITION_KEYS
    assert "旗标" in CONDITION_KEYS


def test_validator_boss_dir_loaded_and_checked():
    """BOSS 目录文件进入校验（data/boss/jk.json 与 qiren.json 全绿）。"""
    errors = ConfigValidator(_DATA).validate_all()
    assert errors == [], "\n".join(errors)


# --- C4：兼容回归（关键行为不变） ---
def test_visited_done_exclusion_semantics():
    """战败不再遇（第1期）：visited/done 任一命中即世界永久排除——目录化后语义不变。"""
    from src.models.player import (EndingCollectionModel, EndingProgressModel,
                                   Investigator, InvestigatorGenerator,
                                   investigator_repo)
    from src.plugins.guest import _world_available

    qq = "qa_cd_visited"
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    data["san"] = 60
    inv = Investigator(investigator_repo.create_and_save(qq, "Cd", data))
    inv.day = 15
    inv.save()
    flow = fe.get_flow("sword_magic")
    assert _world_available(inv, flow, "sword_magic") is True
    inv.set_flag("flow.sword_magic.visited", True)
    inv.save()
    assert _world_available(inv, flow, "sword_magic") is False
    inv.clear_flag("flow.sword_magic.visited")
    inv.set_flag("flow.sword_magic.done", 1)
    inv.save()
    assert _world_available(inv, flow, "sword_magic") is False
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)
