"""配置全量校验器（config_validator）：统一校验 data/*.json 的 schema 正确性与跨文件引用完整性。

设计对齐 `.qa/plans/config-deep-eval.md`（TOP10 #12 全量数据校验 / 2.4 数据校验设计）与
`.qa/plans/config-id-naming.md`（ID 段位规范）：

- **逐文件 schema 校验**：必填键 / 类型 / ID 段位（按 config-id-naming 段位表断言）。
- **跨文件引用检查**：怪物→物品/法术、事件→物品/技能、goods→信物段、flow/boss→文案键/怪物、
  shop→物品、首杀表→怪物、check_point→怪物、npc/flow 效果→物品/技能、door→结局/物品/文案键。
- **报告格式**：收集**全部**错误（不遇错即停），每条错误精确定位到「文件 + key + 原因」。
- **失败策略**：`CONFIG_VALIDATE_MODE` 环境变量——`fail`（默认，校验失败抛 `ConfigValidationError`
  阻止启动）/ `warn`（仅日志告警，保留上一份数据）。生产环境可在 `.env` 配置 `CONFIG_VALIDATE_MODE=warn`。
- **接入点**：`data_loader.load_data()` 在所有 JSON 加载完成后调用 `run_config_validation()`。
- **不引入 jsonschema**（requirements 无此依赖，推送即上线避免新增运行时依赖），手写断言逐项校验。

错误格式统一 `"{文件名} | {位置}：{原因}"`，便于 grep 定位。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import ujson
from loguru import logger

from ..services.effect_keys import STANDARD_EFFECT_KEYS

# 失败策略：fail（默认，抛错阻止启动） / warn（仅告警）
CONFIG_VALIDATE_MODE = os.environ.get("CONFIG_VALIDATE_MODE", "fail")

# 需校验的数据文件（guest 迁移批次2：guest_data.json 已归档 test/archive，不再校验）
# 目录化（第2+3期）：boss/ 与 worlds/ 为目录扫描条目（后缀 "/"），逐文件校验；
# boss_data.json 已拆分为 data/boss/*.json（归档 test/archive）；flow_data.json 仅含 lane_tale。
DATA_FILES: tuple[str, ...] = (
    "monster_data.json",
    "goods_data.json",
    "event_data.json",
    "environment_data.json",
    "check_point.json",
    "reply_data.json",
    "text_data.json",
    "npc_data.json",
    "ending_data.json",
    "flow_data.json",
    "display_data.json",
    "shop_data.json",
    "spell_data.json",
    "mainline_config.json",
    "weights.json",
    "boss_weights.json",
    "loots.json",
    "boss/",
    "worlds/",
)

# 玩家可用技能集合（player.py InvestigatorModel 字段 + 标准 COC 技能）
KNOWN_SKILLS: frozenset[str] = frozenset(
    {
        "力量", "体质", "体型", "智力", "意志", "敏捷",
        "教育", "幸运", "外貌",
        "格斗", "闪避", "侦查", "聆听", "手枪", "步枪", "急救", "医学",
        "克苏鲁神话",
    }
)

# 结局 ID 集合（ending_data.endings 的合法键）
_ENDING_KEYS: frozenset[str] = frozenset(
    {f"E{i:02d}" for i in range(1, 14)}
)

# 物品 type 合法值（goods_data）
GOODS_TYPES: frozenset[str] = frozenset(
    {"武器", "饰品", "misc", "spell_scroll", "纪念品"}
)
# 物品 part 合法值（goods_data）
GOODS_PARTS: frozenset[str] = frozenset(
    {"远程", "近战", "防具", "misc", "饰品", "法术"}
)
# 饰品触发时机（battle/trinkets.py）
TRINKET_TRIGGERS: frozenset[str] = frozenset(
    {"受击", "受到伤害", "濒死", "造成伤害", "致死预判",
     "进入战斗", "回合开始", "回合结束"}
)
# 条件算子（ending_engine.eval_option_condition + event_service.event_condition_ok）
CONDITION_KEYS: frozenset[str] = frozenset(
    {"物品", "SAN", "HP", "克苏鲁神话", "日", "进度", "已死亡", "san_low", "旗标"}
)
# 门扉条件算子（ending_data.door.condition 专属 schema：type/rules/rule）
DOOR_CONDITION_KEYS: frozenset[str] = frozenset(
    {"type", "rules", "min", "max", "id", "value", "key"}
)

# 怪物 ID 段位语义（config-id-naming §二）：不在每日池的固定语义怪
# （47/48=乱入世界 BOSS，49/50=异世界高校第二季 BOSS，均仅被 guest/世界 flow 引用）
_SPECIAL_MONSTER_IDS: frozenset[str] = frozenset(
    {"12", "14", "15", "16", "21", "22", "23", "24", "30", "33", "37",
     "38", "40", "41", "42", "44", "45", "46", "47", "48", "49", "50", "51",
     "52", "53", "55", "56", "57", "61", "62", "63", "64", "65", "66",
     "67", "68", "69", "70", "71", "72", "73", "74", "75", "76", "77", "78", "79"}
)

# 主线三倾向（mainline.py _LANES 一致）
_LANES_MAINLINE = ("dread", "hymn", "free")


class ConfigValidationError(ValueError):
    """配置校验失败（CONFIG_VALIDATE_MODE=fail 时抛出，阻止 bot 启动）。"""


class ConfigValidator:
    """统一配置校验器：schema + 跨文件引用，收集全部错误。"""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else self._default_data_dir()
        self.data: dict[str, dict[str, Any]] = {}
        self.errors: list[str] = []

    @staticmethod
    def _default_data_dir() -> Path:
        return Path(__file__).resolve().parent.parent.parent / "data"

    # --- 加载 ---
    def load(self) -> None:
        """加载全部数据文件（缺失/损坏记错误并置空，不中断其余校验）。

        目录化条目（DATA_FILES 中带 "/" 后缀）：递归扫描目录下 *.json 逐文件加载，
        存为 `{目录}/{文件名}` 键（如 `boss/jk.json`、`worlds/sword_magic.json`）。
        """
        for name in DATA_FILES:
            if name.endswith("/"):
                self._load_dir(name.rstrip("/"), self.data_dir / name)
                continue
            path = self.data_dir / name
            if not path.exists():
                self._err(name, "<文件>", f"文件不存在：{path.name}")
                self.data[name] = {}
                continue
            try:
                with open(path, encoding="utf-8-sig") as f:
                    self.data[name] = ujson.load(f)
            except Exception as e:  # noqa: BLE001 - 加载失败需暴露
                logger.warning(f"静默异常[Exception] in load: {e}")
                self._err(name, "<文件>", f"JSON 解析失败：{e}")
                self.data[name] = {}

    def _load_dir(self, dirname: str, path: Path) -> None:
        """目录扫描：加载 dirname/*.json（缺失记目录错误，单个文件失败置空不中断）。"""
        if not path.is_dir():
            self._err(dirname, "<目录>", f"目录不存在：{dirname}/")
            return
        for f in sorted(path.glob("*.json")):
            key = f"{dirname}/{f.name}"
            try:
                with open(f, encoding="utf-8-sig") as fh:
                    self.data[key] = ujson.load(fh)
            except Exception as e:  # noqa: BLE001 - 加载失败需暴露
                logger.warning(f"静默异常[Exception] in _load_dir: {e}")
                self._err(key, "<文件>", f"JSON 解析失败：{e}")
                self.data[key] = {}

    # --- 错误收集 ---
    def _err(self, file: str, key: str, msg: str) -> None:
        self.errors.append(f"{file} | {key}：{msg}")

    # --- 工具 ---
    @staticmethod
    def _iter_items(file_data: Any):
        if isinstance(file_data, dict):
            for k, v in file_data.items():
                yield k, v

    def _require_dict(self, file: str, key: str, value: Any) -> bool:
        if not isinstance(value, dict):
            self._err(file, key, f"应为 dict，实际 {type(value).__name__}")
            return False
        return True

    def _require_str(self, file: str, key: str, value: Any) -> bool:
        if not isinstance(value, str) or not value:
            self._err(file, key, f"应为非空字符串，实际 {value!r}")
            return False
        return True

    def _require_int(self, file: str, key: str, value: Any) -> bool:
        if isinstance(value, bool) or not isinstance(value, int):
            self._err(file, key, f"应为整数，实际 {value!r}")
            return False
        return True

    @staticmethod
    def _valid_dice(dice: str) -> bool:
        """骰子表达式合法性：`NdM[±K]` 复合式（如 1d6、2d4+1、1d6-2、纯数值 5）。"""
        if not isinstance(dice, str) or not dice:
            return False
        import re

        part = r"[0-9]*d[0-9]+|[0-9]+"
        return bool(
            re.fullmatch(rf"{part}(?:\s*[+-]\s*{part})*", dice.strip())
        )

    def _validate_absorb(self, file: str, key: str, absorb: Any) -> None:
        """攻击吸收字段校验（monster 攻击表）：类型 / 目标 / 骰子 / 持久 / 文案。"""
        if not self._require_dict(file, key, absorb):
            return
        a_type = absorb.get("类型")
        if a_type not in ("hp", "护盾", "属性"):
            self._err(
                file, f"{key}.类型",
                f"非法吸收类型 {a_type!r}（合法：hp/护盾/属性）",
            )
        dice = absorb.get("骰子")
        if dice is not None and not self._valid_dice(str(dice)):
            self._err(
                file, f"{key}.骰子",
                f"非法骰子表达式 {dice!r}（如 1d6 / 1d4+1）",
            )
        if a_type == "属性":
            target = absorb.get("目标")
            if target not in KNOWN_SKILLS:
                self._err(
                    file, f"{key}.目标",
                    f"属性吸收必须指定合法技能名，实际 {target!r}",
                )
        persist = absorb.get("持久")
        if persist is not None and not isinstance(persist, bool):
            self._err(file, f"{key}.持久", "应为 bool（true=永久扣减）")
        text = absorb.get("文案")
        if text is not None and (not isinstance(text, str) or not text):
            self._err(file, f"{key}.文案", "应为非空字符串")

    def _validate_win_condition(self, file: str, key: str, wc: Any) -> None:
        """战斗配置 胜利条件 字段校验（flow 战斗节点 / boss 战斗 通用）。

        schema：{"类型": "坚守", "回合": 正整数, "文案": 可选非空字符串}。
        """
        if wc is None:
            return
        if not self._require_dict(file, key, wc):
            return
        wtype = wc.get("类型")
        if wtype != "坚守":
            self._err(
                file, f"{key}.类型",
                f"非法胜利条件类型 {wtype!r}（合法：坚守）",
            )
        turns = wc.get("回合")
        if isinstance(turns, bool) or not isinstance(turns, int) or turns <= 0:
            self._err(file, f"{key}.回合", f"回合应为正整数，实际 {turns!r}")
        text = wc.get("文案")
        if text is not None and (not isinstance(text, str) or not text):
            self._err(file, f"{key}.文案", "应为非空字符串")

    def _check_items_ref(self, file: str, key: str, items: Any) -> None:
        """物品引用：str 或 list[str]，每一项必须存在于 goods_data。"""
        ids = items if isinstance(items, list) else [items]
        for iid in ids:
            if str(iid) not in self.data.get("goods_data.json", {}):
                self._err(file, key, f"引用的物品 {iid} 不存在于 goods_data")

    def _check_monster_ref(self, file: str, key: str, mid: Any) -> None:
        if str(mid) not in self.data.get("monster_data.json", {}):
            self._err(file, key, f"引用的怪物 {mid} 不存在于 monster_data")

    def _check_spell_ref(self, file: str, key: str, sid: Any) -> None:
        if str(sid) not in self.data.get("spell_data.json", {}):
            self._err(file, key, f"引用的法术 {sid} 不存在于 spell_data")

    def _check_text_key(self, file: str, key: str, text_key: Any) -> None:
        """文案键引用：text_data 点路径必须命中。"""
        if not isinstance(text_key, str) or not text_key:
            return
        if not self._text_key_exists(text_key):
            self._err(file, key, f"文案键 {text_key!r} 不存在于 text_data")

    def _text_key_exists(self, text_key: str) -> bool:
        cur: Any = self.data.get("text_data.json", {})
        for part in text_key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return False
            cur = cur[part]
        return True

    def _check_effects(self, file: str, key: str, effects: Any) -> None:
        """效果 dict 校验：标准键 + 物品引用 + 技能集合。"""
        if not effects:
            return
        if not self._require_dict(file, key, effects):
            return
        if "物品" in effects:
            self._check_items_ref(file, f"{key}.效果.物品", effects["物品"])
        if "技能" in effects:
            sk = effects["技能"]
            if isinstance(sk, dict):
                for skill_name in sk:
                    if skill_name not in KNOWN_SKILLS:
                        self._err(
                            file, f"{key}.效果.技能", f"未知技能 {skill_name!r}"
                        )
            else:
                self._err(file, f"{key}.效果.技能", f"技能应为 dict，实际 {type(sk).__name__}")

    def _check_check_block(self, file: str, key: str, check: Any) -> None:
        """检定块校验：技能 + 成功/失败分支效果。"""
        if not check:
            return
        if not self._require_dict(file, key, check):
            return
        skill = check.get("技能")
        if skill is not None:
            if skill not in KNOWN_SKILLS:
                self._err(file, f"{key}.技能", f"未知技能 {skill!r}")
        for branch in ("成功", "失败", "奖励"):
            br = check.get(branch)
            if isinstance(br, dict):
                self._check_effects(file, f"{key}.{branch}", br.get("效果"))

    def _check_door_condition(self, file: str, key: str, cond: Any) -> None:
        """门扉 condition 校验（ending_data.door 专属 schema：type/rules/min/max/id）。"""
        if not cond:
            return
        if isinstance(cond, list):
            for i, c in enumerate(cond):
                self._check_door_condition(file, f"{key}[{i}]", c)
            return
        if not isinstance(cond, dict):
            return
        ctype = cond.get("type")
        if ctype in ("and", "or"):
            for i, r in enumerate(cond.get("rules") or []):
                self._check_door_condition(file, f"{key}.rules[{i}]", r)
            return
        if ctype == "item":
            self._check_items_ref(file, f"{key}.id", cond.get("id"))
            return
        for k, v in cond.items():
            if k not in DOOR_CONDITION_KEYS:
                self._err(file, f"{key}.{k}", f"未知门扉条件算子 {k!r}")

    def _check_condition(self, file: str, key: str, cond: Any) -> None:
        """条件块校验（eval_option_condition 算子）：物品引用 + 未知算子告警。"""
        if not cond:
            return
        if isinstance(cond, list):
            for i, c in enumerate(cond):
                self._check_condition(file, f"{key}[{i}]", c)
            return
        if not isinstance(cond, dict):
            return
        if "all" in cond:
            self._check_condition(file, f"{key}.all", cond["all"])
        if "any" in cond:
            self._check_condition(file, f"{key}.any", cond["any"])
        for k, v in cond.items():
            if k in ("all", "any"):
                continue
            if k == "物品":
                self._check_items_ref(file, f"{key}.条件", v)
            elif k not in CONDITION_KEYS:
                self._err(file, f"{key}.条件", f"未知条件算子 {k!r}")

    # --- 逐文件 schema 校验 ---
    def validate_monster(self) -> None:
        file = "monster_data.json"
        mon = self.data.get(file, {})
        for mid, m in self._iter_items(mon):
            if not self._require_dict(file, mid, m):
                continue
            # 必填键与类型
            for field in ("名字", "出场", "理智值丧失", "高伤害", "正常伤害", "低伤害"):
                if field not in m:
                    self._err(file, mid, f"缺少必填字段 {field!r}")
                elif not isinstance(m[field], str) or not m[field]:
                    self._err(file, f"{mid}.{field}", f"应为非空字符串，实际 {m[field]!r}")
            for field in ("力量", "体质", "体型", "智力", "意志", "敏捷", "hp"):
                if field not in m:
                    self._err(file, mid, f"缺少必填字段 {field!r}")
                elif isinstance(m[field], bool) or not isinstance(m[field], int):
                    self._err(file, f"{mid}.{field}", f"应为整数，实际 {m[field]!r}")
            # ID 段位（config-id-naming §二）：非每日池怪物必须是固定语义白名单
            # （38/40~42/44/45~47/48）或 ≥49 预留段且登记 check_point
            if mid.isdigit():
                mid_num = int(mid)
                in_daily = mid in self._checkpoint_flat()
                if mid_num > 48 and not in_daily and mid not in _SPECIAL_MONSTER_IDS:
                    self._err(file, mid, "ID > 48 的预留段怪物必须登记进 check_point 每日池")
            # 战利品手动覆盖（loots 系统）：`loots` 字段必须指向 loots.json `pools` 命名池
            loots_ref = m.get("loots")
            if loots_ref is not None:
                if not isinstance(loots_ref, str) or not loots_ref:
                    self._err(file, f"{mid}.loots", "应为非空字符串（loots.json pools 池名）")
                else:
                    pools = (self.data.get("loots.json") or {}).get("pools") or {}
                    if loots_ref not in pools:
                        self._err(
                            file,
                            f"{mid}.loots",
                            f"引用的命名池 {loots_ref!r} 不存在于 loots.json pools",
                        )
            # 攻击表 damage 字段（与 _validate_monster_data 同源，升级为校验）
            for act_name, act in (m.get("攻击") or {}).items():
                if isinstance(act, dict):
                    dmg = act.get("damage")
                    if not isinstance(dmg, str) or not dmg:
                        self._err(file, f"{mid}.攻击.{act_name}.damage", "缺少伤害骰 damage")
                    # 攻击吸收（批次2）：吸收字段 schema 校验
                    if "吸收" in act:
                        self._validate_absorb(
                            file, f"{mid}.攻击.{act_name}.吸收", act["吸收"]
                        )
            # 战斗系统扩展（批次1）：免疫 / 抗性 / 骰子压制
            immune = m.get("免疫")
            if immune is not None:
                if not isinstance(immune, list):
                    self._err(file, f"{mid}.免疫", "应为数组（元素 ∈ {物理, 魔法}）")
                else:
                    for tag in immune:
                        if tag not in ("物理", "魔法"):
                            self._err(
                                file, f"{mid}.免疫", f"非法免疫类型 {tag!r}（合法：物理/魔法）"
                            )
            resist = m.get("抗性")
            if resist is not None:
                if not isinstance(resist, dict):
                    self._err(file, f"{mid}.抗性", "应为映射（{物理|魔法: 0~1 数字}）")
                else:
                    for tag, rate in resist.items():
                        if tag not in ("物理", "魔法"):
                            self._err(file, f"{mid}.抗性.{tag}", f"非法抗性类型 {tag!r}（合法：物理/魔法）")
                        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
                            self._err(file, f"{mid}.抗性.{tag}", "抗性值应为数字")
                        elif not (0 <= rate <= 1):
                            self._err(file, f"{mid}.抗性.{tag}", f"抗性值应落 0~1，实际 {rate!r}")
            ds = m.get("dice_suppress")
            if ds is not None and not isinstance(ds, bool):
                self._err(file, f"{mid}.dice_suppress", "应为 bool（true=压制玩家武器骰）")
            # 免疫文案字段（可选）：映射键 ∈ {物理, 魔法}，值非空字符串
            immune_text = m.get("免疫文案")
            if immune_text is not None:
                if not isinstance(immune_text, dict):
                    self._err(file, f"{mid}.免疫文案", "应为映射（{物理|魔法: 文案}）")
                else:
                    for tag, text in immune_text.items():
                        if tag not in ("物理", "魔法"):
                            self._err(file, f"{mid}.免疫文案.{tag}", f"非法免疫文案类型 {tag!r}")
                        if not isinstance(text, str) or not text:
                            self._err(file, f"{mid}.免疫文案.{tag}", "应为非空字符串")
            # 奖励 → 物品引用
            reward = m.get("奖励")
            if isinstance(reward, dict):
                if isinstance(reward.get("乌帕"), bool) or (
                    "乌帕" in reward and not isinstance(reward["乌帕"], int)
                ):
                    self._err(file, f"{mid}.奖励.乌帕", "应为整数")
                items = reward.get("物品") or []
                for iid in items:
                    if str(iid) not in self.data.get("goods_data.json", {}):
                        self._err(file, f"{mid}.奖励.物品", f"物品 {iid} 不存在于 goods_data")
            # AI 法术引用
            ai = m.get("ai")
            if isinstance(ai, dict):
                injured = ai.get("受伤后") or {}
                if isinstance(injured, dict):
                    for sid in injured.get("法术") or []:
                        self._check_spell_ref(file, f"{mid}.ai.受伤后.法术", sid)
                    for sid in (injured.get("多法术") or {}):
                        self._check_spell_ref(file, f"{mid}.ai.受伤后.多法术", sid)

    def _checkpoint_flat(self) -> set[str]:
        flat: set[str] = set()
        for day, lst in (self.data.get("check_point.json", {}) or {}).items():
            if isinstance(lst, list):
                flat.update(str(x) for x in lst)
        return flat

    def validate_goods(self) -> None:
        file = "goods_data.json"
        goods = self.data.get(file, {})
        for iid, g in self._iter_items(goods):
            if not self._require_dict(file, iid, g):
                continue
            # 必填键
            for field in ("name", "des"):
                if field not in g:
                    self._err(file, iid, f"缺少必填字段 {field!r}")
                elif not isinstance(g[field], str) or not g[field]:
                    self._err(file, f"{iid}.{field}", f"应为非空字符串，实际 {g[field]!r}")
            # type / part 枚举
            gtype = g.get("type")
            if gtype is not None and gtype not in GOODS_TYPES:
                self._err(file, f"{iid}.type", f"非法 type {gtype!r}（合法：{sorted(GOODS_TYPES)}）")
            gpart = g.get("part")
            if gpart is not None and gpart not in GOODS_PARTS:
                self._err(file, f"{iid}.part", f"非法 part {gpart!r}（合法：{sorted(GOODS_PARTS)}）")
            # cross_run：跨周目继承标记（bool）
            cr = g.get("cross_run")
            if cr is not None and not isinstance(cr, bool):
                self._err(file, f"{iid}.cross_run", "cross_run 应为 bool（true=死亡重建时继承）")
            # use_effect：消耗品效果配置（type 枚举 + 参数）
            ue = g.get("use_effect")
            if ue is not None:
                if not isinstance(ue, dict):
                    self._err(file, f"{iid}.use_effect", "use_effect 应为 dict")
                elif ue.get("type") not in ("gel", "summon"):
                    self._err(file, f"{iid}.use_effect.type", f"非法效果类型 {ue.get('type')!r}（合法：gel/summon）")
            # use_desc/effect_desc：使用/效果描述文本（str）
            for field in ("use_desc", "effect_desc"):
                v = g.get(field)
                if v is not None and not isinstance(v, str):
                    self._err(file, f"{iid}.{field}", "应为字符串")
            # ID 段位（config-id-naming §一）
            self._check_goods_segment(file, iid, g)
            # 法术残卷 → spell 引用
            if g.get("type") == "spell_scroll":
                sid = g.get("spell")
                if not sid:
                    self._err(file, iid, "spell_scroll 缺少 spell 字段")
                else:
                    self._check_spell_ref(file, f"{iid}.spell", sid)
            # 饰品 effect schema
            if g.get("part") == "饰品":
                self._check_trinket(file, iid, g)

    def _check_goods_segment(self, file: str, iid: str, g: dict) -> None:
        """物品 ID 段位断言（config-id-naming §一，段位 = 语义快照）。"""
        if not iid.isdigit():
            return
        n = int(iid)
        gpart = g.get("part")
        gtype = g.get("type")
        if gpart == "饰品":
            if n < 600:
                self._err(file, iid, f"饰品 ID 必须 ≥600（实际 {n}，避开信物 400-508）")
        elif gtype == "spell_scroll":
            if not (700 <= n <= 799):
                self._err(file, iid, f"法术残卷 ID 必须落 700~799（实际 {n}）")
        elif gpart == "远程":
            if not ((1 <= n <= 99) or (511 <= n <= 599)):
                self._err(file, iid, f"远程武器 ID 应落 1~99 或 511~599（实际 {n}）")
        elif gpart == "近战":
            if not ((100 <= n <= 199) or (300 <= n <= 399)):
                self._err(file, iid, f"近战武器 ID 应落 100~199 或 300~399（实际 {n}）")
        elif gpart == "防具":
            if not (300 <= n <= 399):
                self._err(file, iid, f"防具 ID 应落 300~399（实际 {n}）")

    def _check_trinket(self, file: str, iid: str, g: dict) -> None:
        eff = g.get("effect")
        if not isinstance(eff, dict):
            self._err(file, f"{iid}.effect", "饰品缺少 effect 配置")
            return
        trigger = eff.get("触发")
        if not trigger:
            self._err(file, f"{iid}.effect.触发", "缺少触发时机")
        elif trigger not in TRINKET_TRIGGERS:
            self._err(file, f"{iid}.effect.触发", f"非法触发时机 {trigger!r}")
        if not isinstance(eff.get("条件"), dict):
            self._err(file, f"{iid}.effect.条件", "条件应为 dict")
        fx = eff.get("效果")
        if not (isinstance(fx, dict) or isinstance(fx, list)):
            self._err(file, f"{iid}.effect.效果", "效果应为 dict 或 dict 数组")
        elif isinstance(fx, dict):
            if not fx.get("type"):
                self._err(file, f"{iid}.effect.效果", "缺少 type 字段")
        elif isinstance(fx, list):
            for i, item in enumerate(fx):
                if isinstance(item, dict) and not item.get("type"):
                    self._err(file, f"{iid}.effect.效果[{i}]", "缺少 type 字段")

    def validate_event(self) -> None:
        file = "event_data.json"
        events = self.data.get(file, {})
        for key, ev in self._iter_items(events):
            if not self._require_dict(file, key, ev):
                continue
            self._require_str(file, f"{key}.描述", ev.get("描述"))
            if not isinstance(ev.get("选项"), list) or not ev["选项"]:
                self._err(file, key, "选项应为非空列表")
                continue
            for i, opt in enumerate(ev["选项"]):
                if not isinstance(opt, dict):
                    self._err(file, f"{key}.选项[{i}]", "选项应为 dict")
                    continue
                self._require_str(file, f"{key}.选项[{i}].输入", opt.get("输入"))
                self._check_effects(file, f"{key}.选项[{i}]", opt.get("效果"))
                self._check_check_block(file, f"{key}.选项[{i}].检定", opt.get("检定"))
                self._check_condition(file, f"{key}.选项[{i}].条件", opt.get("条件"))
            self._check_condition(file, f"{key}.条件", ev.get("条件"))

    def validate_environment(self) -> None:
        file = "environment_data.json"
        envs = self.data.get(file, {})
        for key, env in self._iter_items(envs):
            if not self._require_dict(file, key, env):
                continue
            self._require_str(file, f"{key}.描述", env.get("描述"))
            if not isinstance(env.get("玩家"), dict):
                self._err(file, f"{key}.玩家", "玩家修正应为 dict")
            if not isinstance(env.get("怪物"), dict):
                self._err(file, f"{key}.怪物", "怪物修正应为 dict")
            # 入池标记：bool（缺省视为 true=进普通日随机池；false=专属环境如启的黑色满月）
            pool_flag = env.get("入池")
            if pool_flag is not None and not isinstance(pool_flag, bool):
                self._err(file, f"{key}.入池", "入池应为 bool（true=进普通池 / false=专属排除）")

    def validate_checkpoint(self) -> None:
        file = "check_point.json"
        cp = self.data.get(file, {})
        if not cp:
            return
        for day in range(1, 41):
            if str(day) not in cp:
                self._err(file, str(day), "缺少该日的每日怪物池")
        for day, lst in self._iter_items(cp):
            if not isinstance(lst, list) or not lst:
                self._err(file, f"day {day}", "每日怪物池应为非空列表")
                continue
            for mid in lst:
                if str(mid) not in self.data.get("monster_data.json", {}):
                    self._err(file, f"day {day}", f"怪物 {mid} 不存在于 monster_data")

    def validate_reply(self) -> None:
        file = "reply_data.json"
        reply = self.data.get(file, {})
        # event 段：每日叙事文本齐全
        ev = reply.get("event") or {}
        for day in range(1, 41):
            text = (ev.get(str(day)) or "") if isinstance(ev, dict) else ""
            if not text:
                self._err(file, f"event.{day}", "缺少每日叙事文本")
        # story_variants：flag/value/text 结构
        for day, variants in (reply.get("story_variants") or {}).items():
            if not isinstance(variants, list):
                continue
            for i, v in enumerate(variants):
                if not isinstance(v, dict):
                    continue
                for field in ("flag", "value", "text"):
                    if not v.get(field):
                        self._err(file, f"story_variants.{day}[{i}].{field}", "缺少字段")
        # triggers：物品/纪念品引用
        for i, t in enumerate(reply.get("triggers") or []):
            if not isinstance(t, dict):
                continue
            for item in t.get("items") or []:
                self._check_items_ref(file, f"triggers[{i}].items", item)
            souvenir = t.get("souvenir_id")
            if souvenir:
                self._check_items_ref(file, f"triggers[{i}].souvenir_id", souvenir)
        # loop_linkage：周目联动怪物引用
        ll = reply.get("loop_linkage") or {}
        if isinstance(ll, dict):
            for k in ("hound_vengeance", "hound_hesitate", "hound_weight"):
                sub = ll.get(k) or {}
                if isinstance(sub, dict) and sub.get("monster_id"):
                    self._check_monster_ref(file, f"loop_linkage.{k}.monster_id", sub["monster_id"])

    def validate_text(self) -> None:
        file = "text_data.json"
        text = self.data.get(file, {})
        if not text:
            self._err(file, "<文件>", "text_data 为空")
        if not isinstance(text, dict):
            self._err(file, "<文件>", f"应为 dict，实际 {type(text).__name__}")

    def validate_npc(self) -> None:
        file = "npc_data.json"
        npcs = self.data.get(file, {})
        for nid, n in self._iter_items(npcs):
            if not self._require_dict(file, nid, n):
                continue
            self._require_str(file, f"{nid}.名字", n.get("名字"))
            self._require_str(file, f"{nid}.标题", n.get("标题"))
            if isinstance(n.get("好感度上限"), bool) or (
                "好感度上限" in n and not isinstance(n["好感度上限"], int)
            ):
                self._err(file, f"{nid}.好感度上限", "应为整数")
            if not isinstance(n.get("节点"), list) or not n["节点"]:
                self._err(file, nid, "节点应为非空列表")
                continue
            for i, node in enumerate(n["节点"]):
                if not isinstance(node, dict):
                    continue
                nkey = f"{nid}.节点[{i}]({node.get('id')})"
                self._require_str(file, f"{nkey}.对话", node.get("对话"))
                for j, opt in enumerate(node.get("选项") or []):
                    if not isinstance(opt, dict):
                        continue
                    self._require_str(file, f"{nkey}.选项[{j}].输入", opt.get("输入"))
                    self._check_effects(file, f"{nkey}.选项[{j}]", opt.get("效果"))
            # 助战
            zz = n.get("助战") or {}
            if isinstance(zz, dict):
                self._check_effects(file, f"{nid}.助战", zz.get("效果"))

    def validate_ending(self) -> None:
        file = "ending_data.json"
        ed = self.data.get(file, {})
        if not isinstance(ed, dict):
            return
        endings = ed.get("endings") or {}
        order = ed.get("order") or []
        if isinstance(order, list) and isinstance(endings, dict):
            if list(order) != list(endings.keys()):
                self._err(file, "order", f"order 与 endings 键不一致：{order} vs {list(endings.keys())}")
        for eid, e in (endings or {}).items() if isinstance(endings, dict) else []:
            if not isinstance(e, dict):
                continue
            if not e.get("id"):
                self._err(file, f"endings.{eid}", "缺少 id")
            for field in ("name", "type", "trigger", "outline", "reach_hint"):
                if field not in e:
                    self._err(file, f"endings.{eid}", f"缺少必填字段 {field!r}")
            if not isinstance(e.get("variants"), list):
                self._err(file, f"endings.{eid}.variants", "variants 应为列表")
        # door：结局引用 / 物品引用 / 文案键
        door = ed.get("door") or {}
        if isinstance(door, dict):
            for tk, tkey in (door.get("text_keys") or {}).items():
                self._check_text_key(file, f"door.text_keys.{tk}", tkey)
            for grp in ("main_choices", "hidden_choices", "defeat_choices"):
                for ck, c in (door.get(grp) or {}).items():
                    if not isinstance(c, dict):
                        continue
                    for fkey in ("label_key", "desc_key"):
                        self._check_text_key(file, f"door.{grp}.{ck}.{fkey}", c.get(fkey))
                    for fkey in ("end", "fail_end"):
                        val = c.get(fkey)
                        if val and val not in (endings or {}):
                            self._err(file, f"door.{grp}.{ck}.{fkey}", f"未知结局 {val!r}")
                    self._check_door_condition(file, f"door.{grp}.{ck}.condition", c.get("condition"))
                    fb = c.get("fallback") or {}
                    if isinstance(fb, dict):
                        self._check_text_key(file, f"door.{grp}.{ck}.fallback.desc_key", fb.get("desc_key"))
                        if fb.get("end") and fb["end"] not in (endings or {}):
                            self._err(file, f"door.{grp}.{ck}.fallback.end", f"未知结局 {fb['end']!r}")
        # relics：信物段引用 + 首杀表
        relics = ed.get("relics") or {}
        if isinstance(relics, dict):
            for rid in relics.get("ids") or []:
                self._check_items_ref(file, "relics.ids", rid)
            for sub in relics.get("substitutes") or []:
                for rid in sub:
                    self._check_items_ref(file, "relics.substitutes", rid)
            fk = relics.get("first_kill") or {}
            for mid, iid in fk.items():
                self._check_monster_ref(file, f"relics.first_kill[{mid}]", mid)
                self._check_items_ref(file, f"relics.first_kill[{mid}]", iid)
            for rid, meta in (relics.get("items") or {}).items():
                self._check_items_ref(file, f"relics.items[{rid}]", rid)

    def validate_flow(self) -> None:
        """flow 数据校验：flow_data.json（普通剧情流程）+ data/worlds/*.json（异界世界季）。"""
        file = "flow_data.json"
        flows = self.data.get(file, {})
        for fid, f in self._iter_items(flows):
            if not self._require_dict(file, fid, f):
                continue
            self._validate_flow_entry(file, fid, f)
        for key in sorted(self.data):
            if not key.startswith("worlds/") or not key.endswith(".json"):
                continue
            self._validate_world_file(key, self.data.get(key, {}))

    def _validate_world_file(self, file: str, world: Any) -> None:
        """世界文件校验（目录化第3期）：顶层 {id, 名字, 季:{s1:{...}}}，季 = 完整 flow 条目。

        季 flow_id 规范与注册层一致：单季世界（仅 s1）规范键 = 世界 id（兼容既有
        flow.<世界id>.done 旗标与调用点）；多季世界每季 flow.<世界id>.<季>。
        """
        if not isinstance(world, dict):
            self._err(file, "<文件>", "应为 dict（顶层 {id, 名字, 季}）")
            return
        if not self._require_str(file, "id", world.get("id")):
            return
        wid = str(world["id"])
        self._require_str(file, "名字", world.get("名字"))
        seasons = world.get("季")
        if not isinstance(seasons, dict) or not seasons:
            self._err(file, "季", "应为非空 dict（季.s1...，每季一个完整 flow 条目）")
            return
        single = len(seasons) == 1 and "s1" in seasons
        for skey, season in seasons.items():
            if not isinstance(season, dict):
                self._err(file, f"季.{skey}", "应为 dict（完整 flow 条目）")
                continue
            fid = wid if (single and skey == "s1") else f"flow.{wid}.{skey}"
            # 季级触发概率（设计要点4）：可选 {dice/value/relation}；缺省回退全局 guest 概率
            prob = season.get("触发概率")
            if prob is not None:
                if not isinstance(prob, dict):
                    self._err(file, f"季.{skey}.触发概率", "应为 dict（dice/value/relation）")
                else:
                    for field in ("dice", "relation"):
                        if field in prob and not isinstance(prob[field], str):
                            self._err(file, f"季.{skey}.触发概率.{field}", "应为字符串")
                    if "value" in prob and (
                        isinstance(prob["value"], bool)
                        or not isinstance(prob["value"], (int, float))
                    ):
                        self._err(file, f"季.{skey}.触发概率.value", "应为数字")
            # 名字归属世界顶层：校验前注入世界名（对齐注册层 setdefault）
            season_ctx = dict(season)
            season_ctx.setdefault("名字", world.get("名字") or "")
            self._validate_flow_entry(file, fid, season_ctx)

    def _validate_flow_entry(self, file: str, fid: str, f: dict) -> None:
        """单个 flow 条目校验（flow_data.json 条目 / 世界季条目共用）。"""
        self._require_str(file, f"{fid}.名字", f.get("名字"))
        if f.get("标题") is not None and not isinstance(f["标题"], str):
            self._err(file, f"{fid}.标题", "应为字符串")
        # flow 级新字段（guest 迁移批次2）：入口条件 / 玩家文案 / 纪念品 / 收尾
        self._check_condition(file, f"{fid}.入口条件", f.get("入口条件"))
        texts = f.get("玩家文案")
        if texts is not None and not isinstance(texts, dict):
            self._err(file, f"{fid}.玩家文案", "应为 dict（玩家战斗文案键值）")
        if f.get("纪念品"):
            self._check_items_ref(file, f"{fid}.纪念品", f["纪念品"])
        tail = f.get("收尾") or {}
        if tail:
            if not self._require_dict(file, f"{fid}.收尾", tail):
                pass
            else:
                if tail.get("skip_daily") is not None and not isinstance(
                    tail["skip_daily"], bool
                ):
                    self._err(file, f"{fid}.收尾.skip_daily", "应为布尔值")
                self._check_text_key(file, f"{fid}.收尾.exit_text_key", tail.get("exit_text_key"))
                self._check_text_key(file, f"{fid}.收尾.day_text_key", tail.get("day_text_key"))
        # 接力季标记（guest 同局接力：可选 bool，缺省 false=普通季，true=普通异界后可再 roll）
        if f.get("接力") is not None and not isinstance(f["接力"], bool):
            self._err(file, f"{fid}.接力", "应为布尔值（true=同局接力季）")
        nodes = f.get("节点")
        if not isinstance(nodes, dict) or not nodes:
            self._err(file, fid, "节点应为非空 dict")
            return
        entry = f.get("入口")
        if entry and entry not in nodes:
            self._err(file, f"{fid}.入口", f"入口节点 {entry!r} 不存在")
        for nid, node in nodes.items():
            if not isinstance(node, dict):
                continue
            nkey = f"{fid}.节点.{nid}"
            ntype = node.get("类型")
            if ntype not in ("文本", "选项", "检定", "奖励", "战斗", "纪念品", "结束"):
                self._err(file, nkey, f"非法节点类型 {ntype!r}")
            if node.get("标题") is not None and not isinstance(node["标题"], str):
                self._err(file, f"{nkey}.标题", "应为字符串")
            if node.get("文案") is not None and not isinstance(node["文案"], str):
                self._err(file, f"{nkey}.文案", "应为字符串")
            if node.get("前置文案") is not None and not isinstance(node["前置文案"], str):
                self._err(file, f"{nkey}.前置文案", "应为字符串")
            self._check_text_key(file, f"{nkey}.文案键", node.get("文案键"))
            self._check_effects(file, nkey, node.get("效果"))
            self._check_check_block(file, f"{nkey}.检定", node.get("检定"))
            self._check_check_block(file, f"{nkey}.前置检定", node.get("前置检定"))
            self._check_condition(file, f"{nkey}.条件", node.get("条件"))
            if node.get("玩家文案") is not None and not isinstance(
                node["玩家文案"], dict
            ):
                self._err(file, f"{nkey}.玩家文案", "应为 dict")
            if node.get("完成标记") is not None and not isinstance(
                node["完成标记"], bool
            ):
                self._err(file, f"{nkey}.完成标记", "应为布尔值")
            if node.get("物品"):
                self._check_items_ref(file, f"{nkey}.物品", node["物品"])
            for i, opt in enumerate(node.get("选项") or []):
                if not isinstance(opt, dict):
                    continue
                self._require_str(file, f"{nkey}.选项[{i}].输入", opt.get("输入"))
                self._check_effects(file, f"{nkey}.选项[{i}]", opt.get("效果"))
                self._check_check_block(file, f"{nkey}.选项[{i}].检定", opt.get("检定"))
                self._check_condition(file, f"{nkey}.选项[{i}].条件", opt.get("条件"))
                nxt = opt.get("下一步")
                if nxt and nxt not in nodes and nxt != "结束":
                    self._err(file, f"{nkey}.选项[{i}].下一步", f"未知节点 {nxt!r}")
            nxt = node.get("下一步")
            if nxt and nxt not in nodes and nxt != "结束":
                self._err(file, f"{nkey}.下一步", f"未知节点 {nxt!r}")
            battle = node.get("战斗") or {}
            if isinstance(battle, dict):
                if battle.get("怪物") and battle["怪物"] != "@上次":
                    self._check_monster_ref(file, f"{nkey}.战斗.怪物", battle["怪物"])
                if "胜利条件" in battle:
                    self._validate_win_condition(
                        file, f"{nkey}.战斗.胜利条件", battle["胜利条件"]
                    )
                for br in ("胜利", "战败"):
                    branch = battle.get(br) or {}
                    if isinstance(branch, dict):
                        self._check_effects(file, f"{nkey}.战斗.{br}", branch.get("效果"))
                        nxt = branch.get("下一步")
                        if nxt and nxt not in nodes and nxt != "结束":
                            self._err(file, f"{nkey}.战斗.{br}.下一步", f"未知节点 {nxt!r}")

    def validate_boss(self) -> None:
        """BOSS 目录校验（目录化第2期）：data/boss/*.json 每文件一个 BOSS 配置。

        目录条目键形如 `boss/jk.json`；跳过误放的权重等非 BOSS 文件（缺 id 且缺 怪物id）。
        """
        for key in sorted(self.data):
            if not key.startswith("boss/") or not key.endswith(".json"):
                continue
            file = key
            b = self.data.get(key, {})
            if not isinstance(b, dict):
                self._err(file, "<文件>", "应为 dict（每文件一个 BOSS 配置）")
                continue
            bid = str(b.get("id") or Path(key).stem)
            if not b.get("id") and not b.get("怪物id"):
                self._err(file, "<文件>", "不是合法 BOSS 配置（缺 id/怪物id），请勿在 boss/ 放非 BOSS 文件")
                continue
            if b.get("怪物id"):
                self._check_monster_ref(file, f"{bid}.怪物id", b["怪物id"])
            # 触发：前置物品 / 条件物品 / 前置模式
            trig = b.get("触发") or {}
            if isinstance(trig, dict):
                cond = trig.get("条件") or {}
                if isinstance(cond, dict):
                    for item in cond.get("物品") or []:
                        self._check_items_ref(file, f"{bid}.触发.条件.物品", item)
                for item in trig.get("前置物品") or []:
                    self._check_items_ref(file, f"{bid}.触发.前置物品", item)
                mode = trig.get("前置模式")
                if mode is not None and mode not in ("any", "all"):
                    self._err(file, f"{bid}.触发.前置模式", f"非法前置模式 {mode!r}（合法：any/all）")
            # 对话：文案键
            dlg = b.get("对话") or {}
            if isinstance(dlg, dict):
                for fkey, fallback in (
                    ("标题", f"{bid}.title"),
                    ("台词", f"{bid}.appear"),
                    ("挑战后", f"{bid}.challenge_pending"),
                    ("跳过", f"{bid}.skip_done"),
                ):
                    key = dlg.get(fkey) or fallback
                    self._check_text_key(file, f"{bid}.对话.{fkey}", key)
                for i, btn in enumerate(dlg.get("按钮") or []):
                    if isinstance(btn, dict):
                        self._check_text_key(file, f"{bid}.对话.按钮[{i}].输入", btn.get("输入"))
            # 战斗：开场大喝文案 / 强制环境 / 胜利条件（坚守战）
            battle = b.get("战斗") or {}
            if isinstance(battle, dict):
                self._check_text_key(file, f"{bid}.战斗.开场大喝", battle.get("开场大喝"))
                env_name = battle.get("强制环境")
                if env_name and env_name not in self.data.get("environment_data.json", {}):
                    self._err(file, f"{bid}.战斗.强制环境", f"环境 {env_name} 不存在")
                if "胜利条件" in battle:
                    self._validate_win_condition(
                        file, f"{bid}.战斗.胜利条件", battle["胜利条件"]
                    )
            # 奖励：物品/信物/文案键
            reward = b.get("奖励") or {}
            if isinstance(reward, dict):
                win = reward.get("胜利") or {}
                if isinstance(win, dict):
                    for item in win.get("物品") or []:
                        self._check_items_ref(file, f"{bid}.奖励.胜利.物品", item)
                    for item in win.get("信物") or []:
                        self._check_items_ref(file, f"{bid}.奖励.胜利.信物", item)
                    self._check_text_key(file, f"{bid}.奖励.胜利.文本", win.get("文本"))
                lose = reward.get("战败") or {}
                if isinstance(lose, dict):
                    self._check_text_key(file, f"{bid}.奖励.战败.文本", lose.get("文本"))

    def validate_display(self) -> None:
        file = "display_data.json"
        disp = self.data.get(file, {})
        if not isinstance(disp, dict):
            return
        themes = disp.get("ending_themes") or {}
        if isinstance(themes, dict):
            for eid in themes:
                if eid not in _ENDING_KEYS:
                    self._err(file, f"ending_themes.{eid}", f"非法结局键 {eid!r}")
        # ending_variant_keys：指向 text_data.ending.<eid>.<variant>
        evk = disp.get("ending_variant_keys") or {}
        if isinstance(evk, dict):
            for eid, variants in evk.items():
                if not isinstance(variants, dict):
                    continue
                for vk in variants.values():
                    if vk and not self._text_key_exists(f"ending.{eid}.{vk}"):
                        self._err(file, f"ending_variant_keys.{eid}.{vk}", f"文案键 ending.{eid}.{vk} 不存在")

    def validate_shop(self) -> None:
        file = "shop_data.json"
        shop = self.data.get(file, {})
        for price, entries in self._iter_items(shop):
            if isinstance(entries, dict):
                for iid in entries:
                    self._check_items_ref(file, f"shop[{price}]", iid)
            elif isinstance(entries, list):
                for iid in entries:
                    self._check_items_ref(file, f"shop[{price}]", iid)
            else:
                self._err(file, f"shop[{price}]", f"档位内容应为 dict 或 list，实际 {type(entries).__name__}")

    def validate_spell(self) -> None:
        file = "spell_data.json"
        spells = self.data.get(file, {})
        for sid, s in self._iter_items(spells):
            if not self._require_dict(file, sid, s):
                continue
            for field in ("name", "des", "回复"):
                if field not in s:
                    self._err(file, sid, f"缺少必填字段 {field!r}")
            for field in ("learn_skill",):
                sk = s.get("learn_skill")
                if sk and sk not in KNOWN_SKILLS:
                    self._err(file, f"{sid}.learn_skill", f"未知技能 {sk!r}")
            fx = s.get("effect") or {}
            if isinstance(fx, dict):
                etype = fx.get("type")
                if etype not in (
                    "damage", "heal", "temp_hp", "dot", "属性增减", "san"
                ):
                    self._err(file, f"{sid}.effect.type", f"非法法术效果 {etype!r}")
                elif etype == "dot":
                    self._validate_spell_dot(file, sid, fx)
                elif etype == "属性增减":
                    self._validate_spell_attr(file, sid, fx)
                elif etype == "san":
                    self._validate_spell_san(file, sid, fx)

    def _validate_spell_dot(self, file: str, sid: str, fx: dict) -> None:
        """dot 效果 schema（批次4）：dice 合法骰子、回合 正整数、tick_text 可选非空字符串。"""
        key = f"{sid}.effect"
        dice = fx.get("dice")
        if dice is None:
            self._err(file, f"{key}.dice", "缺少骰子 dice")
        elif not self._valid_dice(str(dice)):
            self._err(file, f"{key}.dice", f"非法骰子表达式 {dice!r}（如 1d3 / 2d4+1）")
        turns = fx.get("回合")
        if isinstance(turns, bool) or not isinstance(turns, int) or turns <= 0:
            self._err(file, f"{key}.回合", f"回合应为正整数，实际 {turns!r}")
        tick = fx.get("tick_text")
        if tick is not None and (not isinstance(tick, str) or not tick):
            self._err(file, f"{key}.tick_text", "应为非空字符串")

    def _validate_spell_attr(self, file: str, sid: str, fx: dict) -> None:
        """属性增减 效果 schema（批次4）：目标/属性/骰子/持续/文案。"""
        key = f"{sid}.effect"
        target = fx.get("目标")
        if target not in ("自身", "怪物", "玩家"):
            self._err(file, f"{key}.目标", f"非法目标 {target!r}（合法：自身/怪物/玩家）")
        attr = fx.get("属性")
        if attr not in KNOWN_SKILLS:
            self._err(file, f"{key}.属性", f"非法属性 {attr!r}（合法技能名）")
        dice = fx.get("骰子")
        if dice is None:
            self._err(file, f"{key}.骰子", "缺少骰子字段")
        elif not self._valid_dice(str(dice)):
            self._err(file, f"{key}.骰子", f"非法骰子表达式 {dice!r}（如 1d3 / 1d4+1）")
        duration = fx.get("持续")
        if duration is not None and duration != "战斗":
            self._err(file, f"{key}.持续", f"非法持续 {duration!r}（合法：战斗）")
        text = fx.get("文案")
        if text is not None and (not isinstance(text, str) or not text):
            self._err(file, f"{key}.文案", "应为非空字符串")

    def _validate_spell_san(self, file: str, sid: str, fx: dict) -> None:
        """san 效果 schema（批次4）：dice 合法骰子。"""
        key = f"{sid}.effect"
        dice = fx.get("dice")
        if dice is None:
            self._err(file, f"{key}.dice", "缺少骰子 dice")
        elif not self._valid_dice(str(dice)):
            self._err(file, f"{key}.dice", f"非法骰子表达式 {dice!r}（如 1d3 / 2d4+1）")

    def validate_weights(self) -> None:
        """权重文件校验：weights.json（组级概率）+ boss_weights.json（BOSS 触发权重）。"""
        file = "weights.json"
        weights = self.data.get(file, {})
        for gid, w in self._iter_items(weights):
            if not self._require_dict(file, gid, w):
                continue
            for field in ("dice", "value", "relation"):
                if field in w and not isinstance(w[field], (str, int)):
                    self._err(file, f"{gid}.{field}", "应为字符串或整数")
        file = "boss_weights.json"
        bw = self.data.get(file, {})
        for gid, w in self._iter_items(bw):
            if not self._require_dict(file, gid, w):
                continue
            if "权重" in w and not isinstance(w["权重"], (int, float)):
                self._err(file, f"{gid}.权重", "应为数字")

    def validate_mainline_config(self) -> None:
        """主线配置（mainline_config.json）：schema + 怪物池引用校验，逐项对齐 mainline 插件读法。

        - 二周目解锁.ng_plus_min：正整数（解锁阈值）
        - 分歧窗口.day / 分支节奏.间隔：正整数（分流日 / 分支节点间隔）
        - 开幕插曲.days：1+ 正整数列表（开幕各幕日）；flow：非空字符串（幕流程引用）
        - 倾向阈值.min：非负整数（MAX 分流门槛）
        - 平局策略：dread/hymn/free 枚举
        - 怪物池 dread/hymn/free：池 id 引用校验；已在 monster_data 的固定怪必须登记
          _SPECIAL_MONSTER_IDS（保证池引用指向明确语义固定怪，而非误标每日池）；
          期2/3 才建的 61-66/73-78 未建怪允许「配置注册」。
        """
        file = "mainline_config.json"
        cfg = self.data.get(file)
        if not isinstance(cfg, dict):
            return

        unlock = cfg.get("二周目解锁")
        if self._require_dict(file, "二周目解锁", unlock):
            _ng = unlock.get("ng_plus_min")
            if _ng is not None and (not self._require_int(file, "二周目解锁.ng_plus_min", _ng) or _ng < 1):
                self._err(file, "二周目解锁.ng_plus_min", "应为正整数（解锁需通过结局数）")

        win = cfg.get("分歧窗口")
        if self._require_dict(file, "分歧窗口", win):
            _d = win.get("day")
            if _d is not None and (not self._require_int(file, "分歧窗口.day", _d) or _d < 1):
                self._err(file, "分歧窗口.day", "应为正整数（分流判定日）")

        opening = cfg.get("开幕插曲")
        if self._require_dict(file, "开幕插曲", opening):
            days = opening.get("days")
            if not isinstance(days, list) or not days:
                self._err(file, "开幕插曲.days", "应为非空整数列表（开幕各幕日）")
            else:
                for i, d in enumerate(days):
                    if isinstance(d, bool) or not isinstance(d, int) or d < 1:
                        self._err(file, f"开幕插曲.days[{i}]", f"应为正整数，实际 {d!r}")
            flow = opening.get("flow")
            if flow is not None:
                self._require_str(file, "开幕插曲.flow", flow)

        rhythm = cfg.get("分支节奏")
        if self._require_dict(file, "分支节奏", rhythm):
            _iv = rhythm.get("间隔")
            if _iv is not None and (not self._require_int(file, "分支节奏.间隔", _iv) or _iv < 1):
                self._err(file, "分支节奏.间隔", "应为正整数（分支节点间隔天数）")

        thresh = cfg.get("倾向阈值")
        if self._require_dict(file, "倾向阈值", thresh):
            _m = thresh.get("min")
            if _m is not None and (not self._require_int(file, "倾向阈值.min", _m) or _m < 0):
                self._err(file, "倾向阈值.min", "应为非负整数（MAX 分流门槛）")

        tie = cfg.get("平局策略")
        if tie is not None and tie not in _LANES_MAINLINE and tie != "courage":
            self._err(file, "平局策略", f"应为三倾向枚举 dread/hymn/free 或 courage，实际 {tie!r}")

        pool = cfg.get("怪物池")
        if pool is None:
            return
        if not isinstance(pool, dict):
            self._err(file, "怪物池", "应为 dict（dread/hymn/free 三个怪物池）")
            return
        monster_data = self.data.get("monster_data.json", {})
        for lane in _LANES_MAINLINE:
            ids = pool.get(lane)
            if ids is None:
                self._err(file, f"怪物池.{lane}", "缺少该主线怪物池")
                continue
            if not isinstance(ids, list) or not ids:
                self._err(file, f"怪物池.{lane}", "怪物池应为非空 id 列表")
                continue
            for iid in ids:
                if not (isinstance(iid, str) and iid.isdigit()):
                    self._err(file, f"怪物池.{lane}", f"怪物 id 应为数字字符串，实际 {iid!r}")
                    continue
                if iid in monster_data and iid not in _SPECIAL_MONSTER_IDS:
                    self._err(
                        file, f"怪物池.{lane}.{iid}",
                        "已建固定怪未登记 _SPECIAL_MONSTER_IDS（应为明确语义怪，非每日池）",
                    )

    def validate_loots(self) -> None:
        """loots.json 校验（战利品系统）：档位区间不重叠、物品 id 存在、权重 ≥1、乌帕区间 min≤max。

        tiers 按 min_hp 升序排列（首档 min_hp ≥ 1，末档 max_hp 可为 null=无上限）；
        每个档位 `乌帕: [min, max]`（min≤max）与 `pool: [{物品, 权重}]`；命名池
        `pools` 段同样逐条校验物品引用与权重。
        """
        file = "loots.json"
        loots = self.data.get(file, {})
        if not isinstance(loots, dict) or not loots:
            return
        tiers = loots.get("tiers")
        if not isinstance(tiers, list) or not tiers:
            self._err(file, "tiers", "应为非空数组（按 hp 升序的档位列表）")
            return
        prev_max: int | None = None
        for i, tier in enumerate(tiers):
            key = f"tiers[{i}]"
            if not self._require_dict(file, key, tier):
                continue
            min_hp = tier.get("min_hp")
            max_hp = tier.get("max_hp")
            if isinstance(min_hp, bool) or not isinstance(min_hp, int):
                self._err(file, f"{key}.min_hp", f"应为 ≥1 整数，实际 {min_hp!r}")
                min_hp = None
            elif min_hp < 1:
                self._err(file, f"{key}.min_hp", f"应 ≥1，实际 {min_hp!r}")
            if max_hp is not None and (
                isinstance(max_hp, bool) or not isinstance(max_hp, int)
            ):
                self._err(file, f"{key}.max_hp", f"应为整数或 null，实际 {max_hp!r}")
                max_hp = None
            if isinstance(min_hp, int) and isinstance(max_hp, int) and min_hp > max_hp:
                self._err(file, key, f"min_hp > max_hp（{min_hp} > {max_hp}）")
            if isinstance(min_hp, int) and prev_max is not None and min_hp <= prev_max:
                self._err(file, key, f"档位区间与上一档重叠（min_hp {min_hp} ≤ 上档 max_hp {prev_max}）")
            if isinstance(max_hp, int):
                prev_max = max_hp
            upat = tier.get("乌帕")
            if not isinstance(upat, list) or len(upat) != 2 or any(
                isinstance(v, bool) or not isinstance(v, int) for v in upat
            ):
                self._err(file, f"{key}.乌帕", f"应为 [min, max] 整数区间，实际 {upat!r}")
            elif upat[0] > upat[1]:
                self._err(file, f"{key}.乌帕", f"min > max（{upat[0]} > {upat[1]}）")
            self._check_loot_pool(file, f"{key}.pool", tier.get("pool"))
        pools = loots.get("pools") or {}
        if not isinstance(pools, dict):
            self._err(file, "pools", f"应为 dict（命名池），实际 {type(pools).__name__}")
            return
        for pname, pool in pools.items():
            self._check_loot_pool(file, f"pools.{pname}", pool)

    def _check_loot_pool(self, file: str, key: str, pool: Any) -> None:
        """掉落池条目校验：{物品: 存在, 权重: ≥1 整数}。"""
        if pool is None:
            return
        if not isinstance(pool, list):
            self._err(file, key, f"应为数组，实际 {type(pool).__name__}")
            return
        for j, entry in enumerate(pool):
            ekey = f"{key}[{j}]"
            if not isinstance(entry, dict):
                self._err(file, ekey, "池条目应为 dict（物品/权重）")
                continue
            iid = entry.get("物品")
            if iid is None:
                self._err(file, ekey, "缺少 物品 字段")
            else:
                self._check_items_ref(file, f"{ekey}.物品", iid)
            w = entry.get("权重")
            if isinstance(w, bool) or not isinstance(w, int) or w < 1:
                self._err(file, f"{ekey}.权重", f"权重应为 ≥1 整数，实际 {w!r}")

    # --- 汇总入口 ---
    def validate_all(self) -> list[str]:
        """运行全部校验，返回错误清单（含已收集错误）。"""
        self.load()
        self.validate_monster()
        self.validate_goods()
        self.validate_event()
        self.validate_environment()
        self.validate_checkpoint()
        self.validate_reply()
        self.validate_text()
        self.validate_npc()
        self.validate_ending()
        self.validate_flow()
        self.validate_boss()
        self.validate_display()
        self.validate_shop()
        self.validate_spell()
        self.validate_weights()
        self.validate_loots()
        self.validate_mainline_config()
        return self.errors


def run_config_validation(data_dir: str | Path | None = None) -> int:
    """启动期校验入口：按 CONFIG_VALIDATE_MODE 决定 fail-fast / warn。

    返回错误数；`fail` 模式有错误时抛 `ConfigValidationError`（阻止 bot 启动），
    `warn` 模式仅记录日志（保留上一份可用数据）。
    """
    validator = ConfigValidator(data_dir)
    errors = validator.validate_all()
    mode = os.environ.get("CONFIG_VALIDATE_MODE", CONFIG_VALIDATE_MODE)
    if not errors:
        logger.info(
            f"配置校验通过：{len(DATA_FILES)} 个 JSON，schema 与跨文件引用全部正确"
        )
        return 0
    if mode == "warn":
        logger.warning(f"配置校验发现 {len(errors)} 个问题（CONFIG_VALIDATE_MODE=warn，仅告警）：")
        for err in errors:
            logger.warning(f"  - {err}")
        return len(errors)
    # fail（默认）：抛出全部错误，阻止 bot 启动
    detail = "\n".join(f"  - {err}" for err in errors)
    logger.error(f"配置校验失败：{len(errors)} 个问题（CONFIG_VALIDATE_MODE=fail，阻止启动）：\n{detail}")
    raise ConfigValidationError(
        f"data/*.json 配置校验失败（{len(errors)} 个问题），请修复后重启：\n{detail}"
    )
