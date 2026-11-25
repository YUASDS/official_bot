"""统一流程引擎扩展（guest 迁移·期1）测试。

覆盖设计 `.qa/plans/guest-migration-design.md` §2 的引擎扩展：
- `纪念品` 节点：首发放幂等（未持有才发 / 已持有跳过 / 战败不发）+ flow 级 `纪念品` 缺省回退；
- 节点 `检定` 消费：文本/选项节点首次进入自动检定（对齐 guest「描述+检定+选项」）；
- 节点 `标题` / 内嵌 `文案` 渲染（优先于 text_data）；
- 战斗节点 `玩家文案` 覆盖（节点级覆盖优先、flow 级回退）与 `前置文案` 开战前先发；
- flow 级 `入口条件`（unlocked 判断）、`收尾.skip_daily`（占当日冒险态 + day+1 <40 冻结）；
- 战斗节点 battle_round_html 图片卡片（render_pic/send_pic 优先，md 回退）；
- 结束节点 `完成标记`（默认 true；false = 战败归途不设 flow.<id>.done）。
只测新增行为，不修改 src/ 既有系统。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import asyncio
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init()

from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    Investigator,
    InvestigatorGenerator,
    ending_repo,
    investigator_repo,
)

# flow 级玩家文案（17 键，参考 guest_data.json「玩家文案」结构）
_FLOW_TEXTS = {
    "格斗成功": "（流）你挥剑如风。",
    "格斗大成功": "（流）剑光暴涨，势不可挡！",
    "格斗失败": "（流）剑锋擦过空气。",
    "格斗大失败": "（流）你脚下踉跄，险些跌倒。",
    "反击成功": "（流）你反手一剑逼退对方。",
    "反击大失败": "（流）反击落空，门户大开。",
    "射击成功": "（流）子弹精准命中。",
    "射击大成功": "（流）弹无虚发！",
    "射击失败": "（流）子弹打偏了。",
    "射击大失败": "（流）枪身卡壳，虎口发麻。",
    "射击卡壳": "（流）弹壳卡住，无法击发。",
    "闪避成功": "（流）你侧身避开。",
    "闪避大失败": "（流）躲闪不及，正中要害。",
    "大失败_初始": "（流）开局即陷入被动。",
    "高伤害": "（流）这一击石破天惊。",
    "正常伤害": "（流）干净利落的一击。",
    "低伤害": "（流）只是轻轻擦到。",
}


def _register_ext_flow() -> None:
    """注册 guest 迁移示例 flow（对齐设计 §3.3 sword_magic 拓扑）。"""
    from src.services.flow_engine import register_flow

    register_flow(
        "ext_guest",
        {
            "id": "ext_guest",
            "名字": "扩展测试世界",
            "标题": "⚔️ 测试王都",
            "入口": "enter",
            "入口条件": {"日": {"min": 5, "max": 39}},
            "玩家文案": dict(_FLOW_TEXTS),
            "纪念品": "310",
            "收尾": {
                "skip_daily": True,
                "exit_text_key": "guest.exit",
                "day_text_key": "guest.day_passed",
            },
            "节点": {
                "enter": {
                    "类型": "文本",
                    "标题": "坠入王都",
                    "文案": "裂缝将你吞入一片琥珀色的光。",
                    "下一步": "s1",
                },
                "s1": {
                    "类型": "选项",
                    "标题": "古龙的叹息",
                    "文案": "钟楼下，古龙在叹气。",
                    "检定": {
                        "技能": "闪避",
                        "成功": {"效果": {"hp": 1}, "回复": "你侧身一让。"},
                        "失败": {"效果": {"hp": -1}, "回复": "你躲闪不及。"},
                    },
                    "选项": [
                        {"输入": "迎风而立", "回复": "你吟唱古语。", "下一步": "battle"},
                        {"输入": "转身离开", "回复": "你转身离开。", "下一步": "end"},
                    ],
                },
                "battle": {
                    "类型": "战斗",
                    "标题": "王座上的幽影",
                    "前置文案": "幽影缓缓起身，黑暗漫过你的脚踝。",
                    "玩家文案": {"格斗成功": "（节点）剑光一闪。"},
                    "战斗": {
                        "怪物": "45",
                        "开场": "拔剑吧，旅人。",
                        "胜利": {
                            "效果": {"金币": 30},
                            "回复": "你赢了。",
                            "下一步": "souvenir",
                        },
                        "战败": {"回复": "你输了。", "下一步": "end_lose"},
                    },
                },
                "souvenir": {
                    "类型": "纪念品",
                    "文案": "口袋里多了一枚刻着断剑纹样的旧徽记。",
                    "下一步": "end",
                },
                "end": {
                    "类型": "结束",
                    "文案": "光重新亮起，你回到庄园。",
                    "完成标记": True,
                },
                "end_lose": {
                    "类型": "结束",
                    "文案": "光重新亮起，你回到庄园。",
                    "完成标记": False,
                },
            },
        },
    )


def _register_flow_texts_only() -> None:
    """战斗节点无节点级「玩家文案」：验证 flow 级缺省回退。"""
    from src.services.flow_engine import register_flow

    register_flow(
        "ext_flow_texts",
        {
            "id": "ext_flow_texts",
            "名字": "文案回退世界",
            "入口": "enter",
            "玩家文案": dict(_FLOW_TEXTS),
            "节点": {
                "enter": {"类型": "文本", "文案": "步入回退世界。", "下一步": "battle"},
                "battle": {
                    "类型": "战斗",
                    "战斗": {
                        "怪物": "45",
                        "开场": "开战。",
                        "胜利": {"回复": "胜。", "下一步": "end"},
                        "战败": {"回复": "负。", "下一步": "end"},
                    },
                },
                "end": {"类型": "结束", "文案": "归。", "完成标记": True},
            },
        },
    )


_register_ext_flow()
_register_flow_texts_only()


def _register_daily_frozen_flow() -> None:
    """无「入口条件」的 skip_daily flow（供 day40 冻结断言：入口条件 max=39 会拦截）。"""
    from src.services.flow_engine import register_flow

    register_flow(
        "ext_daily_frozen",
        {
            "id": "ext_daily_frozen",
            "名字": "冻结世界",
            "入口": "battle",
            "收尾": {"skip_daily": True},
            "节点": {
                "battle": {
                    "类型": "战斗",
                    "战斗": {
                        "怪物": "45",
                        "开场": "开战。",
                        "胜利": {"回复": "胜。", "下一步": "end"},
                        "战败": {"回复": "负。", "下一步": "end"},
                    },
                },
                "end": {"类型": "结束", "文案": "归。", "完成标记": True},
            },
        },
    )


_register_daily_frozen_flow()


def _make_inv(qq: str, name: str = "ExtFlow") -> Investigator:
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    data["san"] = 60
    data["意志"] = 60
    data["闪避"] = 60
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


def _enter_battle(qq: str) -> None:
    """进入 ext_guest 战斗节点（s1 → battle），供战斗类测试复用。"""
    from src.services.flow_engine import flow_states

    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        _explore(qq, "ext_guest")
    out = _drive(qq, "迎风而立")
    assert "你吟唱古语。" in "\n".join(out)
    assert flow_states[qq]["phase"] == "battle"


def _win_action(service):
    def _win(action):
        service.hp_record["mon"] = 0
        return ("你挥出最后一击。", "幽影轰然倒地。", "战斗终结")

    return _win


def _lose_action(service):
    def _lose(action):
        service.hp_record["inv"] = 0
        service.fled = True
        return ("你被击倒了。",)

    return _lose


# --- C1：入口条件（unlocked 判断） ---
def test_entry_condition_blocks_day_out_of_range():
    from src.services.flow_engine import flow_states, start_flow

    qq = "ext_cond"
    inv = _make_inv(qq)
    inv.day = 1  # 低于 入口条件 日 min=5
    inv.save()
    assert start_flow(qq, "ext_guest") is None
    assert qq not in flow_states
    inv.day = 10
    inv.save()
    assert start_flow(qq, "ext_guest") is not None
    assert flow_states[qq]["flow_id"] == "ext_guest"
    _cleanup(qq)


def test_entry_condition_requires_character():
    from src.services.flow_engine import start_flow

    assert start_flow("ext_cond_noinv", "ext_guest") is None


def test_entry_condition_absent_zero_impact():
    """无入口条件的既有 flow 不受影响（lane_tale 零改动）。"""
    from src.services.flow_engine import flow_states, start_flow

    qq = "ext_nocond"
    inv = _make_inv(qq)
    inv.day = 1
    inv.save()
    assert start_flow(qq, "lane_tale") is not None
    assert flow_states[qq]["flow_id"] == "lane_tale"
    _cleanup(qq)


# --- C2：检定消费（文本/选项节点首次自动检定）+ 标题 / 内嵌文案 ---
def test_option_node_first_entry_auto_check_and_title():
    from src.services.flow_engine import flow_states

    qq = "ext_check"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        out = _explore(qq, "ext_guest")
    text = "\n".join(out)
    assert "⚔️ 测试王都 · 坠入王都" in text  # enter 节点标题
    assert "⚔️ 测试王都 · 古龙的叹息" in text  # s1 节点标题
    assert "裂缝将你吞入" in text  # enter 内嵌文案
    assert "钟楼下，古龙在叹气。" in text  # s1 内嵌文案
    assert "闪避 检定" in text  # s1 首次进入自动检定（选项节点带 检定 字段）
    assert flow_states[qq]["node_id"] == "s1"  # 停在选项节点
    _cleanup(qq)


def test_check_consumption_only_first_entry():
    """检定消费仅首次：节点已进入后回看不再重复检定。"""
    from src.services.flow_engine import flow_states, render_flow_node

    qq = "ext_check2"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        out1 = _explore(qq, "ext_guest")
    assert "闪避 检定" in "\n".join(out1)
    # 再次渲染当前节点（entered 已含 s1）：不重复检定
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(render_flow_node(qq, flow_states[qq], ConsoleBot(), send))
    assert "闪避 检定" not in "\n".join(sent)
    _cleanup(qq)


# --- C3：纪念品幂等（未持有才发 / 已持有跳过 / 战败不发 / flow 级缺省回退） ---
def _equipment_count(qq: str, item_id: str) -> int:
    equipments, _ = Investigator(investigator_repo.find_by_qq(qq)).get_equipments()
    return equipments.get(item_id, 0)


def test_souvenir_granted_once_on_victory():
    """完整通关：纪念品 310 首次发放（flow.纪念品 缺省回退），done 标记落库。"""
    from src.services.flow_engine import flow_states

    qq = "ext_sou"
    inv = _make_inv(qq)
    inv.day = 10
    inv.is_adventure = True
    inv.save()
    _enter_battle(qq)
    service = flow_states[qq]["battle"]
    service.execute_action = _win_action(service)
    out = _drive(qq, "格斗")
    text = "\n".join(out)
    assert "你赢了。" in text
    assert "获得" in text  # 纪念品发放行（guest.souvenir_gain）
    assert "断剑纹样" in text  # 纪念品节点文案
    assert _equipment_count(qq, "310") == 1
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.get_flag("flow.ext_guest.done") == 1  # 胜利设完成标记
    _cleanup(qq)


def test_souvenir_idempotent_skip_when_held():
    """已持 310 重通关：不重发、不重复文案（幂等）。"""
    from src.services.flow_engine import flow_states

    qq = "ext_sou2"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    inv.add_item_to_inventory("310", 1)
    _enter_battle(qq)
    service = flow_states[qq]["battle"]
    service.execute_action = _win_action(service)
    out = _drive(qq, "格斗")
    text = "\n".join(out)
    assert "你赢了。" in text
    assert "获得" not in text  # 已持有：不追加发放行
    assert _equipment_count(qq, "310") == 1  # 数量不变
    _cleanup(qq)


def test_souvenir_not_granted_on_defeat():
    """战败路径不经过纪念品节点：不发纪念品、不设 done（完成标记 false）。"""
    from src.services.flow_engine import flow_states

    qq = "ext_sou3"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    _enter_battle(qq)
    service = flow_states[qq]["battle"]
    service.execute_action = _lose_action(service)
    out = _drive(qq, "闪避")
    text = "\n".join(out)
    assert "你输了。" in text
    assert "获得" not in text  # 战败空手归途
    assert _equipment_count(qq, "310") == 0
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert not reloaded.get_flag("flow.ext_guest.done")  # 战败不落完成标记
    _cleanup(qq)


# --- C4：玩家文案覆盖（节点级优先 / flow 级回退） ---
def test_player_texts_node_override_wins():
    from src.services.flow_engine import flow_states

    qq = "ext_texts"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    _enter_battle(qq)
    service = flow_states[qq]["battle"]
    assert service._guest_texts.get("格斗成功") == "（节点）剑光一闪。"  # 节点级覆盖
    assert "（流）" not in "".join(service._guest_texts.values())  # flow 级被整体替换
    _cleanup(qq)


def test_player_texts_flow_fallback_when_node_missing():
    from src.services.flow_engine import flow_states

    qq = "ext_texts2"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    with patch("src.services.flow_engine.roll_dice", return_value=("50", 50)):
        _explore(qq, "ext_flow_texts")
    service = flow_states[qq]["battle"]
    assert service._guest_texts.get("格斗成功") == "（流）你挥剑如风。"
    assert service._guest_texts.get("低伤害") == "（流）只是轻轻擦到。"
    assert len(service._guest_texts) == 17  # flow 级 17 键全量注入
    _cleanup(qq)


# --- C5：收尾 skip_daily（占当日冒险态 + day+1 冻结）+ 归途文案 ---
def test_finish_skip_daily_advances_day_and_texts():
    from src.services.flow_engine import flow_states

    qq = "ext_daily"
    inv = _make_inv(qq)
    inv.day = 10
    inv.is_adventure = True
    inv.save()
    _enter_battle(qq)
    service = flow_states[qq]["battle"]
    service.execute_action = _win_action(service)
    out = _drive(qq, "格斗")
    text = "\n".join(out)
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.day == 11  # day+1（<40 冻结）
    assert reloaded.is_adventure is False  # 解除冒险态
    assert "世界缓缓退去" in text  # guest.exit 替代 flow.exit
    assert "天色已过了一天" in text  # guest.day_passed
    assert reloaded.get_flag("flow.ext_guest.done") == 1
    _cleanup(qq)


def test_finish_skip_daily_day_frozen_at_40():
    from src.services.flow_engine import flow_states

    qq = "ext_daily2"
    inv = _make_inv(qq)
    inv.day = 40
    inv.save()
    _explore(qq, "ext_daily_frozen")
    service = flow_states[qq]["battle"]
    service.execute_action = _win_action(service)
    _drive(qq, "格斗")
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.day == 40  # day40 冻结，不再 +1
    _cleanup(qq)


# --- C6：战斗图片卡片（render_pic/send_pic 优先，md 回退） ---
def test_battle_round_image_success_sends_action_section():
    """图片渲染+发送成功：只发行动抉择段，不发 md 战报。"""
    from src.services.flow_engine import flow_states

    qq = "ext_img_ok"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    _enter_battle(qq)
    service = flow_states[qq]["battle"]

    def _act(action):
        return ("你挥出第一击。", "怪物受到 5 点伤害。", "怪物回合：", "怪物攻击，你躲开了。")

    service.execute_action = _act
    with patch("src.services.flow_engine.render_pic", return_value=b"img"), patch(
        "src.services.flow_engine.send_pic", new=AsyncMock(return_value=True)
    ):
        out = _drive(qq, "格斗")
    text = "\n".join(out)
    assert "你挥出第一击。" not in text  # 图片成功：md 战报不发送
    assert "行动" in text  # 行动抉择段（get_action_section）
    _cleanup(qq)


def test_battle_round_image_fallback_to_md():
    """图片渲染失败：回退 md 战报。"""
    from src.services.flow_engine import flow_states

    qq = "ext_img_fb"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    _enter_battle(qq)
    service = flow_states[qq]["battle"]

    def _act(action):
        return ("你挥出第一击。", "怪物受到 5 点伤害。", "怪物回合：", "怪物攻击，你躲开了。")

    service.execute_action = _act
    with patch("src.services.flow_engine.render_pic", return_value=None):
        out = _drive(qq, "格斗")
    text = "\n".join(out)
    assert "你挥出第一击。" in text  # md 回退全量战报
    _cleanup(qq)


def test_battle_end_image_success_and_fallback():
    """战斗结束最后一轮：图片优先/md 回退后仍走胜利分支。"""
    from src.services.flow_engine import flow_states

    qq = "ext_img_end"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    _enter_battle(qq)
    service = flow_states[qq]["battle"]
    service.execute_action = _win_action(service)
    with patch("src.services.flow_engine.render_pic", return_value=b"img"), patch(
        "src.services.flow_engine.send_pic", new=AsyncMock(return_value=True)
    ):
        out_ok = _drive(qq, "格斗")
    assert "你挥出最后一击。" not in "\n".join(out_ok)  # 图片成功：战报不发
    assert "你赢了。" in "\n".join(out_ok)  # 胜利分支仍走
    _cleanup(qq)

    qq = "ext_img_end2"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    _enter_battle(qq)
    service = flow_states[qq]["battle"]
    service.execute_action = _win_action(service)
    with patch("src.services.flow_engine.render_pic", return_value=None):
        out_fb = _drive(qq, "格斗")
    text = "\n".join(out_fb)
    assert "你挥出最后一击。" in text  # md 回退补发最后一轮战报
    assert "你赢了。" in text
    _cleanup(qq)


# --- C7：完成标记默认语义（结束节点无「完成标记」默认 true） ---
def test_completion_flag_default_true():
    """lane_tale 结束节点无「完成标记」：默认 true，仍写 done。"""
    qq = "ext_done_default"
    inv = _make_inv(qq)
    inv.day = 10
    inv.save()
    _explore(qq, "lane_tale")
    _drive(qq, "当作没看见，转身离开")
    reloaded = Investigator(investigator_repo.find_by_qq(qq))
    assert reloaded.get_flag("flow.lane_tale.done") == 1
    _cleanup(qq)
