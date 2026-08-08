"""Markdown 输出工具：text_data 直接存 MD 格式。

- 启用 MD（QQ 平台）：原样发送
- 未启用：strip_markdown() 反向清理为纯文本
- QQ 平台额外支持行动按钮（Keyboard）
"""

from __future__ import annotations

import re
from typing import Any, Union

from loguru import logger

MD_ENV_KEY = "STORYTELLER_MD"
QQ_BOT_TYPE = "QQ"


def is_md_enabled() -> bool:
    """是否启用 Markdown 输出（由 NoneBot 配置 STORYTELLER_MD 控制）。"""
    from nonebot import get_driver

    try:
        value = getattr(get_driver().config, MD_ENV_KEY.lower(), "")
        return str(value).lower() in ("1", "true", "yes", "on")
    except Exception as e:
        logger.debug(f"Failed to read {MD_ENV_KEY} config: {e}")
        return False


def strip_markdown(text: str) -> str:
    """将 MD 文本反向清理为纯文本（关闭 MD 时降级用）。"""
    if not text:
        return text

    lines: list[str] = []
    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue

        # 标题：## X / ### X
        m = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if m:
            lines.append(m.group(1))
            continue

        # 分隔线
        if re.fullmatch(r"[-*_]{3,}", stripped):
            lines.append("------------------")
            continue

        # 引用块：> text
        m = re.match(r"^>\s*(.*)$", stripped)
        if m:
            cleaned = _strip_inline(m.group(1))
            lines.append(cleaned)
            continue

        # 表格：| a | b |，分隔行（| :--- |）跳过
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            lines.append(" ｜ ".join(_strip_inline(c) for c in cells))
            continue

        # 先去行内标记，再处理列表（避免列表项内残留 **）
        cleaned = _strip_inline(line)

        # 列表项
        m = re.match(r"^\s*[-*]\s+(.+)$", cleaned)
        if m:
            lines.append(f" · {m.group(1)}")
            continue

        lines.append(cleaned)

    return "\n".join(lines)


def _strip_inline(text: str) -> str:
    """去除行内加粗/斜体/行内代码标记。"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text


def report_section(title: str) -> str:
    """构造分节头：**【{title}】**。"""
    from ..services.data_loader import data_loader

    return data_loader.get_text("report.section", title=title)


def report_quote(lines: list[str]) -> str:
    """构造引用块：每行 > 前缀，行尾双空格强制换行。"""
    return "\n".join(f"> {line}  " for line in lines if line)


def report_check_table(rows: list[str]) -> str:
    """构造检定表格：表头 + 分隔行 + 数据行。"""
    from ..services.data_loader import data_loader

    t = data_loader.get_text
    return "\n".join([t("report.check_header"), t("report.check_sep"), *rows])


def output(text: str, bot: Any = None) -> str:
    """按开关决定返回 MD 原文或纯文本。"""
    if is_md_enabled():
        return text
    return strip_markdown(text)


def build_keyboard(rows: list[list[tuple[str, str]]]) -> Any:
    """构建 QQ 行动按钮键盘。

    Args:
        rows: 按钮行列表，每行为 [(按钮文字, 回调数据), ...]

    Returns:
        QQ Keyboard 消息段；构造失败返回 None
    """
    try:
        from nonebot.adapters.qq.message import Keyboard
        from nonebot.adapters.qq.models import (
            Action,
            Button,
            InlineKeyboard,
            InlineKeyboardRow,
            MessageKeyboard,
            Permission,
            RenderData,
        )
    except Exception as e:
        logger.debug(f"Failed to import QQ keyboard models: {e}")
        return None

    try:
        button_rows = []
        for row in rows:
            buttons = []
            for label, data in row:
                buttons.append(
                    Button(
                        id=data,
                        render_data=RenderData(label=label, visited_label=label),
                        action=Action(
                            type=1,  # 回调按钮
                            data=data,
                            permission=Permission(type=2),  # 仅指定用户
                        ),
                    )
                )
            button_rows.append(InlineKeyboardRow(buttons=buttons))

        keyboard = MessageKeyboard(content=InlineKeyboard(rows=button_rows))
        return Keyboard("keyboard", data={"keyboard": keyboard})
    except Exception as e:
        logger.exception(f"Failed to build keyboard: {e}")
        return None


def md_message(text: str, bot: Any = None) -> Union[str, Any]:
    """将文本构造为待发送消息。

    - 未启用 md 或非 QQ 适配器：返回纯文本
    - QQ 适配器且启用 md：返回带 Markdown 消息段的消息对象
    """
    if bot is None or getattr(bot, "type", "") != QQ_BOT_TYPE or not is_md_enabled():
        return strip_markdown(text)
    try:
        from nonebot.adapters.qq.message import Markdown, Message, MessageMarkdown

        segment = Markdown(
            "markdown", data={"markdown": MessageMarkdown(content=text)}
        )
        return Message(segment)
    except Exception as e:
        logger.exception(f"Failed to build markdown message: {e}")
        return text
