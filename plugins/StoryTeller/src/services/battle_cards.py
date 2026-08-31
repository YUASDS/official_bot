"""战报卡片渲染：把战斗状态渲染为 HTML 卡片（入场 CG / 开场 / 回合 / 结算 / SAN 崩塌）。"""

from loguru import logger
import random
import ujson
from pathlib import Path

from ..models.player import Investigator
from ..utils.md_format import md_to_html
from .battle import BattleService
from .data_loader import data_loader
from .dice_roller import get_success_description, get_success_icon

_DISPLAY_DATA_PATH = Path(__file__).parent.parent.parent / "data" / "display_data.json"

_display_cache: dict | None = None


def get_display_config() -> dict:
    """展示配置（display_data.json）一次性加载缓存；文件缺失/损坏回退空 dict。"""
    global _display_cache
    if _display_cache is None:
        try:
            with open(_DISPLAY_DATA_PATH, encoding="utf-8-sig") as f:
                raw = ujson.load(f)
            _display_cache = raw if isinstance(raw, dict) else {}
        except Exception as e:
            logger.warning(f"静默异常[Exception] in get_display_config: {e}")
            _display_cache = {}
    return _display_cache

_CARD_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "battle_card.html"
_END_CARD_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "end_card.html"
_OPEN_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "battle_open.html"
_ROUND_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "battle_round.html"
_MAD_END_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "mad_end.html"
_ENDING_TEMPLATE = Path(__file__).parent.parent.parent / "data" / "ending_card.html"


def _esc(text) -> str:
    """HTML 实体转义（结局文案为纯文本，做基础转义防意外标签）。"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _hex_rgba(hex_color: str, alpha: float) -> str:
    """#rrggbb → rgba(r,g,b,a)（主题色系生成半透明层次用）。"""
    h = hex_color.lstrip("#")
    try:
        r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError) as e:
        logger.warning(f"静默异常[ValueError/IndexError] in _hex_rgba: {e}")
        r = g = b = 200
    return f"rgba({r},{g},{b},{alpha})"


# 结局主题表（纯展示层：图标 + 主题色/背景，按结局类型分视觉家族）。
# E01/E02 门扉胜利系=圣金/极光；E05 战败系=暗红；E06 疯狂系=紫；
# E07 死亡系=墓园暗；E04/E09 隐藏=异色；E10 长眠=雾白。
# 数据源 display_data.json `ending_themes`（配置缺失/缺项回退本默认表，逐字节不变）。
_ENDING_THEMES_DEFAULT = {
    "E01": {
        "icon": "🌌",
        "accent": "#e8c97a",
        "border": "#8a743a",
        "bg_top": "#1c1610",
        "bg_mid": "#241a12",
        "bg_bottom": "#181008",
        "text": "#e8d9b8",
        "text_soft": "#c4b494",
        "text_dim": "#8f8268",
    },
    "E02": {
        "icon": "🏮",
        "accent": "#e0b060",
        "border": "#8a6a30",
        "bg_top": "#1e1710",
        "bg_mid": "#251d12",
        "bg_bottom": "#191108",
        "text": "#ead9b0",
        "text_soft": "#c8b288",
        "text_dim": "#8a7a58",
    },
    "E03": {
        "icon": "🍃",
        "accent": "#9fb4c8",
        "border": "#5a6b7a",
        "bg_top": "#14181c",
        "bg_mid": "#1a2026",
        "bg_bottom": "#101418",
        "text": "#c8d8e0",
        "text_soft": "#a0b0c0",
        "text_dim": "#72808c",
    },
    "E04": {
        "icon": "🪞",
        "accent": "#b07ad0",
        "border": "#6a4a8a",
        "bg_top": "#1a1420",
        "bg_mid": "#221832",
        "bg_bottom": "#140e18",
        "text": "#d8c8ec",
        "text_soft": "#b090c8",
        "text_dim": "#7a5c94",
    },
    "E05": {
        "icon": "🌑",
        "accent": "#c26060",
        "border": "#8a3a3a",
        "bg_top": "#201414",
        "bg_mid": "#281818",
        "bg_bottom": "#160e0e",
        "text": "#e8c8c8",
        "text_soft": "#c09090",
        "text_dim": "#7a5050",
    },
    "E06": {
        "icon": "🌀",
        "accent": "#9a6ac8",
        "border": "#6a3a8a",
        "bg_top": "#1c1424",
        "bg_mid": "#241a30",
        "bg_bottom": "#120c18",
        "text": "#d8c8e8",
        "text_soft": "#a888b8",
        "text_dim": "#70507e",
    },
    "E07": {
        "icon": "🪦",
        "accent": "#7a8a9a",
        "border": "#4a5a6a",
        "bg_top": "#14161a",
        "bg_mid": "#1a1e24",
        "bg_bottom": "#0e1014",
        "text": "#c8d0d8",
        "text_soft": "#a0a8b0",
        "text_dim": "#6a7278",
    },
    "E08": {
        "icon": "🚪",
        "accent": "#b0a070",
        "border": "#7a6a3a",
        "bg_top": "#1e1a12",
        "bg_mid": "#262216",
        "bg_bottom": "#140e08",
        "text": "#dcd0ae",
        "text_soft": "#b0a078",
        "text_dim": "#7a7048",
    },
    "E09": {
        "icon": "📿",
        "accent": "#6ec6c8",
        "border": "#3a7a7a",
        "bg_top": "#12201c",
        "bg_mid": "#182a24",
        "bg_bottom": "#0c1412",
        "text": "#b8dcd8",
        "text_soft": "#90b0ac",
        "text_dim": "#5c7a78",
    },
    "E10": {
        "icon": "🌫️",
        "accent": "#b8b8c8",
        "border": "#6a6a78",
        "bg_top": "#16161a",
        "bg_mid": "#1c1c22",
        "bg_bottom": "#0e0e12",
        "text": "#d0d0d8",
        "text_soft": "#a0a0a8",
        "text_dim": "#6e6e76",
    },
}

def _merge_ending_themes(cfg: dict) -> dict:
    """结局主题合并：display_data.json 覆盖同结局子键，其余键/结局回退默认表。"""
    themes = dict(_ENDING_THEMES_DEFAULT)
    for _eid, _theme in (cfg.get("ending_themes") or {}).items():
        if isinstance(_theme, dict):
            base = dict(themes.get(_eid, {}))
            base.update({k: v for k, v in _theme.items() if v is not None})
            themes[_eid] = base
    return themes


_ENDING_THEMES = _merge_ending_themes(get_display_config())

# 变体级强调色微调（纯展示）：同一结局不同子分支在保留家族的前提下稍作区分。
# 数据源 display_data.json `ending_variant_accents`（键 "{结局}.{变体}"）。
_ENDING_VARIANT_ACCENT_DEFAULT = {
    ("E01", "清醒合流"): "#f0dc9a",
    ("E01", "崩溃合流"): "#b8a468",
    ("E02", "圣灯"): "#e8b860",
    ("E02", "歌谣暂封"): "#c8b070",
    ("E05", "星光变体"): "#d0c0a0",
}


def _merge_ending_variant_accents(cfg: dict) -> dict:
    """变体强调色合并：display_data.json 键 "{结局}.{变体}" → 元组键，缺省回退。"""
    accents = dict(_ENDING_VARIANT_ACCENT_DEFAULT)
    for _key, _accent in (cfg.get("ending_variant_accents") or {}).items():
        if isinstance(_key, str) and "." in _key:
            _parts = _key.split(".", 1)
            accents[(_parts[0], _parts[1])] = _accent
    return accents


_ENDING_VARIANT_ACCENT = _merge_ending_variant_accents(get_display_config())


def _ending_default_icon() -> str:
    """未知结局的默认图标（display_data.json `ending_defaults.icon`，回退 🌌）。"""
    defaults = get_display_config().get("ending_defaults")
    if isinstance(defaults, dict):
        icon = defaults.get("icon")
        if icon:
            return icon
    return "🌌"


def _ending_theme_style(end_id: str, variant: str = "") -> str:
    """结局主题 CSS 注入：按结局 id 覆写模板 CSS 变量（--accent 等）；未知结局返回空串。"""
    theme = _ENDING_THEMES.get(end_id)
    if not theme:
        return ""
    accent = theme["accent"]
    variant_accent = _ENDING_VARIANT_ACCENT.get((end_id, variant))
    if variant_accent:
        accent = variant_accent
    rules = [
        ".card{"
        f"--accent:{accent};"
        f"--border:{theme['border']};"
        f"--accent-soft:{_hex_rgba(accent, .08)};"
        f"--accent-strong:{_hex_rgba(accent, .16)};"
        f"--accent-glow:{_hex_rgba(accent, .45)};"
        f"--bg-top:{theme['bg_top']};"
        f"--bg-mid:{theme['bg_mid']};"
        f"--bg-bottom:{theme['bg_bottom']};"
        f"--text:{theme['text']};"
        f"--text-soft:{theme['text_soft']};"
        f"--text-dim:{theme['text_dim']};"
        f"--chip-bg:{_hex_rgba(accent, .06)};"
        f"--chip-border:{_hex_rgba(accent, .20)};"
        "}"
    ]
    return "<style>" + "".join(rules) + "</style>"


def ending_card_html(
    end_id: str,
    name: str = "",
    etype: str = "",
    variant: str = "",
    body: str = "",
    vbody: str = "",
    note: str = "",
    meta_rows: list[tuple] | None = None,
) -> str:
    """结局卡片 HTML（纯展示层，数据由调用方构造）。

    结构：类型徽章 → 结局图标 → 结局编号+名称 → 变体标签 → 正文 →
    变体正文 → 达成信息行（周目/知识度/信物/进度）→ 补充说明。
    主题按结局 id（+变体微调）注入，无主题的未知结局用模板默认圣金。
    """
    theme = _ENDING_THEMES.get(end_id)
    icon = theme["icon"] if theme else _ending_default_icon()
    title = f"{end_id} · {name}" if name else end_id
    variant_tag = (
        f'<div class="variant-tag">{_esc(variant)}</div>' if variant else ""
    )
    vbody_html = (
        f'<div class="ending variant">{_esc(vbody)}</div>' if vbody else ""
    )
    meta_html = ""
    if meta_rows:
        rows = "".join(
            f'<div class="rowline"><span class="k">{_esc(k)}</span>'
            f'<span class="v">{_esc(v)}</span></div>'
            for k, v in meta_rows
            if k and str(v)
        )
        if rows:
            meta_html = f'<div class="meta">{rows}</div>'
    note_html = f'<div class="note">{_esc(note)}</div>' if note else ""

    html = _ENDING_TEMPLATE.read_text(encoding="utf-8")
    html = (
        html.replace("__TYPE__", _esc(etype))
        .replace("__ICON__", icon)
        .replace("__TITLE__", _esc(title))
        .replace("__VARIANT_TAG__", variant_tag)
        .replace("__BODY__", _esc(body))
        .replace("__VBODY__", vbody_html)
        .replace("__META__", meta_html)
        .replace("__NOTE__", note_html)
    )
    style = _ending_theme_style(end_id, variant)
    if style:
        html = html.replace("</body>", f"{style}</body>")
    return html


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


def _chip_icon(key: str) -> str:
    """回合状态条 chip 图标（display_data.json `battle_chips`，缺失回退现状）。"""
    default_map = {
        "inv": "🧑‍🎤",
        "san": "🧠",
        "monster": "👾",
        "ammo": "🔫",
        "mp": "🔮",
        "temp_hp": "🛡",
    }
    icon = default_map.get(key, "")
    cfg = get_display_config().get("battle_chips")
    if isinstance(cfg, dict):
        icon = cfg.get(key) or icon
    return icon


def _env_icon(key: str) -> str:
    """环境效果摘要行图标（display_data.json `battle_env_icons`，缺失回退现状）。"""
    default_map = {"player": "🧑‍🎤", "monster": "👾"}
    icon = default_map.get(key, "")
    cfg = get_display_config().get("battle_env_icons")
    if isinstance(cfg, dict):
        icon = cfg.get(key) or icon
    return icon


def _env_effect_icon() -> str:
    """环境修正标题图标（display_data.json `battle_env_effect_icon`，回退 ⚙️）。"""
    return get_display_config().get("battle_env_effect_icon") or "⚙️"


def _battle_end_display(key: str) -> tuple[str, str]:
    """结算卡片图标/类名（display_data.json `battle_end`，缺失回退现状）。"""
    default_map = {
        "fled": ("🏃", "win"),
        "victory": ("🏆", "win"),
        "death": ("💀", "dead"),
    }
    icon, cls = default_map.get(key, ("", ""))
    cfg = get_display_config().get("battle_end")
    entry = cfg.get(key) if isinstance(cfg, dict) else None
    if isinstance(entry, dict):
        icon = entry.get("icon") or icon
        cls = entry.get("class") or cls
    return icon, cls


def _white_default_accent() -> str:
    """白色主题无强调色时的默认圣金（display_data.json `defaults.white_accent`，回退 #8a7a52）。"""
    defaults = get_display_config().get("defaults")
    if isinstance(defaults, dict):
        accent = defaults.get("white_accent")
        if accent:
            return accent
    return "#8a7a52"


def round_status_html(service: BattleService) -> str:
    """回合卡片状态条（HP/SAN/弹药/MP/临时生命 chips）。"""
    inv = service.investigator
    chips = [
        f'<div class="chip"><span class="k">{_chip_icon("inv")} {inv.name}</span> '
        f'<span class="v">HP {service.hp_record["inv"]}/{inv.get_max_hp()}</span>'
        f"</div>",
        f'<div class="chip"><span class="k">{_chip_icon("san")} SAN</span> '
        f'<span class="v">{inv.get_skill("san", 0)}</span></div>',
        f'<div class="chip"><span class="k">{_chip_icon("monster")} {service.monster.名字}</span> '
        f'<span class="v">HP {service.hp_record["mon"]}/{service.monster.max_hp}</span>'
        f"</div>",
    ]
    if service.gun:
        chips.append(
            f'<div class="chip"><span class="k">{_chip_icon("ammo")} 弹药</span> '
            f'<span class="v">{service.bullet}/{service.max_bullet}</span></div>'
        )
    if service.max_mp > 0:
        chips.append(
            f'<div class="chip"><span class="k">{_chip_icon("mp")} MP</span> '
            f'<span class="v">{service.mp}/{service.max_mp}</span></div>'
        )
    if service.temp_hp > 0:
        chips.append(
            f'<div class="chip"><span class="k">{_chip_icon("temp_hp")} 临时生命</span> '
            f'<span class="v">{service.temp_hp}</span></div>'
        )
    return "".join(chips)


def battle_round_html(service: BattleService, result: tuple) -> str:
    """回合战报卡片 HTML（检定/交锋/疯狂等 md 渲染进卡片，末段回合提示作脚注）。

    标题用 last_actor（刚结算的行动发起者）：玩家主动攻击显示「你的回合」，
    怪物行动显示「怪物回合」——与卡片正文结算的行动一致，而非下一行动者。
    """
    t = data_loader.get_text
    body = md_to_html("\n\n".join(str(x) for x in result[:-1] if x))
    owner_key = (
        "battle.your_turn" if service.last_actor == "inv" else "battle.monster_turn"
    )
    hint = t("battle.turn_line", owner=t(owner_key)).replace("**", "")
    html = _ROUND_TEMPLATE.read_text(encoding="utf-8")
    html = (
        html.replace("__ROUND__", str(service.get_turn_token()))
        .replace("__TITLE__", t(owner_key))
        .replace("__BODY__", body)
        .replace("__STATUS__", round_status_html(service))
        .replace("__HINT__", hint)
    )
    return _inject_theme(html, service)


def battle_open_html(service: BattleService, body_md: str) -> str:
    """开场战报卡片 HTML（md 渲染进卡片，首个 # 标题行由印章区承担）。"""
    parts = body_md.split("\n\n", 1)
    if parts[0].strip().startswith("# "):
        body_md = parts[1] if len(parts) > 1 else ""
    html = _OPEN_TEMPLATE.read_text(encoding="utf-8")
    html = (
        html.replace("__TITLE__", battle_title(service))
        .replace("__DAY__", str(service.investigator.day))
        .replace("__BODY__", md_to_html(body_md))
    )
    return _inject_theme(html, service)


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
            f"{_env_icon('player')} "
            + " ｜ ".join(f"{k} {fmt_bonus(v)}" for k, v in player.items())
        )
    if monster:
        lines.append(
            f"{_env_icon('monster')} "
            + " ｜ ".join(f"{k} {fmt_bonus(v)}" for k, v in monster.items())
        )
    return lines


def _theme_style(service) -> str:
    """主题 CSS 注入：怪物/环境「卡片」字段 → <style> 覆盖串；无字段返回空串。

    优先级：怪物卡片字段 > 环境卡片字段 > 默认（无字段行为逐字节不变）。
    字段：「滤镜」(CSS filter) /「主题色」(强调色) /「暗色」(暗色背景变体) /
          「背景」(自定义 CSS background，如星之彩的彩虹渐变) /
          「白色主题」(纯白圣洁变体：白底+深色文字全套覆盖，如 JK 的教堂圣洁感)。
    """
    env = getattr(service, "environment", None) or {}
    monster = getattr(service, "monster", None)
    card = getattr(monster, "卡片", None) if monster is not None else None
    if not isinstance(card, dict):
        card = {}
    filt = card.get("滤镜") or env.get("滤镜")
    accent = card.get("主题色") or env.get("主题色")
    white = bool(card.get("白色主题") or env.get("白色主题"))
    if white:
        return _white_theme(accent, filt)
    dark = bool(card.get("暗色") or env.get("暗色"))
    bg = card.get("背景") or env.get("背景")
    if not filt and not accent and not dark and not bg:
        return ""
    rules = []
    if filt:
        rules.append(f".card{{filter:{filt}!important}}")
    if bg:
        rules.append(f".card{{background:{bg}!important}}")
    elif dark:
        rules.append(
            ".card{background:linear-gradient(165deg,#0a0806 0%,#120d0a 45%,#080504 100%)!important}"
        )
    if accent:
        rules.append(f".card{{border-color:{accent}!important}}")
        rules.append(
            f".seal,.title,.round{{color:{accent}!important;"
            f"text-shadow:0 0 18px {accent}55!important}}"
        )
        rules.append(
            f".bar{{background:linear-gradient(90deg,transparent,{accent},transparent)!important}}"
        )
    return "<style>" + "".join(rules) + "</style>"


def _white_theme(accent: str, filt: str) -> str:
    """纯白圣洁主题（`白色主题: true`）：底色纯白，圣金点缀，深色文字保证可读。

    灵感：教堂的七彩光晕实为镜面反射所致——于是回归纯白本身。
    覆盖全套模板类（card/seal/title/round/meta/sec/表格/引用/代码/chip/结算块），
    其余怪物/环境无「白色主题」字段时逐字节不变。
    """
    gold = accent or _white_default_accent()
    rules = [
        "body{background:#f4f1ea!important}",
        ".card{background:#ffffff!important;border:1px solid #e8e2d4!important;"
        "box-shadow:0 8px 32px rgba(185,175,150,.20)!important;color:#3f3f3f!important}",
        f".seal,.title,.round{{color:{gold}!important;text-shadow:none!important}}",
        ".title.win{color:#8a7a52!important}.title.dead{color:#b08080!important}",
        ".meta,.hint{color:#8f8a7e!important}",
        f".bar{{background:linear-gradient(90deg,transparent,{gold},transparent)!important}}",
        f".sec{{color:{gold}!important}}",
        "h2{color:#5c5342!important}h3{color:#5c5342!important}",
        ".tbl th{color:#8a7a52!important;border-bottom:1px solid #ece6d8!important}",
        ".tbl td{border-bottom:1px solid #f2eee4!important}",
        ".quote,.ending{background:#faf8f2!important;border-left:3px solid #d9d0b8!important;color:#4a4a4a!important}",
        "p{color:#4a4a4a!important}",
        "code{background:#f4f1e9!important;color:#6b5b3c!important}",
        ".rule{background:linear-gradient(90deg,transparent,#d9d0b8,transparent)!important}",
        ".chip,.stat,.rowline{background:#faf8f2!important;border:1px solid #e2dccb!important}",
        ".chip .k,.stat .k,.rowline .k{color:#8a7a52!important}",
        ".chip .v,.stat .v,.rowline .v{color:#4a4a4a!important}",
    ]
    if filt and filt != "none":
        rules.append(f".card{{filter:{filt}!important}}")
    return "<style>" + "".join(rules) + "</style>"


def _inject_theme(html: str, service) -> str:
    """在 HTML `</body>` 前注入主题 <style>；无字段时原样返回。"""
    style = _theme_style(service)
    return html.replace("</body>", f"{style}</body>") if style else html


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
            f'<div class="effects"><div class="e-title">{_env_effect_icon()} {t2("adventure.env_effect_title")}</div>'
            f"{rows}</div>"
        )

    html = _CARD_TEMPLATE.read_text(encoding="utf-8")
    html = (
        html.replace("__TITLE__", title)
        .replace("__DAY__", str(inv.day))
        .replace("__ANOMALY__", "\n".join(anomaly_lines))
        .replace("__EFFECTS__", effects)
    )
    return _inject_theme(html, service)


def end_card_html(service: BattleService) -> str:
    """结算卡片 HTML。"""
    t = data_loader.get_text
    d = service.get_end_card_data()
    if d["fled"]:
        icon, cls = _battle_end_display("fled")
        title, hint = t("battle.fled_title"), t("battle.fled_hint")
        ending = t("battle.fled_ending")
    elif d["victory"]:
        icon, cls = _battle_end_display("victory")
        title = t("battle.victory_title").replace("## ", "")
        hint = t("battle.victory_hint")
        ending = d["ending"]
    else:
        icon, cls = _battle_end_display("death")
        title = t("battle.death_text", name=service.player_name).replace("## ", "")
        hint = t("battle.death_hint")
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
    html = (
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
    return _inject_theme(html, service)


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
