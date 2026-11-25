"""架构债修复 P0-批次A 专项验证（本地验证用，不入库）：

1. 变体键单源：ending_engine._variant_keys 与 ending._variant_keys 均读
   display_data.json `ending_variant_keys`（唯一源），两处输出一致且与数据一致；
   两处 _variant_text 渲染结果逐字节一致。
2. triggers 单源：reply_data.json `triggers` 段缺失时，get_triggers/get_trigger/
   _triggers_sorted 均回退 data_loader 默认链（guest/gm_room 等插件只读不定义）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import ujson

import nonebot

nonebot.init()

from src.services import ending_engine
from src.services.data_loader import data_loader, _DEFAULT_TRIGGERS
from src.plugins import ending, guest, gm_room, adventure

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


# --- 变体键单源 ---
def test_variant_keys_single_source_engine_matches_plugin_and_data():
    cfg = _load_display()["ending_variant_keys"]
    eng = ending_engine._variant_keys()
    plug = ending._variant_keys()
    assert eng == cfg, "ending_engine 变体键与 display_data.json 不一致"
    assert plug == cfg, "ending 变体键与 display_data.json 不一致"
    assert eng == plug, "ending_engine 与 ending 变体键不一致"


def test_variant_text_byte_identical_across_sources():
    for eid, variants in ending_engine._variant_keys().items():
        for v in variants:
            a = ending_engine._variant_text(eid, v)
            b = ending._variant_text(eid, v)
            assert a == b, f"{eid}/{v} 两处变体正文不一致"


# --- triggers 缺省回退 ---
def _reply_without_triggers() -> dict:
    import copy

    cfg = copy.deepcopy(data_loader.reply_data)
    cfg.pop("triggers", None)
    return cfg


def test_triggers_default_fallback_when_section_missing(monkeypatch):
    monkeypatch.setattr(data_loader, "reply_data", _reply_without_triggers())
    ids = [t["id"] for t in data_loader.get_triggers()]
    assert ids == ["jk", "qiren", "guest", "npc", "gm_room"]
    assert data_loader.get_triggers() == _DEFAULT_TRIGGERS
    jk = data_loader.get_trigger("jk")
    assert jk["id"] == "jk" and jk["kind"] == "hidden" and jk["priority"] == 1
    assert data_loader.get_trigger("no_such") == {}


def test_triggers_sorted_fallback_when_section_missing(monkeypatch):
    monkeypatch.setattr(data_loader, "reply_data", _reply_without_triggers())
    order = [t["id"] for t in adventure._triggers_sorted()]
    assert order == ["jk", "qiren", "guest", "npc", "gm_room"]
    assert [t["priority"] for t in adventure._triggers_sorted()] == [1, 1, 2, 3, 4]


def test_trigger_consumers_defaults_when_section_missing(monkeypatch):
    monkeypatch.setattr(data_loader, "reply_data", _reply_without_triggers())
    t = guest._guest_trigger()
    assert int(t.get("value", 3)) == 3 and t.get("souvenir_id", "310") == "310"
    g = gm_room._gm_trigger()
    assert int(g.get("value", 1)) == 1
    cfg = adventure._trigger_by_id("gm_room")
    assert cfg.get("after_monster") is True


def test_triggers_real_section_unchanged():
    """带真实 triggers 段时 get_triggers 返回数据源本身，非缺省。"""
    ids = [t["id"] for t in data_loader.get_triggers()]
    assert ids == ["jk", "qiren", "guest", "npc", "gm_room"]
    assert len(data_loader.get_triggers()) == 5
