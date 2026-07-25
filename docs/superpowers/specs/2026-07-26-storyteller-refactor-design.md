# StoryTeller 完整重构设计文档

> 日期: 2026-07-26
> 状态: 已确认
> 目标: 将 old_src/ 全部功能迁移到 src/ 分层架构，保留新加的理智/疯狂系统，最终删除 old_src/

---

## 1. 背景

`official_bot` 是一个基于 NoneBot2 的 COC 主题 QQ 机器人文字冒险游戏。项目处于中途重构状态：
- `src/` — 当前运行的新代码，采用分层架构（models/services/plugins/utils），但功能严重缺失
- `old_src/` — 旧版完整实现，671 行的 CombatSystem 等，但架构混乱，已被弃用

## 2. 技术决策

| 决策 | 选择 |
|------|------|
| 架构 | 保持现有 src/ 分层架构（models → services → plugins） |
| 数据库 | SQLite + Peewee，两个独立库：`database/userData.db`（用户金币/签到，`User` 模型）、`plugins/StoryTeller/inv.db`（调查员角色/装备背包，`InvestigatorModel`/`InventoryItemModel`） |
| 测试 | 边重构边补测试，每层完成后写对应单元测试 |
| 范围 | 完整迁移 old_src 所有功能，保留新加功能 |

## 3. 分层设计

### 第1层：骰子系统 `src/services/dice_roller.py`

**修复 `roll_dice()` 复合表达式解析：**
- 当前正则 `(\d*)d(\d+)([+-]\d+)?` 仅支持单骰组 `XdY+Z`
- 需支持 `1d8+2d6+3` 复合表达式：拆分 → 逐项求值 → 汇总
- `d4` 自动补全为 `1d4`
- 保留 `use_max`，返回格式化记录 `"1d8+2d6+3 → 5+8+3 = 16"`

**新增 `BonusDiceRoll` 类：**
- 投 D100 时额外投 N 个 D10 作为十位奖励骰
- 取最低十位（最好结果）
- `get_result()`, `get_detailed_result()` 接口

**对齐 `PenaltyDiceRoll`：**
- 确保与旧代码逻辑一致：额外 D10 取最高十位（最差结果）

**不改动：** `SuccessLevel`, `DiceRoll`, `ConfrontationRoll`, `get_success_description()`, `calculate_damage_bonus()`

### 第2层：数据模型

#### 2A. 装备模型 `src/models/item.py`

| 方法 | 状态 | 说明 |
|------|------|------|
| `__str__()` | 新增 | 返回装备名称 |
| `get_full_description()` | 新增 | 完整属性：名称、类型、伤害骰、技能加值、护甲、穿透、弹药 |
| `get_brief_description()` | 修改 | 恢复旧版格式：ID + 护甲 + 伤害 + 价格 |

#### 2B. 怪物模型 `src/models/monster.py`

| 改动 | 说明 |
|------|------|
| `__getattr__` 代理 | 对 `_data` 未显式定义的字段动态访问（结局、出场、低伤害等），告别硬编码别名 |
| `generate_loot()` | 恢复三元组 `(gold, Equipment, message)`，message 含掉落物风味文本 |
| `load_random_for_day(day)` | 工厂方法，封装 `find_random_id_for_day()` + 构造 |
| 容错 | 数据缺失时抛 `ValueError` 而非静默空数据 |

#### 2C. 玩家模型 `src/models/player.py`

**Repository 补全：**
- `delete_by_qq(qq)` — 删除角色
- `equip_item(pid, item_id)` — 按部位装备，返回 `(bool, str)`

**Investigator 域对象补全：**

| 方法 | 说明 |
|------|------|
| `update_equipment()` | DB 重读装备，变更后刷新内存表示 |
| `mark_as_deceased()` | 标记死亡 |
| `break_equipped_item(action)` | 移除装备槽 → 删除背包物品 → 持久化 |
| `get_full_attributes_dict()` | 全属性面板 |
| `str_equipments()` | 格式化装备/背包列表 |
| `model_to_dict()` | 序列化完整模型 |
| `add_item_to_inventory()` | 便捷方法 |
| `save()` | 修复：保存 name、db 字段 |
| `set_skill()` | 改用 update_data 字典批量保存 |
| `InvestigatorGenerator` | 支持批量生成（count 参数） |
| `InvestigatorFormatter` | 从旧代码迁移，角色面板格式化 |
| `CreateInvestigator` | 从旧代码迁移，交互式创建流程 |

### 第3层：服务层

#### 3A. 伤害计算 `src/services/damage_calculator.py` (新文件)

```python
calculate_damage(damage_expr: str, success_level: int, has_penetration: bool, armor: int) -> tuple[str, int]
```

- returns `(formatted_expression: str, damage_value: int)` — expression first for display, value for arithmetic

- 大成功 + 穿透 → 双倍（取最大 + 再随机）
- 大成功无穿透 → 骰子取满
- 护甲减伤 → `max(0, raw - armor)`
- 返回 `(伤害值, 格式化表达式)`

#### 3B. 战斗服务 `src/services/battle.py` — 完整重建

保持单一 `BattleService` 类，内部按职责拆分为私有方法组：

**玩家攻击方法组（均返回 `str` 回复文本）：**
- `_玩家近战攻击(action: str, item_id: int) -> str` — 对抗检定 → 伤害公式含 DB → 大失败武器破损/自伤
- `_玩家单发射击(action: str, item_id: int) -> str` — 伤害检定 → 大失败武器破损
- `_玩家多发射击(action: str, shot_count: int, item_id: int) -> str` — 惩罚骰逐发检定 → 累积伤害 → 大失败中止

**伤害应用方法组（返回 `(int, str)` 伤害值+文本）：**
- `_怪物受伤(damage: int, formatted: str) -> tuple[int, str]` — 怪物数据的高/低/正常伤害文本
- `_玩家受伤(damage: int, formatted: str) -> tuple[int, str]` — 伤害阈值判定 + 对应回复模板

**防守处理：**
- `_执行反击/闪避()` — 对抗检定 → 成功/大失败分支 → 武器破损

**大失败处理：**
- `_玩家大失败(action)` — 弹簧折刀自伤 1d4，其余武器破损

**胜利结算：**
- `_胜利结算()` — 侦查检定 → 条件掉落 + 金币 + 技能成长鉴定(1d10) + 怪物结局文本

**回复模板：**
- `_取回复(key)` → `$伤害`/`$骰子`/`$装备` 变量替换

**回合管理：**
- `开始回合()` / `结束回合()` / `_获取下一回合提示()` — HP/弹药/可用动作

**疯狂机制（保留）：**
- `is_madness` / `madness_duration` — 疯狂时随机行动

**状态追踪：** `succeeded_skills` 列表、HP 记录、枪状态、`BATTLE_ACTIONS`

#### 3C. 商店服务 `src/services/shop_service.py`

- DailyRecord 每日缓存：当天首次生成 → JSON 序列化 → 后续读缓存
- 购买确认消息用物品名称

#### 3D. 数据加载器 `src/services/data_loader.py`

- 新增 `get_event(day)` → 日常事件文本
- 新增 `Separator` 常量 `"\n------------------\n"` → 战斗胜利结果各部分间的分隔线

### 第4层：命令/插件层

#### 4A. 冒险 `src/plugins/adventure.py`

- `/今日冒险`：增加怪物「出场」场景展示
- `/行动 使用 <物品ID>`：战斗中切换装备
- 其余流程保持现有（理智检定 → 疯狂判定 → 战斗）

#### 4B. 商店 `src/plugins/shop.py`

- 轻量改动：购买确认文本优化，其余不变

#### 4C. 角色 `src/plugins/character.py`

- `/创建调查员` → 交互式：3组属性选1 → 命名 → 技能点分配 → 确认创建
- `/调查员信息` → 完整属性面板 + 装备/背包列表

### 第5层：清理

- 确认所有功能已迁移且测试通过
- 删除 `old_src/` 整个目录
- 清理 `__init__.py` 和项目中的旧引用

## 4. 测试策略

| 层级 | 测试内容 | 关键场景 |
|------|----------|----------|
| dice_roller | 复合表达式解析、BonusDiceRoll、PenaltyDiceRoll 正确性 | `1d8+2d6+3` 解析、惩罚骰3连射、奖励骰结果 |
| models | 各模型方法单元测试（CRUD、装备、怪物掉落） | 装备破损 → 槽位清空+背包移除、怪物掉落三元组、角色批量生成 |
| services | 战斗流程集成测试 | 近战大失败自伤流程、射击大失败武器破损、3连射惩罚骰累积伤害、胜利侦查掉落+技能成长、疯狂状态随机行动 |
| plugins | 命令端到端测试（模拟 NoneBot 事件） | 完整创建流程、战斗中切换装备 |

## 5. 迁移顺序

```
骰子系统 → 装备模型 → 怪物模型 → 玩家模型 → 伤害计算器 → 战斗服务 → 商店服务 → 数据加载器 → 冒险插件 → 商店插件 → 角色插件 → 清理 old_src
```

## 6. 风险

- **战斗服务重建风险最高**（671 行 → 完整重建），需充分写测试
- **回复模板字段映射**可能因新旧代码命名差异导致显示异常，需逐条验证
- **玩家模型变更影响面广**（所有服务+插件都依赖），需优先完成测试
