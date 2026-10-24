"""批次 5「展示配置化」专项测试：display_data.json 读取 / 映射正确性 / 缺失键回退。

覆盖：
1. display_data.json 结构完整（version/ending_themes/ending_variant_accents/
   ending_variant_keys/item/stat/battle_end/battle_chips/battle_env_icons/defaults）；
2. 各映射与数据文件一致（结局主题/变体强调色/物品标签/门扉标签/变体文本键/结算图标）；
3. 缺失键回退现状（get_display_config 返回空 dict 时，各取值函数回退硬编码默认值）。

只测新增行为，不修改 src/。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import ujson

import nonebot

nonebot.init()

from src.services import battle_cards as bc
from src.services import stat_render
from src.plugins import character, ending
from src.services.battle_cards import get_display_config

_DISPLAY_FILE = (
    Path(__file__).parent.parent
    / "plugins"
    / "StoryTeller"
    / "data"
    / "display_data.json"
)


def _load_display() -> dict:
    with open(_DISPLAY_FILE, encoding="utf-8-sig") as f:
        return ujson.load(f)


# --- D1：display_data.json 结构完整 ---
def test_display_data_file_exists_and_valid():
    cfg = _load_display()
    assert cfg.get("version") == 1
    for section in (
        "ending_themes",
        "ending_variant_accents",
        "ending_defaults",
        "ending_variant_keys",
        "item",
        "stat",
        "battle_end",
        "battle_chips",
        "battle_env_icons",
        "defaults",
    ):
        assert section in cfg, f"display_data.json 缺少段 {section}"


# --- D2：结局主题/变体映射与数据一致 ---
def test_ending_themes_match_config():
    cfg = _load_display()
    themes = cfg["ending_themes"]
    assert set(themes) == set(bc._ENDING_THEMES)
    required_keys = {
        "icon", "accent", "border", "bg_top", "bg_mid",
        "bg_bottom", "text", "text_soft", "text_dim",
    }
    for eid, theme in bc._ENDING_THEMES.items():
        assert set(theme) == required_keys
        assert bc._ENDING_THEMES[eid]["accent"] == themes[eid]["accent"]
        assert bc._ENDING_THEMES[eid]["icon"] == themes[eid]["icon"]


def test_ending_variant_accents_match_config():
    cfg = _load_display()
    for key, accent in cfg["ending_variant_accents"].items():
        eid, variant = key.split(".", 1)
        assert bc._ENDING_VARIANT_ACCENT[(eid, variant)] == accent
    assert len(bc._ENDING_VARIANT_ACCENT) == len(cfg["ending_variant_accents"])


def test_ending_default_icon_from_config():
    assert bc._ending_default_icon() == "🌌"


def test_ending_card_html_uses_theme_and_default():
    html = bc.ending_card_html(end_id="E01", name="测试", body="正文")
    assert bc._ENDING_THEMES["E01"]["accent"] in html
    assert "🌌" in html
    unknown = bc.ending_card_html(end_id="E99", name="未知", body="b")
    assert bc._ENDING_THEMES["E01"]["accent"] in unknown  # 回退默认圣金


# --- D3：物品标签 / 门扉标签 / 变体文本键 ---
def test_item_labels_from_config():
    cfg = _load_display()["item"]
    type_labels = character._item_type_labels()
    part_labels = character._item_part_labels()
    assert type_labels["spell_scroll"] == cfg["type_labels"]["spell_scroll"]
    assert type_labels["misc"] == "道具"
    assert part_labels["近战"] == cfg["part_labels"]["近战"]
    assert part_labels["远程"] == "远程"
    # 未知类型原样回退
    assert type_labels.get("no_such_type", "no_such_type") == "no_such_type"


def test_stat_config_from_display_data():
    assert stat_render.TOTAL_ENDINGS == _load_display()["stat"]["total_endings"] == 12
    assert stat_render._DOOR_LABELS["E01"] == "A门·合流"
    # E10 挂机结局已移除，不再显示门扉标签
    assert "E10" not in stat_render._DOOR_LABELS
    assert len(stat_render._DOOR_LABELS) == 9
    # 未知值/空值回退
    assert stat_render._door_label("E99") == "E99"
    assert stat_render._door_label("") == "未达门扉"


def test_variant_keys_from_config():
    cfg = _load_display()["ending_variant_keys"]
    keys = ending._variant_keys()
    assert keys["E01"]["清醒合流"] == cfg["E01"]["清醒合流"]
    assert keys["E01"]["崩溃合流"] == "variant_collapse"
    assert keys["E02"]["圣灯"] == "variant_light"
    assert keys["E05"]["星光变体"] == "variant_light"
    assert ending._variant_text("E01", "清醒合流")  # 命中 text_data 变体正文


# --- D4：结算卡片图标 / chip / env 图标 ---
def test_battle_end_display_from_config():
    assert bc._battle_end_display("fled") == ("🏃", "win")
    assert bc._battle_end_display("victory") == ("🏆", "win")
    assert bc._battle_end_display("death") == ("💀", "dead")


def test_chip_and_env_icons_from_config():
    assert bc._chip_icon("inv") == "🧑‍🎤"
    assert bc._chip_icon("san") == "🧠"
    assert bc._chip_icon("monster") == "👾"
    assert bc._env_icon("player") == "🧑‍🎤"
    assert bc._env_icon("monster") == "👾"
    assert bc._env_effect_icon() == "⚙️"
    lines = bc.env_effects_lines({"玩家": {"射击": -20}})
    assert lines and lines[0].startswith("🧑‍🎤")


def test_round_status_html_uses_chip_icons():
    class _Inv:
        name = "Hero"
        def get_max_hp(self): return 20
        def get_skill(self, key, default): return 50

    class _Mon:
        名字 = "修格斯"
        max_hp = 30

    class _Svc:
        investigator = _Inv()
        monster = _Mon()
        hp_record = {"inv": 20, "mon": 30}
        gun = False
        max_mp = 0
        mp = 0
        temp_hp = 0

    html = bc.round_status_html(_Svc())
    assert "🧑‍🎤 Hero" in html
    assert "🧠 SAN" in html
    assert "👾 修格斯" in html


# --- D5：缺失键回退（模拟 display_data 缺失） ---
def test_ending_default_icon_fallback_when_config_empty(monkeypatch):
    monkeypatch.setattr(bc, "get_display_config", lambda: {})
    assert bc._ending_default_icon() == "🌌"


def test_battle_end_fallback_when_config_empty(monkeypatch):
    monkeypatch.setattr(bc, "get_display_config", lambda: {})
    assert bc._battle_end_display("fled") == ("🏃", "win")
    assert bc._battle_end_display("victory") == ("🏆", "win")
    assert bc._battle_end_display("death") == ("💀", "dead")


def test_chip_env_fallback_when_config_empty(monkeypatch):
    monkeypatch.setattr(bc, "get_display_config", lambda: {})
    assert bc._chip_icon("inv") == "🧑‍🎤"
    assert bc._chip_icon("mp") == "🔮"
    assert bc._env_icon("monster") == "👾"
    assert bc._env_effect_icon() == "⚙️"
    assert bc._white_default_accent() == "#8a7a52"


def test_item_labels_fallback_when_config_empty(monkeypatch):
    monkeypatch.setattr(character, "get_display_config", lambda: {})
    labels = character._item_type_labels()
    assert labels["spell_scroll"] == "法术残卷"
    assert labels["武器"] == "武器"
    parts = character._item_part_labels()
    assert parts["防具"] == "防具"
    assert parts["misc"] == "道具"


def test_variant_keys_fallback_when_config_empty(monkeypatch):
    """变体键映射缺配置回退：_variant_keys 单源（ending_engine），patch 读取入口验证。"""
    monkeypatch.setattr(bc, "get_display_config", lambda: {})
    keys = ending._variant_keys()
    assert keys["E01"]["清醒合流"] == "variant_clear"
    assert keys["E05"]["星光变体"] == "variant_light"


# --- D6：theme 数据可被配置覆盖（新增结局主题 = 纯数据） ---
def test_theme_override_and_new_ending(monkeypatch):
    """配置新增/覆盖结局主题即可生效：新增 E14 主题、覆盖 E01 accent。"""
    cfg = _load_display()
    patched = dict(cfg["ending_themes"])
    patched["E01"] = dict(patched["E01"], accent="#123456")
    patched["E14"] = {
        "icon": "🧪", "accent": "#111111", "border": "#222222",
        "bg_top": "#000000", "bg_mid": "#111111", "bg_bottom": "#000000",
        "text": "#ffffff", "text_soft": "#cccccc", "text_dim": "#888888",
    }
    merged = bc._merge_ending_themes({"ending_themes": patched})
    assert merged["E01"]["accent"] == "#123456"
    assert merged["E01"]["icon"] == "🌌"  # 未覆盖子键回退默认
    assert merged["E14"]["icon"] == "🧪"
    assert merged["E14"]["accent"] == "#111111"
    assert len(merged) == 14  # 10 默认 + E11/E12/E13 真实 + 1 新增

    monkeypatch.setattr(bc, "_ENDING_THEMES", merged)
    html = bc.ending_card_html(end_id="E14", name="新", body="b")
    assert "#111111" in html and "🧪" in html
    html_e01 = bc.ending_card_html(end_id="E01", name="旧", body="b")
    assert "#123456" in html_e01


def test_theme_partial_override_keeps_other_keys():
    """仅配置 accent 时其余子键（icon/border/bg/text）保持默认，逐字节不变。"""
    cfg = _load_display()["ending_themes"]["E03"]
    partial = {"E03": {"accent": "#abcdef"}}
    merged = bc._merge_ending_themes({"ending_themes": partial})
    assert merged["E03"]["accent"] == "#abcdef"
    assert merged["E03"]["icon"] == cfg["icon"]
    assert merged["E03"]["border"] == cfg["border"]
    assert merged["E03"]["bg_top"] == cfg["bg_top"]
