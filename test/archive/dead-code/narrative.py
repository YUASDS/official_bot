from __future__ import annotations


def render_atmospheric_event(day: int, event_text: str) -> str:
    """将每日事件包装为更有克苏鲁氛围的引导文本。"""
    opening = (
        f"【第 {day} 日：雾潮回响】\n"
        "空气里漂浮着潮湿铁锈味，你能听见墙体深处传来不属于人类的低语。"
    )
    sanity_hint = "你本能地数着自己的呼吸，确认理智还在，但它正缓慢流失。"
    return f"{opening}\n{event_text}\n{sanity_hint}"
