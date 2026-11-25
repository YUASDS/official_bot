from io import BytesIO

import asyncio

from playwright._impl._api_structures import ViewportSize

from util.browser import get_browser


async def html_to_pic(
    html: str,
    viewport: ViewportSize = None,
    selector: str = "",
    wait: float = 0.5,
    js_path: str = "",
    css_path: str = "",
):
    browser = await get_browser()
    page = await browser.new_page()
    try:
        await page.set_content(html, wait_until="load")
        if js_path:
            await page.add_script_tag(path=js_path)
        if css_path:
            await page.add_style_tag(path=css_path)
        if viewport:
            await page.set_viewport_size(viewport)
        await asyncio.sleep(wait)
        if selector:
            # 元素截图：限制等待时间，避免坏卡片卡住整个事件循环；异常由 render_pic 回退 md。
            element = await page.wait_for_selector(selector, timeout=5000)
            if element is None:
                raise RuntimeError(f"card selector not found: {selector}")
            img = await element.screenshot()
        else:
            img = await page.screenshot()
        return BytesIO(img)
    finally:
        try:
            await page.close()
        except Exception:
            pass
