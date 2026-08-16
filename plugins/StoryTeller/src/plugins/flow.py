"""flow 探索命令插件：/探索 <flow_id|名字> 触发统一流程引擎（flow_engine）。

- `/探索` 启动一个 flow（纯配置驱动，见 data/flow_data.json / src/services/flow_engine.py），
  进入后通过 `/行动 <选项输入>` 或按钮推进；战斗节点复用 BattleService（is_gm_room 隔离）。
- 本批次为独立探索通道：不占用当日冒险、不推进 day、不触发结局判定（后续批次再接每日互斥链）。
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.exception import FinishedException
from nonebot.params import CommandArg

from ..models.player import Investigator, investigator_repo
from ..services.data_loader import data_loader
from ..services.flow_engine import (
    flow_states,
    handle_flow_input,
    render_flow_node,
    resolve_flow_ref,
    start_flow,
)
from ..utils.buttons import _send_to_user, register_button_handler
from ..utils.md_format import md_message, need_create_message


async def _handle_explore(user_id: str, ref: str, bot: Bot, send, finish) -> None:
    """/探索 入口：查角色 → 校验未在探索中 → 解析 flow → 启动 + 渲染首节点。"""
    t = data_loader.get_text
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        await finish(need_create_message(bot, mention=user_id))
        return
    inv = Investigator(inv_model)
    if user_id in flow_states:
        await finish(md_message(f"\n{t('flow.no_active')}", bot, mention=user_id))
        return
    flow_id = resolve_flow_ref(ref)
    if flow_id is None:
        await finish(md_message(f"\n{t('flow.unknown')}", bot, mention=user_id))
        return
    state = start_flow(user_id, flow_id)
    if state is None:
        await finish(md_message(f"\n{t('flow.unknown')}", bot, mention=user_id))
        return
    inv.set_flag(f"flow.{flow_id}.visited", True)
    inv.save()
    await send(md_message(f"\n**{t('flow.enter_title')}**", bot, mention=user_id))
    await render_flow_node(user_id, state, bot, send)


explore_cmd = on_command("探索", aliases={"flow"}, priority=10, block=True)


@explore_cmd.handle()
async def handle_explore_command(event: Event, bot: Bot, msg: Message = CommandArg()) -> None:
    """/探索 <flow_id|名字> 命令触发。"""
    ref = msg.extract_plain_text().strip()
    await _handle_explore(
        event.get_user_id(), ref, bot, explore_cmd.send, explore_cmd.finish
    )


def _flow_active_rule(event: Event) -> bool:
    """/行动 兜底命令规则：仅当玩家处于 flow 状态时拦截（对齐 guest 规则）。"""
    try:
        return event.get_user_id() in flow_states
    except Exception:
        return False


flow_action_cmd = on_command("行动", rule=_flow_active_rule, priority=3, block=True)


@flow_action_cmd.handle()
async def handle_flow_action(event: Event, bot: Bot, msg: Message = CommandArg()) -> None:
    """/行动 <选项输入|战斗动作> 命令兜底。"""
    try:
        await handle_flow_input(
            event.get_user_id(),
            msg.extract_plain_text().strip(),
            bot,
            flow_action_cmd.send,
        )
    except FinishedException:
        pass


async def _flow_button_handler(
    user_id: str,
    payload: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """flow 全部按钮回调（flow_stage / flow_battle 共用）。"""

    async def _send(msg) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)

    try:
        await handle_flow_input(user_id, payload, bot, _send, token)
    except FinishedException:
        pass


# --- 按钮回调注册 ---
for _kind in ("flow_stage", "flow_battle"):
    register_button_handler(_kind, _flow_button_handler)
