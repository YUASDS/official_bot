"""卡片主题滤镜测试：怪物/环境「卡片」字段 → battle_cards 注入 <style>。

- 带字段的怪（如 23 星之彩碎片）渲染 HTML 含注入的主题 <style>
- 无字段的怪（如 1 格拉基之仆从）行为逐字节不变（无注入）
- 优先级：怪物卡片字段 > 环境卡片字段 > 默认
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.models.monster import Monster
from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.services.battle import BattleService
from src.services.battle_cards import (
    _inject_theme,
    _theme_style,
    battle_card_html,
    battle_open_html,
    battle_round_html,
    end_card_html,
)
from src.services.data_loader import data_loader


def _service(monster_id: str, qq: str, name: str) -> BattleService:
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(qq, name, data)
    inv = Investigator(inv_model)
    bs = BattleService(inv, Monster(monster_id))
    return bs


def _cleanup(qq: str) -> None:
    investigator_repo.delete_by_qq(qq)


def test_theme_style_empty_for_monster_without_card():
    bs = _service("1", "test_theme_empty", "EmptyHero")
    try:
        assert _theme_style(bs) == ""
    finally:
        _cleanup("test_theme_empty")


def test_theme_style_empty_without_service_fields():
    assert _theme_style(_FakeService({}, None)) == ""


class _FakeService:
    def __init__(self, environment, monster):
        self.environment = environment
        self.monster = monster


def test_theme_style_rainbow_for_monster_23():
    bs = _service("23", "test_theme_23", "StarHero")
    try:
        style = _theme_style(bs)
        assert "<style>" in style
        assert "linear-gradient" in style          # 星之彩彩虹渐变背景
        assert "#a8dce8" in style                  # 冰蓝主题色
        assert "filter:saturate(1.25)" in style    # 滤镜
    finally:
        _cleanup("test_theme_23")


def test_theme_style_dark_filter_for_monster_38():
    bs = _service("38", "test_theme_38", "QiHero")
    try:
        style = _theme_style(bs)
        assert "filter:brightness(0.82)" in style
        assert "#0a0806" in style                  # 暗色背景
        assert "#c9d4e0" in style                  # 月光银主题色
    finally:
        _cleanup("test_theme_38")


def test_theme_style_environment_black_moon():
    bs = _service("1", "test_theme_env", "EnvHero")
    env = data_loader.environment_data["庄园.黑色满月"].copy()
    env["name"] = "庄园.黑色满月"
    bs.set_environment(env)
    try:
        style = _theme_style(bs)
        assert "hue-rotate(235deg)" in style
        assert "#8a7ac0" in style
        assert "#0a0806" in style
    finally:
        _cleanup("test_theme_env")


def test_theme_priority_monster_over_environment():
    """怪物卡片字段 > 环境卡片字段：同 key 冲突时取怪物值。"""
    env = {"滤镜": "env-filter()", "主题色": "#111111", "暗色": True}
    card = {"滤镜": "monster-filter()", "主题色": "#222222", "暗色": False}
    monster = _MonsterFake(card)
    service = _FakeService(env, monster)
    style = _theme_style(service)
    assert "monster-filter()" in style
    assert "env-filter()" not in style
    assert "#222222" in style
    assert "#111111" not in style


class _MonsterFake:
    def __init__(self, card):
        self._card = card

    @property
    def 卡片(self):
        return self._card


def test_inject_theme_returns_html_unchanged_without_fields():
    html = "<html><body><div class='card'>x</div></body></html>"
    assert _inject_theme(html, _FakeService({}, None)) == html


def test_inject_theme_inserts_style_before_body_close():
    html = "<html><body><div class='card'>x</div></body></html>"
    monster = _MonsterFake({"主题色": "#ff0000"})
    out = _inject_theme(html, _FakeService({}, monster))
    assert out.endswith("</body></html>")
    assert out.index("<style>") < out.index("</body>")
    assert "#ff0000" in out


def test_battle_card_html_injects_for_monster_23():
    bs = _service("23", "test_theme_card23", "CardStar")
    try:
        html = battle_card_html(bs, day_event="")
        assert "linear-gradient(165deg,#9b5bd8" in html
        assert "#a8dce8" in html
    finally:
        _cleanup("test_theme_card23")


def test_battle_card_html_unchanged_for_monster_1():
    bs = _service("1", "test_theme_card1", "CardPlain")
    try:
        html = battle_card_html(bs, day_event="")
        assert "<style>" in html                    # 模板自带 style
        assert "linear-gradient(165deg,#9b5bd8" not in html
        assert "filter:" not in html
        assert html.count("</style>") == 1          # 无额外注入
    finally:
        _cleanup("test_theme_card1")


def test_battle_round_and_open_and_end_inject():
    bs = _service("23", "test_theme_multi", "MultiStar")
    try:
        open_html = battle_open_html(bs, "# 遭遇\n\n星之彩的辉光。")
        assert "linear-gradient(165deg,#9b5bd8" in open_html

        round_html = battle_round_html(bs, ("玩家回合", "检定成功"))
        assert "linear-gradient(165deg,#9b5bd8" in round_html

        bs.hp_record["mon"] = 0
        end_html = end_card_html(bs)
        assert "linear-gradient(165deg,#9b5bd8" in end_html
    finally:
        _cleanup("test_theme_multi")


def test_end_card_no_inject_for_monster_without_card():
    bs = _service("1", "test_theme_end1", "EndPlain")
    try:
        bs.hp_record["mon"] = 0
        html = end_card_html(bs)
        assert html.count("</style>") == 1
    finally:
        _cleanup("test_theme_end1")
