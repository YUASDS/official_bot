"""文本审计：收集玩家实际看到的完整输出文本，检查格式问题"""
import io
import random
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "plugins" / "StoryTeller"))

random.seed(1)

from src.models.player import (
    CreateInvestigator,
    Investigator,
    InvestigatorFormatter,
    investigator_repo,
)
from src.models.monster import Monster, monster_repo
from src.models.item import Equipment
from src.services.battle import BattleService
from src.services.sanity import perform_sanity_check
from src.services.shop_service import shop_service
from src.services.data_loader import data_loader

QQ = "text_audit"
investigator_repo.delete_by_qq(QQ)
ci = CreateInvestigator(3)
ci.choose_investigator(1)
sp = ci.skill_point
alloc = {}
remaining = sp
for sk in ["格斗", "手枪", "闪避", "侦查"]:
    base = ci.select.get(sk, 0)
    give = min(75 - base, remaining)
    if give > 0:
        alloc[sk] = give
        remaining -= give
    if remaining <= 0:
        break
if remaining > 0:
    alloc["急救"] = min(45, remaining)
ok, msg = ci.set_skill("".join(f"{k}{v}" for k, v in alloc.items()))
assert ok, msg
inv = ci.create_investigator(QQ, "文本审计员")

print("=" * 70)
print("[1] 创建角色输出（create/choose/st 流程）")
print("=" * 70)
print(repr(InvestigatorFormatter.format_investigator_info("文本审计员", ci.investigators_data)))
print()
print(repr(InvestigatorFormatter.format_investigator_info("文本审计员", ci.select)))
print()

print("=" * 70)
print("[2] 冒险开场（header + 怪物出场 + SC + 先攻）")
print("=" * 70)
day = 3
monster = Monster(monster_repo.find_random_id_for_day(day))
monster_intro = getattr(monster, "出场", f"一只{monster.name}出现了！")
day_event = data_loader.get_event(day)
env_key = random.choice(list(data_loader.environment_data.keys()))
env = data_loader.environment_data[env_key].copy()
env["name"] = env_key
env_desc = f"【{env_key}】{env.get('描述', '')}"
header = f"\n{env_desc}\n{day_event}" if env_desc else f"\n{day_event}"
print(f"env_desc = {env_desc!r}")
print(f"day_event = {day_event[:60]!r}...")
san_passed, san_desc, _ = perform_sanity_check(inv, monster)
print(f"sanity desc = {san_desc!r}")
print(f"monster_intro = {monster_intro[:60]!r}...")
print()

service = BattleService(inv, monster)
service.set_environment(env)
start_text = service.start_turn()
print("--- start_turn 完整文本 ---")
print(repr(start_text))
print()

print("=" * 70)
print("[3] 战斗回合文本（格斗/射击/防御/逃跑/换弹）")
print("=" * 70)
for action in ["格斗", "反击", "闪避", "射击", "换弹", "逃跑", "三连射"]:
    service.current_turn = "inv" if action in ("格斗", "射击", "换弹", "三连射", "逃跑") else "mon"
    result = service.execute_action(action)
    joined = "\n".join([str(x) for x in result if x])
    print(f"--- /行动 {action} ---")
    print(joined)
    print()

print("=" * 70)
print("[4] 胜利结算")
print("=" * 70)
service.hp_record["mon"] = 0
print(repr(service._handle_victory()))
print()

print("=" * 70)
print("[5] 商店与背包")
print("=" * 70)
print(repr(shop_service.format_shop_text(shop_service.get_todays_shop())))
print()
print(inv.str_equipments())
print()

investigator_repo.delete_by_qq(QQ)
