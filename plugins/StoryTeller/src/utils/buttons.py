"""按钮基础设施：回调注册/分发 + QQ 消息发送。

- build_keyboard 见 md_format.py（纯构造，不依赖 NoneBot 运行时）
- 本模块负责 InteractionCreateEvent 回调的注册与分发
- 各插件通过 register_button_handler(kind, handler) 注册自己的回调，
  回调统一签名：handler(user_id, payload, bot, group_openid, token)
"""

from typing import Any, Callable

from loguru import logger
from nonebot.adapters import Bot

BUTTON_HANDLERS: dict[str, Callable] = {}


def register_button_handler(kind: str, handler: Callable) -> None:
    """注册按钮回调处理器；同名 kind 重复注册时告警（不阻断，保持兼容）。"""
    if kind in BUTTON_HANDLERS:
        logger.warning(f"Button handler '{kind}' 重复注册，将覆盖旧处理器")
    BUTTON_HANDLERS[kind] = handler


async def _send_to_user(
    bot: Any,
    user_id: str,
    message: Any,
    group_openid: str = "",
) -> None:
    """按 QQ 会话类型发送消息给用户。

    群聊需 group_openid（post_group_messages），私聊用 user_openid（send_to_c2c）。
    按钮回调来自群聊时，group_openid 从事件中获取。
    """
    try:
        from nonebot.adapters.qq import Bot as QQBot
        from nonebot.adapters.qq.message import Message as QQMessage

        if isinstance(bot, QQBot):
            if isinstance(message, QQMessage):
                msg_segments = message
            else:
                msg_segments = QQMessage(message)
            # 群聊：post_group_messages（媒体消息如图片走适配器 send_to_group 自动上传）
            if group_openid:
                try:
                    if msg_segments.get("file_image"):
                        await bot.send_to_group(
                            group_openid=group_openid, message=msg_segments
                        )
                        return
                    await bot.post_group_messages(
                        group_openid=group_openid,
                        msg_type=2,
                        markdown=msg_segments.get("markdown")[-1].data["markdown"]
                        if msg_segments.get("markdown")
                        else None,
                        content=msg_segments.extract_content()
                        if not msg_segments.get("markdown")
                        else None,
                        keyboard=msg_segments.get("keyboard")[-1].data["keyboard"]
                        if msg_segments.get("keyboard")
                        else None,
                    )
                    return
                except Exception as e:
                    logger.warning(f"post_group_messages failed: {e}")
            # 私聊：send_to_c2c
            try:
                await bot.send_to_c2c(openid=user_id, message=msg_segments)
                return
            except Exception as e:
                logger.warning(f"send_to_c2c failed: {e}")
    except Exception as e:
        logger.warning(f"Failed to send via QQ bot: {e}")
    await bot.send_msg(user_id=user_id, message=message)


def setup_button_callback() -> bool:
    """注册 QQ 交互回调（InteractionCreateEvent）。非 QQ 环境返回 False。"""
    try:
        from nonebot import on_type
        from nonebot.adapters.qq.event import InteractionCreateEvent
    except ImportError:
        return False

    button_callback = on_type(InteractionCreateEvent, priority=1, block=False)

    @button_callback.handle()
    async def handle_button_callback(event: InteractionCreateEvent, bot: Bot) -> None:
        # 事件本身继承 ButtonInteraction，先响应交互，避免 QQ 平台 3 秒超时
        try:
            await bot.put_interaction(interaction_id=event.id, code=0)
        except Exception as e:
            logger.debug(f"Failed to ack interaction: {e}")

        button_data = ""
        user_id = ""
        group_openid = ""
        try:
            button_data = event.data.resolved.button_data or ""
            user_id = event.get_user_id()
            group_openid = event.group_openid or ""
        except Exception as e:
            logger.debug(f"Failed to parse button interaction: {e}")
            return

        if not button_data or not user_id:
            return

        # 回调数据格式: "kind:payload[:token]"
        parts = button_data.split(":")
        kind = parts[0]
        payload = parts[1] if len(parts) > 1 else ""
        token = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None

        handler = BUTTON_HANDLERS.get(kind)
        if handler is None:
            logger.debug(f"Unknown button callback: {button_data}")
            return
        try:
            await handler(user_id, payload, bot, group_openid, token)
        except Exception as e:
            logger.exception(f"Button handler '{kind}' failed: {e}")

    return True
