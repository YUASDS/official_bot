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


async def send_card_or_md(
    bot: Bot,
    send: Callable,
    html: str,
    fallback_msg,
    kb=None,
    render=render_pic,
    send_image=send_pic,
) -> bool:
    """卡片优先：渲染 HTML 为图片发送成功返回 True；失败时发送 fallback_msg 返回 False。

    fallback_msg 为已构造好的待发送消息（`md_message` 结果，可为消息对象或纯文本）；
    kb 非空且消息非纯文本时追加到消息尾部。render / send_image 默认指向本模块的
    render_pic / send_pic，调用方可注入自身名字以保持可打桩性。
    """
    img = await render(html)
    if img is not None and await send_image(bot, img, send):
        return True
    msg = fallback_msg
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)
    return False
