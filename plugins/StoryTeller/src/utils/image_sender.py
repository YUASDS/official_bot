"""HTML 卡片 → 图片渲染与多适配器发送（渲染失败回退 md 的契约由调用方处理）。"""

from typing import Any, Callable

from loguru import logger
from nonebot.adapters import Bot


async def render_pic(html: str):
    """渲染 HTML 卡片为图片 BytesIO；失败返回 None。"""
    try:
        from util.html2pic import html_to_pic

        return await html_to_pic(html, selector=".card", wait=0.8)
    except Exception as e:
        logger.warning(f"静默异常[Exception] in render_pic: {e}")
        return None


async def send_pic(bot: Bot, img: Any, send: Callable) -> bool:
    """按适配器发送图片消息；成功返回 True，失败返回 False（调用方回退 md）。"""
    bt = getattr(bot, "type", "")
    try:
        if bt == "QQ":
            from nonebot.adapters.qq.message import Message as QQMessage
            from nonebot.adapters.qq.message import MessageSegment

            await send(QQMessage(MessageSegment.file_image(img)))
            return True
        if bt == "Console":
            from nonebot.adapters.console.message import ConsoleMessage, Image

            await send(ConsoleMessage(Image(img)))
            return True
        if bt == "Mirai":
            from nonebot.adapters.mirai.message import Image, MessageChain

            await send(MessageChain(Image(img)))
            return True
    except Exception as e:  # noqa: BLE001 - 图片发送失败应回退 md
        logger.warning(f"send image failed on {bt}: {e}")
    return False
