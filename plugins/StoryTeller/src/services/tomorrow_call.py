"""《明天的来电》跨冒险日线索。选项和检定复用普通奇遇，存档只保存节点与线索。"""

import secrets

from ..models.player import Investigator, ending_repo
from .data_loader import data_loader

FLAG = "case.tomorrow_call"


def due_node(inv: Investigator) -> str | None:
    if inv.day >= 40 or any(
        (event.get("触发") or {}).get("day") == inv.day
        for event in data_loader.event_data.values()
    ):
        return None
    case = inv.get_flag(FLAG)
    if case is None:
        if 3 <= inv.day <= 7 and ending_repo.ensure_progress(inv.qq, inv.day).run_id == 1:
            return "ring"
    elif case.get("next") and inv.day >= case["due"]:
        return case["next"]
    return None


def open_node(inv: Investigator, node: str, monster_id: str, environment: dict) -> dict:
    previous = inv.get_flag(FLAG) or {}
    case = {
        "node": node,
        "day": inv.day,
        "note": previous.get("note", ""),
        "token": secrets.randbits(63),
        "monster": monster_id,
        "environment": environment,
    }
    inv.set_flag(FLAG, case)
    inv.save()
    return event_state(case)


def pending(inv: Investigator) -> dict | None:
    case = inv.get_flag(FLAG)
    if case and case.get("node") and case.get("day") == inv.day:
        return case
    return None


def event_state(case: dict) -> dict:
    node = case["node"]
    if node == "cellar":
        name = "地窖"
        event = data_loader.event_data[name]
    else:
        name = "明天的来电"
        scene = data_loader.tomorrow_call[node]
        note = case["note"]
        recap = f"上回的笔记：{note}\n\n" if note else ""
        event = {**scene, "描述": recap + scene["描述"], "标题": f"☎ {name}"}
    return {"event": event, "event_key": name, "token": case["token"], "case_node": node}


def record_choice(inv: Investigator, option: dict) -> None:
    """本节点的标准效果由奇遇服务结算；案件状态随效果在同一事务提交。"""
    if "下次" in option:
        case = {"next": option["下次"], "due": inv.day + 1, "note": option["笔记"]}
    elif "结案" in option:
        outcome = option["结案"]
        case = {"outcome": outcome}
        ending_repo.record_run_choice(inv.qq, "tomorrow_call", outcome)
    else:
        # 玩家选择继续下楼，普通地窖事件处理完后，本周目不再响铃。
        case = {"outcome": "declined"}
    inv.set_flag(FLAG, case)
    inv.save()
