"""战斗系统扩展·批次3 测试：条件胜利（坚守战）。

对齐 .qa/plans/battle-system-extension-design.md §7 批次3 验收用例
（用例 13：坚守满 N 回合胜利 / 未满回合正常战斗 / 怪物停手）+ 剩余回合提示、
胜利条件缺失零回归、flow 战斗节点透传、validator 非法用例。
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
    bs = BattleService(inv, m)
    bs.is_gm_room = True  # flow 战斗隔离（条件胜利的生产接入点）
    return bs


def _set_hold(bs: BattleService, turns: int, text: str = "") -> None:
    """注入坚守战 胜利条件（对齐 flow 战斗节点透传）。"""
    wc = {"类型": "坚守", "回合": turns}
    if text:
        wc["文案"] = text
    bs.set_battle_cfg({"怪物": "1", "胜利条件": wc})


def _clean(qq: str) -> None:
    investigator_repo.delete_by_qq(qq)


@pytest.fixture(autouse=True)
def _cleanup_after_test():
    """每用例结束清理测试调查员（隔离 DB 残留）。"""
    yield
    for qq in (
        "test_b3_hold", "test_b3_noturn", "test_b3_stop", "test_b3_hint",
        "test_b3_hint1", "test_b3_zero", "test_b3_ctext", "test_b3_noconf",
    ):
        investigator_repo.delete_by_qq(qq)


# --- 坚守满 N 回合胜利（玩家存活） ---
def test_hold_win_at_n_turns():
    """坚守满 N 回合且玩家存活 → _check_combat_over 返回条件胜利文案。"""
    bs = _make_battle("test_b3_hold", "HoldWin")
    _set_hold(bs, 5)
    bs._turn_counter = 5  # 坚守满 5 回合
    bs.hp_record["inv"] = 10  # 玩家存活
    out = bs._check_combat_over()
    assert out is not None, "坚守满 N 回合应触发条件胜利"
    assert "你撑住了" in out, "缺省模板 battle.hold_win 应展示"
    assert bs._conditional_won is True
    assert bs.fight_is_over() is True
    _clean("test_b3_hold")


def test_hold_win_custom_text():
    """胜利条件.文案 覆盖缺省模板。"""
    bs = _make_battle("test_b3_ctext", "HoldCustom")
    _set_hold(bs, 3, text="专属坚守文案：你如磐石般纹丝不动。")
    bs._turn_counter = 3
    bs.hp_record["inv"] = 10
    out = bs._check_combat_over()
    assert out is not None
    assert "专属坚守文案" in out
    assert "你撑住了" not in out, "专属文案应覆盖缺省模板"
    _clean("test_b3_ctext")


# --- 未满回合正常战斗不提前结束 ---
def test_hold_no_early_end_below_n():
    """未满 N 回合 → _check_combat_over 返回 None（正常战斗不提前结束）。"""
    bs = _make_battle("test_b3_noturn", "HoldNotEnd")
    _set_hold(bs, 5)
    bs._turn_counter = 4  # 未满 5 回合
    bs.hp_record["inv"] = 10
    assert bs._check_combat_over() is None
    assert bs._conditional_won is False
    assert bs.fight_is_over() is False
    _clean("test_b3_noturn")


# --- 条件胜利后怪物停手不再攻击 ---
def test_hold_monster_stops_attacking():
    """坚守达成后怪物行动不再攻击（防「坚持住却被补刀」）。"""
    bs = _make_battle("test_b3_stop", "HoldStop")
    _set_hold(bs, 3)
    bs._turn_counter = 3
    bs.hp_record["inv"] = 10
    inv_before = bs.hp_record["inv"]
    result = bs._execute_monster_action("反击")
    joined = "\n".join(str(x) for x in result)
    assert "你撑住了" in joined, "坚守达成应返回条件胜利文案"
    assert bs.hp_record["inv"] == inv_before, "怪物停手，玩家不应再承伤"
    assert bs.fight_is_over() is True
    _clean("test_b3_stop")


# --- 坚守战剩余回合提示（≤2 回合展示） ---
def test_hold_remaining_hint_shown_when_le_2():
    bs = _make_battle("test_b3_hint", "HoldHint")
    _set_hold(bs, 5)
    bs._turn_counter = 3  # 还差 2 回合
    bs.current_turn = "inv"
    hint = bs._hold_remaining_hint()
    assert "喘息" in hint
    assert "2" in hint
    _clean("test_b3_hint")


def test_hold_remaining_hint_shown_when_eq_1():
    bs = _make_battle("test_b3_hint1", "HoldHint1")
    _set_hold(bs, 5)
    bs._turn_counter = 4  # 还差 1 回合
    bs.current_turn = "inv"
    hint = bs._hold_remaining_hint()
    assert "喘息" in hint
    assert "1" in hint
    _clean("test_b3_hint1")


def test_hold_remaining_hint_hidden_when_gt_2():
    bs = _make_battle("test_b3_hint", "HoldHintGt")
    _set_hold(bs, 5)
    bs._turn_counter = 1  # 还差 4 回合，不展示
    bs.current_turn = "inv"
    assert bs._hold_remaining_hint() == ""
    _clean("test_b3_hint")


def test_hold_remaining_hint_in_turn_prompt():
    """剩余回合提示并入玩家行动前回合提示（_get_next_turn_prompt）。"""
    bs = _make_battle("test_b3_hint", "HoldHintPrompt")
    _set_hold(bs, 5)
    bs._turn_counter = 3
    bs.current_turn = "inv"
    prompt = bs._get_next_turn_prompt()
    assert "再撑过这 2 次喘息" in prompt
    _clean("test_b3_hint")


# --- 胜利条件缺失零回归 ---
def test_no_win_condition_zero_regression():
    """无 胜利条件 配置 → 现有 HP 归零判定不变（零回归）。"""
    bs = _make_battle("test_b3_zero", "HoldZero")
    bs._turn_counter = 99  # 极高回合数也无条件胜利
    bs.hp_record["inv"] = 10
    bs.hp_record["mon"] = 10
    assert bs._check_combat_over() is None
    assert bs.fight_is_over() is False
    _clean("test_b3_zero")


def test_no_win_condition_monster_death_normal():
    """无 胜利条件：怪物 HP 归零仍走正常胜利判定。"""
    bs = _make_battle("test_b3_noconf", "HoldNoConf")
    bs._turn_counter = 3
    bs.hp_record["inv"] = 10
    bs.hp_record["mon"] = 0
    out = bs._check_combat_over()
    assert out is not None, "怪物死亡仍应触发正常胜利"
    assert "victory" not in bs.hold_win_text or True  # 无配置不写条件胜利文案
    assert bs.hold_win_text == ""
    _clean("test_b3_noconf")


def test_no_win_condition_hint_empty():
    bs = _make_battle("test_b3_noconf", "HoldNoHint")
    bs._turn_counter = 1
    bs.current_turn = "inv"
    assert bs._hold_remaining_hint() == ""
    assert bs._hold_remaining() == 10**9  # 无配置返回极大值（不触发提示）
    _clean("test_b3_noconf")


# --- flow 战斗节点透传（胜利条件 + 条件胜利走胜利分支） ---
def _flow_hold():
    """注册坚守战 flow：战斗节点携带 胜利条件，胜利分支配置效果/回复/下一步。"""
    from src.services.flow_engine import register_flow

    register_flow(
        "test_hold_flow",
        {
            "id": "test_hold_flow",
            "名字": "坚守测试",
            "入口": "n1",
            "节点": {
                "n1": {
                    "类型": "文本",
                    "选项": [{"输入": "迎战", "下一步": "battle"}],
                },
                "battle": {
                    "类型": "战斗",
                    "战斗": {
                        "怪物": "1",
                        "开场": "守门人拦住了去路。",
                        "胜利条件": {"类型": "坚守", "回合": 3},
                        "胜利": {"回复": "你守住了！", "下一步": "end"},
                        "战败": {"回复": "你倒下了。", "下一步": "end"},
                    },
                },
                "end": {"类型": "结束"},
            },
        },
    )


def test_flow_battle_hold_config_passthrough():
    """flow 战斗节点解析透传 胜利条件（service._win_condition 就位）。"""
    import asyncio

    import nonebot

    nonebot.init()

    from src.services.flow_engine import (
        flow_states,
        handle_flow_input,
        render_flow_node,
        resolve_flow_ref,
        start_flow,
    )

    _flow_hold()
    qq = "test_b3_hold"
    _clean(qq)
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    data["san"] = 60
    data["意志"] = 60
    inv_model = investigator_repo.create_and_save(qq, "HoldFlow", data)
    inv = Investigator(inv_model)
    inv.day = 10
    inv.save()

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    fid = resolve_flow_ref("test_hold_flow")
    state = start_flow(qq, fid)
    asyncio.run(render_flow_node(qq, state, None, send))
    asyncio.run(handle_flow_input(qq, "迎战", None, send, None))
    service = flow_states[qq]["battle"]
    assert service is not None
    assert service._win_condition == {"类型": "坚守", "回合": 3}

    # 模拟坚守满 3 回合 → 条件胜利（monkeypatch execute_action，对齐既有 flow 战斗测试）
    def _hold(action):
        service._conditional_won = True
        service.hold_win_text = data_loader.get_text("battle.hold_win")
        return ("你撑过了最后一声钟鸣。",)

    service.execute_action = _hold  # type: ignore[method-assign]
    asyncio.run(handle_flow_input(qq, "格斗", None, send, None))
    joined = "\n".join(str(x) for x in sent)
    assert "你守住了！" in joined, "条件胜利应走 flow 胜利分支（胜利回复）"
    assert "你撑住了" in joined, "条件胜利文案（battle.hold_win 回退）应展示"
    assert qq not in flow_states, "条件胜利推进到 end 并清理"
    _clean(qq)


# --- validator：胜利条件 合法 / 非法用例 ---
def _validator_win_condition(wc, flow: bool = True) -> list[str]:
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
        if flow:
            flowd = _load(d, "flow_data.json")
            flowd["test_wc"] = {
                "名字": "胜利条件测试",
                "入口": "b",
                "节点": {
                    "b": {
                        "类型": "战斗",
                        "战斗": {"怪物": "1", "胜利条件": wc},
                    },
                    "e": {"类型": "结束"},
                },
            }
            _dump(d, "flow_data.json", flowd)
        else:
            mon = _load(d, "monster_data.json")
            text = _load(d, "text_data.json")
            text.setdefault("test_wc_boss", {})
            for key in ("title", "appear", "challenge_pending", "skip_done"):
                text["test_wc_boss"].setdefault(key, f"boss text {key}")
            _dump(d, "text_data.json", text)
            boss = {
                "id": "test_wc_boss",
                "怪物id": "1",
                "对话": {
                    "标题": "test_wc_boss.title",
                    "台词": "test_wc_boss.appear",
                    "挑战后": "test_wc_boss.challenge_pending",
                    "跳过": "test_wc_boss.skip_done",
                },
                "战斗": {"胜利条件": wc},
            }
            (d / "boss").mkdir(parents=True, exist_ok=True)
            (d / "boss" / "test_wc_boss.json").write_text(
                json.dumps(boss, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        return ConfigValidator(d).validate_all()


def test_validator_win_condition_valid():
    for wc in (
        {"类型": "坚守", "回合": 5},
        {"类型": "坚守", "回合": 3, "文案": "专属文案"},
    ):
        errors = _validator_win_condition(wc)
        assert errors == [], f"{wc}\n{errors}"


def test_validator_win_condition_boss_valid():
    errors = _validator_win_condition({"类型": "坚守", "回合": 5}, flow=False)
    assert errors == [], errors


def test_validator_win_condition_illegal_type_reported():
    errors = _validator_win_condition({"类型": "限时", "回合": 5})
    assert any("胜利条件" in e and "类型" in e for e in errors), errors


def test_validator_win_condition_non_positive_turns_reported():
    for bad in (0, -1, "abc", True):
        errors = _validator_win_condition({"类型": "坚守", "回合": bad})
        assert any("胜利条件" in e and "回合" in e for e in errors), (bad, errors)


def test_validator_win_condition_missing_turns_reported():
    errors = _validator_win_condition({"类型": "坚守"})
    assert any("胜利条件" in e and "回合" in e for e in errors), errors


def test_validator_win_condition_not_dict_reported():
    errors = _validator_win_condition("坚守")
    assert any("胜利条件" in e for e in errors), errors
