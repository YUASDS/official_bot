"""创建流程与调查员档案的 HTML 卡片渲染（模板 → HTML 字符串）。"""

from pathlib import Path

from ..models.item import Equipment
from ..models.player import CreateInvestigator, Investigator, InvestigatorFormatter
from ..utils.md_format import md_to_html
from .data_loader import data_loader

_t = data_loader.get_text

SKILL_NAMES = ["格斗", "闪避", "侦查", "聆听", "手枪", "步枪", "急救", "医学"]

_CREATE_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "create_card.html"
_CARD_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "info_card.html"


def create_card_html(
    icon: str, title: str, panels: str, hint_md: str, meta: str = ""
) -> str:
    """创建流程通用卡片 HTML。"""
    return (
        _CREATE_TEMPLATE.read_text(encoding="utf-8")
        .replace("__ICON__", icon)
        .replace("__TITLE__", title)
        .replace("__META__", meta)
        .replace("__PANELS__", panels)
        .replace("__HINT__", md_to_html(hint_md))
    )


def attrs_panel_html(inv: dict, title: str) -> str:
    """属性面板：9 项核心属性 + SAN/HP/DB。"""
    labels = {"san": "SAN", "hp": "HP", "db": "DB"}
    keys = [*InvestigatorFormatter.DISPLAY_ATTRS, "san", "hp", "db"]
    items = "".join(
        f'<span class="p-item"><i>{labels.get(k, k)}</i><b>{inv.get(k, 0)}</b></span>'
        for k in keys
    )
    return (
        f'<div class="panel"><div class="p-title">{title}</div>'
        f'<div class="p-grid">{items}</div></div>'
    )


def skills_panel_html(inv: dict) -> str:
    """技能面板：成对布局 + 闪避单行。"""
    items = "".join(
        f'<span class="p-item"><i>{a}</i><b>{inv.get(a, 0)}</b></span>'
        f'<span class="p-item"><i>{b}</i><b>{inv.get(b, 0)}</b></span>'
        for a, b in (("手枪", "步枪"), ("格斗", "侦查"), ("急救", "医学"))
    )
    items += f'<span class="p-item"><i>闪避</i><b>{inv.get("闪避", 0)}</b></span>'
    return (
        f'<div class="panel"><div class="p-title">技能</div>'
        f'<div class="p-grid">{items}</div></div>'
    )


def candidate_card_html(investigators: list[dict]) -> str:
    """候选列表卡片（3 名候补，属性面板 + 选择提示）。"""
    panels = "".join(
        attrs_panel_html(inv, _t("player.candidate_panel", index=i))
        for i, inv in enumerate(investigators, 1)
    )
    return create_card_html(
        "🌙", "欢迎来到克苏鲁的世界", panels, _t("character.choose_hint")
    )


def choose_success_card_html(ci: CreateInvestigator, name: str) -> str:
    """选择成功卡片：属性 + 技能 + /st 分配提示。"""
    panels = attrs_panel_html(ci.select, _t("character.info_attrs"))
    panels += skills_panel_html(ci.select)
    return create_card_html(
        "✅",
        "选择成功",
        panels,
        _t("character.skill_alloc_hint", points=ci.skill_point),
        meta=_t("character.name_label", name=name),
    )


def create_done_card_html(ci: CreateInvestigator, name: str) -> str:
    """创建完成卡片：属性 + 技能 + 今日冒险提示。"""
    panels = attrs_panel_html(ci.select, _t("character.info_attrs"))
    panels += skills_panel_html(ci.select)
    return create_card_html(
        "🎉",
        _t("character.create_done_title"),
        panels,
        _t("character.create_done_hint"),
        meta=_t("character.name_label", name=name),
    )


# --- 法术显示辅助 ---
def spell_data(spell_id: str) -> dict:
    """法术原始数据；无效返回空 dict。"""
    return data_loader.spell_data.get(spell_id) or {}


def spell_name(spell_id: str) -> str:
    return spell_data(spell_id).get("name", spell_id)


def spell_brief(spell_id: str) -> str:
    """法术简述：MP/SAN/效果。"""
    spell = spell_data(spell_id)
    if not spell:
        return ""
    effect = spell.get("effect", {})
    etype = effect.get("type", "damage")
    dice = effect.get("dice", "")
    effect_key = {
        "damage": "spell.effect_damage",
        "heal": "spell.effect_heal",
        "temp_hp": "spell.effect_temp_hp",
    }.get(etype, "")
    eff = _t(effect_key, dice=dice) if effect_key else ""
    return f"MP {spell.get('mp_cost', 1)}｜SAN {spell.get('san_cost', 0)}｜{eff}"


def info_card_html(inv: Investigator, gold: int) -> str:
    """构建调查员信息 HTML 卡片。"""
    attrs = inv.get_full_attributes_dict()
    attrs["HP"] = inv.get_max_hp()
    attr_items = "".join(
        f'<div class="attr"><span class="k">{k}</span><span class="v">{v}</span></div>'
        for k, v in attrs.items()
    )

    skill_items = "".join(
        f'<div class="attr"><span class="k">{k}</span>'
        f'<span class="v">{inv.get_skill(k, 0)}</span></div>'
        for k in SKILL_NAMES
    )

    equipments, res_name = inv.get_equipments()
    equip_rows = ""
    for part, item_id in inv._equipped.items():
        if not part:
            continue
        equip_rows += (
            f'<div class="row"><span class="k">{part}</span>'
            f'<span class="v">→ {res_name.get(item_id, item_id)}</span></div>'
        )
    if "防具" not in inv._equipped:
        equip_rows += '<div class="row"><span class="k">防具</span><span class="v">→ 无</span></div>'

    bag_rows = "".join(
        f'<div class="row"><span class="k">{item.name} x{qty}</span></div>'
        for item_id, qty in list(equipments.items())[:6]
        for item in [Equipment(item_id)]
        if item.is_valid
    ) or '<div class="row"><span class="k">（空）</span></div>'

    spell_rows = "".join(
        f'<div class="row"><span class="k">{spell_name(sid)}</span>'
        f'<span class="v">{spell_brief(sid)}</span></div>'
        for sid in inv.get_spells()
    ) or f'<div class="row"><span class="k">{_t("spell.list_empty")}</span></div>'

    survival = _t("character.dead") if not inv.is_survive else _t("character.survive")
    html = _CARD_TEMPLATE.read_text(encoding="utf-8")
    return (
        html.replace("__NAME__", inv.name)
        .replace("__STATUS__", survival)
        .replace("__DAY__", str(inv.day))
        .replace("__GOLD__", str(gold))
        .replace("__ATTRS__", attr_items)
        .replace("__SKILLS__", skill_items)
        .replace("__EQUIPS__", equip_rows)
        .replace("__BAG__", bag_rows)
        .replace("__SPELLS__", spell_rows)
    )
