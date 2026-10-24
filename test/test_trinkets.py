"""饰品系统（Trinket）测试。

覆盖：空触发器零回归、4 个示例饰品的触发/条件/次数限制/消耗、
濒死免死不触发 E07/E05、day40 隔离、GM 房间隔离、战斗中换装现读、
数据完整性 schema。只测新增行为，不修改 src/。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import ujson

from src.models.monster import Monster
from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    Investigator,
    InvestigatorGenerator,
    ending_repo,
    investigator_repo,
)
from src.services.battle import BattleService
from src.services.data_loader import data_loader

_TRINKET_IDS = ("600", "601", "602", "603", "604")


def _make_inv(qq: str, name: str = "TrinketHero") -> Investigator:
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    inv_model = investigator_repo.create_and_save(qq, name, data)
    return Investigator(inv_model)


def _cleanup(qq: str) -> None:
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)


def _ending_ids(qq: str) -> list[str]:
    coll = ending_repo.get_collection(qq)
    if not coll:
        return []
    try:
        return [r.get("id") for r in ujson.loads(coll.endings or "[]")]
    except (ValueError, TypeError):
        return []


def _equip(inv: Investigator, item_id: str) -> None:
    investigator_repo.equip_item(inv.qq, item_id)
    inv.update_equipment()


def _set_prob(item_id: str, prob) -> None:
    """临时改写饰品概率并返回恢复闭包（测试隔离）。"""
    key = data_loader.goods_data[item_id]["effect"]["条件"]
    original = key.get("概率")
    key["概率"] = prob

    def _restore():
        if original is None:
            key.pop("概率", None)
        else:
            key["概率"] = original

    return _restore


def _make_bs(qq: str, day: int = 1, mon_id: str = "1", hp: int | None = None) -> BattleService:
    inv = _make_inv(qq)
    inv.day = day
    if hp is not None:
        inv.hp = hp  # 固定 HP：避免随机低血导致 5 伤害文案档位漂移（flaky）
    inv.save()
    m = Monster(mon_id) if mon_id else Monster.load_random_for_day(day)
    return BattleService(inv, m)


# --- 1. 空触发器零行为 ---
def test_no_trinket_zero_behavior():
    qq = "trinket_empty"
    bs = _make_bs(qq, hp=60)  # 高 HP：5 伤害必落「正常伤害」档（防随机血 flaky）
    inv = bs.investigator
    assert inv.get_equipped_id("饰品") is None

    hp_before = bs.hp_record["inv"]
    out = bs._apply_damage_to_player(5, armor_absorbed=True)
    assert out == data_loader.reply_data.get("正常伤害")
    assert bs.hp_record["inv"] == max(0, hp_before - 5)
    assert bs.temp_hp == 0

    mon_before = bs.hp_record["mon"]
    out_m = bs._apply_damage_to_monster(5)
    assert bs.hp_record["mon"] == max(0, mon_before - 5)

    start = bs.start_turn()
    assert "饰品" not in start
    end = bs._end_turn()
    assert "饰品" not in end
    assert bs._check_combat_over() is None
    _cleanup(qq)


# --- 2. 守护挂坠：受击 30% 回 1d3 HP（扣血后回血）---
def test_guardian_pendant_heal_after_damage():
    qq = "trinket_pendant"
    bs = _make_bs(qq)
    inv = bs.investigator
    for iid in _TRINKET_IDS:
        investigator_repo.add_item_to_inventory(inv._model, iid, 1)
    _equip(inv, "600")
    restore = _set_prob("600", 1.0)
    try:
        max_hp = inv.get_max_hp()
        bs.hp_record["inv"] = 2
        out = bs._apply_damage_to_player(3, armor_absorbed=True)
        # 扣血后回血：0 → +1d3，挽救濒死
        assert 0 < bs.hp_record["inv"] <= max_hp
        assert "挂坠发烫" in out

        # 不超 max_hp
        bs2 = _make_bs(qq)
        bs2.hp_record["inv"] = max_hp - 1
        bs2._apply_damage_to_player(1, armor_absorbed=True)
        assert bs2.hp_record["inv"] <= max_hp
    finally:
        restore()
    _cleanup(qq)


# --- 3. 铁壁纹章：受到 >=5 伤害获 2 临时生命（每回合 1 次）---
def test_iron_emblem_temp_hp_threshold():
    qq = "trinket_emblem"
    bs = _make_bs(qq)
    inv = bs.investigator
    for iid in _TRINKET_IDS:
        investigator_repo.add_item_to_inventory(inv._model, iid, 1)
    _equip(inv, "601")

    bs.hp_record["inv"] = 50
    bs._apply_damage_to_player(4, armor_absorbed=True)
    assert bs.temp_hp == 0  # 4 < 5 不触发

    bs._apply_damage_to_player(5, armor_absorbed=True)
    assert bs.temp_hp == 2  # 5 触发
    assert bs.trinket_state["601"]["回合计数"] == 1

    # 同回合二次受击：每回合 1 次不再触发，已有 temp_hp 先抵伤害
    hp_before = bs.hp_record["inv"]
    bs._apply_damage_to_player(5, armor_absorbed=True)
    assert bs.temp_hp == 0
    assert hp_before - bs.hp_record["inv"] == 3  # 5 伤害被 2 临时生命吸收 2 点
    assert bs.trinket_state["601"]["回合计数"] == 1

    # 跨回合再次触发
    bs._turn_counter += 1
    bs._apply_damage_to_player(5, armor_absorbed=True)
    assert bs.temp_hp == 2
    _cleanup(qq)


# --- 4. 古神断指：濒死免死一次并消耗（不触发 E07）---
def test_ancient_finger_death_save_no_e07():
    qq = "trinket_finger"
    bs = _make_bs(qq, day=20)  # 非 day40
    inv = bs.investigator
    for iid in _TRINKET_IDS:
        investigator_repo.add_item_to_inventory(inv._model, iid, 1)
    _equip(inv, "602")

    bs.hp_record["inv"] = 0
    bs.hp_record["mon"] = 50
    out = bs._check_combat_over()
    assert out is None  # 免死：战斗继续
    assert bs.hp_record["inv"] > 0
    assert getattr(bs, "trinket_death_saved", False)
    assert bs.fight_is_over() is False
    assert inv.is_survive is True  # 不落死亡
    assert inv.get_equipped_id("饰品") is None  # 已消耗
    assert "E07" not in _ending_ids(qq)
    progress = ending_repo.get_progress(qq)
    assert progress is None or progress.ended is False

    # 完整回合流：免死文案并入下回合提示
    bs2 = _make_bs(qq, day=20)
    inv2 = bs2.investigator
    investigator_repo.add_item_to_inventory(inv2._model, "602", 1)
    _equip(inv2, "602")
    bs2.hp_record["inv"] = 0
    bs2.hp_record["mon"] = 50
    msg = bs2._end_turn()
    assert bs2.fight_is_over() is False
    assert "断指" in msg
    assert "E07" not in _ending_ids(qq)
    _cleanup(qq)


# --- 5. day40 濒死：免死不进入 _handle_day40_defeat（不 E05）---
def test_day40_death_save_skips_day40_defeat():
    qq = "trinket_day40"
    bs = _make_bs(qq, day=40)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "602", 1)
    _equip(inv, "602")
    assert bs._battle_day == 40

    bs.hp_record["inv"] = 0
    bs.hp_record["mon"] = 50
    out = bs._check_combat_over()
    assert out is None
    assert bs.hp_record["inv"] > 0
    assert inv.is_survive is True
    assert getattr(bs, "door_choice", None) is None  # 未进入门扉抉择
    assert "E05" not in _ending_ids(qq)
    assert inv.get_equipped_id("饰品") is None
    _cleanup(qq)


# --- 6. GM 房间隔离：濒死不触发免死，走 GM 短路 ---
def test_gm_room_death_save_isolated():
    qq = "trinket_gm"
    bs = _make_bs(qq, day=20)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "602", 1)
    _equip(inv, "602")
    bs.is_gm_room = True
    bs.hp_record["inv"] = 0
    bs.hp_record["mon"] = 40
    out = bs._check_combat_over()
    assert bs.gm_room_defeated is True  # GM 短路接管
    assert getattr(bs, "trinket_death_saved", False) is False  # 免死不触发
    assert inv.is_survive is True
    assert inv.get_equipped_id("饰品") is not None  # 饰品未消耗
    assert "E07" not in _ending_ids(qq)
    _cleanup(qq)


# --- 7. 每场次数 / 每回合冷却（防无限触发）---
def test_per_battle_cap():
    qq = "trinket_perbattle"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "600", 1)
    _equip(inv, "600")
    # 改为回SAN效果 + 每场次数2，便于独立计数（不与伤害互相干扰）
    eff = data_loader.goods_data["600"]["effect"]
    old = (eff["效果"], eff["条件"])
    eff["效果"] = {"type": "san", "值": 1}
    eff["条件"] = {"概率": 1.0, "每场次数": 2}
    try:
        san0 = inv.get_skill("san", 0)
        for t in range(3):  # 跨 3 回合各受击一次
            bs._turn_counter = t
            bs.hp_record["inv"] = 50
            bs._apply_damage_to_player(1, armor_absorbed=True)
        assert inv.get_skill("san", 0) == san0 + 2  # 第 3 次被每场次数封顶
        assert bs.trinket_state["600"]["每场次数"] == 2
    finally:
        eff["效果"], eff["条件"] = old
    _cleanup(qq)


def test_per_turn_cap():
    qq = "trinket_perturn"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "600", 1)
    _equip(inv, "600")
    eff = data_loader.goods_data["600"]["effect"]
    old = (eff["效果"], eff["条件"])
    eff["效果"] = {"type": "san", "值": 1}
    eff["条件"] = {"概率": 1.0}  # 无显式冷却 → 默认每回合 1 次
    try:
        san0 = inv.get_skill("san", 0)
        bs._turn_counter = 1
        bs.hp_record["inv"] = 50
        bs._apply_damage_to_player(1, armor_absorbed=True)
        bs.hp_record["inv"] = 50
        bs._apply_damage_to_player(1, armor_absorbed=True)  # 同回合第二次
        assert inv.get_skill("san", 0) == san0 + 1  # 被默认每回合 1 次封顶
        assert bs.trinket_state["600"]["回合计数"] == 1
        bs._turn_counter = 2
        bs.hp_record["inv"] = 50
        bs._apply_damage_to_player(1, armor_absorbed=True)
        assert inv.get_skill("san", 0) == san0 + 2  # 跨回合重新计数
    finally:
        eff["效果"], eff["条件"] = old
    _cleanup(qq)


# --- 8. 战斗中换装：现读机制，下次收口即生效 ---
def test_equip_mid_battle_takes_effect():
    qq = "trinket_swap"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "601", 1)

    # 未装备饰品：承伤不产生 temp_hp
    bs.hp_record["inv"] = 50
    bs._apply_damage_to_player(5, armor_absorbed=True)
    assert bs.temp_hp == 0

    # 战斗中换装（对齐 adventure.py 战斗中换装路径）
    _equip(inv, "601")
    assert inv.get_equipped_id("饰品") == "601"
    bs.hp_record["inv"] = 50
    bs._apply_damage_to_player(5, armor_absorbed=True)
    assert bs.temp_hp == 2  # 下一次收口立即生效（现读不缓存快照）
    _cleanup(qq)


# --- 9. 猎犬之牙：造成伤害追加 + 每回合 1 次 ---
def test_hound_tooth_append_attack():
    qq = "trinket_hound"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "603", 1)
    _equip(inv, "603")
    restore = _set_prob("603", 1.0)
    try:
        bs.hp_record["mon"] = 100
        bs.hp_record["inv"] = 100
        out = bs._apply_damage_to_monster(5)
        dmg = 100 - bs.hp_record["mon"]
        assert 6 <= dmg <= 8  # 5 + 1d3 追加
        assert "追加" in out

        # 同回合第二次攻击：每回合 1 次不再追加
        bs.hp_record["mon"] = 100
        bs._apply_damage_to_monster(5)
        assert 100 - bs.hp_record["mon"] == 5
    finally:
        restore()
    _cleanup(qq)


# --- 10. 数据完整性：600+ 饰品 schema ---
def test_trinket_data_integrity():
    for iid, item in data_loader.goods_data.items():
        if item.get("part") != "饰品":
            continue
        assert int(iid) >= 600, f"饰品 {iid} 必须占用 600+ 段（避开信物 400-508）"
        eff = item.get("effect")
        assert isinstance(eff, dict), f"饰品 {iid} 缺少 effect 配置"
        assert eff.get("触发"), f"饰品 {iid} effect 缺少触发时机"
        assert isinstance(eff.get("效果"), dict), f"饰品 {iid} 缺少效果"
        assert isinstance(eff.get("条件"), dict), f"饰品 {iid} 条件需为 dict"
        assert item.get("des"), f"饰品 {iid} 缺少描述"
        assert item.get("type") == "饰品", f"饰品 {iid} type 应为 饰品"

    for iid in _TRINKET_IDS:
        assert iid in data_loader.goods_data, f"缺少示例饰品 {iid}"
        item = data_loader.goods_data[iid]
        assert item["part"] == "饰品"
        assert item["effect"]["触发"] in {
            "受击", "受到伤害", "濒死", "造成伤害", "致死预判",
            "进入战斗", "回合开始", "回合结束",
        }


# --- 11. 装备/卸下流程：part=饰品 可装备，misc 信物不可进饰品槽 ---
def test_equip_flow_and_whitelist():
    from src.models.player import _EQUIPPABLE_PARTS

    assert "饰品" in _EQUIPPABLE_PARTS
    qq = "trinket_equip"
    inv = _make_inv(qq)
    # 饰品可装备
    investigator_repo.add_item_to_inventory(inv._model, "600", 1)
    ok, msg = investigator_repo.equip_item(qq, "600")
    assert ok, msg
    inv.update_equipment()
    assert inv.get_equipped_id("饰品") == "600"
    # 信物(400)不可装备进饰品槽（F-09 仍生效）
    investigator_repo.add_item_to_inventory(inv._model, "400", 1)
    ok2, _ = investigator_repo.equip_item(qq, "400")
    assert ok2 is False
    # str_equipments 显示饰品行
    assert "饰品" in inv.str_equipments()
    _cleanup(qq)


# --- 12. 幸运徽章：致死预判免疫（伤害预判型，与 602 濒死拦截不同）---
def test_lucky_badge_blocks_lethal_damage():
    qq = "trinket_lucky"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "604", 1)
    _equip(inv, "604")
    restore = _set_prob("604", 1.0)
    try:
        bs.hp_record["inv"] = 1
        out = bs._apply_damage_to_player(5, armor_absorbed=True)
        assert bs.hp_record["inv"] == 1  # 免疫：HP 不变
        assert bs._stat_dmg_taken == 0  # 伤害为 0
        assert "徽章" in out
        assert bs.fight_is_over() is False  # 战斗未结束
    finally:
        restore()
    _cleanup(qq)


def test_lucky_badge_no_trigger_on_non_lethal():
    qq = "trinket_lucky_no"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "604", 1)
    _equip(inv, "604")
    restore = _set_prob("604", 1.0)
    try:
        bs.hp_record["inv"] = 50
        out = bs._apply_damage_to_player(5, armor_absorbed=True)
        assert bs.hp_record["inv"] == 45  # 非致死：正常扣血
        assert "徽章" not in out  # 不触发
    finally:
        restore()
    _cleanup(qq)


# --- 13. 3% 概率（mock random.random 验证触发 / 不触发两路）---
def test_lucky_badge_3pct_probability_paths():
    from unittest.mock import patch

    qq = "trinket_lucky_prob"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "604", 1)
    _equip(inv, "604")
    # 保持配置 3%：random.random() < 0.03 → 触发
    bs.hp_record["inv"] = 1
    with patch("random.random", return_value=0.01):
        out = bs._apply_damage_to_player(5, armor_absorbed=True)
    assert bs.hp_record["inv"] == 1  # 免疫生效
    assert "徽章" in out
    # random.random() >= 0.03 → 不触发（跨回合避开默认每回合 1 次封顶）
    bs._turn_counter += 1
    bs.hp_record["inv"] = 1
    with patch("random.random", return_value=0.99):
        out2 = bs._apply_damage_to_player(5, armor_absorbed=True)
    assert bs.hp_record["inv"] == 0  # 未免疫：承受致死伤害
    assert "徽章" not in out2
    _cleanup(qq)


# --- 14. 护甲减伤后的致死判断（armor_absorbed=False 路径）---
def test_lucky_badge_lethal_judged_after_armor():
    qq = "trinket_lucky_armor"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "604", 1)
    _equip(inv, "604")
    investigator_repo.add_item_to_inventory(inv._model, "303", 1)  # 板甲胸铠 护甲3
    investigator_repo.equip_item(qq, "303")
    inv.update_equipment()
    assert inv.get_armor_value() == 3
    restore = _set_prob("604", 1.0)
    try:
        # 减伤后不致死（6-3=3 < HP5）：不触发，正常扣血
        bs.hp_record["inv"] = 5
        out = bs._apply_damage_to_player(6)
        assert bs.hp_record["inv"] == 2
        assert "徽章" not in out
        # 减伤后致死（10-3=7 >= HP5）：触发免疫，HP 不变
        bs._turn_counter += 1
        bs.hp_record["inv"] = 5
        out2 = bs._apply_damage_to_player(10)
        assert bs.hp_record["inv"] == 5
        assert "徽章" in out2
        # 边界：减伤后恰好等于 HP（HP 将 ≤0）→ 触发
        bs._turn_counter += 1
        bs.hp_record["inv"] = 3
        out3 = bs._apply_damage_to_player(6)  # 6-3=3 == HP
        assert bs.hp_record["inv"] == 3
        assert "徽章" in out3
    finally:
        restore()
    _cleanup(qq)


# --- 15. 与古神断指共存：免疫失败后仍可濒死免死（两道独立防线）---
def test_lucky_badge_coexist_with_death_save():
    qq = "trinket_lucky_coexist"
    bs = _make_bs(qq, day=20)
    inv = bs.investigator
    # 单槽机制：先装徽章（免疫失败承伤），再战中换装 602（濒死兜底）
    investigator_repo.add_item_to_inventory(inv._model, "604", 1)
    _equip(inv, "604")
    restore = _set_prob("604", 0.0)  # 免疫必然失败
    try:
        bs.hp_record["inv"] = 1
        bs.hp_record["mon"] = 50
        out = bs._apply_damage_to_player(5, armor_absorbed=True)
        assert bs.hp_record["inv"] == 0  # 徽章免疫失败：承受致死伤害
        assert "徽章" not in out
        # 古神断指濒死兜底：战斗中换装 602 后判定
        investigator_repo.add_item_to_inventory(inv._model, "602", 1)
        _equip(inv, "602")
        out2 = bs._check_combat_over()
        assert out2 is None  # 免死：战斗继续
        assert bs.hp_record["inv"] > 0
        assert getattr(bs, "trinket_death_saved", False)
        assert inv.is_survive is True  # 不落死亡
        assert inv.get_equipped_id("饰品") is None  # 602 已消耗
        assert "E07" not in _ending_ids(qq)
    finally:
        restore()
    _cleanup(qq)


# --- 16. GM 房间隔离：致死预判不生效（沙盒内不跑终局类饰品）---
def test_lucky_badge_gm_room_isolated():
    qq = "trinket_lucky_gm"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "604", 1)
    _equip(inv, "604")
    restore = _set_prob("604", 1.0)
    try:
        bs.is_gm_room = True
        bs.hp_record["inv"] = 1
        out = bs._apply_damage_to_player(5, armor_absorbed=True)
        assert bs.hp_record["inv"] == 0  # 隔离：徽章不生效，正常承伤
        assert "徽章" not in out
    finally:
        restore()
    _cleanup(qq)


# --- 17. 注册表：9 种既有效果类型全部登记 ---
def test_registry_has_all_effect_types():
    from src.services.battle.trinkets import TRINKET_EFFECTS

    for t in (
        "heal", "temp_hp", "reduce", "damage_bonus", "san", "shield",
        "block_lethal", "immune_death", "append_attack",
    ):
        assert t in TRINKET_EFFECTS, f"效果类型 {t} 未注册"


# --- 18. 注册表：新效果类型 = 装饰器注册即可用，无需改分发链 ---
def test_registry_new_effect_usable():
    from src.services.battle.trinkets import TRINKET_EFFECTS, trinket_effect

    @trinket_effect("test_damage_up")
    def _fx_test_damage_up(self, item, eff, fx, ctx, defer_heal):
        expr, val = self._trinket_fx_value(fx, "dice", "1d2")
        ctx["damage"] = ctx.get("damage", 0) + val
        return [self._trinket_render(eff, expr, val, name=item.name, item_id=item.id)]

    qq = "trinket_registry"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "600", 1)
    _equip(inv, "600")
    eff = data_loader.goods_data["600"]["effect"]
    old = (eff["效果"], eff["条件"], eff["触发"], eff["文案"])
    eff["效果"] = {"type": "test_damage_up", "dice": "1d2"}
    eff["条件"] = {"概率": 1.0}
    eff["触发"] = "造成伤害"
    eff["文案"] = "test {数值}"
    try:
        bs.hp_record["mon"] = 100
        out = bs._apply_damage_to_monster(5)
        dmg = 100 - bs.hp_record["mon"]
        assert 6 <= dmg <= 7  # 5 + 1d2 追加
        assert "test " in out
    finally:
        eff["效果"], eff["条件"], eff["触发"], eff["文案"] = old
        TRINKET_EFFECTS.pop("test_damage_up", None)
    _cleanup(qq)


# --- 19. 未知效果类型：告警 + 安全跳过（不崩溃、无副作用）---
def test_unknown_effect_type_safe_skip():
    import io

    from loguru import logger as loguru_logger

    qq = "trinket_unknown"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "600", 1)
    _equip(inv, "600")
    eff = data_loader.goods_data["600"]["effect"]
    old = (eff["效果"], eff["条件"], eff["触发"])
    eff["效果"] = {"type": "no_such_fx", "值": 999}
    eff["条件"] = {"概率": 1.0}
    eff["触发"] = "受击"
    sink = io.StringIO()
    hid = loguru_logger.add(sink, level="WARNING")
    try:
        hp_before = bs.hp_record["inv"]
        out = bs._apply_damage_to_player(3, armor_absorbed=True)
        assert bs.hp_record["inv"] == max(0, hp_before - 3)  # 正常扣血，无效果
        assert "no_such_fx" in sink.getvalue()  # 告警日志
    finally:
        loguru_logger.remove(hid)
        eff["效果"], eff["条件"], eff["触发"] = old
    _cleanup(qq)


# --- 20. 数组效果：单一饰品多效果组合（san + heal）---
def test_array_effect_multi():
    qq = "trinket_array"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "600", 1)
    _equip(inv, "600")
    eff = data_loader.goods_data["600"]["effect"]
    old = (eff["效果"], eff["条件"], eff["触发"], eff["文案"])
    eff["效果"] = [
        {"type": "san", "值": 2},
        {"type": "heal", "dice": "1d3", "target": "player"},
    ]
    eff["条件"] = {"概率": 1.0}
    eff["触发"] = "受击"
    eff["文案"] = "combo"
    try:
        max_hp = inv.get_max_hp()
        bs.hp_record["inv"] = min(max_hp, 5)
        san0 = inv.get_skill("san", 0)
        bs._apply_damage_to_player(2, armor_absorbed=True)
        assert inv.get_skill("san", 0) == san0 + 2  # san 效果生效
        # heal 1d3 在扣血后回血（上限 max_hp）
        hp_low = min(max_hp, 5 - 2 + 1)
        hp_high = min(max_hp, 5 - 2 + 3)
        assert hp_low <= bs.hp_record["inv"] <= hp_high
    finally:
        eff["效果"], eff["条件"], eff["触发"], eff["文案"] = old
    _cleanup(qq)


# --- 21. target 字段落地：player 生效、未知 target 告警回退 player ---
def test_target_field_player_effect_works():
    qq = "trinket_target"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "600", 1)
    _equip(inv, "600")
    eff = data_loader.goods_data["600"]["effect"]
    old = (eff["效果"], eff["条件"], eff["触发"])
    eff["效果"] = {"type": "san", "值": 1, "target": "player"}
    eff["条件"] = {"概率": 1.0}
    eff["触发"] = "受击"
    try:
        san0 = inv.get_skill("san", 0)
        bs._apply_damage_to_player(1, armor_absorbed=True)
        assert inv.get_skill("san", 0) == san0 + 1
    finally:
        eff["效果"], eff["条件"], eff["触发"] = old
    _cleanup(qq)


def test_unknown_target_fallback_player():
    import io

    from loguru import logger as loguru_logger

    qq = "trinket_target_unknown"
    bs = _make_bs(qq)
    inv = bs.investigator
    investigator_repo.add_item_to_inventory(inv._model, "600", 1)
    _equip(inv, "600")
    eff = data_loader.goods_data["600"]["effect"]
    old = (eff["效果"], eff["条件"], eff["触发"])
    eff["效果"] = {"type": "san", "值": 1, "target": "boss"}
    eff["条件"] = {"概率": 1.0}
    eff["触发"] = "受击"
    sink = io.StringIO()
    hid = loguru_logger.add(sink, level="WARNING")
    try:
        san0 = inv.get_skill("san", 0)
        bs._apply_damage_to_player(1, armor_absorbed=True)
        assert inv.get_skill("san", 0) == san0 + 1  # 回退 player，效果仍生效
        assert "回退 player" in sink.getvalue()  # 告警日志
    finally:
        loguru_logger.remove(hid)
        eff["效果"], eff["条件"], eff["触发"] = old
    _cleanup(qq)
