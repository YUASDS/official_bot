"""🧪 /测试 指令：验证 QQ 官方 Markdown 的 base64 图片内嵌与按钮回调。

- /测试：生成一张小测试卡 → base64 → 以两种形式内嵌进 markdown
  （标准 `![alt](data:image/png;base64,...)` 语法与 `<img>` 标签），
  并附带测试按钮（test:ping / test:pong）。
- 点击按钮触发回调回复，验证按钮链路。
"""

import base64
import time

from loguru import logger
from nonebot import on_command, require
from nonebot.adapters import Bot, Event
from nonebot.exception import FinishedException
from nonebot.plugin import PluginMetadata

from util.html2pic import html_to_pic

__plugin_meta__ = PluginMetadata(
    name="MD 测试",
    description="测试 MD 内嵌 base64 图片与按钮功能",
    usage="/测试 - 发送 base64 图片 markdown + 测试按钮",
    type="application",
)

# 复用 StoryTeller 的消息/按钮基建（nonebot require 跨插件机制）
_st = require("StoryTeller")
_md = _st.src.utils.md_format
_btns = _st.src.utils.buttons

test_cmd = on_command("测试", aliases={"mdtest", "test_md"}, priority=1, block=True)

_TEST_CARD_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><style>
body{margin:0;background:#0e0c08;}
.card{width:200px;padding:12px;background:#1c1610;
border:1px solid #6b4a2a;border-radius:10px;color:#e8d9b8;text-align:center;
font-family:'Microsoft YaHei';}
h2{color:#c9a86a;letter-spacing:3px;margin:0 0 4px;font-size:14px;}
p{color:#a88f6a;font-size:11px;margin:0;}
</style></head><body>
<div class="card"><h2>🧪 MD 测试卡</h2><p>__TS__</p></div>
</body></html>"""


async def _render_test_card() -> bytes:
    html = _TEST_CARD_HTML.replace("__TS__", time.strftime("%H:%M:%S"))
    img = await html_to_pic(html, selector=".card", wait=0.3)
    return img.read()


@test_cmd.handle()
async def handle_test(event: Event, bot: Bot) -> None:
    user_id = event.get_user_id()
    try:
        b64 = base64.b64encode(await _render_test_card()).decode()
        md = (
            "## 🧪 测试：MD 图片 base64\n\n"
            "**① 标准语法** `![alt](data:image/png;base64,...)`\n\n"
            f"![base64](data:image/png;base64,{b64})\n\n"
            '**② HTML img 标签** `<img src="data:image/png;base64,...">`\n\n'
            f'<img src="data:image/png;base64,{b64}" />\n\n'
            "**能看到几张图？** 点击下方按钮测试回调。"
        )
        msg = _md.md_message(md, bot, mention=user_id)
        kb = _md.build_keyboard(
            [
                [
                    ("🖱 测试按钮", "test:ping"),
                    ("🔁 再按一次", "test:pong"),
                ]
            ]
        )
        if kb is not None and not isinstance(msg, str):
            msg.append(kb)
        await test_cmd.finish(msg)
    except FinishedException:
        raise
    except Exception as e:  # noqa: BLE001 - 测试指令需兜底报告错误
        logger.exception(f"MD test failed: {e}")
        await test_cmd.finish(
            _md.md_message(f"\n🧪 测试失败：{e}", bot, mention=user_id)
        )


async def handle_test_button(
    user_id: str,
    payload: str,
    bot: Bot,
    group_openid: str = "",
    _token: int | None = None,
) -> None:
    """测试按钮回调：验证按钮链路。"""
    await _btns._send_to_user(
        bot,
        user_id,
        _md.md_message(
            f"\n✅ 按钮回调正常（payload={payload}）",
            bot,
            mention=user_id,
        ),
        group_openid,
    )


_btns.register_button_handler("test", handle_test_button)
