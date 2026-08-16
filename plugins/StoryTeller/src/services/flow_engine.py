"""统一流程引擎（flow_engine）：节点状态机 · 配置驱动的微型剧情/遭遇引擎。

设计对齐 `.qa/plans/config-deep-eval.md`（TOP10 #2 统一流程引擎 / 2.1 架构设计）与本批次
`.qa/plans/flow-engine-design.md`：

- **节点模型**：`{id, 类型(文本/选项/检定/战斗/奖励/结束), 文案键, 选项列表, 条件, 效果, 下一步}`；
  数据承载在 `data/flow_data.json`，新剧情/遭遇只加配置即可运行，无需改代码。
- **状态机**：`flow_states`（内存 dict，user_id -> {flow_id, node_id, phase, entered, buff, battle}）；
  提供 `start_flow` / `render_flow_node` / `handle_flow_input`（阶段选项 + 战斗行动统一入口）。
- **复用既有地基（M10 收口勿重复造）**：
  - 条件：`eval_option_condition`（ending_engine，全系统统一）；
  - 效果：`apply_event_effects`（event_service，san/hp/金币/物品/技能 统一收口）；
  - 检定：1d100 vs 技能、默认意志、roll<=skill_val（与 guest/event 判定逐字一致）；
  - 按钮：`build_keyboard`（md_format）+ `register_button_handler`（buttons）；
  - 战斗：`BattleService` + `is_gm_room` 隔离（战败不落 is_survive/不登记 E07，胜利不走掉落/门扉）。
- **文案**：从 text_data.json 读，键约定 `flow.<flow_id>.<node_id>`（节点可 `文案键` 覆盖）。
- **注册机制**：纯数据加载（flow_data.json）为主 + `@flow_engine` 装饰器 / `register_flow`
  代码注册为辅（对齐 trinket 注册表：查表分发，新 flow 无需改分发链）。

本批次为「地基」：仅新增，不迁移 guest/npc/event；战斗节点渲染为文本战报（不接图片卡片），
后续批次可平滑升级。flow 为独立探索通道：不占用当日冒险、不推进 day、不触发任何结局判定。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import ujson
from loguru import logger
from nonebot.adapters import Bot

from ..models.monster import Monster
from ..models.player import Investigator, ending_repo, investigator_repo
from ..services.battle import BattleService
from ..services.data_loader import data_loader
from ..services.dice_roller import get_success_icon, roll_dice
from ..services.event_service import apply_event_effects
from ..utils.active_battles import battle_manager
from ..utils.md_format import (
    build_keyboard,
    md_message,
    need_create_message,
    report_section,
)

# 状态机：user_id -> {flow_id, node_id, phase: "stage"|"battle", entered: [], buff: {}, battle}
flow_states: dict[str, dict] = {}

# --- 注册机制：flow_data.json 纯数据 + @flow_engine/register_flow 代码注册（代码优先覆盖） ---
_CODE_FLOWS: dict[str, dict] = {}
_FLOW_DATA_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "flow_data.json"
)
_flow_cache: Optional[dict] = None


def flow_engine(flow_id: str):
    """装饰器：把代码定义的 flow 配置登记进注册表（对齐 @trinket_effect）。"""

    def deco(fn) -> callable:
        _CODE_FLOWS[flow_id] = fn()
        return fn

    return deco


def register_flow(flow_id: str, config: dict) -> None:
    """显式注册一个 flow 配置（测试/代码流程用）。"""
    _CODE_FLOWS[flow_id] = config


def flow_registry() -> dict:
    """flow 注册表：flow_data.json 加载 + 代码注册合并（代码优先覆盖）。"""
    global _flow_cache
    if _flow_cache is None:
        try:
            with open(_FLOW_DATA_PATH, encoding="utf-8-sig") as f:
                _flow_cache = ujson.load(f) or {}
        except Exception:  # noqa: BLE001 - 数据缺失/损坏不影响主流程
            logger.warning("flow_data.json 加载失败，回退空注册表")
            _flow_cache = {}
    merged = dict(_flow_cache)
    merged.update(_CODE_FLOWS)
    return merged


def get_flow(flow_id: str) -> dict:
    """按 flow id 取配置（不存在返回空 dict）。"""
    return flow_registry().get(flow_id) or {}


def resolve_flow_ref(ref: str) -> Optional[str]:
    """按 flow id 或「名字」解析 flow 引用（/探索 巷口异闻 亦可用）。"""
    ref = (ref or "").strip()
    if not ref:
        return None
    reg = flow_registry()
    if ref in reg:
        return ref
    for fid, f in reg.items():
        if f.get("名字") == ref:
            return fid
    return None


def flow_node(flow: dict, node_id: str) -> dict:
    """取节点配置（不存在返回空 dict）。"""
    return ((flow.get("节点") or {}).get(node_id) or {})


def _node_text(flow_id: str, node_id: str) -> str:
    """节点文案：text_data.json 键约定 flow.<flow_id>.<node_id>（节点可用 `文案键` 覆盖）。

    get_text 只支持「段.键」一层点号路径，flow 文案为「段.flow_id.node_id」两级，
    此处做深路径解析（text_data 可直接存嵌套 dict 或扁平键，兼容两者）。
    """
    node = flow_node(get_flow(flow_id), node_id)
    key = node.get("文案键") or f"flow.{flow_id}.{node_id}"
    section = key.split(".", 1)[0]
    cur = data_loader.text_data.get(section)
    if isinstance(cur, dict):
        for part in key.split(".")[1:]:
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(part)
        if isinstance(cur, str):
            return cur
    return data_loader.get_text(key, default="")


def _progress(inv: Investigator):
    """懒加载本局进度（条件求值用），失败回退 None（条件按空处理不拦截）。"""
    try:
        return ending_repo.ensure_progress(inv.qq, inv.day)
    except Exception:  # noqa: BLE001
        return None


def _condition_ok(cond, inv: Investigator, progress=None) -> bool:
    """条件求值（复用事件条件求值器 eval_option_condition）；空条件视为通过。"""
    if not cond or inv is None:
        return True
    from ..services.ending_engine import eval_option_condition

    return eval_option_condition(cond, inv, progress)


def _apply_effects(inv: Investigator, user_id: str, effects: dict) -> str:
    """标准效果（san/hp/金币/物品/技能）走 apply_event_effects 统一收口，返回摘要。"""
    standard = {
        k: v
        for k, v in (effects or {}).items()
        if k in ("san", "hp", "金币", "物品", "技能")
    }
    return apply_event_effects(inv, user_id, standard) if standard else ""


def _flow_check_block(inv: Investigator, user_id: str, check: dict) -> str:
    """节点/选项检定：掷 1d100 vs 技能（默认意志），roll<=skill 判定成功。

    判定与 guest/event 逐字一致（1d100 / 默认意志 / roll<=skill_val）；应用成功/失败
    分支效果并返回「检定行 + 结果」。
    """
    skill = check.get("技能", "意志")
    skill_val = inv.get_skill(skill, 0)
    _expr, roll = roll_dice("1d100")
    passed = roll <= skill_val
    branch = check.get("成功") if passed else check.get("失败")
    t = data_loader.get_text
    icon = get_success_icon(1 if passed else 0)
    level = t("dice.success") if passed else t("dice.failure")
    lines = [
        t(
            "flow.check_line",
            icon=icon,
            skill=skill,
            roll=roll,
            target=skill_val,
            level=level,
        )
    ]
    if branch:
        reply = branch.get("回复", "")
        summary = _apply_effects(inv, user_id, branch.get("效果") or {})
        if reply:
            lines.append(reply)
        if summary:
            lines.append(f"> {summary}")
    return "\n\n".join(lines)


def start_flow(user_id: str, flow_id: str) -> Optional[dict]:
    """启动一个 flow：清理旧状态 → 置入口节点状态。flow 不存在/无入口返回 None。"""
    flow = get_flow(flow_id)
    if not flow:
        return None
    entry = flow.get("入口")
    if not entry or not flow_node(flow, entry):
        return None
    flow_states.pop(user_id, None)
    battle_manager.remove_battle(user_id)
    state = {
        "flow_id": flow_id,
        "node_id": entry,
        "phase": "stage",
        "entered": [],
        "buff": {},
        "battle": None,
    }
    flow_states[user_id] = state
    return state


def _flow_option_rows(
    state: dict, node: dict, options: list[dict], inv: Investigator
) -> list[list[tuple[str, str]]]:
    """选项按钮行（每行 3 个，对齐事件按钮）；条件未满足的选项加「（条件未满足）」标注。

    payload：flow_stage:{node_id}|{option_input}（含节点校验防旧按钮）。
    """
    t = data_loader.get_text
    progress = _progress(inv)
    rows: list[list[tuple[str, str]]] = []
    for i in range(0, len(options), 3):
        row: list[tuple[str, str]] = []
        for opt in options[i : i + 3]:
            label = opt["输入"]
            if not _condition_ok(opt.get("条件"), inv, progress):
                label = f"{label}（{t('flow.locked')}）"
            row.append((label, f"flow_stage:{state['node_id']}|{opt['输入']}"))
        rows.append(row)
    return rows


async def render_flow_node(user_id: str, state: dict, bot: Bot, send) -> None:
    """渲染当前节点：节点条件门 → 自动动作（检定/奖励，仅首次）→ 文案 → 选项按钮/自动推进。

    自动推进节点（无选项的 文本/检定/奖励）会按「下一步」链式渲染直到停在有选项的节点
    或结束节点；战斗节点转入 battle 阶段；结束节点统一收尾。
    """
    t = data_loader.get_text
    guard = 0
    while True:
        guard += 1
        if guard > 100:  # 保险丝：异常节点链防死循环
            await _finish_flow(user_id, state, bot, send)
            return
        flow = get_flow(state["flow_id"])
        node = flow_node(flow, state["node_id"])
        if not node:
            await send(md_message(f"\n{t('flow.unknown')}", bot, mention=user_id))
            await _finish_flow(user_id, state, bot, send)
            return
        ntype = node.get("类型")
        if ntype == "结束":
            await _finish_flow(user_id, state, bot, send)
            return
        if ntype == "战斗":
            await _flow_start_battle(user_id, state, bot, send)
            return
        first = state["node_id"] not in state["entered"]
        inv_model = investigator_repo.find_by_qq(user_id)
        if inv_model is None:
            await send(need_create_message(bot, mention=user_id))
            await _finish_flow(user_id, state, bot, send)
            return
        inv = Investigator(inv_model)
        if node.get("条件") and not _condition_ok(
            node["条件"], inv, _progress(inv)
        ):
            await send(md_message(f"\n{t('flow.locked')}", bot, mention=user_id))
            await _finish_flow(user_id, state, bot, send)
            return
        if first:
            state["entered"].append(state["node_id"])
        lines: list[str] = []
        text = _node_text(state["flow_id"], state["node_id"])
        if text:
            lines.append(text)
        if ntype == "检定" and first:
            check_block = _flow_check_block(
                inv, user_id, node.get("检定") or {}
            )
            if check_block:
                lines.append(check_block)
        if ntype == "奖励" and first:
            summary = _apply_effects(inv, user_id, node.get("效果") or {})
            reply = node.get("回复", "")
            if reply:
                lines.append(reply)
            if summary:
                lines.append(f"> {summary}")
        options = node.get("选项") or []
        if options:
            rows = _flow_option_rows(state, node, options, inv)
            kb = build_keyboard(rows)
            lines.append(f"**{t('flow.option_title')}**")
            msg = md_message(
                "\n\n".join(x for x in lines if x), bot, mention=user_id
            )
            if kb is not None and not isinstance(msg, str):
                msg.append(kb)
            await send(msg)
            return
        if lines:
            await send(
                md_message("\n\n".join(x for x in lines if x), bot, mention=user_id)
            )
        nxt = node.get("下一步", "结束")
        if nxt == "结束" or not flow_node(flow, nxt):
            await _finish_flow(user_id, state, bot, send)
            return
        state["node_id"] = nxt
        state["phase"] = "stage"


async def _flow_handle_choice(
    user_id: str, state: dict, choice: str, bot: Bot, send
) -> None:
    """阶段选项：条件硬门 → 效果/临时 buff → 选项检定 → 回复 → 按「下一步」推进。

    按钮回调携带 node_id|option_input（校验防旧按钮）；命令兜底仅传选项文本。
    """
    option_input = choice
    if "|" in choice:
        parts = choice.split("|", 2)
        if len(parts) < 2:
            return
        node_id, option_input = parts[0], parts[1]
        if node_id != state["node_id"]:
            return  # 旧按钮 / 已过期节点
    flow = get_flow(state["flow_id"])
    node = flow_node(flow, state["node_id"])
    option = next(
        (o for o in node.get("选项") or [] if o.get("输入") == option_input),
        None,
    )
    if option is None:
        await send(
            md_message(
                f"\n{data_loader.get_text('flow.invalid_choice')}",
                bot,
                mention=user_id,
            )
        )
        return
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        await send(need_create_message(bot, mention=user_id))
        return
    inv = Investigator(inv_model)
    if not _condition_ok(option.get("条件"), inv, _progress(inv)):
        await send(
            md_message(
                f"\n{data_loader.get_text('flow.locked')}", bot, mention=user_id
            )
        )
        return
    summary = _apply_effects(inv, user_id, option.get("效果") or {})
    buff = option.get("临时")
    if isinstance(buff, dict):
        for k, v in buff.items():
            state["buff"][k] = state["buff"].get(k, 0) + v
    inv.save()
    reply = option.get("回复", "")
    if summary:
        reply = (
            f"{reply}\n\n"
            f"**{data_loader.get_text('adventure.event_effect_title')}**\n> {summary}"
        )
    if option.get("检定"):
        check_block = _flow_check_block(inv, user_id, option["检定"])
        if check_block:
            reply = f"{reply}\n\n{check_block}" if reply else check_block
    await send(md_message(f"\n{reply}", bot, mention=user_id))
    nxt = option.get("下一步", "结束")
    if nxt == "结束":
        state["node_id"] = "end"
    else:
        state["node_id"] = nxt
    state["phase"] = "stage"
    await render_flow_node(user_id, state, bot, send)


# --- 战斗节点：BattleService + is_gm_room 隔离（对齐 guest 战斗挂接） ---
def _flow_battle_keyboard(service: BattleService):
    """战斗按钮：flow_battle 回调（携带回合令牌防旧按钮）。"""
    actions = service.get_available_actions_for_turn()
    if not actions:
        return None
    token = service.get_turn_token()
    rows = [[(a, f"flow_battle:{a}:{token}") for a in actions[:4]]]
    if len(actions) > 4:
        rows.append([(a, f"flow_battle:{a}:{token}") for a in actions[4:]])
    return build_keyboard(rows)


async def _flow_start_battle(user_id: str, state: dict, bot: Bot, send) -> None:
    """进入战斗节点：BattleService + is_gm_room 隔离，应用已积攒的临时 buff。"""
    flow = get_flow(state["flow_id"])
    node = flow_node(flow, state["node_id"])
    battle_cfg = node.get("战斗") or {}
    monster_id = str(battle_cfg.get("怪物", "45"))
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        await send(need_create_message(bot, mention=user_id))
        return
    inv = Investigator(inv_model)
    service = BattleService(inv, Monster(monster_id))
    service.is_gm_room = True  # 隔离红线：战败不落 is_survive/不登记 E07，胜利不走掉落/门扉
    if state.get("buff"):
        service.set_environment({"玩家": dict(state["buff"])})
    state["battle"] = service
    state["phase"] = "battle"
    service.roll_initiative()
    t = data_loader.get_text
    intro = battle_cfg.get("开场", "")
    if not intro:
        intro = getattr(service.monster, "出场", "") or service.monster.名字
    lines = [
        f"**{t('flow.battle_title')}**",
        intro,
        report_section(t("battle.monster_intro_title")),
        service.monster.名字,
        service.get_dex_compare_section(),
        service.get_status_table(),
        service.get_action_section(),
    ]
    msg = md_message("\n\n".join(x for x in lines if x), bot, mention=user_id)
    kb = _flow_battle_keyboard(service)
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def _flow_battle_input(
    user_id: str, state: dict, choice: str, bot: Bot, send, token: int | None = None
) -> None:
    """战斗行动：执行动作 → 文本战报 + 行动按钮 → 战斗结束分支。

    战斗结束时先补发最后一轮战报（result[:-1]），再走 _flow_battle_end 胜利/战败收尾。
    """
    service = state.get("battle")
    if not service or service.fight_is_over():
        await send(
            md_message(
                f"\n{data_loader.get_text('flow.no_active')}",
                bot,
                mention=user_id,
            )
        )
        return
    if token is not None and token != service.get_turn_token():
        return  # 旧按钮（令牌不匹配）
    if choice == "逃跑":
        await send(
            md_message(
                f"\n{data_loader.get_text('flow.no_exit')}", bot, mention=user_id
            )
        )
        return
    result = service.execute_action(choice)
    if service.fight_is_over():
        if any(x for x in result[:-1]):
            await send(
                md_message(
                    "\n" + "\n\n".join(str(x) for x in result[:-1] if x),
                    bot,
                    mention=user_id,
                )
            )
        await _flow_battle_end(user_id, state, bot, send)
        return
    if not any(x for x in result[:-1]):
        # 单段信息结果（弹药不足/未知行动等）：直接发文本，避免空白战报
        response = "\n" + "\n\n".join(str(x) for x in result if x)
        msg = md_message(response, bot, mention=user_id)
        kb = _flow_battle_keyboard(service)
        if kb is not None and not isinstance(msg, str):
            msg.append(kb)
        await send(msg)
        return
    text = "\n" + "\n\n".join(str(x) for x in result if x)
    msg = md_message(text, bot, mention=user_id)
    kb = _flow_battle_keyboard(service)
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def _flow_battle_end(user_id: str, state: dict, bot: Bot, send) -> None:
    """战斗结束：胜利/战败应用分支效果并按「下一步」推进（默认结束收尾）。"""
    flow = get_flow(state["flow_id"])
    node = flow_node(flow, state["node_id"])
    battle_cfg = node.get("战斗") or {}
    service = state["battle"]
    t = data_loader.get_text
    inv_model = investigator_repo.find_by_qq(user_id)
    inv = Investigator(inv_model) if inv_model else None
    state["phase"] = "stage"
    state["battle"] = None
    battle_manager.remove_battle(user_id)
    if service.hp_record["mon"] <= 0:
        win = battle_cfg.get("胜利") or {}
        reply = win.get("回复", "")
        if inv is not None:
            summary = _apply_effects(inv, user_id, win.get("效果") or {})
            inv.save()
            if summary:
                reply = (
                    f"{reply}\n\n"
                    f"**{t('adventure.event_effect_title')}**\n> {summary}"
                )
        await send(md_message(f"\n{reply}", bot, mention=user_id))
        nxt = win.get("下一步", "结束")
    else:
        lose = battle_cfg.get("战败") or {}
        reply = lose.get("回复", t("flow.defeat_title"))
        if inv is not None:
            summary = _apply_effects(inv, user_id, lose.get("效果") or {})
            inv.save()
            if summary:
                reply = (
                    f"{reply}\n\n"
                    f"**{t('adventure.event_effect_title')}**\n> {summary}"
                )
        await send(
            md_message(f"\n**{t('flow.defeat_title')}**\n\n{reply}", bot, mention=user_id)
        )
        nxt = lose.get("下一步", "结束")
    if nxt == "结束" or not flow_node(flow, nxt):
        await _finish_flow(user_id, state, bot, send)
    else:
        state["node_id"] = nxt
        await render_flow_node(user_id, state, bot, send)


async def _finish_flow(user_id: str, state: dict, bot: Bot, send) -> None:
    """flow 统一收尾：结束文案 + 清状态（不推进 day / 不占当日冒险——独立探索通道）。"""
    t = data_loader.get_text
    text = _node_text(state.get("flow_id", ""), state.get("node_id", "end"))
    lines: list[str] = []
    if text:
        lines.append(text)
    lines.append(t("flow.exit"))
    battle_manager.remove_battle(user_id)
    flow_states.pop(user_id, None)
    await send(
        md_message(
            f"\n**{t('flow.end_title')}**\n\n" + "\n\n".join(lines),
            bot,
            mention=user_id,
        )
    )


# --- 统一输入分发：/行动 命令兜底 与 flow_stage/flow_battle 按钮回调共用 ---
async def handle_flow_input(
    user_id: str, choice: str, bot: Bot, send, token: int | None = None
) -> None:
    """flow 统一输入入口：阶段选项 / 战斗行动按 phase 分发。"""
    state = flow_states.get(user_id)
    if not state:
        await send(
            md_message(
                f"\n{data_loader.get_text('flow.no_active')}", bot, mention=user_id
            )
        )
        return
    if state["phase"] == "stage":
        await _flow_handle_choice(user_id, state, choice, bot, send)
    elif state["phase"] == "battle":
        await _flow_battle_input(user_id, state, choice, bot, send, token)
    else:
        await send(
            md_message(
                f"\n{data_loader.get_text('flow.no_active')}", bot, mention=user_id
            )
        )
