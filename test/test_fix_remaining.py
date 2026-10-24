import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.models.item import Equipment
from src.models.monster import Monster
from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
from src.services.battle import BattleService
from src.services.character_cards import info_card_html
from src.services.character_info import info_kb_msg
from src.services.combat_messaging import send_event_skip_battle
from src.services.dice_roller import PenaltyDiceRoll, SuccessLevel


class FakeBot:
    type = "Console"


def _make_inv(qq: str, name: str) -> tuple[Investigator, object]:
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(qq, name, data)
    return Investigator(inv_model), inv_model


def _make_battle(qq: str, name: str) -> tuple[BattleService, object]:
    inv, inv_model = _make_inv(qq, name)
    m = Monster.load_random_for_day(1)
    assert m is not None
    return BattleService(inv, m), inv_model


# --- 1. 奇遇跳过战斗：图片优先，失败回退 md ---
def test_event_skip_battle_falls_back_to_text():
    import src.services.combat_messaging as cm
    from src.services.data_loader import data_loader

    bs, inv_model = _make_battle("test_skip_md", "SkipMd")
    captured: list[str] = []

    async def send(msg) -> None:
        captured.append(str(msg))

    async def finish(msg) -> None:
        captured.append("FIN:" + str(msg))

    async def _no_render(html):
        return None

    cm.render_pic = _no_render
    asyncio.run(send_event_skip_battle(bs, "事件回复", FakeBot(), send, finish))

    text = "\n".join(captured)
    assert text.startswith("FIN:")
    assert "事件回复" in text
    assert data_loader.get_text("adventure.event_skip_battle") in text
    investigator_repo.delete_by_qq("test_skip_md")


def test_event_skip_battle_sends_image_first():
    import src.services.combat_messaging as cm

    bs, inv_model = _make_battle("test_skip_img", "SkipImg")
    captured: list[str] = []

    async def send(msg) -> None:
        captured.append(str(msg))

    async def finish(msg) -> None:
        captured.append("FIN:" + str(msg))

    async def _render(html):
        return b"img"

    async def _pic(bot, img, send):
        await send("IMG")
        return True

    cm.render_pic = _render
    cm.send_pic = _pic
    asyncio.run(send_event_skip_battle(bs, "事件回复", FakeBot(), send, finish))

    assert captured == ["IMG"]  # 图片路径成功则不再发送文本
    investigator_repo.delete_by_qq("test_skip_img")


# --- 2. 闪避：怪物失败绝不命中；双方失败不显示「闪避成功」 ---
def _dodge(bs, monster_action, level1, level2):
    return bs._resolve_dodge(SimpleNamespace(level1=level1, level2=level2), monster_action)


def _monster_action(bs, damage="5"):
    bs.monster._data["攻击"] = {
        "a": {"skill": 50, "damage": damage, "counterattack": "反击", "attack_false": "落空"}
    }
    bs.monster._data["装甲"] = 0
    return bs.monster.get_action("mon")


def test_dodge_critfail_monster_fail_does_not_hit():
    bs, inv_model = _make_battle("test_dodge_1", "Dodge1")
    action = _monster_action(bs)
    hp_before = bs.hp_record["inv"]
    monster_text, player_text = _dodge(bs, action, level1=0, level2=-1)
    assert monster_text == "落空"  # 怪物失败 → 不命中
    assert bs.hp_record["inv"] == hp_before
    investigator_repo.delete_by_qq("test_dodge_1")


def test_dodge_both_fail_not_shown_as_success():
    bs, inv_model = _make_battle("test_dodge_2", "Dodge2")
    action = _monster_action(bs)
    monster_text, player_text = _dodge(bs, action, level1=0, level2=0)
    assert monster_text == "落空"
    assert player_text == ""
    investigator_repo.delete_by_qq("test_dodge_2")


def test_dodge_both_critfail_miss():
    bs, inv_model = _make_battle("test_dodge_3", "Dodge3")
    action = _monster_action(bs)
    monster_text, player_text = _dodge(bs, action, level1=-1, level2=-1)
    assert monster_text == "落空"
    assert player_text == ""
    investigator_repo.delete_by_qq("test_dodge_3")


def test_dodge_player_fail_monster_success_hits():
    bs, inv_model = _make_battle("test_dodge_4", "Dodge4")
    action = _monster_action(bs)
    hp_before = bs.hp_record["inv"]
    monster_text, player_text = _dodge(bs, action, level1=1, level2=0)
    assert bs.hp_record["inv"] == hp_before - 5
    investigator_repo.delete_by_qq("test_dodge_4")


def test_dodge_success_avoids_damage():
    bs, inv_model = _make_battle("test_dodge_5", "Dodge5")
    action = _monster_action(bs)
    hp_before = bs.hp_record["inv"]
    monster_text, player_text = _dodge(bs, action, level1=1, level2=1)
    assert bs.hp_record["inv"] == hp_before
    assert player_text != ""
    investigator_repo.delete_by_qq("test_dodge_5")


def test_dodge_normal_success_cannot_avoid_critical_attack():
    """普通闪避不能闪掉怪物大成功攻击（等级比较）。"""
    bs, inv_model = _make_battle("test_dodge_6", "Dodge6")
    action = _monster_action(bs)
    hp_before = bs.hp_record["inv"]
    monster_text, player_text = _dodge(bs, action, level1=4, level2=1)
    assert bs.hp_record["inv"] == hp_before - 5  # 大成功攻击命中
    assert monster_text != ""  # 怪物命中文案
    investigator_repo.delete_by_qq("test_dodge_6")


def test_dodge_critical_success_avoids_critical_attack():
    """闪避大成功可闪掉怪物大成功攻击（同等级）。"""
    bs, inv_model = _make_battle("test_dodge_7", "Dodge7")
    action = _monster_action(bs)
    hp_before = bs.hp_record["inv"]
    monster_text, player_text = _dodge(bs, action, level1=4, level2=4)
    assert bs.hp_record["inv"] == hp_before
    assert player_text != ""
    investigator_repo.delete_by_qq("test_dodge_7")


def test_dodge_extreme_success_avoids_hard_attack():
    """更高等级闪避可闪掉较低等级攻击。"""
    bs, inv_model = _make_battle("test_dodge_8", "Dodge8")
    action = _monster_action(bs)
    hp_before = bs.hp_record["inv"]
    monster_text, player_text = _dodge(bs, action, level1=2, level2=3)
    assert bs.hp_record["inv"] == hp_before
    assert player_text != ""
    investigator_repo.delete_by_qq("test_dodge_8")


# --- 4. 三连射成功记录成长技能 ---
def test_multi_shot_records_growth_skill():
    import random

    inv, inv_model = _make_inv("test_multishot_growth", "MultiShot")
    investigator_repo.add_item_to_inventory(inv_model, "1", 1)  # PPK 手枪
    investigator_repo.equip_item("test_multishot_growth", "1")
    inv.update_equipment()

    bs, _ = _make_battle("test_multishot_growth2", "MultiShot2")
    bs.investigator = inv
    bs._update_gun_status()
    assert bs.gun is not None

    hit = None
    for seed in range(500):
        random.seed(seed)
        r = PenaltyDiceRoll(90)
        if r.level > SuccessLevel.FAILURE:
            hit = seed
            break
    assert hit is not None

    random.seed(hit)
    bs._multiple_shot(3, bs.gun, 90)
    assert "手枪" in bs.succeded_skill  # 成功射击参与成长鉴定
    investigator_repo.delete_by_qq("test_multishot_growth")
    investigator_repo.delete_by_qq("test_multishot_growth2")


# --- 5. 三连射伤害格式与单发一致（expr=value）---
def test_multi_shot_damage_format_unified():
    import random

    inv, inv_model = _make_inv("test_multishot_fmt", "MultiShotFmt")
    investigator_repo.add_item_to_inventory(inv_model, "1", 1)
    investigator_repo.equip_item("test_multishot_fmt", "1")
    inv.update_equipment()

    bs, _ = _make_battle("test_multishot_fmt2", "MultiShotFmt2")
    bs.investigator = inv
    bs._update_gun_status()

    hit = None
    for seed in range(500):
        random.seed(seed)
        r = PenaltyDiceRoll(90)
        if r.level > SuccessLevel.FAILURE:
            hit = seed
            break

    random.seed(hit)
    result = bs._multiple_shot(3, bs.gun, 90)
    text = "\n".join(str(x) for x in result)
    assert re.search(r"1d8=\d+=\d+", text)  # 与单发 $骰子=$伤害 格式一致
    investigator_repo.delete_by_qq("test_multishot_fmt")
    investigator_repo.delete_by_qq("test_multishot_fmt2")


# --- 6. 施法进入行动提示与按钮 ---
def test_spell_actions_in_available_and_prompt():
    inv, _ = _make_inv("test_spell_actions", "SpellActions")
    inv.add_spell("s001")
    inv.save()

    bs, inv_model = _make_battle("test_spell_actions2", "SpellActions2")
    bs.investigator = inv

    actions = bs.get_available_actions_for_turn()
    assert "施法s001" in actions

    section = bs.get_action_section()
    assert "施法s001" in section
    assert "MP" in section  # 附名称与 MP 消耗

    prompt = bs._get_next_turn_prompt()  # 回合提示不丢失法术
    assert "施法s001" in prompt
    investigator_repo.delete_by_qq("test_spell_actions")
    investigator_repo.delete_by_qq("test_spell_actions2")


def test_spell_hint_not_shown_on_monster_turn():
    """怪物回合（反击/闪避）：行动小节不显示施法提示。"""
    inv, _ = _make_inv("test_spell_mon", "SpellMon")
    inv.add_spell("s001")
    inv.save()

    bs, inv_model = _make_battle("test_spell_mon2", "SpellMon2")
    bs.investigator = inv

    bs.current_turn = "mon"
    bs.available_actions = bs._player_actions()  # 刷新回合行动缓存
    assert "施法s001" not in bs.get_available_actions_for_turn()  # 按钮无施法
    section = bs.get_action_section()
    assert "施法s001" not in section  # 行动小节无施法泄漏
    assert "反击" in section
    assert "闪避" in section

    bs.current_turn = "inv"
    bs.available_actions = bs._player_actions()
    assert "施法s001" in bs.get_action_section()  # 玩家回合仍显示
    investigator_repo.delete_by_qq("test_spell_mon")
    investigator_repo.delete_by_qq("test_spell_mon2")


# --- 7. 校验失败不消耗回合 ---
def test_unlearned_spell_does_not_end_turn():
    bs, inv_model = _make_battle("test_turn_spell", "TurnSpell")
    token_before = bs.get_turn_token()
    turn_before = bs.current_turn

    result = bs._cast_spell("s999")  # 未学会
    assert len(result) == 1
    assert bs.get_turn_token() == token_before
    assert bs.current_turn == turn_before

    result = bs._cast_spell("s001")  # 未学会的真实法术 ID
    assert len(result) == 1
    assert bs.get_turn_token() == token_before
    investigator_repo.delete_by_qq("test_turn_spell")


def test_melee_without_weapon_does_not_end_turn():
    inv, _ = _make_inv("test_turn_melee", "TurnMelee")
    inv._equipped = {}  # 无近战武器
    bs, inv_model = _make_battle("test_turn_melee2", "TurnMelee2")
    bs.investigator = inv

    token_before = bs.get_turn_token()
    result = bs._melee_attack()
    assert len(result) == 1
    assert bs.get_turn_token() == token_before
    investigator_repo.delete_by_qq("test_turn_melee")
    investigator_repo.delete_by_qq("test_turn_melee2")


# --- 8. 背包完整展示（战利品可见）---
def test_info_card_shows_all_bag_items():
    inv, inv_model = _make_inv("test_bag_card", "BagCard")
    for item_id in range(102, 110):  # 8 件物品（超出原 6 件上限）
        investigator_repo.add_item_to_inventory(inv_model, str(item_id), 1)

    html = info_card_html(inv, gold=0)
    for item_id in range(102, 110):
        assert Equipment(str(item_id)).name in html
    investigator_repo.delete_by_qq("test_bag_card")


def test_info_kb_msg_has_all_use_tags():
    inv, inv_model = _make_inv("test_bag_tags", "BagTags")
    for item_id in range(102, 110):
        investigator_repo.add_item_to_inventory(inv_model, str(item_id), 1)

    msg = info_kb_msg("test_bag_tags", FakeBot())
    # 指令标签为 urlencode 形式（%2F使用物品 108）
    assert "%2F%E4%BD%BF%E7%94%A8%E7%89%A9%E5%93%81%20108" in str(msg)
    investigator_repo.delete_by_qq("test_bag_tags")


# --- 9. 武器损毁不重复发放弹簧折刀 ---
def test_break_weapon_no_duplicate_knife():
    inv, inv_model = _make_inv("test_break_knife", "BreakKnife")
    investigator_repo.add_item_to_inventory(inv_model, "102", 1)
    investigator_repo.equip_item("test_break_knife", "102")
    inv.update_equipment()

    bs, inv_model2 = _make_battle("test_break_knife2", "BreakKnife2")
    bs.investigator = inv
    bs._break_weapon("格斗")  # 战斗内实际调用路径（current_action）

    equipments, _ = inv.get_equipments()
    assert equipments.get("101") == 1  # 不重复补发
    assert inv.get_equipped_id("近战") == "101"  # 回退装备已有折刀
    investigator_repo.delete_by_qq("test_break_knife")
    investigator_repo.delete_by_qq("test_break_knife2")


def test_dodge_fumble_does_not_break_weapon():
    """闪避大失败不掉武器（设计行为）。"""
    inv, inv_model = _make_inv("test_dodge_break", "DodgeBreak")
    investigator_repo.add_item_to_inventory(inv_model, "102", 1)
    investigator_repo.equip_item("test_dodge_break", "102")
    inv.update_equipment()

    bs, inv_model2 = _make_battle("test_dodge_break2", "DodgeBreak2")
    bs.investigator = inv
    text = bs._handle_dodge_fumble()
    assert text != ""  # 叙事仍展示
    assert inv.get_equipped_id("近战") == "102"  # 武器未掉落
    investigator_repo.delete_by_qq("test_dodge_break")
    investigator_repo.delete_by_qq("test_dodge_break2")


# --- 10. 每日已完成：购买冒险卷并开始冒险选项 ---
def test_daily_done_msg_has_scroll_buy_option():
    from src.plugins.adventure import _daily_done_msg, _scroll_price

    assert _scroll_price() > 0
    msg = str(_daily_done_msg("test_daily_done", FakeBot()))
    assert "今日冒险已完成" in msg
    assert "购买冒险卷" in msg
    assert str(_scroll_price()) in msg  # 显示价格


# --- 11. 子弹不足：图片路径不吞消息 ---
def test_no_ammo_message_not_swallowed_in_image_mode():
    import src.services.combat_messaging as cm

    inv, inv_model = _make_inv("test_no_ammo", "NoAmmo")
    investigator_repo.add_item_to_inventory(inv_model, "1", 1)
    investigator_repo.equip_item("test_no_ammo", "1")
    inv.update_equipment()

    bs, _ = _make_battle("test_no_ammo2", "NoAmmo2")
    bs.investigator = inv
    bs._update_gun_status()
    bs.bullet = 0

    captured: list[str] = []

    async def send(msg) -> None:
        captured.append(str(msg))

    async def _render(html):
        return b"img"

    async def _pic(bot, img, send):
        await send("IMG")
        return True

    cm.render_pic = _render
    cm.send_pic = _pic
    result = bs._ranged_attack(1)
    asyncio.run(cm.send_combat_result(bs, FakeBot(), result, send))

    assert "弹药不足" in "\n".join(captured)  # 图片模式仍展示提示
    assert bs.get_turn_token() == 0  # 不消耗回合
    investigator_repo.delete_by_qq("test_no_ammo")
    investigator_repo.delete_by_qq("test_no_ammo2")


# --- 12. 枪械 96-99 卡壳、100 才损毁 ---
def _find_dice_seed(target: int, skill: int = 20) -> int:
    import random

    from src.services.dice_roller import DiceRoll

    for seed in range(5000):
        random.seed(seed)
        if DiceRoll(skill).dice == target:
            return seed
    raise AssertionError(f"未找到掷出 {target} 的种子")


def test_gun_jam_96_99_does_not_break():
    import random

    inv, inv_model = _make_inv("test_jam", "Jam")
    investigator_repo.add_item_to_inventory(inv_model, "1", 1)
    investigator_repo.equip_item("test_jam", "1")
    inv.update_equipment()

    bs, _ = _make_battle("test_jam2", "Jam2")
    bs.investigator = inv
    bs._update_gun_status()
    assert bs.gun is not None

    seed = _find_dice_seed(97)
    random.seed(seed)
    result = bs._single_shot(bs.gun, 20)
    text = "\n".join(str(x) for x in result)
    assert "卡壳" in text  # 卡壳文案
    assert inv.get_equipped_id("远程") == "1"  # 枪械未损毁
    assert bs.bullet == 0  # 弹夹子弹清零
    investigator_repo.delete_by_qq("test_jam")
    investigator_repo.delete_by_qq("test_jam2")


def test_multi_shot_jam_clears_magazine_and_interrupts():
    """连射中卡壳：弹夹清零、中断后续射击、枪械不损毁。"""
    import random

    from src.services.dice_roller import PenaltyDiceRoll

    inv, inv_model = _make_inv("test_jam_multi", "JamMulti")
    investigator_repo.add_item_to_inventory(inv_model, "1", 1)
    investigator_repo.equip_item("test_jam_multi", "1")
    inv.update_equipment()

    bs, _ = _make_battle("test_jam_multi2", "JamMulti2")
    bs.investigator = inv
    bs._update_gun_status()
    assert bs.gun is not None

    seed = None
    for s in range(5000):
        random.seed(s)
        if PenaltyDiceRoll(20).final_result == 97:
            seed = s
            break
    assert seed is not None

    random.seed(seed)
    result = bs._multiple_shot(3, bs.gun, 20)
    text = "\n".join(str(x) for x in result)
    assert "卡壳" in text
    assert inv.get_equipped_id("远程") == "1"  # 枪械未损毁
    assert bs.bullet == 0  # 弹夹清零
    assert "总伤害" not in text  # 连射中断，无总伤害结算
    investigator_repo.delete_by_qq("test_jam_multi")
    investigator_repo.delete_by_qq("test_jam_multi2")


def test_gun_explode_on_100_breaks():
    import random

    inv, inv_model = _make_inv("test_explode", "Explode")
    investigator_repo.add_item_to_inventory(inv_model, "1", 1)
    investigator_repo.equip_item("test_explode", "1")
    inv.update_equipment()

    bs, _ = _make_battle("test_explode2", "Explode2")
    bs.investigator = inv
    bs._update_gun_status()
    assert bs.gun is not None

    seed = _find_dice_seed(100)
    random.seed(seed)
    result = bs._single_shot(bs.gun, 20)
    text = "\n".join(str(x) for x in result)
    assert "大失败" in text or "解体" in text
    assert inv.get_equipped_id("远程") is None  # 枪械损毁
    investigator_repo.delete_by_qq("test_explode")
    investigator_repo.delete_by_qq("test_explode2")
