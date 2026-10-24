"""疯狂失控·current_action 残留回归测试（战报 bug：疯狂随机「格斗」误报没有装备格斗武器）。

根因：_resolve_madness 随机行动后未同步 self.current_action，武器判定读到残留动作
（如前一次「换弹」）→ get_equipped_id("换弹") 为空 → 误报 battle.no_melee_weapon。
修复：疯狂轮执行前 self.current_action = random_action。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

from src.models.monster import Monster
from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.services.battle import BattleService


def _make_inv(qq: str) -> Investigator:
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    model = investigator_repo.create_and_save(qq, "Repro", data)
    inv = Investigator(model)
    return inv


def _madness_melee(qq: str) -> tuple[bool, str]:
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("1"))
    # 数据面控制随机必选「格斗」（避免 mock 全局 random 污染 monster 内部 choice）
    inv.get_available_actions = lambda: {"inv": ["格斗"]}
    bs.is_madness = True
    bs.madness_duration = 1
    bs.current_turn = "inv"
    bs.current_action = "换弹"  # ← 残留动作（战报场景：上一次疯狂轮用了换弹）
    bs.available_actions = None
    parts = bs._resolve_madness()
    text = "\n".join(p for p in parts if isinstance(p, str))
    investigator_repo.delete_by_qq(qq)
    bad = "没有装备格斗武器" in text
    synced = bs.current_action == "格斗"
    return bad, text, synced


def test_madness_melee_no_residual_no_miss_report():
    """疯狂随机「格斗」不会再因 current_action 残留（换弹）误报没有装备格斗武器。"""
    bad, text, synced = _madness_melee("tmp_madfix")
    assert bad is False, f"出现误报文案: {text[:200]}"
    assert synced is True, "疯狂轮未同步 current_action"


def test_madness_melee_normal_player_have_knife():
    """默认玩家（近战槽=折刀101）疯狂格斗正常走检定（修复后无无武器报错）。"""
    bad, text, _ = _madness_melee("tmp_madfix2")
    assert bad is False
    assert "检定" in text  # 正常进格斗检定/交锋
