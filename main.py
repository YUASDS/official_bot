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
