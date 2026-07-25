# StoryTeller 完整重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `old_src/` 全部功能迁移到 `src/` 分层架构，保留新加的理智/疯狂系统，最终删除 `old_src/`

**Architecture:** 自底向上 5 层重构，每层完成后跑测试验证，下层稳定后再重构上层。保持现有 `src/` 的分层结构（models → services → plugins → utils）。

**Tech Stack:** Python 3.9+, NoneBot2, Peewee + SQLite, Pydantic, ujson, loguru

**Spec:** `docs/superpowers/specs/2026-07-26-storyteller-refactor-design.md`

---

### Task 1: 骰子系统 — 修复 `roll_dice` 复合表达式 + 新增 `BonusDiceRoll`

**Files:**
- Modify: `plugins/StoryTeller/src/services/dice_roller.py`
- Create: `test/test_dice_roller.py`

- [ ] **Step 1: 写失败测试 — 复合表达式**

```python
# test/test_dice_roller.py
import sys
sys.path.insert(0, "plugins/StoryTeller")

from src.services.dice_roller import roll_dice

def test_compound_expression():
    expr, val = roll_dice("1d8+2d6+3")
    assert isinstance(val, int)
    assert "=" in expr or val >= 6  # min: 1+2+3=6

def test_bonus_dice_roll():
    from src.services.dice_roller import BonusDiceRoll
    roll = BonusDiceRoll(skill=50, bonus_dice=2)
    assert roll.final_result <= roll.dice
    assert 1 <= roll.final_result <= 100

def test_d_without_number():
    expr, val = roll_dice("d4")
    assert 1 <= val <= 4
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd E:\python\official_bot && python -m pytest test/test_dice_roller.py -v`

- [ ] **Step 3: 重写 `roll_dice()` — 支持复合表达式**

参考 `old_src/dice.py:199-287` 的手动解析逻辑：
1. 用循环逐字符解析 `+-` 分隔符，拆分为 `parts` 列表（值和操作符交错）
2. 对每个 `XdY` 部分：`d4` 自动补全为 `1d4`
3. 按操作符顺序累加/减，记录每步详情
4. 返回 `(detail_str, total)`

```python
def roll_dice(dice_expression: str, use_max: bool = False) -> Tuple[str, int]:
    def _roll_single(part: str) -> Tuple[int, str]:
        part = part.strip()
        if "d" in part:
            if part.startswith("d"):
                count, sides = 1, int(part[1:])
            else:
                count_str, sides_str = part.split("d")
                count = int(count_str) if count_str else 1
                sides = int(sides_str)
            if use_max:
                rolls = [sides] * count
                return count * sides, "+".join(map(str, rolls))
            else:
                rolls = [random.randint(1, sides) for _ in range(count)]
                return sum(rolls), "+".join(map(str, rolls))
        else:
            return int(part), part

    expression = dice_expression.lower().replace(" ", "")
    if expression.replace("+", "").replace("-", "").isdigit():
        return expression, int(expression)

    parts = []
    current = ""
    for ch in expression:
        if ch in "+-" and current:
            parts.append(current)
            parts.append(ch)
            current = ""
        else:
            current += ch
    if current:
        parts.append(current)

    if len(parts) == 1:
        val, detail = _roll_single(parts[0])
        return detail, val

    total = 0
    details = []
    op = "+"
    for part in parts:
        if part in "+-":
            op = part
        else:
            val, detail = _roll_single(part)
            if op == "+":
                total += val
                details.append(f"+{detail}")
            else:
                total -= val
                details.append(f"-{detail}")
    if details and details[0].startswith("+"):
        details[0] = details[0][1:]
    return "".join(details), total
```

- [ ] **Step 4: 新增 `BonusDiceRoll` 类**

参考 `old_src/dice.py:101-128`：

```python
class BonusDiceRoll(DiceRoll):
    def __init__(self, skill: int, bonus_dice: int):
        self.bonus_dice_count = bonus_dice
        super().__init__(skill)
        self._apply_bonus_dice()
        self.level = self._calculate_success_level(skill, self.final_result)

    def _apply_bonus_dice(self):
        self.bonus_rolls = []
        self.final_result = self.dice
        for _ in range(self.bonus_dice_count):
            bonus_val = random.randint(0, 9)
            self.bonus_rolls.append(bonus_val)
            bonus_result = self.dice
            if self.dice // 10 > bonus_val:
                bonus_result = self.dice % 10 + bonus_val * 10
            self.final_result = min(bonus_result, self.final_result)
```

- [ ] **Step 5: 验证 `PenaltyDiceRoll` 与旧代码逻辑一致**

对比 `old_src/dice.py:131-158` 与当前实现：
- 确认使用 `max(penalty_roll, self.final_result)` 取最高十位（最差结果）
- 确认 `penalty_rolls` 列表追踪每次惩罚骰值
- 确认 `final_result` 为 `0` 时替换为 `100`

若不一致则按旧代码逻辑修正。

- [ ] **Step 6: 跑测试确认通过**

Run: `cd E:\python\official_bot && python -m pytest test/test_dice_roller.py -v`
Expected: all PASS

---

### Task 2: 装备模型 — 补全 `__str__` / `get_full_description` / `get_brief_description`

**Files:**
- Modify: `plugins/StoryTeller/src/models/item.py`
- Modify: `test/test_dice_roller.py` (追加装备测试)

- [ ] **Step 1: 在现有测试文件追加装备模型测试**

```python
def test_equipment_str():
    from src.models.item import Equipment
    e = Equipment("101")
    assert str(e) == e.name

def test_equipment_full_description():
    from src.models.item import Equipment
    e = Equipment("101")
    desc = e.get_full_description()
    assert "弹簧折刀" in desc

def test_equipment_brief_description():
    from src.models.item import Equipment
    e = Equipment("101")
    desc = e.get_brief_description()
    assert "ID" in desc or "伤害" in desc
```

- [ ] **Step 2: 实现 `__str__`、`get_full_description`、修复 `get_brief_description`**

参考 `old_src/Equipment.py:107-150`：

```python
# item.py Equipment class additions

def __str__(self) -> str:
    return self.name

def get_full_description(self) -> str:
    if not self.is_valid:
        return f"ID: {self.id}\n无效物品"
    attributes = [
        ("ID", self.id),
        ("名称", self.name),
        ("护甲", str(self.armor_point) if self.armor_point else ""),
        ("伤害", self.damage_dice),
        ("行动", ", ".join(self.skill_bonus) if isinstance(self.skill_bonus, list) else str(self.skill_bonus)),
        ("部位", self.part),
        ("鉴定技能", self.identify_skill),
        ("描述", self.description),
    ]
    non_empty = [(k, v) for k, v in attributes if v]
    return "\n".join(f"{k}: {v}" for k, v in non_empty)

def get_brief_description(self) -> str:
    if not self.is_valid:
        return f"ID: {self.id}\n无效物品"
    attributes = [
        ("ID", self.id),
        ("护甲", str(self.armor_point) if self.armor_point else ""),
        ("伤害", self.damage_dice),
        ("价格", str(self.price)),
    ]
    non_empty = [(k, v) for k, v in attributes if v]
    return " ".join(f"{k}: {v}" for k, v in non_empty)
```

- [ ] **Step 3: 跑测试确认通过**

Run: `cd E:\python\official_bot && python -m pytest test/test_dice_roller.py -v`
Expected: all PASS

---

### Task 3: 怪物模型 — `__getattr__` 代理、`generate_loot` 修复、`load_random_for_day`、错误处理

**Files:**
- Modify: `plugins/StoryTeller/src/models/monster.py`
- Modify: `test/test_dice_roller.py` (追加怪物测试)

- [ ] **Step 1: 追加怪物模型测试**

```python
def test_monster_dynamic_attr():
    from src.models.monster import Monster
    m = Monster("1")
    assert m.is_valid
    assert hasattr(m, "name") or m.名字 is not None

def test_monster_invalid_raises():
    import pytest
    from src.models.monster import Monster
    with pytest.raises(ValueError):
        Monster("nonexistent_99999")

def test_monster_load_random_for_day():
    from src.models.monster import Monster
    m = Monster.load_random_for_day(1)
    assert m.is_valid
```

- [ ] **Step 2: 重写 `Monster.__init__` 添加 `__getattr__` 和错误处理**

```python
class Monster:
    def __init__(self, monster_id: str):
        self.id = monster_id
        self._data = monster_repo.find_by_id(monster_id)
        if not self._data:
            raise ValueError(f"Monster ID '{monster_id}' not found in data.")

        self.is_valid = True
        self.name = self._data.get("name", "未知怪物")
        self.hp = self._data.get("hp", 10)
        self.max_hp = self.hp
        self.san_loss = self._data.get("san_loss", "0/0")
        self.description = self._data.get("intro", "一个看起来很恐怖的生物。")
        self.damage_dice = self._data.get("damage", "1d3")
        self.dex = self._data.get("dex", 50)
        self.str = self._data.get("str", 50)
        self.fight = self._data.get("fight", 50)
        self.armor = self._data.get("armor", 0)
        self.is_alive = True
        self.敏捷 = self.dex
        self.名字 = self.name

    def __getattr__(self, name):
        if name.startswith("_") or name in self.__dict__:
            raise AttributeError(name)
        return self._data.get(name, "")
```

- [ ] **Step 3: 修复 `generate_loot` 返回三元组**

参考 `old_src/GlobalData.py` 中 `get_event` 的逻辑生成掉落信息：

```python
def generate_loot(self):
    reward_data = self._data.get("奖励", {})
    gold_max = reward_data.get("乌帕", 10)
    gold = random.randint(1, gold_max)

    items = reward_data.get("物品", [])
    dropped_item = None
    message = ""
    if items:
        item_id = random.choice(items)
        from .item import Equipment
        dropped_item = Equipment(item_id)
        if dropped_item.is_valid:
            message = f"获得了 {dropped_item.name}（{dropped_item.get_brief_description()}）和 {gold} 乌帕。"
        else:
            message = f"获得了 {gold} 乌帕。"
    else:
        message = f"获得了 {gold} 乌帕。"

    return gold, dropped_item, message
```

- [ ] **Step 4: 添加 `load_random_for_day` 工厂方法**

```python
@classmethod
def load_random_for_day(cls, day: int) -> Optional[Monster]:
    monster_id = monster_repo.find_random_id_for_day(day)
    if not monster_id:
        return None
    try:
        return cls(monster_id)
    except ValueError:
        keys = list(monster_repo._monster_data.keys())
        if keys:
            return cls(random.choice(keys))
        return None
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd E:\python\official_bot && python -m pytest test/test_dice_roller.py -v`
Expected: all PASS

---

### Task 4: 玩家模型 — 完整重建（Repository 补全 + Investigator 域对象 + Generator/Formatter/CreateInvestigator）

**Files:**
- Modify: `plugins/StoryTeller/src/models/player.py`
- Modify: `test/test_dice_roller.py` → rename to `test/test_models.py`
- Create or reuse: `test/test_player.py`

- [ ] **Step 1: 分离测试文件，写玩家模型测试**

先 rename 测试文件避免随测试增多而混乱：
```bash
Move-Item test/test_dice_roller.py test/test_models.py
```

追加玩家测试：

```python
def test_investigator_repo_delete_by_qq():
    from src.models.player import investigator_repo, InvestigatorModel
    # 创建临时测试角色
    inv = investigator_repo.create_and_save("test_del_qq", "TestDel", InvestigatorGenerator.generate_investigator_data())
    assert investigator_repo.find_by_qq("test_del_qq") is not None
    investigator_repo.delete_by_qq("test_del_qq")
    assert investigator_repo.find_by_qq("test_del_qq") is None

def test_investigator_repo_equip_item():
    from src.models.player import investigator_repo
    # 先用已有角色测试
    inv = investigator_repo.find_by_qq("test_del_qq")  
    # if None, skip or create
    if inv:
        ok, msg = investigator_repo.equip_item(inv.qq, "101")
        assert ok

def test_investigator_break_equipped_item():
    from src.models.player import Investigator
    inv = Investigator.load("test_break_qq", "TestBreak")
    inv.break_equipped_item("格斗")
    # 验证近战槽位已清空
    assert inv.get_equipped_id("近战") is None

def test_investigator_get_full_attributes():
    from src.models.player import Investigator
    inv = Investigator.load("test_attr_qq", "TestAttr")
    attrs = inv.get_full_attributes_dict()
    assert "力量" in attrs
    assert "hp" in attrs

def test_investigator_mark_as_deceased():
    from src.models.player import Investigator
    inv = Investigator.load("test_dead_qq", "TestDead")
    inv.mark_as_deceased()
    assert not inv.is_survive

def test_generator_multiple():
    from src.models.player import InvestigatorGenerator
    data = InvestigatorGenerator.generate_investigator_data(3)
    assert len(data) == 3

def test_create_investigator_flow():
    from src.models.player import CreateInvestigator
    ci = CreateInvestigator(3)
    assert ci.choose_investigator(1)
    assert ci.set_skill("格斗 30 侦查 30 手枪 30")
```

- [ ] **Step 2: Repository 补全 — `delete_by_qq` + `equip_item`**

```python
# InvestigatorRepository additions

def delete_by_qq(self, qq: str) -> bool:
    inv_model = self.find_by_qq(qq)
    if not inv_model:
        return False
    with self.db.atomic():
        InventoryItemModel.delete().where(
            InventoryItemModel.investigator == inv_model
        ).execute()
        inv_model.delete_instance()
    return True

def equip_item(self, qq: str, item_id: str):
    inv_model = self.find_by_qq(qq)
    if not inv_model:
        return False, "调查员不存在"
    item_record = InventoryItemModel.select().where(
        (InventoryItemModel.investigator == inv_model) &
        (InventoryItemModel.item_id == item_id)
    ).first()
    if not item_record:
        return False, "背包中未找到物品"
    item = Equipment(item_id)
    part = item.part
    with self.db.atomic():
        equipped_data = ujson.loads(inv_model.equipped_items or "{}")
        equipped_data[part] = item_id
        inv_model.equipped_items = ujson.dumps(equipped_data, ensure_ascii=False)
        inv_model.save()
    return True, f"装备物品成功，装备:{item.name}，部位:{part}"
```

- [ ] **Step 3: Investigator 域对象 — 补全所有缺失方法**

在现有 `Investigator` 类中追加：

```python
def update_equipment(self):
    model = investigator_repo.find_by_qq(self.qq)
    if model:
        self._equipped = ujson.loads(model.equipped_items or "{}")
        self._model = model

def mark_as_deceased(self):
    self.is_survive = False

def break_equipped_item(self, action: str) -> bool:
    part = action2part(action)
    if not part:
        return False
    item_id_to_break = self._equipped.get(part)
    if not item_id_to_break:
        return False
    del self._equipped[part]
    investigator_repo.remove_item_from_inventory(self.qq, item_id_to_break, 1)
    self.save()
    return True

def get_full_attributes_dict(self) -> Dict[str, Any]:
    data = {}
    for field in self._model._meta.fields.keys():
        data[field] = getattr(self._model, field)
    data["hp"] = self.hp
    return data

def get_equipments(self):
    items = InventoryItemModel.select().where(
        InventoryItemModel.investigator == self._model
    )
    res = {}
    res_name = {}
    for inv_item in items:
        res[inv_item.item_id] = inv_item.quantity
        res_name[inv_item.item_id] = inv_item.item_name
    return res, res_name

def str_equipments(self) -> str:
    equipments, res_name = self.get_equipments()
    all_equipments = equipment_repo.brief_equipment(equipments)
    res = "已装备：\n"
    for key, value in self._equipped.items():
        res += f"{key}：{res_name.get(value, value)}\n"
    return all_equipments + res

def model_to_dict(self) -> Dict[str, Any]:
    data = {}
    for field_name in self._model._meta.fields.keys():
        data[field_name] = getattr(self._model, field_name)
    return data

def add_item_to_inventory(self, item_id, quantity=1):
    investigator_repo.add_item_to_inventory(self._model, item_id, quantity)
```

修复 `save()` 方法 — 保存 `name` + `db` 字段：

```python
def save(self):
    self.update_data = getattr(self, 'update_data', {})
    update_dict = {
        "name": self.name,
        "db": self.db,
        "hp": self.hp,
        "issurvive": self.is_survive,
        "day": self.day,
        "equipped_items": ujson.dumps(self._equipped, ensure_ascii=False),
    }
    update_dict.update(self.update_data)
    investigator_repo.update(self._model, update_dict)
```

修复 `set_skill()` — 改用 `update_data` 字典批量保存：

```python
def set_skill(self, skill_name: str, skill: int = 0):
    if not hasattr(self, 'update_data'):
        self.update_data = {}
    self.update_data[skill_name] = skill
```

- [ ] **Step 4: 更新 `InvestigatorGenerator.generate_investigator_data` — 支持 `count` 参数**

```python
@classmethod
def generate_investigator_data(cls, count: int = 1) -> List[Dict[str, Any]]:
    investigators = []
    for _ in range(count):
        investigators.append(cls._generate_single())
    return investigators

@classmethod
def _generate_single(cls) -> Dict[str, Any]:
    # 原有单人生成逻辑移到这里
    attributes = {}
    for attr, (dice_expr, mult) in cls.BASE_ATTRIBUTES.items():
        if dice_expr == "2d6+6":
            res = roll_dice("2d6")[1] + 6
        else:
            res = roll_dice(dice_expr)[1]
        attributes[attr] = res * mult
    attributes.update(cls.DEFAULT_SKILLS)
    attributes["san"] = attributes["意志"]
    attributes["db"] = calculate_damage_bonus(attributes["体型"], attributes["力量"])
    attributes["hp"] = (attributes["体质"] + attributes["体型"]) // 10
    attributes["闪避"] = attributes["敏捷"] // 2
    return attributes
```

**决议**：`create_and_save` 保持只接受单 `Dict`（不改签名）。`generate_investigator_data(count=3)` 返回 `List[Dict]`，调用方（如 `Investigator.load`、`CreateInvestigator.create_investigator`）取 `data[0]` 传入。旧 `Investigator.load` 调用处需将 `data = InvestigatorGenerator.generate_investigator_data()` 改为 `data = InvestigatorGenerator.generate_investigator_data()[0]`。

- [ ] **Step 5: 添加 `InvestigatorFormatter` 类**

从 `old_src/Investigator.py:573-656` 完整迁移：

```python
class InvestigatorFormatter:
    @staticmethod
    def format_investigator_info(name: str, investigator_data: Union[Dict, List[Dict]]) -> str:
        if isinstance(investigator_data, list):
            return InvestigatorFormatter._format_investigator_list(name, investigator_data)
        else:
            return InvestigatorFormatter._format_single_investigator(name, investigator_data)

    @staticmethod
    def _format_investigator_list(name: str, investigators: List[Dict]) -> str:
        header = f"{name}的调查员做成:\n"
        body_lines = []
        for inv in investigators:
            filtered = {k: v for k, v in inv.items()
                        if not k.startswith("_") and k not in ("id", "equipped_items", "current_armor")}
            body_lines.append(" ".join(f"{key}:{value}" for key, value in filtered.items()))
        return header + "\n".join(body_lines)

    @staticmethod
    def _format_single_investigator(name: str, investigator: Dict) -> str:
        header = f"{name}的角色属性为:\n"
        body_lines = []
        current_line = ""
        filtered = {k: v for k, v in investigator.items()
                    if not k.startswith("_") and k not in ("id", "equipped_items", "current_armor")}
        for key, value in filtered.items():
            attribute = f"{key}:{value} "
            if len(current_line) + len(attribute) > 60:
                body_lines.append(current_line.strip())
                current_line = attribute
            else:
                current_line += attribute
            if key == "总点数":
                body_lines.append(current_line.strip())
                current_line = ""
        if current_line:
            body_lines.append(current_line.strip())
        return header + "\n".join(body_lines)
```

- [ ] **Step 6: 添加 `CreateInvestigator` 类**

从 `old_src/Investigator.py:659-717` 完整迁移：

```python
class CreateInvestigator:
    def __init__(self, number: int = 1):
        self.investigators_data = InvestigatorGenerator.generate_investigator_data(number)
        self.select = {}
        self.skill_point = 0

    def choose_investigator(self, index: int) -> bool:
        if 1 <= index < len(self.investigators_data) + 1:
            self.select = self.investigators_data[index - 1].copy()
            self.skill_point = self.select.get("教育", 0) + self.select.get("智力", 0)
            return True
        return False

    def set_skill(self, skills: str):
        import re
        pattern = re.compile(r"[^\d\s]+|\d+")
        match = pattern.findall(skills)
        if not str.isdigit(match[-1]):
            return False, "技能设置错误了哦~"
        a = iter(match)
        match_dic = dict(zip(a, a))
        for key in match_dic:
            match_dic[key] = int(match_dic[key])
        tol = sum(match_dic.values())
        if tol > self.skill_point:
            return False, "当前总点数过多了哦~"
        if tol < self.skill_point:
            return False, "当前总点数过少了哦~"
        user_select_tmp = self.select.copy()
        for key in match_dic:
            if key in user_select_tmp:
                user_select_tmp[key] += match_dic[key]
                if user_select_tmp[key] > 75:
                    return False, f"当前技能{key}点数高于了75哦~"
            else:
                return False, f"不存在技能{key}~"
        self.select.update(user_select_tmp)
        return True, "技能设置成功啦~"

    def create_investigator(self, qq: str, name: str) -> Investigator:
        if not self.select:
            raise ValueError("尚未选择调查员模板。")
        investigator_repo.delete_by_qq(qq)
        new_model = investigator_repo.create_and_save(qq, name, self.select)
        return Investigator(new_model)
```

- [ ] **Step 7: 跑所有测试确认通过**

Run: `cd E:\python\official_bot && python -m pytest test/ -v`
Expected: all PASS (fix any failures before proceeding)

---

### Task 5: 伤害计算模块 — `src/services/damage_calculator.py` (新文件)

**Files:**
- Create: `plugins/StoryTeller/src/services/damage_calculator.py`
- Create: `test/test_damage_calculator.py`

- [ ] **Step 1: 写测试**

```python
# test/test_damage_calculator.py
import sys
sys.path.insert(0, "plugins/StoryTeller")

def test_calculate_damage_normal():
    from src.services.damage_calculator import calculate_damage
    # 普通成功，无穿透，无护甲
    expr, val = calculate_damage("1d6", success_level=1, has_penetration=False, armor=0)
    assert isinstance(val, int)
    assert 1 <= val <= 6

def test_calculate_damage_crit_with_penetration():
    from src.services.damage_calculator import calculate_damage
    expr, val = calculate_damage("1d6", success_level=4, has_penetration=True, armor=0)
    assert val >= 7  # max(6) + random(1-6) >= 7

def test_calculate_damage_with_armor():
    from src.services.damage_calculator import calculate_damage
    expr, val = calculate_damage("2d6", success_level=1, has_penetration=False, armor=3)
    assert val <= 12  # 2d6 max 12
```

- [ ] **Step 2: 实现 `damage_calculator.py`**

参考 `old_src/combat_damage.py:20-53` 完整迁移：

```python
from __future__ import annotations
from typing import Tuple
from .dice_roller import SuccessLevel, roll_dice

def calculate_damage(
    damage: str,
    success_level: int = SuccessLevel.SUCCESS,
    has_penetration: bool = False,
    armor: int = 0,
) -> Tuple[str, int]:
    is_critical = success_level > SuccessLevel.HARD_SUCCESS

    if is_critical and has_penetration:
        expr, val = _double_damage(damage)
    elif is_critical:
        expr, val = roll_dice(damage, use_max=True)
    else:
        expr, val = roll_dice(damage)

    original_val = val
    if armor > 0:
        val = max(1 if has_penetration else 0, val - armor)

    if "d" in damage:
        return f"{damage}={expr}", val
    else:
        return f"{expr}", val

def _double_damage(damage: str) -> Tuple[str, int]:
    max_expr, max_val = roll_dice(damage, use_max=True)
    rand_expr, rand_val = roll_dice(damage)
    return f"{max_expr}+{rand_expr}", max_val + rand_val
```

- [ ] **Step 3: 跑测试确认通过**

Run: `cd E:\python\official_bot && python -m pytest test/test_damage_calculator.py -v`

---

### Task 6: 战斗服务 — 完整重建 `src/services/battle.py`

这是最大最关键的改动。参考 `old_src/Fight.py` (671行) 的完整实现，在当前 `BattleService` (257行) 的基础上注入缺失方法。

**Files:**
- Modify: `plugins/StoryTeller/src/services/battle.py`
- Create: `test/test_battle.py`

- [ ] **Step 1: 写战斗核心流程测试**

```python
# test/test_battle.py
import sys
sys.path.insert(0, "plugins/StoryTeller")
from pathlib import Path
import tempfile, os

def test_battle_init():
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
    from src.models.monster import Monster
    from src.services.battle import BattleService
    data = InvestigatorGenerator.generate_investigator_data()
    inv_model = investigator_repo.create_and_save("test_battle_qq", "BattleTest", data[0] if isinstance(data, list) else data)
    inv = Investigator(inv_model)
    monster = Monster.load_random_for_day(1)
    if not monster:
        return  # skip if no monster data
    bs = BattleService(inv, monster)
    assert bs.fight_is_over() == False
    assert bs.current_turn in ("inv", "mon")

def test_melee_attack_flow():
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
    from src.models.monster import Monster
    from src.services.battle import BattleService
    data = InvestigatorGenerator.generate_investigator_data()
    single = data[0] if isinstance(data, list) else data
    inv_model = investigator_repo.create_and_save("test_melee_qq", "MeleeTest", single)
    inv = Investigator(inv_model)
    monster = Monster.load_random_for_day(1)
    if not monster:
        return
    bs = BattleService(inv, monster)
    result = bs._melee_attack()
    assert isinstance(result, tuple)
    assert len(result) > 0

def test_ranged_attack_no_gun():
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
    from src.models.monster import Monster
    from src.services.battle import BattleService
    data = InvestigatorGenerator.generate_investigator_data()
    single = data[0] if isinstance(data, list) else data
    inv_model = investigator_repo.create_and_save("test_range_qq", "RangeTest", single)
    inv = Investigator(inv_model)
    monster = Monster.load_random_for_day(1)
    if not monster:
        return
    bs = BattleService(inv, monster)
    result = bs._ranged_attack(1)
    assert "没有装备" in result[0]

def test_victory_handling():
    from src.models.player import Investigator, InvestigatorGenerator, investigator_repo
    from src.models.monster import Monster
    from src.services.battle import BattleService
    data = InvestigatorGenerator.generate_investigator_data()
    single = data[0] if isinstance(data, list) else data
    inv_model = investigator_repo.create_and_save("test_victory_qq", "VictoryTest", single)
    inv = Investigator(inv_model)
    monster = Monster.load_random_for_day(1)
    if not monster:
        return
    bs = BattleService(inv, monster)
    # 手动设置怪物死亡
    bs.hp_record["mon"] = 0
    result = bs._end_turn()
    assert "结局" in result or "倒" in result or "loot" in result.lower()
```

- [ ] **Step 2: 融合旧代码的完整战斗方法到现有的 `BattleService`**

按以下优先级逐步修改 `battle.py`：

**2a. 替换 `_melee_attack` — 使用 `old_src/Fight.py:133-171` 的逻辑**

关键改动：
- 伤害必须包含 DB：`_get_player_damage_formula(weapon)` 拼接武器伤害 + db
- 大失败单独拆出：`_handle_player_critical_failure`
- 成功/失败分支使用 `_handle_player_melee_success` / `_handle_player_melee_failure`
- 使用 `damage_calculator.calculate_damage` 替代 `self._calculate_damage`
- 回复文本用 `$伤害`/`$骰子`/`$装备` 变量替换

**2b. 添加 `_handle_player_critical_failure` — `Fight.py:450-463`**

弹簧折刀 → 自伤 1d4，其余武器 → 破损。

**2c. 添加 `_get_player_damage_formula` — `Fight.py:467-473`**

```python
def _get_player_damage_formula(self, weapon: Equipment) -> str:
    damage = weapon.damage_dice
    db = self.investigator.db
    if db and db != "0":
        damage = f"{damage}+{db}" if not db.startswith("-") else f"{damage}{db}"
    return damage
```

**2d. 重写 `_ranged_attack` — 恢复 `_single_shot` / `_multiple_shot` 分离 — `Fight.py:173-285`**

单发：`DiceRoll` → 伤害 + 回复模板 + 大失败武器破损
多发：`PenaltyDiceRoll` 逐发 → 累积伤害 + 大失败中止

**2e. 重写 `_handle_defensive_action` — `Fight.py:294-344`**

- 反击时必须有近战武器
- 玩家大失败武器破损处理
- 怪物攻击成功时带护甲减伤
- 双方都失败时的特殊回复（`counter_false`）

**2f. 重写 `_apply_damage_to_monster` — `Fight.py:492-506`**

使用怪物数据的 `高伤害`/`低伤害`/`正常伤害` 风味文本。

**2g. 重写 `_apply_damage_to_player` — `Fight.py:475-490`**

伤害阈值判定 + 回复模板。

**2h. 重写 `_handle_victory` — `Fight.py:551-585`**

完整流程：侦查检定 → 条件掉落 + 金币(add_gold) → 技能成长(1d10) → 怪物结局文本 + Separator 分隔。

- [ ] **Step 3: 更新 `_calculate_damage` → 委托给 `damage_calculator.py`**

```python
from .damage_calculator import calculate_damage as calc_dmg

def _calculate_damage(self, dice_expr, success_level=1, penetration=False, armor=0):
    return calc_dmg(dice_expr, success_level=success_level,
                    has_penetration=penetration, armor=armor)
```

- [ ] **Step 4: 跑所有测试**

Run: `cd E:\python\official_bot && python -m pytest test/ -v`
Expected: all PASS (fix any failures)

---

### Task 7: 商店服务 — 每日缓存

**Files:**
- Modify: `plugins/StoryTeller/src/services/shop_service.py`

- [ ] **Step 1: 引入 `DaylyRecord` 缓存**

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
from util.DaylyRecord import add_data, get_data, write_json

class ShopService:
    def __init__(self):
        pass

    def get_todays_shop(self, day_seed: str = "") -> Dict[str, int]:
        cached = get_data("shop", "items")
        if cached:
            return cached

        shop_items = {}
        for price_key, item_ids in data_loader.shop_data.items():
            if price_key == "0":
                for item_id, price in item_ids.items():
                    shop_items[item_id] = price
            else:
                if isinstance(item_ids, list) and item_ids:
                    item_id = random.choice(item_ids)
                    shop_items[item_id] = int(price_key)

        add_data("shop", "items", shop_items)
        write_json()
        return shop_items
```

- [ ] **Step 2: 修复 `buy_item` — 使用物品名称**

```python
def buy_item(self, user_qq: str, item_id: str, quantity: int, shop_items: Dict[str, int]) -> str:
    if item_id not in shop_items:
        return "该物品今日未出售。"

    price = shop_items[item_id]
    total_cost = price * quantity

    if reduce_gold(user_qq, total_cost):
        inv = investigator_repo.find_by_qq(user_qq)
        if inv:
            item = Equipment(item_id)
            investigator_repo.add_item_to_inventory(inv, item_id, quantity)
            return f"成功购买了 {quantity} x {item.name}，花费 {total_cost} 乌帕。"
        else:
            add_gold(user_qq, total_cost)
            return "调查员不存在，购买已取消。"
    else:
        return "乌帕不足。"
```

- [ ] **Step 3: 跑现有测试验证不破坏功能**

Run: `cd E:\python\official_bot && python -m pytest test/test_battle.py test/test_damage_calculator.py -v`

---

### Task 7B: 商店插件验证 — `src/plugins/shop.py`

**Files:**
- Modify: `plugins/StoryTeller/src/plugins/shop.py` (验证，无需大改)

- [ ] **Step 1: 确认购买消息使用物品名称**

当前 `shop.py` 的 `handle_buy` 已调用 `shop_service.buy_item()` 返回结果文本（Task 7 已改为物品名称）。验证 `handle_shop` 的 `format_shop_text` 展示一致，无需额外修改。

- [ ] **Step 2: 确认 `get_todays_shop` 种子参数可用**

`handle_shop` 传入 `"seed"` 调用 `shop_service.get_todays_shop()`，新实现使用 `DaylyRecord` 缓存，忽略 seed 参数。确保行为正确（每天固定商店）。

---

### Task 8: 数据加载器 — `get_event` + `Separator`

**Files:**
- Modify: `plugins/StoryTeller/src/services/data_loader.py`

- [ ] **Step 1: 添加方法**

```python
Separator = "\n------------------\n"

class DataLoader:
    # ... existing code ...

    def get_event(self, day: str | int) -> str:
        reply = self.reply_data
        return reply.get(str(day), "")
```

- [ ] **Step 2: 验证 — 在 `data_loader.py` 底部加测试块（可选）或直接用 interactive 验证**

---

### Task 9: 冒险插件 — 怪物出场 + 战斗中装备切换

**Files:**
- Modify: `plugins/StoryTeller/src/plugins/adventure.py`

- [ ] **Step 1: 在 `/今日冒险` 中添加怪物出场展示**

```python
# after monster = Monster(monster_id):
intro_text = data_loader.get_event(inv.day)
monster_intro = getattr(monster, "出场", f"一只{monster.name}出现了！")
# prepend to reply
```

- [ ] **Step 2: 在 `/行动` 中处理 `使用 <物品ID>` 命令**

```python
# in handle_combat:
action = msg.extract_plain_text().strip()
if action.startswith("使用 "):
    item_id = action.split()[1]
    from ..models.player import investigator_repo
    ok, msg_text = investigator_repo.equip_item(user_id, item_id)
    if ok:
        inv = Investigator.load(user_id)
        inv.update_equipment()
        if battle.current_turn == "inv":
            battle._update_gun_status()
        await combat_cmd.send(msg_text + "\n" + battle._get_next_turn_prompt())
    else:
        await combat_cmd.send(msg_text)
    return
```

- [ ] **Step 3: 更新 `adventure_cmd` 结束时重置 `is_adventure` 标记**

在战斗结束时将 `inv.is_adventure = False` 并 `inv.save()`。

---

### Task 10: 角色插件 — 交互式创建 + 完整信息展示

**Files:**
- Modify: `plugins/StoryTeller/src/plugins/character.py`

- [ ] **Step 1: 重写 `/创建调查员`**

```python
from nonebot_plugin_waiter import waiter
from ..models.player import Investigator, InvestigatorGenerator, InvestigatorFormatter, CreateInvestigator

# 存储创建中的状态
_create_sessions = {}

@create_cmd.handle()
async def handle_create(event: Event, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    name = msg.extract_plain_text().strip()

    # Step 1: 没有名字就提示
    if not name:
        await create_cmd.send("请输入角色名，例如：/创建调查员 霍华德")
        return

    # Step 2: 生成3组属性
    ci = CreateInvestigator(3)
    formatted = InvestigatorFormatter.format_investigator_info(name, ci.investigators_data)
    await create_cmd.send(f"{formatted}\n请选择其中一组（输入 1/2/3）：")

    # Step 3: 等待选择（用 nonebot-plugin-waiter 或简单会话）
    @waiter(waits=["message"], keep_session=True)
    async def wait_choice(ev: Event):
        return ev.get_plaintext().strip()
    
    choice = await wait_choice.wait(timeout=120)
    if not choice or not choice.isdigit():
        await create_cmd.finish("超时或输入无效。")
    
    idx = int(choice)
    if not ci.choose_investigator(idx):
        await create_cmd.finish("选择无效。")

    # Step 4: 技能分配
    skill_point = ci.skill_point
    await create_cmd.send(f"你有 {skill_point} 技能点可以分配。请输入技能和点数（如：格斗30 侦查30 手枪30）：")
    
    @waiter(waits=["message"], keep_session=True)
    async def wait_skill(ev: Event):
        return ev.get_plaintext().strip()
    
    skills = await wait_skill.wait(timeout=120)
    if not skills:
        await create_cmd.finish("超时。")

    ok, skill_msg = ci.set_skill(skills)
    if not ok:
        await create_cmd.finish(skill_msg)

    # Step 5: 创建
    inv = ci.create_investigator(user_id, name)
    await create_cmd.finish(f"调查员 {inv.name} 创建成功！")
```

注意：`nonebot-plugin-waiter` 已在 `pyproject.toml` 中依赖 (`0.8.1`)。

- [ ] **Step 2: 重写 `/调查员信息` — 完整属性面板**

```python
@info_cmd.handle()
async def handle_info(event: Event):
    inv = Investigator.load(event.get_user_id())
    attrs = inv.get_full_attributes_dict()
    formatted = InvestigatorFormatter.format_investigator_info(inv.name, attrs)
    equip_str = inv.str_equipments()
    res = f"{formatted}\n{equip_str}"
    await info_cmd.finish(res)
```

---

### Task 11: 清理 — 删除 `old_src/` + 最终验证

**Files:**
- Delete: `plugins/StoryTeller/old_src/` (entire directory)
- Modify: `plugins/StoryTeller/__init__.py`

- [ ] **Step 1: 确认所有测试通过**

Run: `cd E:\python\official_bot && python -m pytest test/ -v`

- [ ] **Step 2: 删除 `old_src/` 目录**

```bash
Remove-Item -Recurse -Force "plugins/StoryTeller/old_src"
```

- [ ] **Step 3: 更新 `__init__.py` — 移除旧引用（如果有任何残留）**

当前 `__init__.py` 仅从 `src.plugins.*` 导入，已无旧引用。确认无需改动。

- [ ] **Step 4: 最终全量测试 + lint**

```bash
cd E:\python\official_bot
python -m pytest test/ -v
ruff check plugins/StoryTeller/src/
```

- [ ] **Step 5: 手动冒烟测试**

使用 `python tes.py` 启动 console adapter，依次执行：
- `/创建调查员 TestBot`
- `/调查员信息`
- `/今日冒险`
- `/行动 格斗` (重复直到战斗结束)
- `/今日商店`
- `/购买 201 1`

---

### Summary

| Task | 文件 | 预计改动量 |
|------|------|-----------|
| 1. 骰子系统 | dice_roller.py | ~60行改写 |
| 2. 装备模型 | item.py | ~40行追加 |
| 3. 怪物模型 | monster.py | ~50行改写 |
| 4. 玩家模型 | player.py | ~200行追加 |
| 5. 伤害计算 | damage_calculator.py (新) | ~40行 |
| 6. 战斗服务 | battle.py | ~300行改写 |
| 7. 商店服务 | shop_service.py | ~30行改写 |
| 7B. 商店插件验证 | shop.py | 验证无改动 |
| 8. 数据加载器 | data_loader.py | ~10行追加 |
| 9. 冒险插件 | adventure.py | ~30行追加 |
| 10. 角色插件 | character.py | ~80行改写 |
| 11. 清理 | old_src/ 删除 | 删除 ~2500行 |
