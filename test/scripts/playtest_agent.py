"""真实游玩模拟器（BUG 猎手 / 六视角评测）：按真实游戏流程模拟 1 名调查员从第 1 天冒险到第 30 天。

流程与 plugins/adventure.py 一致：环境 → 奇遇事件 → 理智检定 → 战斗（BattleService）。
死亡：背包有 501 复活道具则复活；否则重开新角色继续（记录死亡原因）。
日志输出完整战斗 md 文本与状态变化，供人工/Agent 审查。

六视角评测扩展：
- --tutorial         开头输出完整新手教程（/冒险帮助 + 角色创建流程反馈），模拟新手体验
- --no-shop          不买商店（模拟不知道商店/金币不足的新手）
- --no-equip         只用手上默认武器（弹簧折刀），不自动装备
- --probe            战斗中随机注入非法指令（如「攻击」「暴击」「施法s999」）捕获反馈文本
- --event-strategy   事件选项策略：best（收益最大化）/ worst（专挑最坑）/ random / first
- --json-summary     输出结构化逐日 JSON（数值/难度曲线/挫败感分析用）
- --policy           新增 random（随机行动）、confused（随机+偶尔非法输入）
- --full             关闭描述截断（叙事分析用）

用法:
    python test/scripts/playtest_agent.py --qq test_play_melee --build melee --seed 7 --gold 200 \
        --out test/logs/melee_7.log --json-summary test/logs/melee_7.json
"""

import argparse
import json
import random
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # 项目根（util / database）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "plugins" / "StoryTeller"))  # src 包

from src.models.item import Equipment
from src.models.monster import Monster, monster_repo
from src.models.player import (
    CreateInvestigator,
    Investigator,
    InvestigatorGenerator,
    investigator_repo,
)
from src.services.battle import BattleService
from src.services.data_loader import data_loader
from src.services.event_service import (
    apply_event_choice,
    delta_value,
    pick_random_event,
)
from src.services.resurrect import do_resurrect
from src.services.sanity import run_sanity_and_madness
from src.services.shop_service import shop_service

from database.db import get_info

# ---- 角色构建 ----
MELEE_PREF = ["110", "106", "104", "102", "101"]
GUN_PREF = ["15", "10", "9", "6", "4", "7", "12", "11", "13", "14", "3", "2", "1", "5", "8"]
ARMOR_PREF = ["303", "302", "305", "301", "304", "400"]

BUILDS = {
    "melee": {
        "attrs": {"力量": 80, "体质": 85, "体型": 75, "敏捷": 60, "意志": 70},
        "skills": {"格斗": 90, "闪避": 55, "侦查": 50, "急救": 60, "手枪": 30, "步枪": 30},
        "buy": ["106", "104", "501"],
        "policy": "melee",
    },
    "gun": {
        "attrs": {"敏捷": 85, "体质": 65, "体型": 60, "力量": 55, "意志": 65},
        "skills": {"手枪": 90, "步枪": 75, "格斗": 40, "闪避": 55, "侦查": 60, "急救": 50},
        "buy": ["104", "501"],
        "policy": "gun",
    },
    "mage": {
        "attrs": {"智力": 90, "意志": 85, "教育": 75, "体质": 60, "体型": 55},
        "skills": {"急救": 70, "医学": 60, "格斗": 40, "闪避": 40, "侦查": 55, "手枪": 30},
        "buy": ["106", "501"],
        "policy": "mage",
    },
    "dodge": {
        "attrs": {"敏捷": 90, "体质": 70, "体型": 60, "力量": 50, "意志": 65},
        "skills": {"闪避": 90, "格斗": 55, "侦查": 50, "急救": 55, "手枪": 35},
        "buy": ["106", "501"],
        "policy": "dodge",
    },
    "balance": {
        "attrs": {"力量": 65, "体质": 65, "体型": 65, "敏捷": 65, "意志": 65, "智力": 65},
        "skills": {"格斗": 65, "手枪": 55, "闪避": 45, "侦查": 55, "急救": 50, "步枪": 40},
        "buy": ["104", "501"],
        "policy": "balance",
    },
    "fumble": {
        "attrs": {"力量": 45, "体质": 45, "体型": 50, "敏捷": 40, "意志": 30},
        "skills": {"格斗": 1, "手枪": 1, "闪避": 1, "侦查": 1, "急救": 1, "步枪": 1},
        "buy": ["501"],
        "policy": "fumble",
    },
}

MAX_TURNS = 200

# 非法/越权指令池（用于 probe / confused，捕获反馈文本）
INVALID_COMMANDS = [
    "攻击", "暴击", "平砍", "施法s999", "施法", "使用 999", "999", "哈", "换弹药",
    "三连射击", "挥砍", "跑", "防御", "格斗 80",
]


class Log:
    def __init__(self, path: Path):
        self.f = open(path, "w", encoding="utf-8")

    def line(self, s: str = ""):
        try:
            print(s)
        except UnicodeEncodeError:
            pass
        self.f.write(s + "\n")
        self.f.flush()

    def close(self):
        self.f.close()


def make_character(qq: str, build: dict, gold: int, log: Log, day: int = 1) -> Investigator:
    inherited = investigator_repo.collect_inherited_scrolls(qq)
    investigator_repo.delete_by_qq(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    model = investigator_repo.create_and_save(qq, build["name"], data)
    model.day = day
    model.save()
    inv = Investigator(model)
    for k, v in build["attrs"].items():
        inv.set_skill(k, v)
    for k, v in build["skills"].items():
        inv.set_skill(k, v)
    inv.set_skill("san", inv.get_skill("意志", 0))
    inv.restore_hp()
    inv.save()
    for item_id, qty in inherited:
        inv.add_item_to_inventory(item_id, qty)
        log.line(f"[继承] 残卷/道具 {item_id} x{qty} 随角色继承")
    from database.db import add_gold

    add_gold(qq, gold)
    log.line(f"[创建] {build['name']} 第{day}天 HP={inv.hp}/{inv.get_max_hp()} "
             f"SAN={inv.get_skill('san',0)}/{(inv.get_skill('意志',0) or inv.get_skill('san',0))} "
             f"DB={inv.db} 金币={gold} 属性={ {k: inv.get_skill(k) for k in ['力量','体质','敏捷','意志','智力']} }")
    return Investigator.load(qq)


def simulate_tutorial(qq: str, log: Log) -> None:
    """模拟新手流程：/冒险帮助 全文 + /创建调查员 选择 + /st 技能分配反馈。"""
    t = data_loader.get_text
    log.line("\n" + "=" * 70)
    log.line("【新手教程模拟】玩家输入 /冒险帮助")
    log.line("-" * 70)
    for section in ["help.title", "help.intro_title", "help.intro", "help.cmd_title",
                    "help.cmd_table", "help.battle_title", "help.battle_table",
                    "help.attr_title", "help.attr_table", "help.sanity_title", "help.sanity",
                    "help.shop_title", "help.shop", "help.goal_title", "help.goal"]:
        log.line(f"[帮助] {t(section)}")

    log.line("\n" + "=" * 70)
    log.line("【新手教程模拟】玩家输入 /创建调查员 → 3 名候选")
    candidates = InvestigatorGenerator.generate_investigator_data(3)
    for i, c in enumerate(candidates, 1):
        log.line(f"[候选{i}] " + " ".join(f"{k}:{v}" for k, v in c.items()
                                          if k in ["力量", "体质", "体型", "敏捷", "意志", "智力", "教育", "san", "hp", "db", "格斗", "手枪", "闪避", "侦查"]))
    creator = CreateInvestigator(3)
    creator.choose_investigator(1)

    log.line("\n[技能分配] 玩家输入 /st 格斗30手枪20（用不满全部点数）")
    ok, msg = creator.set_skill("格斗30手枪20")
    log.line(f"[反馈] ok={ok} | {msg}")
    log.line("\n[技能分配] 玩家输入 /st 格斗99（超上限）")
    ok, msg = creator.set_skill("格斗99")
    log.line(f"[反馈] ok={ok} | {msg}")
    log.line("\n[技能分配] 玩家输入 /st 飞升术50（不存在技能）")
    ok, msg = creator.set_skill("飞升术50")
    log.line(f"[反馈] ok={ok} | {msg}")
    points = creator.skill_point
    log.line(f"\n[技能分配] 玩家按格式输入 /st 手枪30步枪20 剩余点数={points - 50} →")
    ok, msg = creator.set_skill("手枪30步枪20")
    log.line(f"[反馈] ok={ok} | {msg}")
    log.line("[新手教程模拟结束]")


def equip_best(qq: str, inv: Investigator, build: dict, log: Log):
    bag, _ = inv.get_equipments()
    order = MELEE_PREF + GUN_PREF
    if build["policy"] == "gun":
        order = GUN_PREF + MELEE_PREF
    chosen = {"近战": None, "远程": None, "防具": None}
    for item_id in order:
        if item_id not in bag:
            continue
        if chosen["远程"] is None and Equipment(item_id).part == "远程":
            chosen["远程"] = item_id
        if chosen["近战"] is None and Equipment(item_id).part == "近战":
            chosen["近战"] = item_id
    for item_id in ARMOR_PREF:
        if item_id in bag and chosen["防具"] is None:
            chosen["防具"] = item_id
    for slot, item_id in chosen.items():
        if item_id is None:
            continue
        ok, msg = investigator_repo.equip_item(qq, item_id)
        if ok:
            log.line(f"[装备] {slot} → {item_id} ({Equipment(item_id).name} 伤害={Equipment(item_id).damage_dice})")
    inv.update_equipment()


def shop_buy(qq: str, build: dict, log: Log):
    gold = get_info(qq).gold
    shop_items = shop_service.get_todays_shop()
    for item_id in build["buy"]:
        if item_id not in shop_items:
            continue
        price = shop_items[item_id]
        if item_id == "501":
            bag, _ = Investigator.load(qq).get_equipments()
            if "501" in bag:
                continue
        if gold >= price:
            ok, msg = shop_service.buy_item(qq, item_id, 1, shop_items)
            if ok:
                gold -= price
                log.line(f"[购买] {item_id} ({Equipment(item_id).name}) 花费{price}，余额{gold} | {msg}")


def _score_event_option(opt: dict) -> float:
    """事件选项贪心评分：正收益高分为好，负收益低分为坑（骰子表达式取期望值）。"""
    e = opt.get("效果") or {}
    s = 0.0
    s += delta_value(e.get("san", 0)) if e.get("san") is not None else 0
    s += delta_value(e.get("hp", 0)) if e.get("hp") is not None else 0
    s += (delta_value(e.get("金币", 0)) if e.get("金币") is not None else 0) / 10.0
    s += sum((e.get("技能") or {}).values())
    if e.get("物品"):
        s += 8.0
    check = opt.get("检定")
    if check:
        r = check.get("奖励") or {}
        f = check.get("失败") or {}
        sr = (delta_value(r.get("san", 0)) if r.get("san") is not None else 0)
        sr += (delta_value(r.get("hp", 0)) if r.get("hp") is not None else 0)
        sr += (delta_value(r.get("金币", 0)) if r.get("金币") is not None else 0) / 10.0
        sr += sum((r.get("技能") or {}).values())
        sf = (delta_value(f.get("san", 0)) if f.get("san") is not None else 0)
        sf += (delta_value(f.get("hp", 0)) if f.get("hp") is not None else 0)
        sf += (delta_value(f.get("金币", 0)) if f.get("金币") is not None else 0) / 10.0
        sf += sum((f.get("技能") or {}).values())
        s += 0.5 * sr + 0.5 * sf
        if opt.get("跳过战斗"):
            s += 25.0
    return s


def pick_event_option(ev: dict, inv: Investigator, strategy: str, log: Log):
    options = ev.get("选项") or []
    if not options:
        return None
    if strategy == "random":
        return random.choice(options)
    if strategy == "first":
        return options[0]
    if strategy in ("best", "worst"):
        scored = sorted(options, key=_score_event_option)
        return scored[-1] if strategy == "best" else scored[0]
    return random.choice(options)


def pick_action(service: BattleService, policy: str, probe: bool, log: Log) -> str:
    inv = service.investigator
    if service.current_turn == "mon":
        hp_pct = service.hp_record["inv"] / max(1, inv.get_max_hp())
        if policy == "dodge":
            return "闪避"
        if hp_pct < 0.3:
            return "闪避"
        return "反击"

    if probe and random.random() < 0.12:
        return random.choice(INVALID_COMMANDS)

    acts = service.get_available_actions_for_turn()
    hp_pct = service.hp_record["inv"] / max(1, inv.get_max_hp())

    if policy == "fumble":
        for a in ["格斗", "射击", "二连射"]:
            if a in acts:
                return a
        return "格斗" if "格斗" in acts else ("逃跑" if acts else "格斗")

    if policy == "random":
        if random.random() < 0.15:
            return "逃跑"
        return random.choice(acts) if acts else "格斗"

    if policy == "confused":
        if random.random() < 0.25:
            return random.choice(INVALID_COMMANDS)
        return random.choice(acts) if acts else "格斗"

    if policy == "mage":
        spells = inv.get_spells()
        if spells and service.mp > 0:
            return f"施法 {spells[0]}"
        if hp_pct < 0.2:
            return "逃跑"
        return "格斗" if "格斗" in acts else ("逃跑" if acts else "格斗")

    if policy == "gun":
        if service.gun and service.gun.is_valid:
            for a in ["三连射", "二连射", "射击", "换弹"]:
                if a not in acts:
                    continue
                if a == "三连射" and service.bullet < 3:
                    continue
                if a == "二连射" and service.bullet < 2:
                    continue
                if a == "射击" and service.bullet < 1:
                    continue
                if a == "换弹" and service.max_bullet > 0 and service.bullet >= service.max_bullet:
                    continue
                return a
        if hp_pct < 0.2:
            return "逃跑"
        return "格斗" if "格斗" in acts else ("逃跑" if acts else "格斗")

    if policy == "melee":
        if hp_pct < 0.15:
            return "逃跑"
        return "格斗" if "格斗" in acts else ("逃跑" if acts else "格斗")

    if policy == "dodge":
        if hp_pct < 0.15:
            return "逃跑"
        return "格斗" if "格斗" in acts else ("逃跑" if acts else "格斗")

    # balance
    if hp_pct < 0.2:
        return "逃跑"
    if service.gun and service.gun.is_valid and service.bullet >= 2:
        for a in ["二连射", "射击"]:
            if a in acts:
                return a
    return "格斗" if "格斗" in acts else ("逃跑" if acts else "格斗")


def run_day(qq: str, build: dict, log: Log, args) -> tuple[str, dict]:
    inv = Investigator.load(qq)
    day = inv.day
    gold = get_info(qq).gold
    log.line(f"\n{'='*70}\n【第 {day} 天】 {inv.name} HP={inv.hp}/{inv.get_max_hp()} "
             f"SAN={inv.get_skill('san',0)}/{(inv.get_skill('意志',0) or inv.get_skill('san',0))} 金币={gold} "
             f"MP={inv.get_skill('意志',0)//5}")

    day_rec = {
        "day": day, "env": None, "event": None, "event_choice": None, "event_reply": None,
        "monster_id": None, "monster_name": None, "monster_hp": None, "monster_damage": None,
        "monster_san_loss": None, "outcome": None, "turns": 0, "hp_before": inv.hp,
        "san_before": inv.get_skill("san", 0), "gold_before": gold, "hp_after": None,
        "san_after": None, "gold_after": None, "san_loss": 0, "madness": False,
        "madness_duration": 0, "growth": {}, "loot": None, "player_dmg_taken": 0,
        "monster_dmg_taken": 0, "fled": False, "low_hp_turns": 0, "feedback_errors": [],
        "weapon_broken": False,
    }

    inv.restore_hp()
    inv.is_adventure = True
    inv.save()

    # 环境
    env_name = random.choice(list(data_loader.environment_data.keys()))
    env = data_loader.environment_data[env_name].copy()
    env["name"] = env_name
    day_rec["env"] = env_name
    log.line(f"环境: 【{env_name}】{env.get('描述','')} 修正=玩家{env.get('玩家')} 怪物{env.get('怪物')}")

    # 怪物
    mid = monster_repo.find_random_id_for_day(day)
    if not mid:
        log.line("!! 无当日怪物配置")
        day_rec["outcome"] = "no_monster"
        return "no_monster", day_rec
    monster = Monster(mid)
    day_rec.update(monster_id=mid, monster_name=monster.name, monster_hp=monster.hp,
                   monster_damage=monster.damage_preview(), monster_san_loss=monster.san_loss)
    log.line(f"怪物: [{mid}] {monster.name} HP={monster.hp} 装甲={monster.armor} 敏捷={monster.dex} "
             f"SAN损失={monster.san_loss} 伤害={monster.damage_preview()} 出场文本={str(monster.出场)[:80]}")

    # 奇遇事件
    ev = pick_random_event(inv)
    if ev:
        day_rec["event"] = ev.get("名称") or next((k for k, v in data_loader.event_data.items() if v is ev), "?")
        log.line(f"奇遇: {ev.get('描述')!s}")
        for opt in ev.get("选项") or []:
            log.line(f"  选项[{opt.get('输入')}] 效果={opt.get('效果')} 检定={opt.get('检定')} "
                     f"跳过战斗={opt.get('跳过战斗')}")
        choice = pick_event_option(ev, inv, args.event_strategy, log)
        if choice:
            day_rec["event_choice"] = choice.get("输入")
            reply, skip = apply_event_choice(inv, qq, choice)
            day_rec["event_reply"] = reply
            log.line(f"选择[{choice.get('输入')}] → {reply}")
            if skip:
                log.line("事件成功 → 跳过今日战斗")
                inv.is_adventure = False
                inv.save()
                day_rec["outcome"] = "skip"
                day_rec["hp_after"] = inv.hp
                day_rec["san_after"] = inv.get_skill("san", 0)
                day_rec["gold_after"] = get_info(qq).gold
                return "skip", day_rec
    else:
        log.line("今日无奇遇")

    # 理智检定
    san_desc, madness_desc, is_mad, mad_dur, san_zero, san_loss = run_sanity_and_madness(
        inv, monster
    )
    day_rec["san_loss"] = san_loss
    log.line(f"理智: {str(san_desc)[:200]}")
    if madness_desc:
        day_rec["madness"] = True
        day_rec["madness_duration"] = mad_dur
        log.line(f"疯狂: {str(madness_desc)[:200]} ({mad_dur}回合)")
    if san_zero:
        inv.is_survive = False
        inv.save()
        day_rec["outcome"] = "san_zero"
        log.line("!! SAN 归零 → 永久疯狂死亡")
        return "san_zero", day_rec

    # 战斗
    service = BattleService(inv, monster)
    if env:
        service.set_environment(env)
    service.roll_initiative()
    log.line(f"先手: {service.current_turn}")
    turns = 0
    hp_start = inv.hp
    while not service.fight_is_over() and turns < MAX_TURNS:
        turns += 1
        action = pick_action(service, build["policy"], args.probe, log)
        try:
            result = service.execute_action(action)
        except Exception:
            log.line(f"!! 战斗异常 action={action}:\n{traceback.format_exc()}")
            day_rec["outcome"] = "error"
            return "error", day_rec
        for part in result:
            if part:
                text = str(part)
                if len(text) <= 120 and any(
                    k in text for k in ("未知", "无效", "不足", "没有", "未学会", "法力不足")
                ):
                    day_rec["feedback_errors"].append(text.strip().replace("\n", " "))
                if args.full:
                    log.line(f"  [{action}] {text}")
                else:
                    log.line(f"  [{action}] {text.replace(chr(10), ' ⏎ ')[:400]}")
        if service.is_madness and service.madness_duration > 0:
            log.line(f"（疯狂失控自动结算中，剩余 {service.madness_duration} 回合）")
    if turns >= MAX_TURNS:
        log.line("!! 达到回合上限（可能死循环）")
    day_rec["turns"] = turns
    day_rec["player_dmg_taken"] = hp_start - max(0, service.hp_record["inv"])
    day_rec["monster_dmg_taken"] = monster.max_hp - max(0, service.hp_record["mon"])

    if service.hp_record["mon"] <= 0:
        outcome = "victory"
    elif service.hp_record["inv"] <= 0:
        outcome = "death"
    else:
        outcome = "fled"
        day_rec["fled"] = True
    day_rec["outcome"] = outcome
    log.line(f"结果: {outcome} | 玩家HP={service.hp_record['inv']}/{inv.get_max_hp()} "
             f"SAN={inv.get_skill('san',0)} 怪物HP={service.hp_record['mon']} 回合数={turns}")
    if outcome == "victory":
        inv.is_adventure = False
        inv.save()
        day_rec["growth"] = service.end_card_ext.get("growth") or {}
        try:
            day_rec["loot"] = str(service.end_card_ext.get("bonus"))[:150]
        except Exception:
            pass
        log.line(f"结算标题: {str(service.end_parts[0])[:150]}")
        log.line(f"结算明细: {str(service.end_parts[1])[:500]}")
        log.line(f"成长: {service.end_card_ext.get('growth')}")
        log.line(f"战利品: {str(service.end_card_ext.get('bonus'))[:150]}")
        log.line(f"新天数: {inv.day}")
    elif outcome == "fled":
        inv.is_adventure = False
        inv.save()
    day_rec["hp_after"] = inv.hp
    day_rec["san_after"] = inv.get_skill("san", 0)
    day_rec["gold_after"] = get_info(qq).gold
    return outcome, day_rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qq", required=True)
    ap.add_argument("--build", required=True, choices=list(BUILDS))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--gold", type=int, default=200)
    ap.add_argument("--out", required=True)
    ap.add_argument("--target-day", type=int, default=30)
    ap.add_argument("--tutorial", action="store_true", help="开头输出新手教程全文")
    ap.add_argument("--no-shop", action="store_true", help="不购买商店物品")
    ap.add_argument("--no-equip", action="store_true", help="不自动装备背包物品")
    ap.add_argument("--probe", action="store_true", help="战斗中注入非法指令收集反馈")
    ap.add_argument("--event-strategy", choices=["best", "worst", "random", "first"], default="random")
    ap.add_argument("--json-summary", default="")
    ap.add_argument("--full", action="store_true", help="关闭战斗文本截断")
    args = ap.parse_args()

    random.seed(args.seed)
    log = Log(Path(args.out))
    build = BUILDS[args.build]
    build["name"] = {"melee": "铁拳", "gun": "猎枪", "mage": "学者", "dodge": "夜枭",
                     "balance": "游侠", "fumble": "倒霉蛋"}[args.build]

    if args.tutorial:
        simulate_tutorial(args.qq, log)

    stats = {"victory": 0, "death": 0, "fled": 0, "skip": 0, "error": 0, "san_zero": 0,
             "lives": 0, "resurrect": 0, "restart": 0}
    days_rec = []
    current_day = 1
    calendar = 0
    alive = False

    while current_day <= args.target_day and calendar < 60:
        calendar += 1
        if not alive or investigator_repo.find_by_qq(args.qq) is None:
            stats["lives"] += 1
            make_character(args.qq, build, args.gold, log, day=current_day)
            alive = True
        if not args.no_equip:
            equip_best(args.qq, Investigator.load(args.qq), build, log)
        if not args.no_shop:
            shop_buy(args.qq, build, log)
            if not args.no_equip:
                equip_best(args.qq, Investigator.load(args.qq), build, log)
        outcome, day_rec = run_day(args.qq, build, log, args)
        stats[outcome] += 1
        days_rec.append(day_rec)

        inv = Investigator.load(args.qq)
        if outcome == "victory":
            current_day = inv.day
        elif outcome == "death" or outcome == "san_zero":
            alive = False
            bag, _ = inv.get_equipments()
            if "501" in bag:
                msg = do_resurrect(args.qq)
                stats["resurrect"] += 1
                log.line(f"复活: {msg}")
                alive = True
                current_day += 1
                inv = Investigator.load(args.qq)
                inv.day = current_day
                inv.save()
            else:
                stats["restart"] += 1
                log.line(f"无复活道具 → 重开新角色（从第 {current_day} 天继续）")
                make_character(args.qq, build, args.gold, log, day=current_day)
                alive = True
        elif outcome in ("fled", "skip", "no_monster", "error"):
            current_day += 1
            inv = Investigator.load(args.qq)
            inv.day = current_day
            inv.save()

    final = Investigator.load(args.qq)
    log.line(f"\n{'#'*70}\n模拟结束: 最终第 {final.day} 天 | {stats}")
    log.line(f"装备: {final._equipped} | 法术: {final.get_spells()} | 金币: {get_info(args.qq).gold} | 存活: {final.is_survive}")
    log.close()

    if args.json_summary:
        summary = {
            "qq": args.qq, "build": args.build, "seed": args.seed, "gold": args.gold,
            "target_day": args.target_day, "tutorial": args.tutorial, "no_shop": args.no_shop,
            "no_equip": args.no_equip, "probe": args.probe, "event_strategy": args.event_strategy,
            "stats": stats, "days": days_rec,
            "final": {"day": final.day, "gold": get_info(args.qq).gold,
                      "spells": final.get_spells(),
                      "skills": {k: final.get_skill(k) for k in
                                 ["格斗", "手枪", "步枪", "闪避", "侦查", "急救", "意志", "敏捷", "克苏鲁神话"]},
                      "equipped": final._equipped, "survive": final.is_survive},
        }
        Path(args.json_summary).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_summary, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)
        print(f"[json] wrote {args.json_summary}")


if __name__ == "__main__":
    main()
