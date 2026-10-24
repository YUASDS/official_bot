"""四项小优化专项测试（行为不变，零回归基线 480 之上新增）。

覆盖：
1. register_button_handler 同名 kind 重复注册 → logger.warning 告警（仍覆盖，兼容既有行为）；
2. dice_roller 阈值入配置：display_data.dice 读取 / 覆盖生效 / 缺失或非法回退现状默认值；
3. flow_engine._node_text 文案键缺失 → logger.warning 告警（带 flow_id/node_id/键名）。
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()


def _warn_capture():
    from loguru import logger as loguru_logger

    sink = io.StringIO()
    hid = loguru_logger.add(sink, level="WARNING")
    return sink, hid


def _warn_release(sink, hid):
    from loguru import logger as loguru_logger

    loguru_logger.remove(hid)
    return sink.getvalue()


# --- 1. 按钮回调同名注册冲突告警 ---
def test_register_button_handler_conflict_warns():
    from src.utils.buttons import BUTTON_HANDLERS, register_button_handler

    kind = "test_conflict_kind"
    old = BUTTON_HANDLERS.get(kind)
    BUTTON_HANDLERS.pop(kind, None)

    def handler_a(user_id, payload, bot, group_openid, token):
        return "a"

    def handler_b(user_id, payload, bot, group_openid, token):
        return "b"

    try:
        register_button_handler(kind, handler_a)
        assert BUTTON_HANDLERS[kind] is handler_a

        sink, hid = _warn_capture()
        try:
            register_button_handler(kind, handler_b)
        finally:
            text = _warn_release(sink, hid)
        # 告警包含 kind 与冲突语义，且注册仍覆盖（行为不变）
        assert "重复注册" in text
        assert kind in text
        assert BUTTON_HANDLERS[kind] is handler_b
    finally:
        BUTTON_HANDLERS.pop(kind, None)
        if old is not None:
            BUTTON_HANDLERS[kind] = old


# --- 2. dice_roller 阈值配置读取 / 覆盖 / 回退 ---
def test_dice_default_thresholds_zero_behavior():
    from src.services.dice_roller import DiceRoll, SuccessLevel

    roll = DiceRoll(100)
    calc = roll._calculate_success_level
    # 现状硬编码阈值边界（缺配置/配置同现状值 → 行为逐字节不变）
    assert calc(100, 96) == SuccessLevel.CRITICAL_FAILURE  # roll > 95
    assert calc(100, 95) != SuccessLevel.CRITICAL_FAILURE
    assert calc(100, 5) == SuccessLevel.CRITICAL_SUCCESS  # roll < 6
    assert calc(100, 6) != SuccessLevel.CRITICAL_SUCCESS
    assert calc(50, 95) == SuccessLevel.FAILURE  # ratio = 1.9 > 1（roll 未超 95）
    assert calc(100, 60) == SuccessLevel.SUCCESS  # 0.5 < ratio <= 1
    assert calc(100, 30) == SuccessLevel.HARD_SUCCESS  # 0.2 < ratio <= 0.5
    assert calc(100, 10) == SuccessLevel.EXTREME_SUCCESS  # ratio <= 0.2


def test_dice_thresholds_from_display_data_override(monkeypatch):
    from src.services.data_loader import data_loader
    from src.services.dice_roller import DiceRoll, SuccessLevel

    roll = DiceRoll(100)
    calc = roll._calculate_success_level
    old_display = data_loader.display_data
    data_loader.display_data = {
        "dice": {
            "fumble_above": 50,
            "crit_below": 10,
            "hard_ratio": 0.6,
            "extreme_ratio": 0.3,
        }
    }
    try:
        assert calc(100, 60) == SuccessLevel.CRITICAL_FAILURE  # 60 > 50
        assert calc(100, 50) != SuccessLevel.CRITICAL_FAILURE  # 边界 roll == fumble_above
        assert calc(100, 8) == SuccessLevel.CRITICAL_SUCCESS  # 8 < 10
        assert calc(100, 50) == SuccessLevel.HARD_SUCCESS  # 0.3 < 0.5 <= 0.6
        assert calc(100, 20) == SuccessLevel.EXTREME_SUCCESS  # 0.2 <= 0.3
    finally:
        data_loader.display_data = old_display


def test_dice_thresholds_fallback_when_missing_or_invalid(monkeypatch):
    from src.services.data_loader import data_loader
    from src.services.dice_roller import DiceRoll, SuccessLevel

    roll = DiceRoll(100)
    calc = roll._calculate_success_level
    old_display = data_loader.display_data
    try:
        # dice 段缺失 → 回退现状默认值
        data_loader.display_data = {}
        assert calc(100, 96) == SuccessLevel.CRITICAL_FAILURE
        assert calc(100, 5) == SuccessLevel.CRITICAL_SUCCESS
        # dice 段非 dict → 回退现状默认值
        data_loader.display_data = {"dice": None}
        assert calc(100, 96) == SuccessLevel.CRITICAL_FAILURE
        # dice 段类型非法（fumble_above 非数字）→ 整体回退现状默认值
        data_loader.display_data = {"dice": {"fumble_above": "abc"}}
        assert calc(100, 96) == SuccessLevel.CRITICAL_FAILURE
        assert calc(100, 30) == SuccessLevel.HARD_SUCCESS
    finally:
        data_loader.display_data = old_display


# --- 3. flow 文案键缺失告警 ---
def test_flow_missing_text_key_warns():
    from src.services.flow_engine import _node_text, register_flow

    fid = "test_missing_text"
    register_flow(fid, {"id": fid, "入口": "n1", "节点": {"n1": {"类型": "文本"}}})

    sink, hid = _warn_capture()
    try:
        value = _node_text(fid, "n1")
    finally:
        text = _warn_release(sink, hid)
    assert "文案键缺失" in text
    assert fid in text
    assert "n1" in text
    assert "flow.test_missing_text.n1" in text  # 键名
    assert value == "flow.test_missing_text.n1"  # 行为不变：回退键名
