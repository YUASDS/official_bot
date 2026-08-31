"""创建调查员向导：候选生成、选择、技能分配（命令与按钮共用）。"""

from typing import Any

from ..models.player import CreateInvestigator, InvestigatorFormatter
from .data_loader import data_loader

_t = data_loader.get_text

# --- State storage ---
user_states: dict[str, Any] = {}


def build_create_reply(user_id: str, name: str) -> str:
    """构建候选列表回复，并保存创建状态（供命令与按钮共用）。"""
    ci = CreateInvestigator(3)
    formatted = InvestigatorFormatter.format_investigator_info(name, ci.investigators_data)
    user_states[user_id] = {"creator": ci, "name": name}
    return (
        f"\n{_t('character.create_title')}\n\n"
        f"{formatted}\n\n"
        f"{_t('character.choose_hint')}"
    )


def _choose_success_text(ci: CreateInvestigator, name: str) -> str:
    """选择成功面板：属性/技能每行两项布局。"""
    labels = {"san": "SAN", "hp": "HP", "db": "DB"}

    # 属性配对（每行 ≤15 字符：幸运配 HP、SAN 配 DB）
    attr_pairs = [
        ("力量", "体质"),
        ("体型", "敏捷"),
        ("外貌", "智力"),
        ("意志", "教育"),
        ("幸运", "hp"),
        ("san", "db"),
    ]
    attr_lines = [
        _t(
            "player.attr_pair",
            a=labels.get(a, a),
            av=ci.select.get(a, 0),
            b=labels.get(b, b),
            bv=ci.select.get(b, 0),
        )
        for a, b in attr_pairs
    ]

    # 技能配对 + 闪避单行
    skill_pairs = [("手枪", "步枪"), ("格斗", "侦查"), ("急救", "医学")]
    skill_lines = [
        _t(
            "player.attr_pair",
            a=a,
            av=ci.select.get(a, 0),
            b=b,
            bv=ci.select.get(b, 0),
        )
        for a, b in skill_pairs
    ]
    skill_lines.append(
        _t("player.attr_single", a="闪避", av=ci.select.get("闪避", 0))
    )

    return (
        f"\n{_t('character.choose_success')}\n\n"
        f"{_t('character.name_label', name=name)}\n\n"
        f"{_t('character.info_attrs')}\n{chr(10).join(attr_lines)}\n\n"
        f"{_t('character.skill_title')}\n{chr(10).join(skill_lines)}\n\n"
        f"{_t('character.skill_alloc_hint', points=ci.skill_point)}"
    )


def choose_reply(user_id: str, idx: int) -> str | None:
    """按钮/命令共用：选择候选，返回成功面板；无状态或失败返回 None。"""
    state = user_states.get(user_id)
    if not state or "creator" not in state:
        return None
    ci: CreateInvestigator = state["creator"]
    if not ci.choose_investigator(idx):
        return None
    return _choose_success_text(ci, state["name"])
