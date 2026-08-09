from util.browser import get_browser
import asyncio
from io import BytesIO
from playwright._impl._api_structures import ViewportSize


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
    await page.set_content(html)
    if js_path:
        await page.add_script_tag(path=js_path)
    if css_path:
        await page.add_style_tag(path=css_path)
    if viewport:
        await page.set_viewport_size(viewport)
    await asyncio.sleep(wait)
    if selector:
        # 元素级截图：按元素真实边界裁剪，避免 body padding/边框超出视口被截断
        element = await page.wait_for_selector(selector)
        assert element is not None
        img = await element.screenshot()
    else:
        img = await page.screenshot()
    await page.close()
    return BytesIO(img)
