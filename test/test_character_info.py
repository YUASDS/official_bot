import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.services.character_cards import SKILL_NAMES, info_card_html
from src.services.character_info import build_info_message

SKILL_NAMES_SORTED = ["格斗", "闪避", "侦查", "聆听", "手枪", "步枪", "急救", "医学"]


class FakeBot:
    type = "Console"


def test_info_text_shows_skills():
    investigator_repo.delete_by_qq("test_info_skill_text")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_info_skill_text", "SkillHero", data
    )
    inv = Investigator(inv_model)
    inv.set_skill("格斗", 66)
    inv.set_skill("手枪", 55)
    inv.save()

    msg = asyncio.run(build_info_message("test_info_skill_text", FakeBot()))
    text = str(msg)
    for name in SKILL_NAMES_SORTED:
        assert name in text
    assert "66" in text
    assert "55" in text
    investigator_repo.delete_by_qq("test_info_skill_text")


def test_info_card_html_shows_skills():
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(
        "test_info_skill_card", "SkillCardHero", data
    )
    inv = Investigator(inv_model)
    inv.set_skill("侦查", 44)
    inv.save()

    html = info_card_html(inv, gold=0)
    for name in SKILL_NAMES_SORTED:
        assert name in html
    assert "__SKILLS__" not in html  # 占位符已替换
    assert "44" in html
    investigator_repo.delete_by_qq("test_info_skill_card")


def test_info_text_shows_dream_fragments():
    """碎片展示迁移：调查员信息不再显示碎片行（账号级数据）；/个人信息 有碎片才显示。"""
    from src.models.player import ending_repo
    from src.plugins.profile import handle_profile

    investigator_repo.delete_by_qq("test_info_dream")
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    investigator_repo.create_and_save("test_info_dream", "DreamHero", data)

    # 清零账号级碎片计数（ending_collection 跨角色保留，需显式重置）
    coll = ending_repo.ensure_collection("test_info_dream")
    coll.dream_fragments = 0
    coll.save()

    # 调查员信息：不再显示碎片行
    msg = asyncio.run(build_info_message("test_info_dream", FakeBot()))
    assert "梦之碎片" not in str(msg)

    # 无碎片时 /个人信息 不显示碎片行
    assert "梦之碎片" not in _profile_text("test_info_dream")

    # 发放 2 枚后：调查员信息仍不显示；/个人信息 显示
    ending_repo.add_dream_fragment("test_info_dream", 2)
    msg = asyncio.run(build_info_message("test_info_dream", FakeBot()))
    assert "梦之碎片" not in str(msg)
    assert "梦之碎片：2" in _profile_text("test_info_dream")

    # 还原碎片计数，避免污染其他测试
    coll = ending_repo.ensure_collection("test_info_dream")
    coll.dream_fragments = 0
    coll.save()
    investigator_repo.delete_by_qq("test_info_dream")


def _profile_text(user_id: str) -> str:
    """构造 /个人信息 的文本（复用 handle_profile 的拼接逻辑，不真正发消息）。"""
    from src.models.player import ending_repo
    from src.services.data_loader import data_loader
    from src.services.stat_render import career_section

    parts = [f"你的个人ID：`{user_id}`"]
    career = career_section(user_id)
    if career:
        parts.append(career)
    dream = ending_repo.get_dream_fragments(user_id)
    if dream > 0:
        parts.append(data_loader.get_text("dream_fragment.profile_line", count=dream))
    return "\n\n".join(parts)
