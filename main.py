import os

import nonebot
from loguru import logger
from pathlib import Path
from nonebot.adapters.qq import Adapter as QQAdapter  # 避免重复命名

# 初始化 NoneBot
nonebot.init()

# 注册适配器
driver = nonebot.get_driver()
driver.register_adapter(QQAdapter)


def _patch_qq_reply_model() -> None:
    """nonebot-adapter-qq 1.7.x 引用消息容错补丁（数据层兜底）。

    QQ 官方部分群消息的引用元素（msg_elements[0]）缺失 message_type / msg_idx 字段，
    而适配器 QQReplyMessage 模型将其声明为必填 → pydantic 校验崩（GroupMessageCreateEvent
    ValidationError，冒险启动报错）。在 adapter.payload_to_event 前给缺失字段补默认值
    （message_type=0 / msg_idx=""），使校验通过且不影响其他消息字段。
    """
    try:
        from nonebot.adapters.qq import Adapter as QQAdapter

        def _fill_reply_defaults(obj):
            if isinstance(obj, dict):
                if "content" in obj and "message_type" not in obj and "msg_idx" not in obj:
                    obj.setdefault("message_type", 0)
                    obj.setdefault("msg_idx", "")
                for v in obj.values():
                    _fill_reply_defaults(v)
            elif isinstance(obj, list):
                for v in obj:
                    _fill_reply_defaults(v)

        _orig_payload_to_event = QQAdapter.payload_to_event

        def _patched_payload_to_event(self, payload):
            try:
                _fill_reply_defaults(payload)
            except Exception:  # noqa: BLE001 - 填充失败回退原逻辑
                pass
            return _orig_payload_to_event(self, payload)

        QQAdapter.payload_to_event = _patched_payload_to_event
        logger.info("QQ adapter reply model patched (payload 引用元素补默认)")
    except Exception as e:  # noqa: BLE001 - 补丁失败不应阻塞启动
        logger.warning(f"QQ adapter reply patch skipped: {type(e).__name__} {e}")


_patch_qq_reply_model()

LOGPATH = Path("./logs")
LOGPATH.mkdir(exist_ok=True)
logger.add(
    LOGPATH.joinpath("latest.log"),
    encoding="utf-8",
    backtrace=True,
    diagnose=True,
    rotation="00:00",
    retention="30 days",
    compression="tar.xz",
    colorize=False,
)
logger.info("Bot is starting...")

ignore = ["__init__.py", "__pycache__"]

nonebot.load_plugins("plugins")

logger.info("nonebot加载完成")


@driver.on_startup
async def _warmup_browser():
    """启动时预热 Chromium 浏览器，避免首次图片渲染等待。"""
    try:
        from util.browser import get_browser

        await get_browser()
        logger.info("Chromium Browser warmed up")
    except Exception as e:  # noqa: BLE001 - 预热失败不应阻塞启动
        logger.warning(f"Chromium Browser warmup failed: {type(e).__name__} {e}")


if __name__ == "__main__":
    nonebot.run()
