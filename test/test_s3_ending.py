"""S3 补测：多结局接线 / 战斗减伤 / 周目继承 / 事件条件 / 消耗品（light 风格）。

覆盖 P0 接线（E06/E07/E04/重赴次数门）、calc_dmg 护甲、侦查失败保底、
「使用」空参数提示、new_run 周目继承、eval_option_condition、pick_random_event
去重、505/506 消耗品。全部只测现有行为，不修改 src/。
"""
import random
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
from src.services import event_service
from src.services.battle import BattleService
from src.services.damage_calculator import calculate_damage
from src.services.data_loader import data_loader
from src.services.ending_engine import (
    check_san_zero,
    first_kill_drop,
    judge_door_choice,
    mark_mirror_defeated,
    register_e07,
    render_door_choice,
)
from src.services.resurrect import do_resurrect
from src.services.dice_roller import SuccessLevel


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


# --- P0·E06：SAN 归零 → check_san_zero 登记 + 复活拦截 ---
def test_e06_san_zero_registers_ending():
    qq = "test_e06_register"
    inv, _ = _make_inv(qq, "SanZero")
    result = check_san_zero(inv)

    assert result["triggered"] is True
    assert result["ending"] == "E06"
    assert result["block_resurrect"] is True
    assert "永夜低语" in result["message"]  # E06 名称

    progress = ending_repo.get_progress(qq)
    assert progress is not None
    assert progress.san_zero_hit is True
    assert progress.ended is True
    assert _ending_ids(qq) == ["E06"]
    _cleanup(qq)


def test_e06_san_zero_idempotent_no_duplicate():
    qq = "test_e06_idem"
    inv, _ = _make_inv(qq, "SanZero2")
    check_san_zero(inv)
    result = check_san_zero(inv)  # 二次调用：不再重复登记
    assert result["triggered"] is False
    assert _ending_ids(qq).count("E06") == 1
    _cleanup(qq)


def test_e06_resurrect_blocked_for_mad():
    qq = "test_e06_block"
    inv, inv_model = _make_inv(qq, "MadBlock")
    check_san_zero(inv)
    investigator_repo.add_item_to_inventory(inv_model, "501", 1)
    inv.is_survive = False
    inv.save()

    msg = do_resurrect(qq)
    assert msg == data_loader.get_text("adventure.resurrect_blocked")
    # 拦截发生在消耗道具之前：501 未被扣掉
    fresh = Investigator.load(qq)
    equipments, _ = fresh.get_equipments()
    assert equipments.get("501") == 1
    _cleanup(qq)


# --- P0·E07：非 day40 死亡登记（无 501 登记 / 持 501 不登记）---
def test_e07_register_without_501():
    qq = "test_e07_reg"
    inv, _ = _make_inv(qq, "E07Dead")
    text = register_e07(inv)
    assert text is not None
    assert "墓园拾骨" in text
    assert _ending_ids(qq) == ["E07"]
    # E07 不写 ended：事后购得 501 仍可复活继续
    progress = ending_repo.get_progress(qq)
    assert progress.ended is False
    _cleanup(qq)


def test_e07_not_registered_with_501():
    qq = "test_e07_501"
    inv, inv_model = _make_inv(qq, "E07Hold501")
    investigator_repo.add_item_to_inventory(inv_model, "501", 1)
    assert register_e07(inv) is None
    assert "E07" not in _ending_ids(qq)
    _cleanup(qq)


def test_e07_death_registers_via_battle():
    qq = "test_e07_battle"
    bs, _ = _make_battle(qq, "BattleE07")
    bs.hp_record["inv"] = 0
    text = bs._check_combat_over()
    assert "墓园拾骨" in text
    assert "E07" in _ending_ids(qq)
    _cleanup(qq)


def test_e07_death_skipped_with_501_via_battle():
    qq = "test_e07_battle_501"
    bs, inv_model = _make_battle(qq, "BattleE07501")
    investigator_repo.add_item_to_inventory(inv_model, "501", 1)
    bs.hp_record["inv"] = 0
    text = bs._check_combat_over()
    assert "墓园拾骨" not in text
    assert "E07" not in _ending_ids(qq)
    _cleanup(qq)


# --- P0·E04：击败 37 → mirror_defeated=True；首杀 502 ---
def test_e04_mirror_defeated_on_kill_37():
    qq = "test_e04_mirror"
    inv, _ = _make_inv(qq, "MirrorSlayer")
    bs = BattleService(inv, Monster("37"))
    bs.hp_record["mon"] = 0
    bs._handle_victory()
    progress = ending_repo.get_progress(qq)
    assert progress is not None and progress.mirror_defeated is True
    # 首杀 37 必掉 502（经 _victory_parts 实际入库）
    equipments, _ = inv.get_equipments()
    assert equipments.get("502", 0) >= 1
    _cleanup(qq)


def test_e04_mirror_defeated_idempotent_and_first_kill_once():
    qq = "test_e04_once"
    inv, inv_model = _make_inv(qq, "MirrorOnce")
    mark_mirror_defeated(inv)
    mark_mirror_defeated(inv)  # 幂等
    progress = ending_repo.get_progress(qq)
    assert progress.mirror_defeated is True

    assert first_kill_drop(inv, "37") == "502"
    assert first_kill_drop(inv, "37") is None  # 二次不再掉
    assert first_kill_drop(inv, "999") is None  # 非首杀表怪不掉
    investigator_repo.delete_by_qq(qq)
    _cleanup(qq)


# --- P0·重赴次数门：refought 置位后 R_refight 被拒 ---
def test_refight_door_rejected_after_refought():
    qq = "test_refight"
    inv, _ = _make_inv(qq, "Refighter")
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    progress.dead_once = True
    progress.save()

    # 战败后首见：R_refight / O_observe 可选
    render = render_door_choice(inv)
    keys = [c["key"] for c in render["choices"]]
    assert "R_refight" in keys and "O_observe" in keys

    r = judge_door_choice(inv, "R_refight")
    assert r.get("refight") is True
    progress = ending_repo.get_progress(qq)
    assert progress.refought is True

    # 已重赴：次数门关闭，重赴/旁观不再可选
    render2 = render_door_choice(inv)
    keys2 = [c["key"] for c in render2["choices"]]
    assert "R_refight" not in keys2 and "O_observe" not in keys2

    r2 = judge_door_choice(inv, "R_refight")
    assert r2.get("error") is True
    _cleanup(qq)


def test_refight_observe_ends_e08():
    qq = "test_refight_observe"
    inv, _ = _make_inv(qq, "Observer")
    progress = ending_repo.ensure_progress(inv.qq, inv.day)
    progress.dead_once = True
    progress.save()

    r = judge_door_choice(inv, "O_observe")
    assert r.get("ended") is True
    assert r.get("ending") == "E08"
    assert "旁观者" in r["message"]
    progress = ending_repo.get_progress(qq)
    assert progress.ended is True
    _cleanup(qq)


# --- 战斗·护甲减伤（calc_dmg）---
def test_calc_dmg_armor_reduction():
    expr, val = calculate_damage("5", armor=3)
    assert (expr, val) == ("5", 2)


def test_calc_dmg_armor_zero_floor():
    expr, val = calculate_damage("3", armor=99)
    assert val == 0  # 0 伤害下限，不出现负伤


def test_calc_dmg_ex_double_only_on_critical():
    # 大成功 + ex：双倍（最大值 + 随机值）
    random.seed(42)
    _, v = calculate_damage(
        "1d6", success_level=SuccessLevel.CRITICAL_SUCCESS, is_extreme_double=True
    )
    assert 7 <= v <= 12
    # 非大成功 + ex：不翻倍，普通单次掷骰
    random.seed(42)
    expr, v2 = calculate_damage(
        "1d6", success_level=SuccessLevel.SUCCESS, is_extreme_double=True
    )
    assert 1 <= v2 <= 6
    assert "+" not in expr


def test_calc_dmg_ex_armor_applied_once():
    # 同种子下：ex 双倍伤害同样受护甲削减（ex 只负责暴击翻倍，不改变护甲规则）
    random.seed(7)
    _, with_armor = calculate_damage(
        "1d6",
        success_level=SuccessLevel.CRITICAL_SUCCESS,
        is_extreme_double=True,
        armor=4,
    )
    random.seed(7)
    _, without_armor = calculate_damage(
        "1d6",
        success_level=SuccessLevel.CRITICAL_SUCCESS,
        is_extreme_double=True,
    )
    assert without_armor - with_armor == 4


# --- 战斗·侦查失败保底（half_loot_gold = 档位乌帕上限一半，向下取整）---
def test_half_loot_gold_floor():
    assert Monster("30").half_loot_gold() == 10  # hp14 → 档2 上限 20 → 10
    assert Monster("37").half_loot_gold() == 10  # hp15 → 档2 上限 20 → 10
    assert Monster("35").half_loot_gold() == 12  # hp20 → 档3 上限 25 → 12


# --- 战斗·「使用␣」空参数友好提示 ---
def test_use_item_empty_parameter_friendly_hint():
    bs, inv_model = _make_battle("test_use_empty", "UseEmpty")
    token_before = bs.get_turn_token()
    expected = data_loader.get_text("battle.need_item_id")

    for action in ("使用", "使用 "):
        result = bs._use_item_action(action)
        assert len(result) == 1
        assert result[0] == expected
        assert bs.get_turn_token() == token_before  # 空参数不消耗回合
    investigator_repo.delete_by_qq("test_use_empty")


# --- 周目继承：new_run 表 B 保留、表 A 重建 ---
def test_new_run_preserves_table_b_rebuilds_table_a():
    qq = "test_newrun"
    _make_inv(qq, "NewRun")
    ending_repo.ensure_collection(qq)
    assert ending_repo.get_collection(qq).total_runs == 0

    ending_repo.add_ending(qq, "E01", variant="清醒合流")
    ending_repo.touch_relic_collection(qq, ["502", "503"])
    ending_repo.ensure_progress(qq, 5)
    p = ending_repo.get_progress(qq)
    p.ended = True
    p.save()

    coll = ending_repo.new_run(qq)
    assert coll.total_runs == 1  # 表 B 累计周目 +1
    assert "E01" in coll.endings  # 表 B 结局记录保留
    assert "502" in ujson.loads(coll.collection)  # 表 B 信物图鉴保留

    new_p = ending_repo.get_progress(qq)
    assert new_p is not None
    assert new_p.run_id == 1
    assert new_p.day == 1  # 表 A 重建：回到 day 1
    assert new_p.ended is False  # 旧局状态清空
    _cleanup(qq)


# --- 事件条件：eval_option_condition ---
def test_eval_option_condition_basic_ops():
    inv, inv_model = _make_inv("test_cond_basic", "CondBasic")
    investigator_repo.add_item_to_inventory(inv_model, "301", 1)
    inv.set_skill("san", 30)
    inv.hp = 8
    inv.save()

    from src.services.ending_engine import eval_option_condition

    # 物品（单值）
    assert eval_option_condition({"物品": "301"}, inv) is True
    assert eval_option_condition({"物品": "102"}, inv) is False
    # 物品（多值 list 按 all 语义）
    assert eval_option_condition({"物品": ["301", "101"]}, inv) is True
    assert eval_option_condition({"物品": ["301", "999"]}, inv) is False
    # SAN
    assert eval_option_condition({"SAN": {"min": 20}}, inv) is True
    assert eval_option_condition({"SAN": {"min": 40}}, inv) is False
    assert eval_option_condition({"SAN": 25}, inv) is True  # 纯数值按 min 语义
    # HP
    assert eval_option_condition({"HP": {"min": 5}}, inv) is True
    assert eval_option_condition({"HP": {"min": 20}}, inv) is False
    # 克苏鲁神话
    assert eval_option_condition({"克苏鲁神话": {"min": 1}}, inv) is False
    # 日
    assert eval_option_condition({"日": {"eq": 1}}, inv) is True
    assert eval_option_condition({"日": {"min": 2}}, inv) is False
    # 已死亡
    assert eval_option_condition({"已死亡": False}, inv) is True
    assert eval_option_condition({"已死亡": True}, inv) is False
    _cleanup("test_cond_basic")


def test_eval_option_condition_all_any_progress_unknown():
    inv, inv_model = _make_inv("test_cond_combo", "CondCombo")
    investigator_repo.add_item_to_inventory(inv_model, "301", 1)
    inv.set_skill("san", 50)
    inv.hp = 10
    inv.save()

    from src.services.ending_engine import eval_option_condition

    progress = type("P", (), {"mirror_defeated": True})()
    # 进度
    assert eval_option_condition({"进度": "mirror_defeated"}, inv, progress) is True
    assert eval_option_condition({"进度": "mirror_defeated"}, inv, None) is False
    assert eval_option_condition({"进度": "boss36_defeated"}, inv, progress) is False
    # all
    cond_all = {"all": [{"SAN": {"min": 10}}, {"HP": {"min": 1}}]}
    assert eval_option_condition(cond_all, inv) is True
    cond_all_fail = {"all": [{"SAN": {"min": 10}}, {"HP": {"min": 99}}]}
    assert eval_option_condition(cond_all_fail, inv) is False
    # any
    cond_any = {"any": [{"SAN": {"min": 999}}, {"物品": "301"}]}
    assert eval_option_condition(cond_any, inv) is True
    cond_any_fail = {"any": [{"SAN": {"min": 999}}, {"物品": "999"}]}
    assert eval_option_condition(cond_any_fail, inv) is False
    # 空条件 / 未知键放行
    assert eval_option_condition(None, inv) is True
    assert eval_option_condition({}, inv) is True
    assert eval_option_condition({"未知键": "whatever"}, inv) is True
    assert eval_option_condition("plain-string", inv) is True
    # any 内包裹物品多值
    cond_any_item = {"any": [{"物品": "301"}, {"物品": ["502", "503"]}]}
    assert eval_option_condition(cond_any_item, inv) is True
    _cleanup("test_cond_combo")


# --- 事件选择：pick_random_event 近 3 天去重 + 全去重回退 ---
def _event_key(ev: dict) -> str:
    return next(
        k for k, v in data_loader.event_data.items() if v is ev
    )


def _eligible_keys() -> list[str]:
    return [
        k for k, ev in data_loader.event_data.items() if "触发" not in ev
    ]


def test_pick_random_event_dedup_and_queue():
    qq = "test_pick_dedup"
    inv, _ = _make_inv(qq, "PickDedup")
    inv.set_skill("san", 80)  # 保证安神茶铺条件通过，随机池最全
    inv.save()
    es = event_service
    es._recent_events.pop(qq, None)

    calls: list[list[str]] = []
    orig_choice, orig_random = es.random.choice, es.random.random

    def fake_choice(seq):
        calls.append(list(seq))
        return seq[0]

    try:
        es.random.choice = fake_choice
        es.random.random = lambda: 0.0  # 必触发随机事件
        ev1 = es.pick_random_event(inv)
        assert ev1 is not None
        key1 = _event_key(ev1)
        assert es._recent_events[qq] == [key1]

        ev2 = es.pick_random_event(inv)
        assert ev2 is not None
        key2 = _event_key(ev2)
        pool2 = calls[-1]
        assert key1 not in pool2  # 近 3 天已触发事件排除
        assert es._recent_events[qq] == [key1, key2]
    finally:
        es.random.choice = orig_choice
        es.random.random = orig_random
        es._recent_events.pop(qq, None)
    _cleanup(qq)


def test_pick_random_event_fallback_when_all_dedup():
    qq = "test_pick_fallback"
    inv, _ = _make_inv(qq, "PickFallback")
    inv.set_skill("san", 80)
    inv.save()
    es = event_service
    eligible = _eligible_keys()
    assert len(eligible) > 3
    es._recent_events[qq] = list(eligible)  # 全部已在近 3 天：池为空

    orig_choice, orig_random = es.random.choice, es.random.random
    try:
        es.random.choice = lambda seq: seq[0]
        es.random.random = lambda: 0.0
        ev = es.pick_random_event(inv)
        assert ev is not None  # 全去重时回退，保证当日有事件
    finally:
        es.random.choice = orig_choice
        es.random.random = orig_random
        es._recent_events.pop(qq, None)
    _cleanup(qq)


def test_pick_random_event_fixed_day_overrides_chance():
    qq = "test_pick_fixed"
    inv, _ = _make_inv(qq, "PickFixed")
    inv.day = 5
    inv.save()
    es = event_service
    orig_random = es.random.random
    try:
        es.random.random = lambda: 0.99  # 随机概率应失败
        ev = es.pick_random_event(inv)
        assert ev is not None
        assert ev["触发"]["day"] == 5  # 固定日期事件必触发
    finally:
        es.random.random = orig_random
        es._recent_events.pop(qq, None)
    _cleanup(qq)


def test_pick_random_event_chance_blocks():
    qq = "test_pick_chance"
    inv, _ = _make_inv(qq, "PickChance")
    es = event_service
    orig_random = es.random.random
    try:
        es.random.random = lambda: 0.99
        assert es.pick_random_event(inv) is None
    finally:
        es.random.random = orig_random
        es._recent_events.pop(qq, None)
    _cleanup(qq)


# --- 消耗品 505 米戈神经凝胶 ---
def test_consumable_505_heal():
    qq = "test_gel_heal"
    inv, inv_model = _make_inv(qq, "GelHeal")
    investigator_repo.add_item_to_inventory(inv_model, "505", 1)
    bs = BattleService(inv, Monster.load_random_for_day(1))
    bs.hp_record["inv"] = 1
    bs.investigator.hp = 1

    result = bs._use_item_action("使用505")
    assert "米戈神经凝胶" in result[0]
    assert bs.hp_record["inv"] >= 2  # 1d6 回复生效
    assert bs.hp_record["inv"] <= inv.get_max_hp()  # 封顶
    equipments, _ = inv.get_equipments()
    assert "505" not in equipments  # 消耗背包
    assert bs.get_turn_token() == 1  # 消耗回合
    _cleanup(qq)


def test_consumable_505_temp_shield():
    qq = "test_gel_temp"
    inv, inv_model = _make_inv(qq, "GelTemp")
    investigator_repo.add_item_to_inventory(inv_model, "505", 2)
    bs = BattleService(inv, Monster.load_random_for_day(1))

    remaining = 2
    for action in ("使用505 临时", "使用505 护盾"):
        result = bs._use_item_action(action)
        assert "米戈神经凝胶" in result[0]
        assert 4 <= bs.temp_hp <= 16  # 4d4 临时生命（use_effect.temp.dice）
        remaining -= 1
        equipments, _ = inv.get_equipments()
        assert equipments.get("505", 0) == remaining  # 每次消耗一个
        bs.temp_hp = 0
    _cleanup(qq)


# --- 消耗品 506 廷达洛斯的骨哨 ---
def test_consumable_506_bone_whistle_start():
    qq = "test_whistle"
    inv, inv_model = _make_inv(qq, "Whistle")
    investigator_repo.add_item_to_inventory(inv_model, "506", 1)
    bs = BattleService(inv, Monster.load_random_for_day(1))

    result = bs._use_item_action("使用506")
    assert "骨哨" in result[0]
    assert bs.bone_whistle == 3  # 助战 3 回合
    equipments, _ = inv.get_equipments()
    assert "506" not in equipments  # 消耗背包
    assert bs.get_turn_token() == 1
    _cleanup(qq)


def test_bone_whistle_monster_tick_damage():
    qq = "test_whistle_tick"
    bs, inv_model = _make_battle(qq, "WhistleTick")
    bs.bone_whistle = 3
    bs.monster.armor = 0
    bs.monster._data["装甲"] = 0
    bs.hp_record["mon"] = 9999  # 防助战伤害提前击杀

    result = bs._execute_monster_action("闪避")
    assert bs.bone_whistle == 2  # 递减
    assert bs.hp_record["mon"] >= 9999 - 8  # 2d4 伤害（max 8）
    text = "\n".join(str(x) for x in result)
    assert "骨哨" in text
    assert "剩余 2" in text
    _cleanup(qq)


def test_consumable_guards_no_item_and_non_consumable():
    qq = "test_cons_guard"
    bs, _ = _make_battle(qq, "ConsGuard")
    token_before = bs.get_turn_token()

    r = bs._use_item_action("使用505")  # 背包无 505
    assert "没有该物品" in r[0]
    assert bs.get_turn_token() == token_before

    r2 = bs._use_item_action("使用101")  # 弹簧折刀非消耗品
    assert "消耗品" in r2[0]
    assert bs.get_turn_token() == token_before
    _cleanup(qq)
