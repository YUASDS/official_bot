"""法术系统定向验证：MP/施法/对抗/效果/学习/持久化。"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.models.player import (
    CreateInvestigator,
    Investigator,
    investigator_repo,
)
from src.models.monster import Monster
from src.services.battle import BattleService
from src.services.data_loader import data_loader

random.seed(7)
ci = CreateInvestigator(1)
ci.choose_investigator(1)
qq = "spell_test"
investigator_repo.delete_by_qq(qq)
inv = ci.create_investigator(qq, "测试员")
inv.set_skill("意志", 50)  # MP = 10
inv.set_skill("智力", 99)  # 学习/施法必成
inv.set_skill("san", 30)
inv.save()

m = Monster("2")
svc = BattleService(inv, m)
svc.current_turn = "inv"

# 1. MP 初始化与不回复
assert svc.max_mp == 10 and svc.mp == 10, f"MP init: {svc.max_mp}"
svc._end_turn()
assert svc.mp == 10, "MP must not recover"
svc.current_turn = "inv"  # 恢复玩家回合
print("1. MP init no recovery: PASS")

# 2. 未学会 → 拒绝
res = svc.execute_action("施法s001")
assert "尚未学会" in str(res[0])
print("2. not learned rejected: PASS")

# 3. 学习 s002（愈合术）
inv.add_item_to_inventory("702", 1)
learn = svc._learn_scrolls()
assert any("愈合术" in x for x in learn), learn
assert inv.has_spell("s002"), "s002 learned"
assert "702" not in inv.get_equipments()[0], "scroll consumed"
print("3. learn heal success: PASS")

# 4. 学习失败：残卷保留 + SAN 扣
inv.set_skill("智力", 0)
inv.add_item_to_inventory("701", 1)
inv.set_skill("san", 30)
learn = svc._learn_scrolls()
assert inv.has_spell("s001") is False, "wither must not be learned"
assert "701" in inv.get_equipments()[0], "scroll retained"
assert inv.get_skill("san") == 28, f"san -2 expected, got {inv.get_skill('san')}"
print("4. learn fail: scroll retained, san deducted: PASS")
inv.set_skill("智力", 99)

# 5. 施法愈合术（非对抗）：回复 HP 封顶
inv.hp = 5
inv._model.hp = 5
svc.hp_record["inv"] = 5
svc.current_turn = "inv"
res = svc.execute_action("施法s002")
out = "\n".join(str(x) for x in res)
assert "愈合术" in out and "MP" in out
assert svc.mp == 8, f"mp after heal: {svc.mp}"
assert svc.hp_record["inv"] >= 5 and svc.hp_record["inv"] <= inv.get_max_hp()
print("5. cast heal: PASS")

# 6. 肉体守护：temp_hp + 伤害先抵护盾
inv.set_skill("智力", 99)
inv.add_item_to_inventory("703", 1)
svc._learn_scrolls()
assert inv.has_spell("s003"), "s003 must be learned"
svc.current_turn = "inv"
svc.execute_action("施法s003")
svc.current_turn = "inv"
assert svc.temp_hp >= 3, f"temp_hp: {svc.temp_hp}"
hp_before = svc.hp_record["inv"]
svc._apply_damage_to_player(4)
assert svc.hp_record["inv"] == hp_before, "damage must be absorbed by temp_hp"
print("6. temp_hp absorbs damage: PASS")

# 7. 对抗失败：法术无效，MP/SAN 已扣
inv.set_skill("智力", 0)
mp_before = svc.mp
svc.set_environment({"玩家": {}, "怪物": {}})
random.seed(3)
svc.current_turn = "inv"
res = svc.execute_action("施法s001")
svc.current_turn = "inv"
out = "\n".join(str(x) for x in res)
assert "法术在对抗中溃散" in out or "枯萎术" in out
assert svc.mp == mp_before - 2, f"mp consumed on fail: {svc.mp}"
print("7. contested fail consumes resources: PASS")
inv.set_skill("智力", 99)

# 8. 枯萎术成功：怪物掉血
mon_hp_before = svc.hp_record["mon"]
random.seed(5)
svc.current_turn = "inv"
res = svc.execute_action("施法s001")
svc.current_turn = "inv"
out = "\n".join(str(x) for x in res)
assert svc.hp_record["mon"] < mon_hp_before, f"monster must take damage: {out[:100]}"
print("8. wither damage: PASS")

# 9. spells 持久化
inv.save()
reloaded = Investigator.load(qq)
assert reloaded.has_spell("s002"), "spells persisted"
print("9. spells persisted: PASS")

# 10. SAN 不足拒绝施法（s003 san_cost 1）
svc.mp = svc.max_mp
inv.set_skill("san", 0)
svc.investigator.set_skill("san", 0)
svc.current_turn = "inv"
res = svc.execute_action("施法s003")
assert "理智不足" in str(res[0])
print("10. insufficient san rejected: PASS")

investigator_repo.delete_by_qq(qq)
print("ALL PASS")
