"""结局卡片系统测试：ending_card_html 主题渲染 / ending_card_payload 达成信息 / 发送回退。

- 每个结局（E01-E10）主题色注入、变体强调色
- 可选块（变体/变体正文/meta/note）为空时不留空 div
- HTML 转义 / 未知结局回退默认圣金主题
- ending_engine 结局结果 dict 带展示 note（不影响判定）
- 冒险插件发送封装：渲染失败返回 False（md 回退契约）/ 渲染成功发出返回 True
"""

import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    Investigator,
    InvestigatorGenerator,
    ending_repo,
    investigator_repo,
)
from src.services.battle_cards import (
    _ENDING_THEMES,
    _ENDING_VARIANT_ACCENT,
    ending_card_html,
)
from src.services.ending_engine import (
    check_san_zero,
    ending_card_payload,
    judge_door_choice,
)


def _make_inv(qq: str, name: str = "CardHero") -> Investigator:
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(qq, name, data)
    return Investigator(inv_model)


def _cleanup(qq: str) -> None:
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)


# --- ending_card_html：主题色 / 变体 / 可选块 / 转义 ---
def test_ending_card_theme_color_injected_per_ending():
    for eid, theme in _ENDING_THEMES.items():
        html = ending_card_html(end_id=eid, name="测试", body="正文")
        assert theme["accent"] in html
        assert theme["icon"] in html


def test_ending_card_variant_accent_override():
    for (eid, variant), accent in _ENDING_VARIANT_ACCENT.items():
        html = ending_card_html(end_id=eid, name="x", variant=variant, body="b")
        assert accent in html
        assert f"class=\"variant-tag\">{variant}<" in html


def test_ending_card_title_contains_id_and_name():
    html = ending_card_html(end_id="E03", name="迷雾散尽·离去", etype="普通", body="正文")
    assert "E03 · 迷雾散尽·离去" in html
    assert "普通" in html


def test_ending_card_optional_blocks_stripped():
    html = ending_card_html(end_id="E03", name="x", body="b")
    assert "class=\"variant-tag\"" not in html
    assert "ending variant" not in html
    assert '<div class="meta">' not in html
    assert '<div class="note">' not in html
    assert "__META__" not in html and "__VBODY__" not in html


def test_ending_card_html_escapes_raw_text():
    html = ending_card_html(
        end_id="E01", name='x"<b>', etype="<i>", variant="<script>", body="<img src=x>", note="&"
    )
    assert "<b>" not in html.split(".card")[0] or "&lt;b&gt;" in html
    assert "&lt;img src=x&gt;" in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html


def test_ending_card_unknown_id_falls_back_to_default_theme():
    html = ending_card_html(end_id="E99", name="未知", body="b")
    assert "E99 · 未知" in html
    assert _ENDING_THEMES["E01"]["accent"] in html  # 默认圣金
    assert "<style>" in html


# --- ending_card_payload：达成信息行 ---
def test_payload_meta_rows_with_inv():
    qq = "test_payload_inv"
    inv = _make_inv(qq)
    inv.set_skill("克苏鲁神话", 30)
    inv.save()
    investigator_repo.add_item_to_inventory(inv._model, "503", 1)
    progress = ending_repo.ensure_progress(qq, inv.day)
    progress.knowledge = 45
    progress.boss36_defeated = True
    progress.mirror_defeated = True
    progress.save()
    try:
        payload = ending_card_payload("E01", variant="清醒合流", inv=inv)
        assert payload["end_id"] == "E01"
        assert payload["name"] == "门扉之邀·合流"
        assert payload["etype"] == "真·真相"
        assert payload["variant"] == "清醒合流"
        assert payload["vbody"]  # 清醒合流变体正文
        rows = dict(payload["meta_rows"])
        assert "知识度" in rows and rows["知识度"] == "45"
        assert "信物" in rows
        assert "周目" in rows
        assert "进度" in rows and "击败守门人" in rows["进度"] and "击败镜中人" in rows["进度"]
    finally:
        _cleanup(qq)


def test_payload_detail_record_no_inv():
    payload = ending_card_payload(
        "E02",
        variant="圣灯",
        record={"id": "E02", "unlocked_at": "2026-08-16 10:00", "run": 3, "variant": "圣灯"},
    )
    rows = dict(payload["meta_rows"])
    assert rows["达成时间"] == "2026-08-16 10:00"
    assert rows["周目"] == "第 3 周目"
    assert "知识度" not in rows and "信物" not in rows
    assert payload["vbody"]  # 圣灯变体正文


def test_payload_empty_fields_default():
    payload = ending_card_payload("E07")
    assert payload["name"] == "墓园拾骨"
    assert payload["variant"] == ""
    assert payload["meta_rows"] == []


# --- ending_engine 结果 dict 带展示 note（纯展示，不影响判定）---
def test_check_san_zero_result_includes_note():
    qq = "test_san_note"
    inv = _make_inv(qq)
    try:
        result = check_san_zero(inv)
        assert result["ending"] == "E06"
        assert result["note"]
        assert result["note"] in result["message"]
    finally:
        _cleanup(qq)


def test_door_result_includes_note():
    qq = "test_door_note"
    inv = _make_inv(qq)
    inv.set_skill("克苏鲁神话", 45)
    inv.day = 40
    inv.save()
    progress = ending_repo.ensure_progress(qq, 40)
    progress.knowledge = 45
    progress.boss36_defeated = True
    progress.save()
    try:
        result = judge_door_choice(inv, "A")
        assert result["ending"] == "E01"
        assert result["note"]
        assert result["note"] in result["message"]
    finally:
        _cleanup(qq)


# --- 冒险插件发送封装：渲染失败 False（md 回退） / 成功 True ---
def test_try_send_ending_card_returns_false_on_render_failure(monkeypatch):
    from src.plugins import adventure as adv_mod

    sent = []

    async def fake_render_pic(html):
        assert isinstance(html, str) and "E01" in html
        return None

    async def fake_send(msg):
        sent.append(msg)

    monkeypatch.setattr(adv_mod, "render_pic", fake_render_pic)
    monkeypatch.setattr(adv_mod, "send_pic", lambda bot, img, send: _never())
    ok = _run_async(
        adv_mod._try_send_ending_card(None, "qq", "", "E01", variant="清醒合流", send=fake_send)
    )
    assert ok is False
    assert sent == []


def test_try_send_ending_card_returns_true_on_render_ok(monkeypatch):
    from src.plugins import adventure as adv_mod

    calls = []

    async def fake_render_pic(html):
        return BytesIO(b"img")

    async def fake_send_pic(bot, img, send):
        calls.append((bot, img, send))
        return True

    monkeypatch.setattr(adv_mod, "render_pic", fake_render_pic)
    monkeypatch.setattr(adv_mod, "send_pic", fake_send_pic)

    async def fake_send(msg):
        raise AssertionError("不应发送文本")

    ok = _run_async(
        adv_mod._try_send_ending_card(None, "qq", "", "E01", variant="清醒合流", send=fake_send)
    )
    assert ok is True
    assert len(calls) == 1


async def _never():
    raise AssertionError("send_pic 不应被调用")


def _run_async(coro):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)
