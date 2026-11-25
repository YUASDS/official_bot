"""NPC / 伙伴系统 测试。

覆盖：flag 基建（investigators.flags）、NPC 注册与解锁、好感度推进与结伴、
伙伴助战（engine.py companion 泛化）、adventure 挂载（触发对话/正常流程/助战注入）、
/伙伴 状态命令。只测新增行为，不修改 src/。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import ujson

import nonebot

nonebot.init()

from src.models.monster import Monster
from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    Investigator,
    InvestigatorGenerator,
    ending_repo,
    investigator_repo,
)
from src.services.battle import BattleService
from src.services.ending_engine import relic_ids


def _make_inv(qq: str, name: str = "Npc") -> Investigator:
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


# --- C1：flag 基建 ---
def test_flags_column_exists():
    """schema 回归：investigators 存在 flags 列（_migrate 容错），缺省 {}。"""
    from src.models.player import InvestigatorModel

    assert "flags" in InvestigatorModel._meta.fields
    inv = _make_inv("npc_flags_schema")
    assert inv.get_all_flags() == {}
    assert inv.get_flag("anything") is None
    _cleanup("npc_flags_schema")


def test_flag_roundtrip_persists():
    qq = "npc_flags_rt"
    inv = _make_inv(qq)
    inv.set_flag("npc.guardian.好感度", 3)
    inv.set_flag("mark", True)
    inv.save()
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.get_flag("npc.guardian.好感度") == 3
    assert reloaded.get_flag("mark") is True
    assert reloaded.get_all_flags()["npc.guardian.好感度"] == 3
    _cleanup(qq)


def test_flags_cleared_on_rebuild():
    """单局语义：角色重建后 flags 清空。"""
    qq = "npc_flags_rebuild"
    inv = _make_inv(qq)
    inv.set_flag("npc.merchant.好感度", 2)
    inv.save()
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    investigator_repo.create_and_save(qq, "Npc2", data)
    rebuilt = Investigator(investigator_repo.find_by_qq(qq))
    assert rebuilt.get_all_flags() == {}
    _cleanup(qq)


# --- C2：NPC 注册 / 解锁 / 概率触发 ---
def test_npc_registry_loaded():
    from src.plugins.npc import npc_registry

    reg = npc_registry()
    assert set(reg.keys()) >= {"guardian", "merchant"}
    assert reg["guardian"]["好感度上限"] == 5
    assert reg["merchant"]["好感度上限"] == 4


def test_npc_data_no_relic_rewards():
    """红线：NPC 效果物品不得含 400~508 信物（防 E09 稀缺性污染）。"""
    from src.plugins.npc import npc_registry

    relics = set(relic_ids())
    for npc_id, npc in npc_registry().items():
        for node in npc.get("节点") or []:
            for opt in node.get("选项") or []:
                item = (opt.get("效果") or {}).get("物品")
                if item:
                    assert str(item) not in relics, (
                        f"{npc_id} 选项掉落信物 {item}"
                    )


def test_npc_available_day_gate():
    from src.plugins.npc import npc_available, npc_registry

    guardian = npc_registry()["guardian"]
    merchant = npc_registry()["merchant"]
    inv = _make_inv("npc_gate")
    inv.day = 4
    assert npc_available(inv, merchant) is False  # day < 5
    assert npc_available(inv, guardian) is False  # day < 8
    inv.day = 8
    assert npc_available(inv, merchant) is True
    assert npc_available(inv, guardian) is True
    inv.day = 40
    assert npc_available(inv, guardian) is False  # day40 门扉冻结不触发
    _cleanup("npc_gate")


def test_npc_should_trigger_probability():
    from unittest.mock import patch

    from src.plugins import npc

    inv = _make_inv("npc_prob")
    inv.day = 15
    with patch("src.plugins.npc.roll_dice", return_value=("20", 1)):
        assert npc.npc_should_trigger(inv) == "guardian"
    with patch("src.plugins.npc.roll_dice", return_value=("20", 9)):
        assert npc.npc_should_trigger(inv) is None
    npc._npc_met_day.pop(inv.qq, None)
    _cleanup("npc_prob")


def test_npc_should_trigger_once_per_day():
    """当日已发送过对话（_npc_met_day 守卫）→ 不再触发；次日恢复。"""
    from unittest.mock import patch

    from src.plugins import npc

    inv = _make_inv("npc_once")
    inv.day = 15
    npc._npc_met_day[inv.qq] = 15  # 模拟当日对话已发送
    with patch("src.plugins.npc.roll_dice", return_value=("20", 1)):
        assert npc.npc_should_trigger(inv) is None
    inv.day = 16  # 次日守卫失效
    with patch("src.plugins.npc.roll_dice", return_value=("20", 1)):
        assert npc.npc_should_trigger(inv) == "guardian"
    npc._npc_met_day.pop(inv.qq, None)
    _cleanup("npc_once")


def test_npc_should_trigger_not_on_day40():
    from unittest.mock import patch

    from src.plugins.npc import npc_should_trigger

    inv = _make_inv("npc_day40")
    inv.day = 40
    with patch("src.plugins.npc.roll_dice", return_value=("20", 1)):
        assert npc_should_trigger(inv) is None  # 解锁条件 max:39 已排除
    _cleanup("npc_day40")


# --- C3：好感度推进 / 节点选择 / 结伴 ---
def test_npc_affinity_default_zero():
    from src.plugins.npc import npc_affinity

    inv = _make_inv("npc_aff0")
    assert npc_affinity(inv, "guardian") == 0
    assert npc_affinity(inv, "merchant") == 0
    _cleanup("npc_aff0")


def test_npc_current_node_by_affinity():
    from src.plugins.npc import npc_current_node, npc_registry

    guardian = npc_registry()["guardian"]
    assert npc_current_node(guardian, 0)["id"] == "g1"
    assert npc_current_node(guardian, 3)["id"] == "g2"
    assert npc_current_node(guardian, 5)["id"] == "g3"


def test_apply_npc_choice_affinity_and_effects():
    from src.plugins.npc import apply_npc_choice, npc_affinity, npc_current_node, npc_registry

    qq = "npc_choice1"
    inv = _make_inv(qq)
    guardian = npc_registry()["guardian"]
    node = npc_current_node(guardian, 0)  # g1
    option = next(o for o in node["选项"] if o["输入"] == "询问守门的职责")
    reply = apply_npc_choice(inv, qq, guardian, node, option)
    assert "门后不是路" in reply
    assert npc_affinity(inv, "guardian") == 1
    assert "好感度" in reply
    _cleanup(qq)


def test_apply_npc_choice_san_and_mythos_effects():
    from src.plugins.npc import apply_npc_choice, npc_current_node, npc_registry

    qq = "npc_choice2"
    inv = _make_inv(qq)
    san_before = inv.get_skill("san", 0)
    mythos_before = inv.get_skill("克苏鲁神话", 0)
    guardian = npc_registry()["guardian"]
    node = npc_current_node(guardian, 3)  # g2
    option = next(o for o in node["选项"] if o["输入"] == "倾听他的低语")
    apply_npc_choice(inv, qq, guardian, node, option)
    assert inv.get_skill("san", 0) == san_before - 3
    assert inv.get_skill("克苏鲁神话", 0) == mythos_before + 2
    _cleanup(qq)


def test_apply_npc_choice_caps_at_max():
    from src.plugins.npc import apply_npc_choice, npc_affinity, npc_current_node, npc_registry

    qq = "npc_cap"
    inv = _make_inv(qq)
    inv.set_flag("npc.guardian.好感度", 5)
    inv.save()
    guardian = npc_registry()["guardian"]
    node = npc_current_node(guardian, 5)  # g3
    option = next(o for o in node["选项"] if o["输入"] == "婉拒他的好意")
    apply_npc_choice(inv, qq, guardian, node, option)
    assert npc_affinity(inv, "guardian") == 5  # 不超上限
    _cleanup(qq)


def test_ally_unlock_at_max_affinity():
    """好感度递增到上限且 NPC 配置 助战 → 自动结为伙伴（置 解锁 flag）。"""
    from src.plugins.npc import (
        apply_npc_choice,
        npc_affinity,
        npc_current_node,
        npc_registry,
    )

    qq = "npc_ally"
    inv = _make_inv(qq)
    inv.set_flag("npc.guardian.好感度", 4)
    inv.save()
    assert inv.get_flag("npc.guardian.ally") is None
    guardian = npc_registry()["guardian"]
    node = npc_current_node(guardian, 4)  # g2（4 < 5）
    option = next(o for o in node["选项"] if o["输入"] == "倾听他的低语")  # 好感 +1
    reply = apply_npc_choice(inv, qq, guardian, node, option)
    assert npc_affinity(inv, "guardian") == 5
    assert inv.get_flag("npc.guardian.ally") is True
    assert "成为你的伙伴" in reply
    _cleanup(qq)


def test_ally_unlock_via_flag_effect():
    """显式 flag 效果键直接置位（g3 告别节点）。"""
    from src.plugins.npc import apply_npc_choice, npc_current_node, npc_registry

    qq = "npc_flag_effect"
    inv = _make_inv(qq)
    inv.set_flag("npc.merchant.好感度", 4)
    inv.save()
    merchant = npc_registry()["merchant"]
    node = npc_current_node(merchant, 4)  # m3
    option = next(o for o in node["选项"] if o["输入"] == "收下提灯")
    apply_npc_choice(inv, qq, merchant, node, option)
    assert inv.get_flag("npc.merchant.ally") is True
    _cleanup(qq)


# --- C4：伙伴助战（engine.py companion 泛化）---
def _comp(extra: str = "", turns: int = 3) -> dict:
    return {
        "每回合": "1d3",
        "回合数": turns,
        "名字": "残念的石卫",
        "文案": f"残念的石卫举起长戈，替你挡下一击（{{damage}} 点伤害，剩余 {{remaining}} 回合）。{extra}",
    }


def test_companion_assist_deals_damage():
    from unittest.mock import patch

    from src.services.battle.engine import roll_dice

    qq = "npc_comp_dmg"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("1"))
    bs.companion = _comp()
    bs.hp_record["mon"] = 9999
    bs.monster.armor = 0
    bs.current_turn = "mon"
    # patch 玩家反击伤害（actions._handle_player_counter_success），只测 companion 助战
    with patch("src.services.battle.engine.roll_dice", return_value=("3", 3)), patch(
        "src.services.battle.actions.BattleActionsMixin._handle_player_counter_success",
        return_value=("", ""),
    ):
        result = bs._execute_monster_action("反击")
    out = "\n".join(str(x) for x in result)
    assert "残念的石卫" in out
    assert bs.hp_record["mon"] == 9999 - 3
    assert bs.companion["剩余"] == 2
    _cleanup(qq)


def test_companion_armor_reduction():
    from unittest.mock import patch

    qq = "npc_comp_armor"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("1"))
    bs.companion = _comp()
    bs.hp_record["mon"] = 9999
    bs.monster.armor = 1
    bs.current_turn = "mon"
    # patch 玩家反击伤害，只测 companion 助战
    with patch("src.services.battle.engine.roll_dice", return_value=("3", 3)), patch(
        "src.services.battle.actions.BattleActionsMixin._handle_player_counter_success",
        return_value=("", ""),
    ):
        bs._execute_monster_action("反击")
    assert bs.hp_record["mon"] == 9999 - 2  # 3 - 装甲 1
    _cleanup(qq)


def test_companion_ends_after_turns():
    from unittest.mock import patch

    qq = "npc_comp_end"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("1"))
    bs.companion = _comp(turns=1)
    bs.hp_record["mon"] = 9999
    bs.monster.armor = 0
    bs.current_turn = "mon"
    # patch 玩家反击伤害，只测 companion 助战
    with patch("src.services.battle.engine.roll_dice", return_value=("3", 3)), patch(
        "src.services.battle.actions.BattleActionsMixin._handle_player_counter_success",
        return_value=("", ""),
    ):
        bs._execute_monster_action("反击")
    assert bs.companion is None  # 1 回合耗尽后清空
    _cleanup(qq)


def test_companion_takes_priority_over_bone_whistle():
    from unittest.mock import patch

    qq = "npc_comp_prio"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("1"))
    bs.companion = _comp()
    bs.bone_whistle = 3
    bs.hp_record["mon"] = 9999
    bs.monster.armor = 0
    bs.current_turn = "mon"
    with patch("src.services.battle.engine.roll_dice", return_value=("3", 3)), patch(
        "src.services.battle.actions.BattleActionsMixin._handle_player_counter_success",
        return_value=("", ""),
    ):
        result = bs._execute_monster_action("反击")
    out = "\n".join(str(x) for x in result)
    assert "残念的石卫" in out and "骨哨" not in out
    assert bs.bone_whistle == 3  # 骨哨未消耗
    _cleanup(qq)


def test_bone_whistle_regression():
    """506 骨哨逻辑回归：无伙伴时行为不变（2d4/回合 ×3）。"""
    from unittest.mock import patch

    qq = "npc_bone_whistle"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("1"))
    bs.bone_whistle = 3
    bs.hp_record["mon"] = 9999
    bs.monster.armor = 0
    bs.current_turn = "mon"
    with patch("src.services.battle.engine.roll_dice", return_value=("4", 4)), patch(
        "src.services.battle.actions.BattleActionsMixin._handle_player_counter_success",
        return_value=("", ""),
    ):
        result = bs._execute_monster_action("反击")
    out = "\n".join(str(x) for x in result)
    assert "骨哨猎犬" in out
    assert bs.hp_record["mon"] == 9999 - 4
    assert bs.bone_whistle == 2
    _cleanup(qq)


def test_no_companion_no_assist_text():
    """无伙伴且无骨哨：助战分支零输出。"""
    qq = "npc_none"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("1"))
    bs.hp_record["mon"] = 9999
    bs.current_turn = "mon"
    result = bs._execute_monster_action("反击")
    out = "\n".join(str(x) for x in result)
    assert "残念的石卫" not in out and "骨哨" not in out
    _cleanup(qq)


def test_inject_companion_picks_first_unlocked():
    from src.plugins.npc import inject_companion

    qq = "npc_inject"
    inv = _make_inv(qq)
    inv.set_flag("npc.guardian.ally", True)
    inv.set_flag("npc.merchant.ally", True)
    inv.save()
    bs = BattleService(inv, Monster("1"))
    inject_companion(bs, inv)
    assert bs.companion is not None
    assert bs.companion["名字"] == "残念的石卫"  # 注册表顺序第一个
    # 副本语义：战斗内修改不影响注册表
    bs.companion["剩余"] = 99
    from src.plugins.npc import npc_registry

    assert "剩余" not in npc_registry()["guardian"]["助战"]
    _cleanup(qq)


def test_inject_companion_none_without_flag():
    from src.plugins.npc import inject_companion

    qq = "npc_inject_none"
    inv = _make_inv(qq)
    bs = BattleService(inv, Monster("1"))
    inject_companion(bs, inv)
    assert bs.companion is None
    _cleanup(qq)


# --- C5：adventure 挂载 ---
class FakeBot:
    type = "Console"

    async def send_msg(self, user_id=None, message=None):
        pass


def test_adventure_npc_trigger_sends_dialogue():
    """NPC 概率命中 → 发送对话，不创建战斗；按钮处理器已注册。"""
    import asyncio
    from unittest.mock import patch

    from src.plugins.adventure import _run_adventure
    from src.plugins.npc import npc_states
    from src.utils.active_battles import battle_manager
    from src.utils.buttons import BUTTON_HANDLERS

    qq = "npc_adv_trigger"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()

    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    async def finish(msg):
        sent.append("FIN:" + str(msg))

    with patch("src.plugins.adventure.npc_should_trigger", return_value="guardian"), patch(
        "src.plugins.adventure.hidden_should_trigger", return_value=None
    ), patch(
        "src.plugins.adventure.guest_should_trigger", return_value=None
    ), patch(
        "src.plugins.adventure.gm_room_should_trigger", return_value=False
    ):
        asyncio.run(_run_adventure(qq, FakeBot(), send, finish))

    text = "\n".join(sent)
    assert "石台边的人影" in text  # NPC 标题
    assert "守门人的残影" in text  # 出场文案
    assert battle_manager.get_battle(qq) is None  # 触发对话不直接开战
    assert qq in npc_states  # 未决状态已挂起
    assert "npc" in BUTTON_HANDLERS
    npc_states.pop(qq, None)
    battle_manager.remove_battle(qq)
    _cleanup(qq)


def test_adventure_npc_not_triggered_normal_battle():
    """NPC 未触发 → 正常开战；残留 npc_states 被清理。"""
    import asyncio
    from unittest.mock import patch

    from src.plugins.adventure import _run_adventure
    from src.plugins.npc import npc_states
    from src.utils.active_battles import battle_manager

    qq = "npc_adv_normal"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()
    npc_states[qq] = {"npc_id": "guardian", "node_id": "g1"}

    sent: list[str] = []

    async def send(msg):
        pass

    async def finish(msg):
        raise SystemExit

    with patch("src.plugins.adventure.npc_should_trigger", return_value=None), patch(
        "src.plugins.adventure.pick_random_event", return_value=None
    ), patch(
        "src.plugins.adventure.run_sanity_and_madness",
        return_value=("", "", False, 0, False, 0),
    ), patch(
        "src.plugins.adventure.hidden_should_trigger", return_value=None
    ), patch(
        "src.plugins.adventure.guest_should_trigger", return_value=None
    ), patch(
        "src.plugins.adventure.gm_room_should_trigger", return_value=False
    ):
        try:
            asyncio.run(_run_adventure(qq, FakeBot(), send, finish))
        except SystemExit:
            pass

    battle = battle_manager.get_battle(qq)
    assert battle is not None, "未触发 NPC 时应正常开战"
    assert qq not in npc_states  # 残留状态已清理
    battle_manager.remove_battle(qq)
    _cleanup(qq)


def test_adventure_companion_injected_in_battle():
    """伙伴解锁后 /今日冒险 构造的 BattleService 注入 companion。"""
    import asyncio
    from unittest.mock import patch

    from src.plugins.adventure import _run_adventure
    from src.utils.active_battles import battle_manager

    qq = "npc_adv_comp"
    inv = _make_inv(qq)
    inv.day = 15
    inv.set_flag("npc.guardian.ally", True)
    inv.save()

    async def send(msg):
        pass

    async def finish(msg):
        raise SystemExit

    with patch("src.plugins.adventure.npc_should_trigger", return_value=None), patch(
        "src.plugins.adventure.pick_random_event", return_value=None
    ), patch(
        "src.plugins.adventure.run_sanity_and_madness",
        return_value=("", "", False, 0, False, 0),
    ), patch(
        "src.plugins.adventure.hidden_should_trigger", return_value=None
    ), patch(
        "src.plugins.adventure.guest_should_trigger", return_value=None
    ), patch(
        "src.plugins.adventure.gm_room_should_trigger", return_value=False
    ):
        try:
            asyncio.run(_run_adventure(qq, FakeBot(), send, finish))
        except SystemExit:
            pass

    battle = battle_manager.get_battle(qq)
    assert battle is not None
    assert battle.companion is not None
    assert battle.companion["名字"] == "残念的石卫"
    battle_manager.remove_battle(qq)
    _cleanup(qq)


# --- C6：/伙伴 状态命令 ---
def test_npc_status_text():
    from src.plugins.npc import npc_status

    qq = "npc_status"
    inv = _make_inv(qq)
    text = npc_status(inv)
    assert "伙伴与好感度" in text
    assert "尚未相遇" in text  # 初始无好感
    inv.set_flag("npc.merchant.好感度", 2)
    inv.set_flag("npc.guardian.ally", True)
    inv.save()
    text2 = npc_status(inv)
    assert "已结为伙伴" in text2
    assert "好感度 2/4" in text2
    _cleanup(qq)


def test_npc_command_intercept_applies_choice():
    """npc_states 未决时 `/行动 <选项输入>` 完成对话（命令兜底，对齐事件/门扉通道）。"""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch

    import src.plugins.adventure as adv_mod
    from src.plugins.npc import npc_states

    qq = "npc_cmd"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()
    npc_states[qq] = {"npc_id": "guardian", "node_id": "g1"}

    class FakeEvent:
        def get_user_id(self):
            return qq

    class FakeMsg:
        def __init__(self, text):
            self._text = text

        def extract_plain_text(self):
            return self._text

    sent: list[str] = []

    async def cmd_send(msg):
        sent.append(str(msg))

    async def cmd_finish(msg):
        sent.append("FIN:" + str(msg))

    mock_cmd = SimpleNamespace(send=cmd_send, finish=cmd_finish)
    with patch.object(adv_mod, "combat_cmd", mock_cmd):
        asyncio.run(
            adv_mod.handle_combat(FakeEvent(), FakeBot(), FakeMsg("/行动 询问守门的职责"))
        )

    text = "\n".join(sent)
    assert "门后不是路" in text  # 选项回复
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.get_flag("npc.guardian.好感度") == 1
    assert qq not in npc_states
    _cleanup(qq)


def test_npc_button_handler_flow():
    """按钮回调：payload = npc_id|选项 → 应用好感度/效果；重复点击被守卫忽略。"""
    import asyncio

    from src.plugins.npc import handle_npc_button, npc_states

    qq = "npc_btn"
    inv = _make_inv(qq)
    inv.day = 15
    inv.save()
    npc_states[qq] = {"npc_id": "guardian", "node_id": "g1"}

    class ConsoleBot:
        type = "Console"

        async def send_msg(self, user_id=None, message=None):
            pass

    async def inner():
        await handle_npc_button(qq, "guardian|询问守门的职责", ConsoleBot(), "")

    asyncio.run(inner())
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.get_flag("npc.guardian.好感度") == 1
    assert qq not in npc_states  # 状态已消费

    # 重复点击旧按钮：守卫拦截，好感度不再叠加
    asyncio.run(inner())
    reloaded2 = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded2.get_flag("npc.guardian.好感度") == 1
    _cleanup(qq)
