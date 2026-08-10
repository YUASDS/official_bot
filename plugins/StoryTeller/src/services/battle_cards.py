"""战报卡片渲染：把战斗状态渲染为 HTML 卡片（入场 CG / 开场 / 回合 / 结算 / SAN 崩塌）。"""

import random
from pathlib import Path

from ..models.player import Investigator
from ..utils.md_format import md_to_html
from .battle import BattleService
from .data_loader import data_loader
from .dice_roller import get_success_description, get_success_icon

_CARD_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "battle_card.html"
_END_CARD_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "end_card.html"
_OPEN_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "battle_open.html"
_ROUND_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "battle_round.html"
_MAD_END_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "mad_end.html"


def battle_title(service: BattleService) -> str:
    """战报卡片标题（环境名，无环境用默认）。"""
    t = data_loader.get_text
    env_name = service.environment.get("name", "") if service.environment else ""
    title = (
        t("battle.report_title", env=env_name)
        if env_name
        else t("battle.report_title_default")
    )
    return title.replace("# 🕯️ ", "").replace(" · 实时战报", "")


def round_status_html(service: BattleService) -> str:
    """回合卡片状态条（HP/SAN/弹药/MP/临时生命 chips）。"""
    inv = service.investigator
    chips = [
        f'<div class="chip"><span class="k">🧑‍🎤 {inv.name}</span> '
        f'<span class="v">HP {service.hp_record["inv"]}/{inv.get_max_hp()}</span>'
        f"</div>",
        f'<div class="chip"><span class="k">🧠 SAN</span> '
        f'<span class="v">{inv.get_skill("san", 0)}</span></div>',
        f'<div class="chip"><span class="k">👾 {service.monster.名字}</span> '
        f'<span class="v">HP {service.hp_record["mon"]}/{service.monster.max_hp}</span>'
        f"</div>",
    ]
    if service.gun:
        chips.append(
            f'<div class="chip"><span class="k">🔫 弹药</span> '
            f'<span class="v">{service.bullet}/{service.max_bullet}</span></div>'
        )
    if service.max_mp > 0:
        chips.append(
            f'<div class="chip"><span class="k">🔮 MP</span> '
            f'<span class="v">{service.mp}/{service.max_mp}</span></div>'
        )
    if service.temp_hp > 0:
        chips.append(
            f'<div class="chip"><span class="k">🛡 临时生命</span> '
            f'<span class="v">{service.temp_hp}</span></div>'
        )
    return "".join(chips)


def battle_round_html(service: BattleService, result: tuple) -> str:
    """回合战报卡片 HTML（检定/交锋/疯狂等 md 渲染进卡片，末段回合提示作脚注）。"""
    t = data_loader.get_text
    body = md_to_html("\n\n".join(str(x) for x in result[:-1] if x))
    owner_key = (
        "battle.your_turn" if service.current_turn == "inv" else "battle.monster_turn"
    )
    hint = t("battle.turn_line", owner=t(owner_key)).replace("**", "")
    return (
        _ROUND_TEMPLATE.read_text(encoding="utf-8")
        .replace("__ROUND__", str(service.get_turn_token()))
        .replace("__TITLE__", t(owner_key))
        .replace("__BODY__", body)
        .replace("__STATUS__", round_status_html(service))
        .replace("__HINT__", hint)
    )


def battle_open_html(service: BattleService, body_md: str) -> str:
    """开场战报卡片 HTML（md 渲染进卡片，首个 # 标题行由印章区承担）。"""
    parts = body_md.split("\n\n", 1)
    if parts[0].strip().startswith("# "):
        body_md = parts[1] if len(parts) > 1 else ""
    return (
        _OPEN_TEMPLATE.read_text(encoding="utf-8")
        .replace("__TITLE__", battle_title(service))
        .replace("__DAY__", str(service.investigator.day))
        .replace("__BODY__", md_to_html(body_md))
    )


def fmt_bonus(v) -> str:
    """数值显示带符号（如 -20 / +10），骰子表达式原样（如 +1d4）。"""
    if isinstance(v, int):
        return f"{v:+d}"
    return str(v)


def env_effects_lines(env: dict) -> list[str]:
    """环境效果摘要行（如「🧑‍🎤 射击 -20」「👾 敏捷 -10」）。"""
    if not env:
        return []
    lines = []
    player = env.get("玩家", {})
    monster = env.get("怪物", {})
    if player:
        lines.append(
            "🧑‍🎤 " + " ｜ ".join(f"{k} {fmt_bonus(v)}" for k, v in player.items())
        )
    if monster:
        lines.append(
            "👾 " + " ｜ ".join(f"{k} {fmt_bonus(v)}" for k, v in monster.items())
        )
    return lines


def battle_card_html(
    service: BattleService,
    day_event: str = "",
    event_desc: str = "",
) -> str:
    """入场 CG 卡片 HTML（环境氛围图，怪物出场在 md 中展示）。

    event_desc 非空时（奇遇场景）：异象区 = 环境描述 + 奇遇描述。
    """
    t = data_loader.get_text
    env_name = service.environment.get("name", "")
    title = (
        t("battle.report_title", env=env_name)
        if env_name
        else t("battle.report_title_default")
    )
    title = title.replace("# 🕯️ ", "").replace(" · 实时战报", "")
    inv = service.investigator

    anomaly_lines = []
    env_desc = service.environment.get("描述", "") if service.environment else ""
    if env_desc:
        anomaly_lines.append(f"<p>{env_desc}</p>")
    if event_desc:
        anomaly_lines.append(f"<p>{event_desc}</p>")
    elif day_event:
        anomaly_lines.append(f"<p>{day_event}</p>")

    # 环境修正区块
    effects = ""
    env_lines = env_effects_lines(service.environment)
    if env_lines:
        t2 = data_loader.get_text
        rows = "".join(f'<div class="e-row">{l}</div>' for l in env_lines)
        effects = (
            f'<div class="effects"><div class="e-title">⚙️ {t2("adventure.env_effect_title")}</div>'
            f"{rows}</div>"
        )

    html = _CARD_TEMPLATE.read_text(encoding="utf-8")
    return (
        html.replace("__TITLE__", title)
        .replace("__DAY__", str(inv.day))
        .replace("__ANOMALY__", "\n".join(anomaly_lines))
        .replace("__EFFECTS__", effects)
    )


def end_card_html(service: BattleService) -> str:
    """结算卡片 HTML。"""
    t = data_loader.get_text
    d = service.get_end_card_data()
    if d["fled"]:
        icon, cls, title, hint = (
            "🏃",
            "win",
            t("battle.fled_title"),
            t("battle.fled_hint"),
        )
        ending = t("battle.fled_ending")
    elif d["victory"]:
        icon, cls, title, hint = (
            "🏆",
            "win",
            t("battle.victory_title").replace("## ", ""),
            "明日可继续冒险",
        )
        ending = d["ending"]
    else:
        icon, cls, title, hint = (
            "💀",
            "dead",
            t("battle.death_text", name=service.player_name).replace("## ", ""),
            "重新创建调查员继续冒险",
        )
        ending = t("battle.death_ending")

    # 胜利明细（侦查检定/战利品/成长）渲染进卡片
    detail = ""
    ext = service.end_card_ext
    if d["victory"] and ext:
        search = ext.get("search", {})
        level = search.get("level", 0)
        icon_s = get_success_icon(level)
        desc = get_success_description(level)
        detail = (
            f'<div class="detail">'
            f'<div class="rowline"><span class="k">🔍 侦查检定：<br></span>'
            f'<span class="v">{icon_s} {desc}（{search.get("dice", "?")}/{search.get("target", "?")}）</span></div>'
            f'<div class="rowline"><span class="k">🎁 战利品：<br></span>'
            f'<span class="v">{ext.get("bonus", "")}</span></div>'
        )
        growth = ext.get("growth", [])
        if growth:
            g = "".join(x.strip() for x in growth)
            detail += (
                f'<div class="rowline"><span class="k">📈 成长鉴定：</span>'
                f'<span class="v">{g}</span></div>'
            )
        learn = ext.get("learn", [])
        if learn:
            g2 = "".join(x.strip() for x in learn)
            detail += (
                f'<div class="rowline"><span class="k">📜 研读·法术：</span>'
                f'<span class="v">{g2}</span></div>'
            )
        luck = ext.get("luck", "")
        if luck:
            detail += (
                f'<div class="rowline"><span class="k">🕯️ 幸运眷顾：</span>'
                f'<span class="v">{luck}</span></div>'
            )
        detail += "</div>"

    html = _END_CARD_TEMPLATE.read_text(encoding="utf-8")
    return (
        html.replace("__ICON__", icon)
        .replace("__CLS__", cls)
        .replace("__TITLE__", title)
        .replace("__ENDING__", ending)
        .replace("__HP__", f"{d['hp']}/{d['max_hp']}")
        .replace("__SAN__", f"{d['san']}")
        .replace("__DAY__", str(d["day"]))
        .replace("__DETAIL__", detail)
        .replace("__HINT__", hint)
    )


def sanity_zero_card_html(inv: Investigator, san_loss: int = 0) -> str:
    """心智·崩塌结算卡片 HTML（SAN 归零）。"""
    t = data_loader.get_text
    san_label = "0"
    if san_loss > 0:
        san_label += f"（-{san_loss}）"
    rambles = data_loader.text_data.get("madness", {}).get("rambles") or []
    ramble = random.choice(rambles) if rambles else ""
    return (
        _MAD_END_TEMPLATE.read_text(encoding="utf-8")
        .replace("__VICTIM__", t("madness.victim", name=inv.name))
        .replace("__RAMBLE__", ramble)
        .replace("__SAN__", san_label)
        .replace("__HP__", f"{inv.hp}/{inv.get_max_hp()}")
        .replace("__DAY__", str(inv.day))
        .replace("__ENDING__", t("adventure.sanity_zero_ending"))
        .replace("__HINT__", t("adventure.sanity_zero_hint"))
    )
