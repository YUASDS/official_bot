"""BOSS 通用框架（boss_framework）：配置驱动的隐藏/特殊 BOSS 统一引擎。

设计对齐 `.qa/plans/config-deep-eval.md`（TOP10 #3 BOSS 通用框架 / 2.1 架构 / 2.3 演进二期）
与本批次 `.qa/plans/boss-framework-design.md`：

- **配置 schema**：`data/boss/<id>.json`（内容目录化，每 BOSS 一文件），每条 BOSS 配置含
  `{id, 怪物id, 触发(条件/前置物品/概率/共享组), 对话(标题/台词/按钮/挑战后/跳过/流程),
   战斗(开场大喝/强制环境/隔离), 奖励(胜利/战败), 特殊, 图片, 卡片}`。
  新 BOSS = 在 data/boss/ 添加一个文件 + 复用 monster_data AI 战斗，无需写新插件。
- **统一调度**：`boss_pick_daily` 遍历 boss 配置表——同「共享组」一次掷骰（组内解锁候选随机选一），
  无共享组的独立 BOSS 各自掷骰；替代 jk.py/qiren.py 镜像逻辑（M1）。
- **复用既有地基（M10 收口勿重复造）**：
  - 战斗：Monster AI（monster_data）原样复用；胜利/战败结算走 `battle/settlement.py`
    （38/48 专属奖励分支一字不改，本条 `奖励` 仅承载描述性元数据）；
  - 触发概率/日范围：boss 目录配置权威，缺省回退 reply_data `triggers`（批次2，零行为）；
  - 按钮：`register_button_handler`（kind = boss id，payload = 动作）；
   - 跳过今日：`daily_service._skip_daily` / `_mark_adventure_done` + `ending_engine.check_daily`；
   - 重入今日冒险：`adventure` 插件注册 `_run_adventure` 委托（register_adventure_runner，解循环①）。
- **对话流程**：静态对话节点（标题+台词+挑战/不挑战按钮）为默认；`对话.流程` 可复用 flow_engine
  （flow_data.json / register_flow）承载复杂对话（自包含遭遇，由 flow 节点全权推进）。
- **红线**：day40 归守门人（残留挑战旗标不覆盖守门人 36）；图片发送失败静默；
  奖励结算不在此处（settlement.py 属规则层，批内未授权修改）。
"""

from __future__ import annotations

import copy
import random
import sys
from pathlib import Path
from typing import Callable, Optional

import ujson
from loguru import logger
from nonebot.adapters import Bot
from nonebot.exception import FinishedException

from ..models.player import Investigator, investigator_repo
from ..services.data_loader import data_loader
from ..services.dice_roller import roll_dice as _default_roll
from ..utils.buttons import (
    BUTTON_HANDLERS,
    _send_to_user,
    register_button_handler,
)
from ..utils.md_format import build_keyboard, md_message, need_create_message

# 待挑战旗标：user_id -> boss_id（点「挑战」后置位，下一次 /今日冒险 强制绑定怪物）。
# 内存 dict 为快路径；持久化镜像写 inv.flags `boss.pending`，重启后挑战意图不丢，
# `boss_resolve_forced` 读时双读（内存优先 → flags 兜底 → 旧薄壳 jk_pending/qiren_pending）。
boss_pending: dict[str, str] = {}


def _boss_pending_set(user_id: str, boss_id: str) -> None:
    """置位待挑战旗标：内存 dict + flags 持久化双写（挑战意图跨重启保留）。"""
    boss_pending[user_id] = boss_id
    inv_model = investigator_repo.find_by_qq(user_id)
    if inv_model is not None:
        inv = Investigator(inv_model)
        inv.set_flag("boss.pending", boss_id)
        inv.save()


# --- 冒险流程回调注册（解循环①：services 不再 import plugins/adventure） ---
# adventure 插件在模块加载时注册 `_run_adventure` 的委托；运行时经 _run_adventure_runner
# 调用。注册委托在 adventure 模块全局解析 `_run_adventure`，故 mock.patch 的测试桩同样生效；
# 未注册时回退 sys.modules 动态读取（兼容加载顺序/未注册场景，行为与旧惰性导入一致）。
#
# 插件前缀按本模块自身 __name__ 推导：测试以 `src.services.boss_framework` 导入、
# test_fullflow 等另以 `plugins.StoryTeller.src.services.boss_framework` 导入（同一物理文件
# 的第二个模块实例），旧相对惰性导入按实例自洽解析同前缀插件；此处用前缀推导保持自洽。
_PLUGIN_PREFIX = __name__.rsplit(".services.boss_framework", 1)[0] + ".plugins."

_adventure_runner: Optional[Callable] = None


def register_adventure_runner(runner: Callable) -> None:
    """注册「重入今日冒险」流程回调（adventure 插件模块加载时调用）。"""
    global _adventure_runner
    _adventure_runner = runner


def _plugin_module(name: str):
    """按本模块同前缀取已加载的插件模块（不触发 import，无 plugins 依赖）。"""
    return sys.modules.get(_PLUGIN_PREFIX + name)


def _adventure_from_modules() -> Optional[Callable]:
    """sys.modules 动态读取 adventure._run_adventure（未注册/加载顺序兜底，不 import）。"""
    mod = _plugin_module("adventure")
    if mod is None:
        return None
    fn = getattr(mod, "_run_adventure", None)
    return fn if callable(fn) else None


async def _run_adventure_runner(
    user_id: str, bot: Bot, send: Callable, finish: Callable
) -> None:
    """重入今日冒险：注册回调优先，未注册回退 sys.modules 动态读取。"""
    runner = _adventure_runner
    if runner is None:
        runner = _adventure_from_modules()
    if runner is None:
        logger.warning("adventure 冒险流程回调未注册，无法重入今日冒险")
        return
    await runner(user_id, bot, send, finish)


def _legacy_pending_pop(user_id: str) -> Optional[str]:
    """旧薄壳 jk_pending/qiren_pending 兼容读取（sys.modules 动态取 dict，不 import plugins）。

    测试/旧调用方直接写 `jk_pending` / `qiren_pending` dict 仍被调度器消费（行为逐字节不变）。
    """
    qiren_forced = False
    jk_forced = False
    qiren_mod = _plugin_module("qiren")
    if qiren_mod is not None:
        qiren_forced = bool(getattr(qiren_mod, "qiren_pending", {}).pop(user_id, False))
    jk_mod = _plugin_module("jk")
    if jk_mod is not None:
        jk_forced = bool(getattr(jk_mod, "jk_pending", {}).pop(user_id, False))
    if qiren_forced:
        return "qiren"
    if jk_forced:
        return "jk"
    return None

# --- 注册表：data/boss/*.json 目录纯数据 + register_boss 代码注册（代码优先覆盖，对齐 flow 注册表） ---
# 内容目录化（第2期）：原单文件 boss_data.json 拆分为「每 BOSS 一文件」data/boss/<id>.json，
# 目录 glob 统一加载合并；boss_weights.json 留在 data/ 根（触发权重，不进本注册表）。
_CODE_BOSSES: dict[str, dict] = {}
_BOSS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "boss"
_boss_cache: Optional[dict] = None


def _load_boss_files() -> dict:
    """加载 data/boss/*.json 目录：每文件一个 BOSS 配置（顶层 id），合并为 {id: 配置}。

    非 BOSS 形状的文件（无 `id` 且无 `怪物id`，如误放的权重文件）跳过；目录缺失/损坏
    回退空注册表（数据缺失不影响主流程）。
    """
    merged: dict[str, dict] = {}
    if not _BOSS_DIR.is_dir():
        logger.warning(f"boss 目录不存在：{_BOSS_DIR}，回退空注册表")
        return merged
    for path in sorted(_BOSS_DIR.glob("*.json")):
        try:
            with open(path, encoding="utf-8-sig") as f:
                cfg = ujson.load(f) or {}
        except Exception:  # noqa: BLE001 - 单个文件损坏不影响其余
            logger.warning(f"{path.name} 加载失败，跳过")
            continue
        if not isinstance(cfg, dict):
            continue
        bid = cfg.get("id") or path.stem
        if not cfg.get("id") and not cfg.get("怪物id"):
            logger.warning(f"{path.name} 不是 BOSS 配置（缺 id/怪物id），跳过")
            continue
        merged[str(bid)] = cfg
    return merged


def _load_boss_file() -> dict:
    """BOSS 数据缓存（目录 glob 合并；兼容旧调用名）。"""
    global _boss_cache
    if _boss_cache is None:
        _boss_cache = _load_boss_files()
    return _boss_cache


def boss_registry() -> dict:
    """BOSS 注册表：data/boss/*.json 目录加载 + 代码注册合并（代码优先覆盖）。"""
    merged = dict(_load_boss_file())
    merged.update(_CODE_BOSSES)
    return merged


def get_boss(boss_id: str) -> dict:
    """按 id 取 BOSS 配置（不存在返回空 dict）。"""
    return boss_registry().get(boss_id) or {}


def register_boss(boss_id: str, config: dict) -> None:
    """代码注册一个 BOSS 配置（测试/代码流程用），并注册其按钮回调。"""
    _CODE_BOSSES[boss_id] = config
    _ensure_boss_button_handler(boss_id)


def unregister_boss(boss_id: str) -> None:
    """注销代码注册的 BOSS（测试隔离用）。"""
    _CODE_BOSSES.pop(boss_id, None)


def _ensure_boss_button_handler(boss_id: str) -> None:
    """按 boss id 注册按钮回调（kind = boss_id，payload = 动作），防重复注册。"""
    if boss_id in BUTTON_HANDLERS:
        return
    register_button_handler(boss_id, _make_boss_button_handler(boss_id))


def _make_boss_button_handler(boss_id: str) -> Callable:
    async def handler(
        user_id: str,
        payload: str,
        bot: Bot,
        group_openid: str = "",
        token: int | None = None,
    ) -> None:
        await handle_boss_button(boss_id, user_id, payload, bot, group_openid, token)

    return handler


# 模块导入：为 data/boss/*.json 中全部 BOSS 预注册按钮回调（新 BOSS 纯数据即可点击）
for _bid in list(boss_registry().keys()):
    _ensure_boss_button_handler(_bid)


# --- 触发条件（boss_data 权威 + reply_data triggers 缺省回退，零行为） ---
def boss_trigger_cfg(boss_id: str) -> dict:
    """归一化触发配置：{日: {min, max}, 物品: [], 概率: {骰, 值}, 共享组, 前置模式}。

    boss_data `触发` 优先；缺省字段回退 reply_data `triggers`（批次2 配置，零行为）。
    前置模式：`触发.前置模式` 驱动前置物品判定（all=全部持有 / any=任一持有，缺省 any）。
    """
    trig = (get_boss(boss_id) or {}).get("触发") or {}
    rep = data_loader.get_trigger(boss_id)
    day = dict((trig.get("条件") or {}).get("日") or {})
    if not day.get("min") and rep.get("day_min") is not None:
        day["min"] = rep.get("day_min")
    if not day.get("max") and rep.get("day_max") is not None:
        day["max"] = rep.get("day_max")
    prob = dict(trig.get("概率") or {})
    if not prob.get("骰") and rep.get("dice"):
        prob["骰"] = rep["dice"]
    if not prob.get("值") and rep.get("value") is not None:
        prob["值"] = rep["value"]
    cond_items = (trig.get("条件") or {}).get("物品")
    items = trig.get("前置物品") or cond_items
    if not items and rep.get("items"):
        items = rep["items"]
    return {
        "日": day,
        "物品": items or [],
        "概率": prob,
        "共享组": trig.get("共享组") or rep.get("group") or "",
        "前置模式": trig.get("前置模式") or "any",
    }


def boss_unlocked(boss_id: str, inv: Investigator) -> bool:
    """触发条件门：日范围 + 前置物品（any 任一持有 / all 全部持有）。day40 归守门人（日.max=39 天然排除）。"""
    t = boss_trigger_cfg(boss_id)
    day = t["日"]
    if day:
        dmin = int(day.get("min", 1))
        dmax = int(day.get("max", 39))
        if not (dmin <= inv.day <= dmax):
            return False
    items = t["物品"]
    if items:
        equipments, _ = inv.get_equipments()
        if t.get("前置模式") == "all":
            if not all(equipments.get(iid, 0) > 0 for iid in items):
                return False
        else:
            if not any(equipments.get(iid, 0) > 0 for iid in items):
                return False
    return True


def boss_should_trigger_standalone(
    boss_id: str, inv: Investigator, roll_fn: Optional[Callable] = None
) -> bool:
    """独立 BOSS（无共享组）每日概率触发：解锁后掷骰出触发值则 True。"""
    if not boss_unlocked(boss_id, inv):
        return False
    prob = boss_trigger_cfg(boss_id)["概率"]
    dice = prob.get("骰", "d20")
    trigger_value = int(prob.get("值", 1))
    _expr, val = (roll_fn or _default_roll)(dice)
    return val == trigger_value


def _group_prob(group: str, bids: list[str]) -> dict:
    """共享组概率骰：读组内首条 BOSS 的触发概率（缺省回退 d20/1）。"""
    for bid in bids:
        prob = boss_trigger_cfg(bid)["概率"]
        if prob:
            return prob
    return {"骰": "d20", "值": 1}


def boss_pick_daily(inv: Investigator, roll_fn: Optional[Callable] = None) -> str | None:
    """每日 BOSS 链：遍历 boss 配置表——同共享组一次掷骰（命中后组内解锁候选随机选一），
    无共享组的独立 BOSS 各自掷骰。返回命中的 boss id 或 None。"""
    registry = boss_registry()
    groups: dict[str, list[str]] = {}
    standalone: list[str] = []
    for bid, cfg in registry.items():
        g = (cfg.get("触发") or {}).get("共享组")
        if g:
            groups.setdefault(str(g), []).append(bid)
        else:
            standalone.append(bid)
    roll = roll_fn or _default_roll
    for g, bids in groups.items():
        prob = _group_prob(g, bids)
        dice = prob.get("骰", "d20")
        trigger_value = int(prob.get("值", 1))
        _expr, val = roll(dice)
        if val != trigger_value:
            continue
        candidates = [b for b in bids if boss_unlocked(b, inv)]
        if candidates:
            return random.choice(candidates)
    for bid in standalone:
        if boss_should_trigger_standalone(bid, inv, roll_fn=roll):
            return bid
    return None


# --- 对话 / 按钮 / 挑战重入 / 跳过今日 ---
async def send_boss_dialogue(boss_id: str, user_id: str, bot: Bot, send) -> None:
    """发送 BOSS 现身台词 + 按钮（等待玩家选择）。

    `对话.流程` 设置时复用 flow_engine（flow_data.json）驱动自包含对话；
    否则静态对话节点：标题 + 台词 + 挑战/不挑战按钮（kind=boss_id，payload=动作）。
    """
    cfg = get_boss(boss_id)
    if not cfg:
        return
    dlg = cfg.get("对话") or {}
    flow_ref = dlg.get("流程")
    if flow_ref:
        from ..services.flow_engine import (
            render_flow_node,
            resolve_flow_ref,
            start_flow,
        )

        fid = resolve_flow_ref(flow_ref) or flow_ref
        state = start_flow(user_id, fid)
        if state is not None:
            await render_flow_node(user_id, state, bot, send)
        return
    t = data_loader.get_text
    rows: list[list[tuple[str, str]]] = [
        [
            (t(btn.get("输入", "")), f"{boss_id}:{btn.get('动作', '')}")
            for btn in dlg.get("按钮") or []
        ]
    ]
    kb = build_keyboard(rows)
    msg = md_message(
        f"\n{t(dlg.get('标题') or f'{boss_id}.title')}\n\n"
        f"{t(dlg.get('台词') or f'{boss_id}.appear')}",
        bot,
        mention=user_id,
    )
    if kb is not None and not isinstance(msg, str):
        msg.append(kb)
    await send(msg)


async def handle_boss_button(
    boss_id: str,
    user_id: str,
    choice: str,
    bot: Bot,
    group_openid: str = "",
    token: int | None = None,
) -> None:
    """挑战/不挑战 按钮回调（配置驱动，逐字节对齐 jk/qiren 原镜像实现）。

    挑战 → 置位 boss_pending + 发送挑战后文案 + 重入今日冒险（强制绑定怪物）；
    不挑战 → 跳过今日（_skip_daily day40 冻结 + _mark_adventure_done + check_daily）。
    """
    cfg = get_boss(boss_id)
    if not cfg:
        return
    dlg = cfg.get("对话") or {}
    challenge_action = dlg.get("挑战动作", "挑战")
    decline_action = dlg.get("不挑战动作", "不挑战")

    async def _send(msg) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)

    async def _finish(msg) -> None:
        await _send_to_user(bot, user_id, msg, group_openid)
        raise FinishedException

    try:
        t = data_loader.get_text
        if choice == challenge_action:
            _boss_pending_set(user_id, boss_id)
            await _send(
                md_message(
                    f"\n{t(dlg.get('挑战后') or f'{boss_id}.challenge_pending')}",
                    bot,
                    mention=user_id,
                )
            )
            # 重入今日冒险流程：boss_pending 旗标 → 强制绑定怪物（adventure 调度器消费）。
            # adventure 插件经 register_adventure_runner 注册委托（解耦 services→plugins）。
            await _run_adventure_runner(user_id, bot, _send, _finish)
        elif choice == decline_action:
            # 跳过今日冒险：_skip_daily（day+1 day40 冻结）+ 日常结算管线（对齐事件跳过战斗）
            from ..services.daily_service import _mark_adventure_done, _skip_daily
            from ..services.ending_engine import check_daily

            inv_model = investigator_repo.find_by_qq(user_id)
            if inv_model is None:
                await _send(need_create_message(bot, mention=user_id))
                return
            inv = Investigator(inv_model)
            _skip_daily(user_id, inv)
            _mark_adventure_done(user_id)
            check_daily(inv)
            await _send(
                md_message(
                    f"\n{t(dlg.get('跳过') or f'{boss_id}.skip_done')}",
                    bot,
                    mention=user_id,
                )
            )
    except FinishedException:
        pass


def boss_resolve_forced(user_id: str, inv: Investigator) -> str | None:
    """挑战后重入：弹出待挑战旗标 → 返回 boss id；day40 守卫（门扉归守门人）。

    读时双读：内存 boss_pending 优先 → flags `boss.pending` 兜底（重启后挑战意图恢复）
    → 旧薄壳 jk_pending/qiren_pending（测试/旧调用方直接写 dict，收编为薄壳后仍生效）。
    消费后清理 flags（内存已 pop），防重复消费；day40 归守门人时同样清旗标。
    """
    boss_id = boss_pending.pop(user_id, None)
    if not boss_id:
        flag_boss = inv.get_flag("boss.pending")
        if flag_boss:
            boss_id = str(flag_boss)
    if not boss_id:
        boss_id = _legacy_pending_pop(user_id)
    if boss_id and inv.get_flag("boss.pending"):
        inv.clear_flag("boss.pending")
        inv.save()
    if boss_id and inv.day >= 40:
        # day40 门扉归守门人，隐藏挑战不参与（防御：残留挑战旗标不覆盖守门人 36）
        return None
    return boss_id


def boss_forced_environment(boss_id: str) -> dict:
    """强制环境：`战斗.强制环境` 配置的环境副本（无配置返回空 dict）。"""
    env_name = ((get_boss(boss_id) or {}).get("战斗") or {}).get("强制环境")
    if not env_name:
        return {}
    env = copy.deepcopy(data_loader.environment_data.get(env_name, {}))
    if not env:
        return {}
    env["name"] = env_name
    return env


def boss_battle_cry(monster_id: str) -> str:
    """开场大喝：按怪物 id 查 BOSS 配置 `战斗.开场大喝` 文本键（无配置返回空串）。"""
    for cfg in boss_registry().values():
        if str(cfg.get("怪物id")) == str(monster_id):
            key = (cfg.get("战斗") or {}).get("开场大喝")
            if key:
                return data_loader.get_text(key, default="")
    return ""


def boss_image_for_monster(monster_id: str) -> Optional[tuple[str, str]]:
    """BOSS 形象图：按怪物 id 查 `图片` 配置，返回 (路径, flag) 或 None。"""
    for cfg in boss_registry().values():
        if str(cfg.get("怪物id")) == str(monster_id):
            img = cfg.get("图片") or {}
            if img.get("路径"):
                return str(img["路径"]), str(img.get("flag") or "")
    return None
