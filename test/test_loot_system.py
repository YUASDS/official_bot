"""战利品系统（loots.json 按 hp 分档 + 归一化权重掉落 + 乌帕随档位）测试。

覆盖（对齐 .qa/plans/loot-system-v2.md 测试计划）：
1. 加权抽取归一化（mock random）；空池/未抽中 → 仅乌帕
2. 档位边界（1/11/12/17/18/24/25/39/40/79/80/130）正确落档
3. 侦查失败不掉物品（half_loot_gold = 档位乌帕上限一半）
4. flow 战斗胜利掉落行 + 节点胜利效果共存（GM 房间排除）
5. BOSS 档掉落 + 信物首杀保留
6. 配置校验（非法物品 id / 档位区间重叠 / 乌帕 min>max → 报错）
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

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
from src.models.monster import Monster, _weighted_choice
from src.services.data_loader import data_loader

_DATA_DIR = Path(__file__).parent.parent / "plugins" / "StoryTeller" / "data"

_BOSS_POOL_IDS = {"10", "511", "600", "601", "602", "603", "703"}  # T6 BOSS 档（档位池）
_HIDDEN_ELITE_IDS = {
    str(e["物品"])
    for e in json.load(
        open("plugins/StoryTeller/data/loots.json", encoding="utf-8-sig")
    )["pools"]["hidden_jk"]
}  # 48/38 已挂 hidden_elite 手动池
_TIER2_POOL_IDS = {"109", "116", "102", "301", "105", "701"}


def _make_inv(qq: str, name: str = "Loot") -> Investigator:
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


def _drive(qq: str, choice: str, bot=None, token=None) -> list[str]:
    from src.services.flow_engine import handle_flow_input

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(handle_flow_input(qq, choice, bot or ConsoleBot(), send, token))
    return sent


def _explore(qq: str, ref: str, bot=None) -> list[str]:
    from src.services.flow_engine import render_flow_node, resolve_flow_ref, start_flow

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    fid = resolve_flow_ref(ref)
    state = start_flow(qq, fid) if fid else None
    if state is not None:
        asyncio.run(render_flow_node(qq, state, bot or ConsoleBot(), send))
    return sent


# --- 1. 加权抽取归一化（mock random） / 空池 ---
def test_weighted_choice_normalized_mock_random():
    pool = [{"物品": "101", "权重": 1}, {"物品": "102", "权重": 3}]
    with patch("src.models.monster.random.uniform", return_value=0.0):
        assert _weighted_choice(pool) == "101"  # roll=0 → 首件
    with patch("src.models.monster.random.uniform", return_value=1.01):
        assert _weighted_choice(pool) == "102"  # 越过 101 累计权重 1 → 102
    with patch("src.models.monster.random.uniform", return_value=1.0):
        assert _weighted_choice(pool) == "101"  # 边界 roll==cum → 前件胜
    with patch("src.models.monster.random.uniform", return_value=0.99):
        assert _weighted_choice(pool) == "101"  # 0.99 ≤ 1 → 101


def test_weighted_choice_empty_and_bad_pool():
    assert _weighted_choice([]) is None
    assert _weighted_choice(None) is None
    assert _weighted_choice([{"物品": "101", "权重": 0}]) is None  # 全非法权重
    assert _weighted_choice([{"物品": "101", "权重": "x"}]) is None


def test_generate_loot_no_pool_only_gold():
    """无档位池（空 tier）→ 不掉物品，仅乌帕。"""
    m = Monster("1")
    with patch.object(
        data_loader, "get_loot_tier", return_value={"min_hp": 1, "max_hp": 999, "乌帕": [5, 5], "pool": []}
    ), patch.object(data_loader, "get_loot_pool", return_value=None):
        gold, dropped, msg = m.generate_loot(1)
    assert dropped is None
    assert gold == 5
    assert msg.endswith("5 乌帕。")


# --- 2. 档位边界 ---
def test_loot_tier_boundaries():
    for hp, (lo, hi) in {
        1: (8, 15), 11: (8, 15), 12: (10, 20), 17: (10, 20), 18: (12, 25),
        24: (12, 25), 25: (15, 30), 39: (15, 30), 40: (20, 40), 79: (20, 40),
        80: (30, 60), 130: (30, 60),
    }.items():
        tier = data_loader.get_loot_tier(hp)
        assert tier["乌帕"] == [lo, hi], f"hp={hp} 落档错误"


def test_loot_tier_boss_override():
    assert data_loader.get_loot_tier(10, boss=True)["min_hp"] == 80  # 38/48 hp 不足 80 仍落 BOSS 档
    assert data_loader.get_loot_tier(999)["max_hp"] is None  # 超档落末档


def test_loot_pool_named_empty():
    assert data_loader.get_loot_pool(None) is None
    assert data_loader.get_loot_pool("不存在的池") is None


# --- 3. 侦查失败保底（half_loot_gold = 档位上限一半）---
def test_half_loot_gold_from_tier():
    assert Monster("2").half_loot_gold() == 7   # hp10 → 档1 上限 15
    assert Monster("30").half_loot_gold() == 10  # hp14 → 档2 上限 20
    assert Monster("35").half_loot_gold() == 12  # hp20 → 档3 上限 25
    assert Monster("40").half_loot_gold() == 30  # hp110 → 档6 上限 60（GM 守卫强化后）
    assert Monster("45").half_loot_gold() == 10  # hp15 → 档2 上限 20
    assert Monster("52").half_loot_gold() == 30  # hp999 → 档6 上限 60


def test_generate_loot_gold_in_tier_range():
    m = Monster("69")  # 兽骸猎手 hp16 → 档2 [10, 20]（无 loots 手动池，纯档位样本）
    for _ in range(30):
        gold, dropped, _msg = m.generate_loot(1)
        assert 10 <= gold <= 20
        assert dropped is None or dropped.id in _TIER2_POOL_IDS


def test_generate_loot_boss_tier_pool():
    m = Monster("69")  # 无 loots 手动池；模拟 BOSS 档（hp999 → 档6）
    m.hp = 999
    for _ in range(30):
        gold, dropped, _msg = m.generate_loot(1, boss=True)
        assert 30 <= gold <= 60
        if dropped:
            assert dropped.id in _BOSS_POOL_IDS


# --- 4. flow 战斗胜利掉落行 + 节点效果共存（GM 房间排除）---
def _flow_win_flow():
    from src.services.flow_engine import register_flow

    register_flow(
        "test_loot_flow",
        {
            "id": "test_loot_flow",
            "名字": "战利品测试",
            "入口": "n1",
            "节点": {
                "n1": {
                    "类型": "文本",
                    "选项": [{"输入": "迎战", "下一步": "battle"}],
                },
                "battle": {
                    "类型": "战斗",
                    "战斗": {
                        "怪物": "45",
                        "胜利": {
                            "效果": {"金币": 30, "hp": 5},
                            "回复": "你赢了。",
                            "下一步": "end",
                        },
                        "战败": {"回复": "你输了。", "下一步": "end"},
                    },
                },
                "end": {"类型": "结束"},
            },
        },
    )


def test_flow_battle_victory_loot_line_with_node_effects():
    from database.db import get_info

    from src.services.flow_engine import flow_states

    _flow_win_flow()
    qq = "loot_flow_win"
    inv = _make_inv(qq)
    inv.day = 10
    inv.hp = 5
    inv.save()
    _explore(qq, "test_loot_flow")
    _drive(qq, "迎战")
    state = flow_states[qq]
    service = state["battle"]
    gold_before = get_info(qq).gold
    hp_before = Investigator(investigator_repo.find_by_qq(qq)).hp  # 开战后的实际 HP

    def _win(action):
        service.hp_record["mon"] = 0
        return ("你挥出最后一击。",)

    service.execute_action = _win  # type: ignore[method-assign]
    out = _drive(qq, "格斗")
    text = "\n".join(out)
    assert "你赢了。" in text  # 节点胜利回复
    assert "乌帕" in text  # 战利品行（怪物 45 hp15 → 档2）
    assert gold_before + 40 <= get_info(qq).gold <= gold_before + 50  # 效果 +30 与掉落共存
    assert Investigator(investigator_repo.find_by_qq(qq)).hp == hp_before + 5  # 节点效果 hp+5
    assert qq not in flow_states
    _cleanup(qq)


def test_flow_battle_victory_gm_room_no_loot():
    """GM 房间（flow_id=gm_room）隔离：胜利不掉落（守卫奖励为空）。"""
    import asyncio

    from database.db import get_info

    from src.services.flow_engine import flow_states
    from test_gm_room import _drive_to_battle, _flow_resolve

    qq = "loot_gm_room"
    inv = _make_inv(qq)
    inv.day = 20
    inv.save()
    asyncio.run(_drive_to_battle(qq))
    gold_before = get_info(qq).gold
    asyncio.run(_flow_resolve(qq, win=True))
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    eq, _ = reloaded.get_equipments()
    assert set(eq) <= {"101", "306"}  # 仅默认弹簧折刀 + 节点胜利效果 306，无战利品掉落
    assert get_info(qq).gold == gold_before  # 不掉乌帕
    assert qq not in flow_states
    _cleanup(qq)


# --- 5. BOSS 档掉落 + 信物首杀保留 ---
def test_boss_victory_loot_and_relic_first_kill():
    from database.db import get_info

    from src.services.battle import BattleService

    qq = "loot_jk_boss"
    inv = _make_inv(qq)
    gold_before = get_info(qq).gold
    bs = BattleService(inv, Monster("48"))
    bs.hp_record["mon"] = 0
    _header, detail = bs._victory_parts()
    equip, _ = inv.get_equipments()
    assert any(equip.get(i, 0) == 1 for i in _HIDDEN_ELITE_IDS)  # 48 挂 hidden_elite 池 1 件
    assert equip.get("510") == 1  # 信物段首杀保留
    assert gold_before + 30 <= get_info(qq).gold <= gold_before + 60
    # 二次胜利：物品可重复，信物 510 不重复
    inv2 = Investigator(investigator_repo.find_by_qq(qq))
    bs2 = BattleService(inv2, Monster("48"))
    bs2.hp_record["mon"] = 0
    bs2._victory_parts()
    equip2, _ = inv2.get_equipments()
    assert equip2.get("510") == 1
    _cleanup(qq)


def test_normal_battle_victory_scout_fail_no_item():
    """侦查失败：不掉物品，半额乌帕保底（档位上限一半）。"""
    from database.db import get_info

    from src.services.battle import BattleService

    qq = "loot_scout_fail"
    inv = _make_inv(qq)
    gold_before = get_info(qq).gold
    bs = BattleService(inv, Monster("45"))  # hp15 → 档2，上限 20 → 半额 10
    bs.hp_record["mon"] = 0
    with patch("src.services.dice_roller.random.randint", return_value=100):  # 侦查大失败
        _header, detail = bs._victory_parts()
    equip, _ = inv.get_equipments()
    assert set(equip) <= {"101"}  # 不掉物品
    assert get_info(qq).gold == gold_before + 10  # 半额保底
    assert "残破的收获" in detail
    _cleanup(qq)


# --- 6. 配置校验（非法物品 id / 区间重叠 / 乌帕 min>max → 报错）---
def _loot_tmp(tmp: Path) -> Path:
    from src.services.config_validator import DATA_FILES

    dst = tmp / "data"
    dst.mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
        if name.endswith("/"):
            sub = dst / name
            sub.mkdir(parents=True, exist_ok=True)
            for f in (_DATA_DIR / name).glob("*.json"):
                shutil.copy(f, sub / f.name)
        else:
            src = _DATA_DIR / name
            if src.exists():
                shutil.copy(src, dst / name)
    return dst


def _loot_errors(tmp_data: Path, loots: dict) -> list:
    from src.services.config_validator import ConfigValidator

    (tmp_data / "loots.json").write_text(
        json.dumps(loots, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return ConfigValidator(tmp_data).validate_all()


def _valid_loots() -> dict:
    return json.loads((_DATA_DIR / "loots.json").read_text(encoding="utf-8-sig"))


def test_loot_validator_production_passes():
    from src.services.config_validator import ConfigValidator

    errors = ConfigValidator().validate_all()
    assert errors == [], "\n".join(errors)


def test_loot_validator_bad_item_id_reported():
    with tempfile.TemporaryDirectory() as tmp:
        loots = _valid_loots()
        loots["tiers"][0]["pool"].append({"物品": "99999", "权重": 1})
        d = _loot_tmp(Path(tmp))
        errors = _loot_errors(d, loots)
        assert any("99999" in e and "goods_data" in e for e in errors), errors


def test_loot_validator_tier_overlap_reported():
    with tempfile.TemporaryDirectory() as tmp:
        loots = _valid_loots()
        loots["tiers"][1]["min_hp"] = 10  # 与档 0（1~11）重叠
        d = _loot_tmp(Path(tmp))
        errors = _loot_errors(d, loots)
        assert any("重叠" in e and "tiers[1]" in e for e in errors), errors


def test_loot_validator_upat_min_gt_max_reported():
    with tempfile.TemporaryDirectory() as tmp:
        loots = _valid_loots()
        loots["tiers"][0]["乌帕"] = [15, 8]
        d = _loot_tmp(Path(tmp))
        errors = _loot_errors(d, loots)
        assert any("乌帕" in e and "min > max" in e for e in errors), errors


def test_loot_validator_bad_weight_reported():
    with tempfile.TemporaryDirectory() as tmp:
        loots = _valid_loots()
        loots["tiers"][0]["pool"][0]["权重"] = 0
        d = _loot_tmp(Path(tmp))
        errors = _loot_errors(d, loots)
        assert any("权重" in e and "≥1" in e for e in errors), errors


def test_loot_validator_named_pool_item_reported():
    with tempfile.TemporaryDirectory() as tmp:
        loots = _valid_loots()
        loots["pools"] = {"gm": [{"物品": "88888", "权重": 2}]}
        d = _loot_tmp(Path(tmp))
        errors = _loot_errors(d, loots)
        assert any("88888" in e and "goods_data" in e for e in errors), errors


# --- 7. 生成器基础 ---
def test_generate_loot_returns_tuple3():
    m = Monster("1")
    gold, dropped, msg = m.generate_loot()
    assert isinstance(gold, int)
    assert dropped is None or dropped.is_valid
    assert isinstance(msg, str) and msg
