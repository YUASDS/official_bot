import asyncio
from pathlib import Path
from typing import Optional

from loguru import logger
from playwright.async_api import BrowserContext, async_playwright

_HERE = Path(__file__).parent
PATH_TO_EXTENSION = _HERE / "extension" / "ad"
USER_DATA_DIR = _HERE / "data"

_browser: Optional[BrowserContext] = None
_playwright = None
_lock: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def init() -> BrowserContext:
    """启动持久化 Chromium（无扩展时自动跳过扩展参数）。"""
    global _browser, _playwright
    if _browser is not None:
        return _browser
    _playwright = await async_playwright().start()
    args: list[str] = []
    if PATH_TO_EXTENSION.is_dir():
        args.extend(
            [
                f"--disable-extensions-except={PATH_TO_EXTENSION}",
                f"--load-extension={PATH_TO_EXTENSION}",
            ]
        )
    else:
        logger.debug(f"浏览器扩展目录不存在，跳过加载: {PATH_TO_EXTENSION}")
    try:
        _browser = await _playwright.chromium.launch_persistent_context(
            str(USER_DATA_DIR),
            headless=True,
            args=args,
            device_scale_factor=1.25,
        )
    except Exception:
        await _playwright.stop()
        _playwright = None
        raise
    return _browser


async def get_browser() -> BrowserContext:
    async with _get_lock():
        if _browser is None:
            await init()
        assert _browser is not None
        return _browser


async def close() -> None:
    """关闭浏览器与 Playwright 驱动（on_shutdown 调用）。"""
    global _browser, _playwright
    if _browser is not None:
        try:
            await _browser.close()
        except Exception as e:  # noqa: BLE001 - 关闭失败不阻断退出
            logger.warning(f"Chromium context 关闭失败: {type(e).__name__} {e}")
        _browser = None
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Playwright 停止失败: {type(e).__name__} {e}")
        _playwright = None
