"""统一流程引擎（flow_engine）：节点状态机 · 配置驱动的微型剧情/遭遇引擎。

设计对齐 `.qa/plans/config-deep-eval.md`（TOP10 #2 统一流程引擎 / 2.1 架构设计）与本批次
`.qa/plans/flow-engine-design.md`：

- **节点模型**：`{id, 类型(文本/选项/检定/战斗/奖励/纪念品/结束), 文案键, 文案, 标题, 检定,
  选项列表, 条件, 效果, 下一步, 玩家文案, 前置文案, 前置检定, 完成标记, 物品}`；
  数据承载在 `data/flow_data.json`（普通剧情流程）与 `data/worlds/<世界id>.json`
  （异界奇遇，一世界一文件含多季），新剧情/遭遇只加配置即可运行，无需改代码。
- **状态机**：`flow_states`（内存 dict，user_id -> {flow_id, node_id, phase, entered, buff, battle}）；
  提供 `start_flow` / `render_flow_node` / `handle_flow_input`（阶段选项 + 战斗行动统一入口）。
- **复用既有地基（M10 收口勿重复造）**：
  - 条件：`eval_option_condition`（ending_engine，全系统统一）；
  - 效果：`apply_event_effects`（event_service，san/hp/金币/物品/技能 统一收口）；
  - 检定：1d100 vs 技能、默认意志、roll<=skill_val（与 guest/event 判定逐字一致）；
  - 按钮：`build_keyboard`（md_format）+ `register_button_handler`（buttons）；
  - 战斗：`BattleService` + `is_gm_room` 隔离（战败不落 is_survive/不登记 E07，胜利不走掉落/门扉）。
- **文案**：从 text_data.json 读，键约定 `flow.<flow_id>.<node_id>`（节点可 `文案键` 覆盖）。
- **注册机制**：纯数据加载（flow_data.json + data/worlds/*.json）为主 + `@flow_engine`
  装饰器 / `register_flow` 代码注册为辅（对齐 trinket 注册表：查表分发，新 flow 无需改分发链）。

本批次（guest 迁移·期1）为「地基扩展」：仅新增，不迁移 guest/npc/event；对齐
`.qa/plans/guest-migration-design.md` §2 扩展设计——新增 `纪念品` 节点类型（首发放幂等）、
节点 `文案/标题/检定消费/玩家文案/前置文案/前置检定/完成标记` 字段、flow 级
`入口条件/玩家文案/纪念品/收尾` 配置；战斗节点升级支持 battle_round_html 图片卡片
（render_pic/send_pic 优先，md 回退）。

flow 默认仍为独立探索通道：不占用当日冒险、不推进 day、不触发任何结局判定；开启
`收尾.skip_daily` 的 flow（对齐 guest 归途）才会占当日冒险态（解除）+ day+1（<40 冻结）。
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
from ..services.battle_cards import battle_round_html
from ..services.data_loader import data_loader
from ..services.dice_roller import get_success_icon, roll_dice
from ..services.event_service import apply_event_effects
from ..utils.active_battles import battle_manager
from ..utils.image_sender import render_pic, send_pic
from ..utils.md_format import (
    build_keyboard,
    md_message,
    need_create_message,
    report_section,
)
from ..utils.state_registry import register_state_store

# 状态机：user_id -> {flow_id, node_id, phase: "stage"|"battle", entered: [], buff: {}, battle}
flow_states: dict[str, dict] = {}
register_state_store(flow_states)

# --- 注册机制：flow_data.json + data/worlds/*.json 纯数据 + @flow_engine/register_flow 代码注册 ---
# （代码优先覆盖）内容目录化（第3期）：异界奇遇（世界）迁入 data/worlds/<世界id>.json
# （一世界一文件，顶层 {id, 名字, 季:{s1:{...}}}），lane_tale 等普通剧情流程保留 flow_data.json。
_CODE_FLOWS: dict[str, dict] = {}
_FLOW_DATA_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "flow_data.json"
)
_WORLDS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "worlds"
_flow_cache: Optional[dict] = None
_world_alias: dict[str, str] = {}


def _expand_world_file(path: Path) -> tuple[dict[str, dict], dict[str, str]]:
    """展开一个世界文件 → (注册 flow 表, 别名映射表)。

    世界文件结构：顶层 `{id, 名字, 季:{s1:{...}, s2:{...}}}`，每季一个完整 flow 条目。

    **季 flow_id 兼容方案（目录化第3期决策）**：
    - 单季世界（仅 `s1`）：注册规范键 = **世界 id**（如 `sword_magic`）——保持现状
      `flow.<世界id>.done/visited` 旗标命名与既有调用点（guest.py resolve_flow_ref/start_flow、
      测试断言 flow.sword_magic.done 等）零破坏；另加别名 `flow.<世界id>.s1` 指向规范键，
      满足设计 §四 flow_id 规范 `flow.<世界id>.<季>` 的可解析性。
    - 多季世界（≥2 季）：每季注册 `flow.<世界id>.<季>`（如 flow.anime_academy.s1），
      避免季间旗标冲突；另加别名 世界 id → 首季，裸世界引用（旧调用/旧按钮）仍可解析。
    别名只在 resolve_flow_ref 层生效，不进注册表本体（guest_registry 过滤不受别名污染）。
    """
    try:
        with open(path, encoding="utf-8-sig") as f:
            world = ujson.load(f) or {}
    except Exception:  # noqa: BLE001 - 单个文件损坏不影响其余
        logger.warning(f"{path.name} 加载失败，跳过")
        return {}, {}
    if not isinstance(world, dict):
        return {}, {}
    wid = str(world.get("id") or path.stem)
    name = str(world.get("名字") or "")
    seasons = world.get("季")
    if not isinstance(seasons, dict) or not seasons:
        logger.warning(f"{path.name} 缺少季（季.s1...），跳过")
        return {}, {}
    flows: dict[str, dict] = {}
    aliases: dict[str, str] = {}
    single = len(seasons) == 1 and "s1" in seasons
    for skey, season in seasons.items():
        if not isinstance(season, dict):
            continue
        if single:
            canonical = wid  # 单季兼容：规范键 = 世界 id
        else:
            canonical = f"flow.{wid}.{skey}"
        cfg = dict(season)
        cfg.setdefault("id", canonical)
        cfg.setdefault("名字", name)
        flows[canonical] = cfg
    if single:
        aliases[f"flow.{wid}.s1"] = wid
    else:
        aliases[wid] = f"flow.{wid}.s1"  # 裸世界 id → 首季（多季世界的入口季）
    return flows, aliases


def _load_flow_files() -> tuple[dict[str, dict], dict[str, str]]:
    """加载 flow_data.json（普通剧情流程）+ data/worlds/*.json（异界世界季）→ 注册表 + 别名表。"""
    flows: dict[str, dict] = {}
    aliases: dict[str, str] = {}
    try:
        with open(_FLOW_DATA_PATH, encoding="utf-8-sig") as f:
            legacy = ujson.load(f) or {}
    except Exception:  # noqa: BLE001 - 数据缺失/损坏不影响主流程
        logger.warning("flow_data.json 加载失败，回退空注册表")
        legacy = {}
    if isinstance(legacy, dict):
        flows.update(legacy)
    if _WORLDS_DIR.is_dir():
        for path in sorted(_WORLDS_DIR.glob("*.json")):
            wf, walias = _expand_world_file(path)
            flows.update(wf)
            aliases.update(walias)
    return flows, aliases


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
    """flow 注册表：flow_data.json + data/worlds/*.json 加载 + 代码注册合并（代码优先覆盖）。"""
    global _flow_cache, _world_alias
    if _flow_cache is None:
        _flow_cache, _world_alias = _load_flow_files()
    merged = dict(_flow_cache)
    merged.update(_CODE_FLOWS)
    return merged


def get_flow(flow_id: str) -> dict:
    """按 flow id 取配置（不存在返回空 dict）。"""
    return flow_registry().get(flow_id) or {}


def resolve_flow_ref(ref: str) -> Optional[str]:
    """按 flow id / 世界季 flow_id / 世界名字 解析 flow 引用（/探索 巷口异闻 亦可用）。

    别名映射（单季世界 flow.<世界id>.s1 ↔ 世界 id；多季世界 世界 id → 首季）仅在此层生效，
    注册表本体不含别名——guest_registry 等按注册表过滤的入口不受别名污染。
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    reg = flow_registry()
    if ref in reg:
        return ref
    canon = _world_alias.get(ref)
    if canon and canon in reg:
        return canon
    for fid, f in reg.items():
        if f.get("名字") == ref:
            return fid
    return None


def flow_node(flow: dict, node_id: str) -> dict:
    """取节点配置（不存在返回空 dict）。"""
    return ((flow.get("节点") or {}).get(node_id) or {})


def _node_text(flow_id: str, node_id: str) -> str:
    """节点文案：内嵌 `文案` 优先，其次 text_data.json 键约定 flow.<flow_id>.<node_id>
    （节点可用 `文案键` 覆盖）。

    get_text 只支持「段.键」一层点号路径，flow 文案为「段.flow_id.node_id」两级，
    此处做深路径解析（text_data 可直接存嵌套 dict 或扁平键，兼容两者）。
    """
    node = flow_node(get_flow(flow_id), node_id)
    inline = node.get("文案")
    if isinstance(inline, str) and inline:
        return inline
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
    value = data_loader.get_text(key, default="")
    if not value or value == key:  # get_text 缺省回退返回键名本身 → 文案键缺失
        logger.warning(
            f"flow 文案键缺失: flow_id={flow_id} node_id={node_id} key={key}"
        )
    return value


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
    """标准效果（san/hp/金币/物品/技能）走 apply_event_effects 统一收口，返回摘要。

    主线专属效果（纯增量，缺省零变化，不影响日常事件）：
    - `倾向`：开幕选择 → mainline.apply_tendency（累积主线倾向 + 置当日已选）。值形如
      `{"tendency": "dread", "day": 1}`；day 缺省回退 inv.day。
    - `结局`：分支终局达成 → mainline.register_mainline_ending（登记独立结局 + 置线 done）。
    二者仅在 flow 效果层生效（apply_event_effects 未扩展），日常事件走 apply_event_effects
    不受影响——倾向只来自主线插曲选择。
    """
    effects = effects or {}
    standard = {
        k: v
        for k, v in effects.items()
        if k in ("san", "hp", "金币", "物品", "技能")
    }
    summary = apply_event_effects(inv, user_id, standard) if standard else ""
    tend = effects.get("倾向")
    if isinstance(tend, dict):
        from ..plugins import mainline as _ml

        _ml.apply_tendency(
            inv,
            tend.get("day", inv.day),
            tend.get("tendency"),
            save=True,
        )
    end_id = effects.get("结局")
    if end_id:
        from ..plugins import mainline as _ml

        _ml.register_mainline_ending(inv, str(end_id))
    return summary


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


def _flow_grant_souvenir(user_id: str, item_id: str) -> str:
    """纪念品首发放：未持有才发、已持有幂等跳过（对齐 guest._guest_grant_souvenir）。

    判定键一致（`equipments.get(id, 0) > 0` 则跳过），存量已持玩家重通关不重发。
    返回物品名；未发放（已持有 / 无角色 / 缺 id）返回空字符串。
    """
    if not item_id:
        return ""
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is None:
        return ""
    equipments, _ = Investigator(inv_model).get_equipments()
    if equipments.get(item_id, 0) > 0:
        return ""
    investigator_repo.add_item_to_inventory(inv_model, item_id, 1)
    from ..models.item import Equipment

    return Equipment(item_id).name


def start_flow(user_id: str, flow_id: str) -> Optional[dict]:
    """启动一个 flow：校验入口条件 → 清理旧状态 → 置入口节点状态。

    flow 不存在/无入口返回 None；flow 级 `入口条件`（= guest 世界「解锁」）不满足返回 None
    （无角色则无法求值同样返回 None）。无入口条件的既有 flow 零影响。
    """
    flow = get_flow(flow_id)
    if not flow:
        return None
    entry = flow.get("入口")
    if not entry or not flow_node(flow, entry):
        return None
    if flow.get("入口条件"):
        inv_model = investigator_repo.find_by_qq(user_id)
        if inv_model is None:
            return None
        inv = Investigator(inv_model)
        if not _condition_ok(flow["入口条件"], inv, _progress(inv)):
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
        if node.get("标题"):
            flow_title = flow.get("标题") or ""
            lines.append(
                f"**{flow_title} · {node['标题']}**"
                if flow_title
                else f"**{node['标题']}**"
            )
        text = _node_text(state["flow_id"], state["node_id"])
        if text:
            lines.append(text)
        if first and node.get("检定"):
            # 检定消费：对「文本/选项」等任意节点首次进入自动检定（对齐 guest 阶段描述+检定+选项）
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
        if ntype == "纪念品" and first:
            souvenir_id = node.get("物品") or flow.get("纪念品") or ""
            name = _flow_grant_souvenir(user_id, souvenir_id)
            if name:
                lines.append(t("guest.souvenir_gain", name=name))
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
    """进入战斗节点：BattleService + is_gm_room 隔离，应用已积攒的临时 buff。

    `玩家文案` 注入：节点级覆盖优先，缺省回退 flow 级（= guest 世界「玩家文案」17 键）；
    开战前若配置 `标题/前置文案/前置检定` 先单独发一段描述（对齐 guest 纯战斗阶段先描述后开战）。
    """
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
    service.set_battle_cfg(battle_cfg)  # 战斗配置透传（含 胜利条件，坚守战）
    service.set_guest_texts(node.get("玩家文案") or flow.get("玩家文案") or {})
    if state.get("buff"):
        service.set_environment({"玩家": dict(state["buff"])})
    state["battle"] = service
    state["phase"] = "battle"
    service.roll_initiative()
    t = data_loader.get_text
    pre_lines: list[str] = []
    if node.get("标题"):
        flow_title = flow.get("标题") or ""
        pre_lines.append(
            f"**{flow_title} · {node['标题']}**"
            if flow_title
            else f"**{node['标题']}**"
        )
    if node.get("前置文案"):
        pre_lines.append(node["前置文案"])
    if node.get("前置检定"):
        check_line = _flow_check_block(inv, user_id, node["前置检定"])
        if check_line:
            pre_lines.append(check_line)
    if pre_lines:
        await send(md_message("\n\n".join(pre_lines), bot, mention=user_id))
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
        # 1. 最后一轮检定/交锋战报（不含结束文本；battle_round_html 图片优先/md 回退）
        if any(x for x in result[:-1]):
            img = await render_pic(battle_round_html(service, result))
            if img is None or not await send_pic(bot, img, send):
                combat_text = "\n" + "\n\n".join(str(x) for x in result[:-1] if x)
                await send(md_message(combat_text, bot, mention=user_id))
        # 2. 结束分支（胜利回复/战败归途，不附行动按钮）
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
    # 普通回合：battle_round_html 图片卡片优先，渲染/发送失败回退 md 战报（附行动按钮）
    img = await render_pic(battle_round_html(service, result))
    if img is not None and await send_pic(bot, img, send):
        text = service.get_action_section()
    else:
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
    if service.hp_record["mon"] <= 0 or getattr(service, "_conditional_won", False):
        win = battle_cfg.get("胜利") or {}
        reply = win.get("回复", "")
        hold_text = getattr(service, "hold_win_text", "")
        if hold_text:
            reply = f"{hold_text}\n\n{reply}" if reply else hold_text
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
    """flow 统一收尾：结束文案 + 清状态。

    默认不推进 day / 不占当日冒险（独立探索通道）；flow 级 `收尾.skip_daily` 开启则
    调 daily_service._skip_daily（解除冒险态 + day+1 <40 冻结，对齐 guest 归途）并用
    `exit_text_key`/`day_text_key` 替换默认 flow.exit 文案。
    结束节点 `完成标记`（默认 true）：false = 战败归途不设 `flow.<flow_id>.done`（对齐
    guest 仅胜利路径记完成）。

    flow_states（活跃流程态：node_id/entered/buff/battle）保持内存——重启后中断合理
    （同战斗）；完成标记 `flow.<flow_id>.done` 持久化到 flags，供后续条件/展示读取。
    """
    t = data_loader.get_text
    flow_id = state.get("flow_id", "")
    node_id = state.get("node_id", "end")
    text = _node_text(flow_id, node_id)
    lines: list[str] = []
    if text:
        lines.append(text)
    flow = get_flow(flow_id)
    tail = flow.get("收尾") or {}
    battle_manager.remove_battle(user_id)
    flow_states.pop(user_id, None)
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is not None and flow_id:
        inv = Investigator(inv_model)
        node = flow_node(flow, node_id)
        if node.get("完成标记", True) is not False:
            inv.set_flag(f"flow.{flow_id}.done", 1)
        if tail.get("skip_daily"):
            from ..services.daily_service import _skip_daily

            _skip_daily(user_id, inv)
        inv.save()
    if tail.get("skip_daily"):
        lines.append(t(tail.get("exit_text_key") or "guest.exit"))
        lines.append(t(tail.get("day_text_key") or "guest.day_passed"))
    else:
        lines.append(t("flow.exit"))
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
