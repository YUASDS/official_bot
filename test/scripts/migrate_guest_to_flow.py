"""guest 迁移批次2 一次性迁移脚本：guest_data.json 三世界 → flow_data.json 三 flow。

按 `.qa/plans/guest-migration-design.md` §3.2 映射规则：
- world_id → flow_id；世界「解锁」→ flow.入口条件；「玩家文案」17键原样搬移；
  「入场」→ enter 节点（类型文本，文案=入场）；「结尾.门」→ end/end_lose 节点文案；
  「结尾.纪念品文案」→ souvenir 节点文案；souvenir_id → flow.纪念品/节点.物品。
- 阶段：
  - 普通阶段（无战斗）→ 选项节点：标题/描述(→文案)/检定(自动消费)/选项；
    选项「效果」中的 `临时` 键拆出为兄弟键（flow_engine `_flow_handle_choice` 消费
    option.临时 作为临时 buff；放在 效果 内会丢失，见 §3.2 文本与引擎实现偏差）。
  - 纯战斗阶段（有战斗无选项）→ 战斗节点：标题 + 前置文案(=阶段描述) + 战斗。
    战斗.胜利.下一步 → souvenir；战斗.战败.下一步 → end_lose。
- 归途顺序按用户拍板：纪念品节点(souvenir) → 门(结束节点 end/end_lose)。
- 结束节点「完成标记」：end=true（胜利设 done），end_lose=false（战败不设 done）。

脚本一次性运行，产物为 stdout JSON（三个 flow 条目），由人工合并进 flow_data.json。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
GUEST = ROOT / "plugins" / "StoryTeller" / "data" / "guest_data.json"


def _split_temp(effects: dict) -> tuple[dict, dict | None]:
    """把「效果」中的 `临时` 键拆出为兄弟键（flow_engine 消费 option.临时）。"""
    eff = dict(effects or {})
    temp = eff.pop("临时", None)
    return eff, temp


def _map_option(opt: dict, nodes: dict[str, dict]) -> dict:
    out: dict = {"输入": opt["输入"]}
    eff, temp = _split_temp(opt.get("效果"))
    if eff:
        out["效果"] = eff
    if temp:
        out["临时"] = temp
    if opt.get("回复"):
        out["回复"] = opt["回复"]
    nxt = opt.get("下一步", "结束")
    if nxt == "战斗" or nxt == "结束":
        raise SystemExit(f"选项下一步特殊值 {nxt!r} 未处理: {opt}")
    if nxt not in nodes:
        raise SystemExit(f"选项下一步引用未知阶段: {opt}")
    out["下一步"] = nxt
    return out


def migrate_world(wid: str, world: dict, souvenir_id: str) -> dict:
    stages = world.get("阶段") or []
    if not stages:
        raise SystemExit(f"{wid} 无阶段")
    nodes: dict[str, dict] = {}
    for st in stages:
        sid = st["id"]
        battle = st.get("战斗") or {}
        if battle and not (st.get("选项")):
            nodes[sid] = {
                "类型": "战斗",
                "标题": st["标题"],
                "前置文案": st["描述"],
                "战斗": {
                    "怪物": str(battle["怪物"]),
                    "开场": battle.get("开场", ""),
                    "胜利": {
                        "效果": battle.get("胜利", {}).get("效果") or {},
                        "回复": battle.get("胜利", {}).get("回复", ""),
                        "下一步": "souvenir",
                    },
                    "战败": {
                        "回复": battle.get("战败", {}).get("回复", ""),
                        "下一步": "end_lose",
                    },
                },
            }
            continue
        node: dict = {"类型": "选项", "标题": st["标题"], "文案": st["描述"]}
        if st.get("检定"):
            node["检定"] = st["检定"]
        opts = [_map_option(o, {x["id"] for x in stages}) for o in (st.get("选项") or [])]
        node["选项"] = opts
        nodes[sid] = node
    ending = world.get("结尾") or {}
    nodes["enter"] = {"类型": "文本", "文案": world.get("入场", ""), "下一步": stages[0]["id"]}
    nodes["souvenir"] = {
        "类型": "纪念品",
        "物品": souvenir_id,
        "文案": ending.get("纪念品文案", ""),
        "下一步": "end",
    }
    nodes["end"] = {"类型": "结束", "文案": ending.get("门", ""), "完成标记": True}
    nodes["end_lose"] = {"类型": "结束", "文案": ending.get("门", ""), "完成标记": False}
    return {
        "id": wid,
        "名字": world["名字"],
        "标题": world.get("标题", ""),
        "入口": "enter",
        "入口条件": world.get("解锁") or {},
        "玩家文案": world.get("玩家文案") or {},
        "纪念品": souvenir_id,
        "收尾": {
            "skip_daily": True,
            "exit_text_key": "guest.exit",
            "day_text_key": "guest.day_passed",
        },
        "节点": nodes,
    }


def main() -> None:
    data = json.loads(GUEST.read_text(encoding="utf-8-sig"))
    triggers = json.loads(
        (ROOT / "plugins" / "StoryTeller" / "data" / "reply_data.json").read_text(
            encoding="utf-8-sig"
        )
    ).get("triggers") or []
    souvenir = str(next((t.get("souvenir_id") for t in triggers if t.get("id") == "guest"), "310"))
    flows = {wid: migrate_world(wid, w, souvenir) for wid, w in data.items()}
    print(json.dumps(flows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
