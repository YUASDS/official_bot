"""异世界高校 S3「修学旅行篇」专项测试。

覆盖设计文档 anime-academy-s3-design.md §7 的 12 项验收用例：
入口条件（s2 done 前后）/ 接力链路 / 全流程节点走通 / b2 坚守 4 回合 /
56 dice_suppress / 56 删改之手吸收力量（临时恢复）/ 57 终稿之笔吸 hp + 冗长删除吸敏捷 /
57 物理抗性 / 57 半血变身 + 回合数施法（s003 第 3 回合）/ 纪念品 313 幂等 /
战败不置 done / validator 白名单 55/56/57。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import nonebot

nonebot.init()

from src.models.monster import Monster
from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    Investigator,
    InvestigatorGenerator,
    investigator_repo,
)
from src.services.battle import BattleService
from src.services.battle import damage as damage_mod
from src.services.config_validator import ConfigValidator
from src.services.damage_calculator import calculate_damage
from src.services.dice_roller import SuccessLevel
from src.services.flow_engine import flow_states

_S3_ID = "flow.anime_academy.s3"

# S3 节点推进选项（阶段 → 检定 → 战斗 → …）
_S3_PATHS = {
    "s1": "抢过导游小姐的喇叭，宣布自己是本车代理导游",
    "s2": "追着乘务员，走向车尾那节「不存在的车厢」",
    "s3": "偷看大厅柜台的「最终回企划书」",
    "s4": "撕掉最终话脚本，自己写结局",
}


def _make_inv(qq: str, name: str = "Guest") -> Investigator:
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
    from src.plugins.guest import _guest_handle_input

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(_guest_handle_input(qq, choice, bot or ConsoleBot(), send, token))
    return sent


def _enter(qq: str, inv: Investigator, world_id: str, bot=None) -> list[str]:
    from src.plugins.guest import guest_enter

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    try:
        asyncio.run(guest_enter(qq, inv, bot or ConsoleBot(), send, world_id))
    except BaseException:
        pass
    return sent


def _win_action(service):
    def _win(action):
        service.hp_record["mon"] = 0
        return ("你挥出最后一击。",)

    return _win


# --- 1. 入口条件：s2 done 前后 / 已完成不再触发 ---
def test_s3_entry_before_s2_done():
    """s2 未完成：入口条件不满足，S3 不在接力候选。"""
    from src.plugins import guest
    from src.plugins.guest import _world_available, guest_registry

    inv = _make_inv("s3_gate")
    inv.day = 16
    inv.set_flag("guest.run_triggered", 1)
    inv.save()
    s3 = guest_registry()["flow.anime_academy.s3"]
    assert _world_available(inv, s3, "flow.anime_academy.s3") is False
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        assert guest.guest_should_trigger(inv) is None
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup(inv.qq)


def test_s3_entry_after_s2_done():
    """s2 done（规范键 flow.anime_academy.s2.done）：骰 ≤ 15 触发 S3。"""
    from src.plugins import guest
    from src.plugins.guest import _world_available, guest_registry

    inv = _make_inv("s3_gate2")
    inv.day = 16
    inv.set_flag("flow.flow.anime_academy.s2.done", 1)
    inv.set_flag("guest.run_triggered", 1)
    inv.save()
    s3 = guest_registry()["flow.anime_academy.s3"]
    assert _world_available(inv, s3, "flow.anime_academy.s3") is True
    with patch("src.plugins.guest._guest_daily_roll", return_value=15):
        assert guest.guest_should_trigger(inv) == "flow.anime_academy.s3"
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup(inv.qq)


def test_s3_entry_requires_s2_done():
    """A3 修复：S3 严格依赖 s2 完成，仅 s1.done 不再解锁 s3（旧 s1 兜底已移除）。

    - 仅 s1.done → s3 不可用（_world_available False）；s2 由 s1 解锁、接力优先级在前。
    - s1+s2 均 done → s3 可用并接力触发。
    """
    from src.plugins import guest
    from src.plugins.guest import _world_available, guest_registry

    inv = _make_inv("s3_gate3")
    inv.day = 16
    inv.set_flag("flow.flow.anime_academy.s1.done", 1)
    inv.set_flag("guest.run_triggered", 1)
    inv.save()
    s3 = guest_registry()["flow.anime_academy.s3"]
    # 仅 s1.done：s3 不因 s1 通关解锁（严格 s2 语义）
    assert _world_available(inv, s3, "flow.anime_academy.s3") is False
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        assert guest.guest_should_trigger(inv) == "flow.anime_academy.s2"
    # s2 已完成 → s3 接力触发
    inv.set_flag("flow.flow.anime_academy.s2.done", 1)
    inv.save()
    assert _world_available(inv, s3, "flow.anime_academy.s3") is True
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        assert guest.guest_should_trigger(inv) == "flow.anime_academy.s3"
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup(inv.qq)


def test_s3_entry_dice_threshold():
    """触发概率 value=15：骰 > 15 不触发（边界值 16）。"""
    from src.plugins import guest

    inv = _make_inv("s3_gate4")
    inv.day = 16
    inv.set_flag("flow.flow.anime_academy.s2.done", 1)
    inv.set_flag("guest.run_triggered", 1)
    inv.save()
    with patch("src.plugins.guest._guest_daily_roll", return_value=16):
        assert guest.guest_should_trigger(inv) is None
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup(inv.qq)


def test_s3_entry_done_or_visited_excluded():
    """已完成/已进入过 S3 → 不再相遇。"""
    from src.plugins import guest
    from src.plugins.guest import _world_available, guest_registry

    inv = _make_inv("s3_gate5")
    inv.day = 16
    inv.set_flag("flow.flow.anime_academy.s2.done", 1)
    inv.set_flag("guest.run_triggered", 1)
    inv.set_flag("flow.flow.anime_academy.s3.visited", 1)
    inv.save()
    s3 = guest_registry()["flow.anime_academy.s3"]
    assert _world_available(inv, s3, "flow.anime_academy.s3") is False
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        assert guest.guest_should_trigger(inv) is None
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup(inv.qq)


# --- 2. 接力：本局已触发过异界后 S3 仍可触发 ---
def test_s3_relay_after_run_triggered():
    """run_triggered 置位（本局已触发过普通异界）后，接力季 S3 仍可触发。"""
    from src.plugins import guest

    inv = _make_inv("s3_relay")
    inv.day = 16
    inv.set_flag("flow.flow.anime_academy.s2.done", 1)
    inv.set_flag("guest.run_triggered", 1)
    inv.save()
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        assert guest.guest_should_trigger(inv) == "flow.anime_academy.s3"
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup(inv.qq)


# --- 3. 全流程：enter→s1→s2→b1→s3→b2→s4→b3→souvenir→end ---
def test_s3_full_flow_nodes():
    """S3 全链路：三战逐级（55→56→57）+ 纪念品 313 + done + day+1。"""
    from src.plugins import guest

    qq = "s3_full"
    inv = _make_inv(qq)
    inv.day = 15
    inv.set_flag("flow.flow.anime_academy.s2.done", 1)
    inv.save()
    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        _enter(qq, inv, _S3_ID)
    assert flow_states[qq]["node_id"] == "s1"

    _drive(qq, _S3_PATHS["s1"])
    assert flow_states[qq]["node_id"] == "s2"
    _drive(qq, _S3_PATHS["s2"])
    state = flow_states[qq]
    assert state["phase"] == "battle"
    assert state["battle"].monster.id == "55"  # 列车长
    service = state["battle"]
    service.execute_action = _win_action(service)
    _drive(qq, "格斗")
    assert flow_states[qq]["node_id"] == "s3"

    _drive(qq, _S3_PATHS["s3"])
    state = flow_states[qq]
    assert state["phase"] == "battle"
    assert state["battle"].monster.id == "56"  # 审查官
    assert state["battle"]._win_condition == {
        "类型": "坚守",
        "回合": 4,
        "文案": "你撑住了四轮删改。审查官的红笔停在半空，他低头看着被你「写」乱的稿纸，第一次露出类似困惑的表情：「……改不动了。这段，留给你。」",
    }
    service = state["battle"]
    service.execute_action = _win_action(service)
    _drive(qq, "格斗")
    assert flow_states[qq]["node_id"] == "s4"

    _drive(qq, _S3_PATHS["s4"])
    state = flow_states[qq]
    assert state["phase"] == "battle"
    assert state["battle"].monster.id == "57"  # 总编辑
    service = state["battle"]
    service.execute_action = _win_action(service)
    _drive(qq, "格斗")

    assert qq not in flow_states  # 已收尾清理
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.get_flag("flow.flow.anime_academy.s3.done") == 1
    assert reloaded.day == 16  # skip_daily day+1
    assert reloaded.is_adventure is False
    equipments, _ = reloaded.get_equipments()
    assert equipments.get("313", 0) == 1  # 纪念品终稿车票
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


# --- 4. b2 坚守：满 4 回合条件胜利 / 未满回合正常战斗 ---
def test_s3_b2_hold_win_at_4_turns():
    """坚守满 4 回合且玩家存活 → 条件胜利（怪物停手）。"""
    qq = "s3_hold"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("56"))
    bs.is_gm_room = True
    bs.set_battle_cfg({
        "怪物": "56",
        "胜利条件": {
            "类型": "坚守",
            "回合": 4,
            "文案": "你撑住了四轮删改。",
        },
    })
    bs._turn_counter = 4
    bs.hp_record["inv"] = 10
    inv_before = bs.hp_record["inv"]
    out = bs._execute_monster_action("反击")
    joined = "\n".join(str(x) for x in out)
    assert "你撑住了四轮删改。" in joined, "专属坚守文案应展示"
    assert bs.hp_record["inv"] == inv_before, "条件胜利后怪物停手不再攻击"
    assert bs._conditional_won is True
    assert bs.fight_is_over() is True
    _cleanup(qq)


def test_s3_b2_hold_no_early_end():
    """未满 4 回合 → 正常战斗不提前结束。"""
    qq = "s3_hold2"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("56"))
    bs.is_gm_room = True
    bs.set_battle_cfg({"怪物": "56", "胜利条件": {"类型": "坚守", "回合": 4}})
    bs._turn_counter = 2
    bs.hp_record["inv"] = 10
    assert bs._check_combat_over() is None
    assert bs._conditional_won is False
    assert bs.fight_is_over() is False
    _cleanup(qq)


# --- 5. 56 dice_suppress：玩家武器骰强制最小 / 法术道具不受压制 ---
def test_s3_56_dice_suppress_weapon_min():
    """56 dice_suppress：武器骰强制最小（4d6→4），固定加成不受影响。"""
    qq = "s3_ds"
    inv = _make_inv(qq)
    mon = Monster("56")
    assert mon.dice_suppress is True
    bs = BattleService(inv, mon)
    from src.models.item import Equipment

    weapon = Equipment("1")
    orig = damage_mod.roll_dice
    damage_mod.roll_dice = lambda *a, **k: ("50", 50)
    try:
        _expr, val = calculate_damage("4d6", is_extreme_double=True, min_roll=True)
        assert val == 4, "4d6 压制后应为 4"
        _expr2, val2 = calculate_damage("3d6+1", is_extreme_double=True, min_roll=True)
        assert val2 == 4, "3d6+1 压制后应为 3+1=4"
        _expr3, val3 = calculate_damage("1d4+2", is_extreme_double=True, min_roll=True)
        assert val3 == 3, "固定 +2 不受压制：1+2=3"
        result = bs._handle_player_melee_success(
            SimpleNamespace(level1=SuccessLevel.EXTREME_SUCCESS, level2=SuccessLevel.FAILURE),
            weapon,
            mon.get_action("mon"),
            "",
        )
        assert "最弱的一击" in str(result), "压制提示文案 battle.dice_suppressed 应展示"
    finally:
        damage_mod.roll_dice = orig
    _cleanup(qq)


def test_s3_56_dice_suppress_magic_not_affected():
    """56 dice_suppress 只压武器骰：法术/道具（魔法标签）全伤。"""
    qq = "s3_ds2"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("56"))
    bs.hp_record["mon"] = 10
    bs.monster.hp = 10
    text = bs._apply_damage_to_monster(6, dmg_type="magic")
    assert bs.hp_record["mon"] == 4, "魔法标签不受 dice_suppress 影响"
    assert "毫无效果" not in text
    _cleanup(qq)


# --- 6. 56 删改之手吸收力量（战斗内临时扣减、战斗结束恢复） ---
def _get_absorb_action(mid: str, action_name: str) -> dict:
    """从生产 monster_data 取攻击动作（含 吸收 配置，验证真实数据）。"""
    from src.services.data_loader import data_loader

    return data_loader.monster_data[mid]["攻击"][action_name]


def test_s3_56_absorb_str_temp_and_restore():
    """删改之手吸力量 1d3：战斗内扣减生效、战斗结束恢复原值。"""
    qq = "s3_abs"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("56"))
    str_before = inv.get_skill("力量")
    action = _get_absorb_action("56", "删改之手")
    assert action["吸收"]["目标"] == "力量", "56 吸收目标应为力量"
    assert action["吸收"]["持久"] is False
    orig = damage_mod.roll_dice
    damage_mod.roll_dice = lambda *a, **k: ("3", 3)
    try:
        monster_text, _ = bs._handle_monster_attack_success(
            action,
            SimpleNamespace(level1=SuccessLevel.SUCCESS, level2=SuccessLevel.FAILURE),
        )
    finally:
        damage_mod.roll_dice = orig
    assert inv.get_skill("力量") == str_before - 3, "战斗内力量临时扣减生效"
    assert "力量" in monster_text, "自定义吸收文案应包含属性名"
    assert "{属性}" not in monster_text, "自定义吸收文案不得残留占位符"
    bs._restore_absorb_snapshot()
    assert inv.get_skill("力量") == str_before, "战斗结束恢复原值"
    _cleanup(qq)


# --- 7. 57 终稿之笔吸 hp / 冗长删除吸敏捷（临时恢复） ---
def test_s3_57_absorb_hp():
    """终稿之笔吸 hp 1d6：玩家扣血 = 怪物回血（封顶 max_hp）。"""
    qq = "s3_ahp"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("57"))
    bs.hp_record["inv"] = 20
    bs.hp_record["mon"] = 10
    bs.monster.hp = 10
    action = _get_absorb_action("57", "终稿之笔")
    assert action["吸收"]["类型"] == "hp", "57 终稿之笔应吸 hp"
    orig = damage_mod.roll_dice
    damage_mod.roll_dice = lambda *a, **k: ("5", 5)
    try:
        monster_text, _ = bs._handle_monster_attack_success(
            action,
            SimpleNamespace(level1=SuccessLevel.SUCCESS, level2=SuccessLevel.FAILURE),
        )
    finally:
        damage_mod.roll_dice = orig
    assert bs.hp_record["inv"] == 15, "玩家扣血 = 骰值 5"
    assert bs.hp_record["mon"] == 15, "怪物回血 = 骰值 5"
    assert "生命力" in monster_text
    _cleanup(qq)


def test_s3_57_absorb_dex_temp_and_restore():
    """冗长删除吸敏捷 1d3：战斗内临时扣减、战斗结束恢复。"""
    qq = "s3_adex"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("57"))
    dex_before = inv.get_skill("敏捷")
    action = _get_absorb_action("57", "冗长删除")
    assert action["吸收"]["目标"] == "敏捷", "57 冗长删除应吸敏捷"
    assert action["吸收"]["持久"] is False
    orig = damage_mod.roll_dice
    damage_mod.roll_dice = lambda *a, **k: ("3", 3)
    try:
        monster_text, _ = bs._handle_monster_attack_success(
            action,
            SimpleNamespace(level1=SuccessLevel.SUCCESS, level2=SuccessLevel.FAILURE),
        )
    finally:
        damage_mod.roll_dice = orig
    assert inv.get_skill("敏捷") == dex_before - 3, "战斗内敏捷临时扣减生效"
    assert "敏捷" in monster_text, "自定义吸收文案应包含属性名"
    assert "{属性}" not in monster_text, "自定义吸收文案不得残留占位符"
    bs._restore_absorb_snapshot()
    assert inv.get_skill("敏捷") == dex_before, "战斗结束恢复原值"
    _cleanup(qq)


# --- 8. 57 物理抗性 0.5：武器减半、法术全伤 ---
def test_s3_57_physical_resist():
    """57 物理抗性 0.5：物理伤害减半向下取整、法术（魔法标签）全伤。"""
    qq = "s3_res"
    inv = _make_inv(qq)
    mon = Monster("57")
    assert mon.抗性 == {"物理": 0.5}
    bs = BattleService(inv, mon)
    bs.hp_record["mon"] = 10
    bs.monster.hp = 10
    bs._apply_damage_to_monster(10, dmg_type="physical")
    assert bs.hp_record["mon"] == 5, "物理 10 ×0.5 = 5"
    bs.hp_record["mon"] = 10
    bs.monster.hp = 10
    bs._apply_damage_to_monster(10, dmg_type="magic")
    assert bs.hp_record["mon"] == 0, "法术 10 全伤（不抗魔法）"
    _cleanup(qq)


# --- 9. 57 半血变身（终章之刃 + s002 + 闪避 60）+ 回合数施法（第 3 怪物回合 s003） ---
def test_s3_57_transform_at_half_hp():
    """57 半血触发受伤后：变身文本 + 终章之刃 + s002 施法 + 闪避 60。"""
    qq = "s3_tf"
    inv = _make_inv(qq)
    mon = Monster("57")
    assert mon._ai_dodge == 60, "闪避 60"
    mon.hp = 5  # < 22×0.5=11
    mon.advance_ai()
    assert mon._ai_transformed is True
    assert mon._ai_phase == "受伤后"
    assert mon._ai_transform_text is not None, "变身文本应设置"
    assert mon._ai_pending_spell == "s002", "变身当回合预取 s002"
    spell_action = mon.get_ai_action("mon")
    assert spell_action["type"] == "spell"
    assert spell_action["spell"] == "s002"
    mon.complete_spell_turn()
    melee_action = mon.get_ai_action("mon")
    assert melee_action["动作"] == "终章之刃"
    assert melee_action["skill"] == 78
    assert melee_action["damage"] == "2d6+1"
    _cleanup(qq)


def test_s3_57_turn_count_spell_s003():
    """57 多法术 s003 回合数触发：第 3 个怪物回合施法一次（且仅一次）。"""
    qq = "s3_ts"
    inv = _make_inv(qq)
    mon = Monster("57")
    assert mon._ai_pending_spell is None, "初始无待施法术"
    mon.advance_ai()  # 回合 1
    assert mon._ai_pending_spell is None, "回合1非s003"
    mon.advance_ai()  # 回合 2
    assert mon._ai_pending_spell is None, "回合2非s003"
    mon.advance_ai()  # 回合 3 → s003
    assert mon._ai_pending_spell == "s003", "第 3 个怪物回合应预取 s003"
    assert mon.get_ai_action("mon")["type"] == "spell"
    assert mon.get_ai_action("mon")["spell"] == "s003"
    mon.complete_spell_turn()
    mon.advance_ai()  # 回合 4 → 不再重复
    assert mon._ai_pending_spell is None, "仅第 3 回合一次"
    _cleanup(qq)


# --- 10. 纪念品 313 首发放幂等 / end_lose 不发放不置 done ---
def test_s3_souvenir_313_idempotent():
    """已持 313 重通关：不重发、不重复文案（幂等）。"""
    from src.plugins import guest

    qq = "s3_sou"
    inv = _make_inv(qq)
    inv.day = 15
    inv.set_flag("flow.flow.anime_academy.s2.done", 1)
    inv.add_item_to_inventory("313", 1)
    inv.save()
    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        _enter(qq, inv, _S3_ID)
    _drive(qq, _S3_PATHS["s1"])
    _drive(qq, _S3_PATHS["s2"])
    service = flow_states[qq]["battle"]
    service.execute_action = _win_action(service)
    _drive(qq, "格斗")
    _drive(qq, _S3_PATHS["s3"])
    service = flow_states[qq]["battle"]
    service.execute_action = _win_action(service)
    _drive(qq, "格斗")
    _drive(qq, _S3_PATHS["s4"])
    service = flow_states[qq]["battle"]
    service.execute_action = _win_action(service)
    out = _drive(qq, "格斗")
    text = "\n".join(out)
    assert "异界纪念品" not in text, "已持有 313：不追加发放行"
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    equipments, _ = reloaded.get_equipments()
    assert equipments.get("313", 0) == 1, "数量不变"
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


def test_s3_defeat_no_souvenir_no_done():
    """S3 战败（b1）：空手归途、不设 done、visited 排除后续相遇。"""
    from src.plugins import guest

    qq = "s3_lose"
    inv = _make_inv(qq)
    inv.day = 15
    inv.set_flag("flow.flow.anime_academy.s2.done", 1)
    inv.save()
    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        _enter(qq, inv, _S3_ID)
    _drive(qq, _S3_PATHS["s1"])
    _drive(qq, _S3_PATHS["s2"])
    service = flow_states[qq]["battle"]

    def _lose(action):
        service.hp_record["inv"] = 0
        service.fled = True
        return ("你被击倒了。",)

    service.execute_action = _lose
    out = _drive(qq, "格斗")
    text = "\n".join(out)
    assert "不再停靠" in text, "b1 战败回复"
    assert "异界纪念品" not in text, "空手，无纪念品"
    assert qq not in flow_states
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert not reloaded.get_flag("flow.flow.anime_academy.s3.done"), "战败不设 done"
    assert reloaded.get_flag("flow.flow.anime_academy.s3.visited") == 1, "战败后 visited 排除再相遇"
    equipments, _ = reloaded.get_equipments()
    assert equipments.get("313", 0) == 0
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


# --- 11. 战败后不再相遇 / skip_daily 生效 ---
def test_s3_defeat_visited_excludes_retrigger():
    """战败后 visited/done 排除：S3 不再进入触发候选。"""
    from src.plugins import guest
    from src.plugins.guest import _world_available, guest_registry

    inv = _make_inv("s3_retrig")
    inv.day = 16
    inv.set_flag("flow.flow.anime_academy.s2.done", 1)
    inv.set_flag("flow.flow.anime_academy.s3.visited", 1)
    inv.set_flag("guest.run_triggered", 1)
    inv.save()
    s3 = guest_registry()["flow.anime_academy.s3"]
    assert _world_available(inv, s3, "flow.anime_academy.s3") is False
    with patch("src.plugins.guest._guest_daily_roll", return_value=1):
        assert guest.guest_should_trigger(inv) is None
    guest._guest_met_day.pop(inv.qq, None)
    _cleanup(inv.qq)


def test_s3_skip_daily():
    """收尾 skip_daily：占当日冒险 + day+1。"""
    from src.plugins import guest

    qq = "s3_skip"
    inv = _make_inv(qq)
    inv.day = 20
    inv.is_adventure = True
    inv.set_flag("flow.flow.anime_academy.s2.done", 1)
    inv.save()
    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        _enter(qq, inv, _S3_ID)
    _drive(qq, _S3_PATHS["s1"])
    _drive(qq, _S3_PATHS["s2"])
    service = flow_states[qq]["battle"]
    service.execute_action = _win_action(service)
    _drive(qq, "格斗")
    _drive(qq, _S3_PATHS["s3"])
    service = flow_states[qq]["battle"]
    service.execute_action = _win_action(service)
    _drive(qq, "格斗")
    _drive(qq, _S3_PATHS["s4"])
    service = flow_states[qq]["battle"]
    service.execute_action = _win_action(service)
    _drive(qq, "格斗")
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.day == 21, "skip_daily day+1"
    assert reloaded.is_adventure is False
    guest._guest_met_day.pop(qq, None)
    _cleanup(qq)


# --- 12. validator：55/56/57 白名单 + 世界结构校验通过 ---
def test_s3_validator_whitelist_and_world():
    """55/56/57 白名单 + 世界结构校验 + 全量数据校验通过。"""
    from src.services.config_validator import _SPECIAL_MONSTER_IDS

    assert {"55", "56", "57"} <= _SPECIAL_MONSTER_IDS, "55/56/57 应在特殊怪物白名单"
    errors = ConfigValidator().validate_all()
    assert errors == [], "\n".join(errors)

    from src.services.flow_engine import flow_registry

    s3 = flow_registry()["flow.anime_academy.s3"]
    assert s3["纪念品"] == "313"
    assert s3.get("接力") is True
    assert s3["触发概率"] == {"dice": "d100", "value": 15, "relation": "lte"}
    nodes = s3["节点"]
    assert set(nodes) == {
        "enter", "s1", "s2", "b1", "s3", "b2", "s4", "b3", "souvenir", "end", "end_lose",
    }
    assert len(s3["玩家文案"]) == 17
    # 纪念品幂等：souvenir 节点物品 313
    assert nodes["souvenir"]["物品"] == "313"
