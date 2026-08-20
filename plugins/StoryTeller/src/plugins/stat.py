"""统计系统三期 · 展示层命令插件。

- `/统计` 个人战斗/经济/成长/法术/「启」遭遇聚合
- `/周目回顾` 最近 N=5 局快照
- `/排行榜`（别名 `/排行`）击杀榜 / 乌帕榜 / 图鉴进度榜
- 每日 0 点运营日报：读 daily_stats 生成日报，按 STATS_REPORT_TARGET
  （平台:chat_id）推送；未配置则仅落库不推送（不报错）

原则：只读统计表渲染，不改任何游戏逻辑。
"""

from __future__ import annotations

import asyncio
import os

from loguru import logger
from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg

from ..services import stats_service  # noqa: F401 - 先注册 daily_stats 快照钩子
from ..services.stat_render import (
    build_daily_report,
    build_rank_reply,
    build_review_reply,
    build_stats_reply,
)
from ..utils.md_format import md_message

# --- Commands ---
stats_cmd = on_command("统计", aliases={"/统计"}, priority=10, block=True)
review_cmd = on_command("周目回顾", aliases={"/周目回顾"}, priority=10, block=True)
rank_cmd = on_command(
    "排行榜", aliases={"排行", "/排行榜", "/排行"}, priority=10, block=True
)


@stats_cmd.handle()
async def handle_stats(event: Event, bot: Bot):
    user_id = event.get_user_id()
    reply = build_stats_reply(user_id)
    await stats_cmd.finish(md_message(f"\n{reply}", bot, mention=user_id))


@review_cmd.handle()
async def handle_review(event: Event, bot: Bot):
    user_id = event.get_user_id()
    reply = build_review_reply(user_id)
    await review_cmd.finish(md_message(f"\n{reply}", bot, mention=user_id))


@rank_cmd.handle()
async def handle_rank(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    board = msg.extract_plain_text().strip()
    reply = build_rank_reply(board)
    await rank_cmd.finish(md_message(f"\n{reply}", bot, mention=user_id))


# --- 运营日报：每日 0 点钩子 + 推送 ---
_REPORT_LOOP = None
_REPORT_TARGET_ENV = "STATS_REPORT_TARGET"


def report_target() -> tuple[str, str]:
    """解析 STATS_REPORT_TARGET（平台:chat_id）；未配置返回 ("", "")。"""
    raw = os.environ.get(_REPORT_TARGET_ENV, "") or ""
    raw = raw.strip()
    if not raw:
        return "", ""
    if ":" in raw:
        platform, chat_id = raw.split(":", 1)
        return platform.strip().lower(), chat_id.strip()
    return "qq", raw.strip()  # 未带平台前缀时按 qq 平台


async def _push_report(platform: str, chat_id: str, text: str) -> None:
    """向配置目标推送日报（qq 私聊优先，失败回退群聊；一切异常仅记日志）。"""
    try:
        from nonebot import get_bot

        bot = get_bot()
        if platform == "qq":
            try:
                await bot.send_to_c2c(openid=chat_id, message=text)
            except Exception as e:  # noqa: BLE001 - 私聊失败回退群聊
                logger.warning(f"静默异常[Exception] in _push_report: {e}")
                await bot.send_to_group(group_openid=chat_id, message=text)
            return
        if platform == "onebot":
            await bot.send_msg(user_id=chat_id, message=text)
            return
        logger.warning(f"[统计日报] 未知推送平台 {platform}，跳过")
    except Exception as e:  # noqa: BLE001 - 日报推送失败不阻断每日轮转
        logger.warning(f"[统计日报] 推送失败: {type(e).__name__} {e}")


def _daily_report_hook() -> list[str]:
    """每日 0 点轮转钩子：生成日报并异步推送；未配置目标只落库（不报错）。"""
    try:
        text = build_daily_report()
        if not text:
            return []
        platform, chat_id = report_target()
        if platform and chat_id and _REPORT_LOOP is not None:
            _REPORT_LOOP.call_soon_threadsafe(
                lambda: asyncio.create_task(
                    _push_report(platform, chat_id, text)
                )
            )
            logger.info(f"[统计日报] 已推送到 {platform}:{chat_id}")
        else:
            logger.info(
                f"[统计日报] 未配置 {_REPORT_TARGET_ENV}，仅落库 daily_stats"
            )
        return []
    except Exception as e:  # noqa: BLE001 - 日报生成失败不阻断每日轮转
        logger.warning(f"[统计日报] 钩子执行失败: {type(e).__name__} {e}")
        return []


def _capture_loop() -> None:
    """启动时记录主事件循环（供 0 点调度线程排程异步推送）。"""
    global _REPORT_LOOP  # noqa: PLW0603 - 启动时一次性赋值
    try:
        _REPORT_LOOP = asyncio.get_running_loop()
    except RuntimeError as e:
        logger.warning(f"静默异常[RuntimeError] in _capture_loop: {e}")
        _REPORT_LOOP = None


# 注册启动回调（幂等）与 0 点轮转钩子（异常一律吞掉，不阻断插件加载）
try:
    from nonebot import get_driver

    get_driver().on_startup(_capture_loop)
except Exception as e:  # noqa: BLE001 - 非 bot 环境（测试/脚本）忽略
    logger.warning(f"[统计日报] 启动回调注册失败: {type(e).__name__} {e}")

try:
    from util.DaylyRecord import register_daily_rollover

    register_daily_rollover(_daily_report_hook)
except Exception as e:  # noqa: BLE001
    logger.warning(f"[统计日报] 轮转钩子注册失败: {type(e).__name__} {e}")
