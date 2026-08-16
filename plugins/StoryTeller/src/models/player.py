from __future__ import annotations

import contextlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

import ujson
from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DoesNotExist,
    ForeignKeyField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)

from ..services.data_loader import data_loader
from ..services.dice_roller import calculate_damage_bonus, roll_dice
from ..utils.game_utils import action2part
from .item import Equipment

# 可装备的部位白名单：仅武器/防具/饰品可进对应槽（近战/远程/防具/饰品）；
# misc（信物/复活道具）与 法术（spell_scroll 残卷）不可装备（见 F-09/F-10）
_EQUIPPABLE_PARTS = {"近战", "远程", "防具", "饰品"}


# --- Database Model ---
class BaseModel(Model):
    class Meta:
        db_path = Path(__file__).parent.parent.parent / "inv.db"
        database = SqliteDatabase(
            db_path,
            timeout=30,
            pragmas={"journal_mode": "wal", "busy_timeout": 30000},
        )


class InvestigatorModel(BaseModel):
    id = AutoField(primary_key=True)
    qq = CharField(unique=True, verbose_name="QQ号")
    name = CharField(default="调查员", verbose_name="名称")
    db = CharField(default="0", verbose_name="伤害加值")

    # Base Stats
    力量 = IntegerField(default=0, verbose_name="力量")
    体质 = IntegerField(default=0, verbose_name="体质")
    体型 = IntegerField(default=0, verbose_name="体型")
    智力 = IntegerField(default=0, verbose_name="智力")
    意志 = IntegerField(default=0, verbose_name="意志")
    敏捷 = IntegerField(default=0, verbose_name="敏捷")
    教育 = IntegerField(default=0, verbose_name="教育")
    幸运 = IntegerField(default=0, verbose_name="幸运")
    外貌 = IntegerField(default=0, verbose_name="外貌")

    # Status
    san = IntegerField(default=0, verbose_name="理智值")
    hp = IntegerField(default=0, verbose_name="生命值")

    # Skills
    格斗 = IntegerField(default=25, verbose_name="格斗")
    闪避 = IntegerField(default=0, verbose_name="闪避")
    侦查 = IntegerField(default=25, verbose_name="侦查")
    聆听 = IntegerField(default=20, verbose_name="聆听")
    手枪 = IntegerField(default=20, verbose_name="手枪")
    步枪 = IntegerField(default=25, verbose_name="步枪")
    急救 = IntegerField(default=30, verbose_name="急救")
    医学 = IntegerField(default=1, verbose_name="医学")

    # 克苏鲁神话：初始 0，创建时不可分配，仅随奇遇事件增长
    克苏鲁神话 = IntegerField(default=0, verbose_name="克苏鲁神话")

    # Flags
    issurvive = BooleanField(default=True, verbose_name="是否存活")
    isadventure = BooleanField(default=False, verbose_name="是否冒险中")
    day = IntegerField(default=1, verbose_name="当前天数")

    # Equipment (JSON)
    equipped_items = TextField(default="{}", verbose_name="装备物品")

    # Spells (JSON list of spell ids)
    spells = TextField(default="[]", verbose_name="已学会法术")

    # Flags (JSON dict)：单局剧情旗标（好感度/伙伴解锁等，随角色重建清空）
    flags = TextField(default="{}", verbose_name="剧情旗标(JSON)")

    class Meta:
        table_name = "investigators"


class InventoryItemModel(BaseModel):
    id = AutoField(primary_key=True)
    investigator = ForeignKeyField(InvestigatorModel, backref="inventory")
    item_id = CharField()
    item_name = CharField()
    quantity = IntegerField(default=1)

    class Meta:
        table_name = "inventory"


class EndingProgressModel(BaseModel):
    """表 A `ending_progress`：本局结局进度（按 qq 一行，新周目清空重建）。

    字段严格对齐设计文档 3.1 表 A。
    """

    qq = CharField(unique=True, verbose_name="QQ号")
    run_id = IntegerField(default=1, verbose_name="周目号")
    day = IntegerField(default=1, verbose_name="本局当前day")
    boss36_defeated = BooleanField(default=False, verbose_name="击败过守门人")
    dead_once = BooleanField(default=False, verbose_name="第40天战败复活过")
    refought = BooleanField(default=False, verbose_name="第40天已重赴过守门人")
    mirror_defeated = BooleanField(default=False, verbose_name="击败过镜中之人")
    fled_day40 = BooleanField(default=False, verbose_name="第40天逃跑过")
    san_zero_hit = BooleanField(default=False, verbose_name="触发过SAN归零")
    inactive_days = IntegerField(default=0, verbose_name="连续未冒险天数")
    items_first = TextField(default="[]", verbose_name="首次获得信物ID列表(JSON)")
    knowledge = IntegerField(default=0, verbose_name="知识度快照")
    door_choice = CharField(default="", verbose_name="门扉抉择结果(空/A/B/C/Hidden/Witness)")
    ended = BooleanField(default=False, verbose_name="本局是否已结算结局")

    class Meta:
        table_name = "ending_progress"


class EndingCollectionModel(BaseModel):
    """表 B `ending_collection`：账号级结局收集（跨周目/重建保留）。

    字段严格对齐设计文档 3.1 表 B；独立于调查员表，delete_by_qq 不删它。
    """

    qq = CharField(unique=True, verbose_name="QQ号")
    total_runs = IntegerField(default=0, verbose_name="累计周目数")
    total_days = IntegerField(default=0, verbose_name="累计存活天数")
    endings = TextField(default="[]", verbose_name="已解锁结局(JSON)")
    ng_plus = IntegerField(default=0, verbose_name="新周目加成等级")
    collection = TextField(default="{}", verbose_name="账号级图鉴收集标记(JSON)")
    # GM 房间彩蛋：梦之碎片（账号级纪念道具，跨周目/重建保留，不进背包）
    dream_fragments = IntegerField(default=0, verbose_name="梦之碎片")
    # 周目联动：上一周目关键行为记录（跨周目保留，幂等"首遇优先"；注入下周目 past.* flags）
    last_run_choices = TextField(default="{}", verbose_name="上一周目关键行为记录(JSON)")

    class Meta:
        table_name = "ending_collection"


# --- Repository ---
class InvestigatorRepository:
    def __init__(self) -> None:
        db = BaseModel._meta.database
        if db.is_closed():
            db.connect()
        db.create_tables([InvestigatorModel, InventoryItemModel], safe=True)
        self._migrate(db)
        self.db = db

    @staticmethod
    def _migrate(db: Any) -> None:
        """旧库补充新增列（spells / 克苏鲁神话 / flags）。"""
        with contextlib.suppress(Exception):
            db.execute_sql(
                "ALTER TABLE investigators ADD COLUMN spells TEXT DEFAULT '[]'"
            )
        with contextlib.suppress(Exception):
            db.execute_sql(
                "ALTER TABLE investigators ADD COLUMN 克苏鲁神话 INTEGER DEFAULT 0"
            )
        with contextlib.suppress(Exception):
            db.execute_sql(
                "ALTER TABLE investigators ADD COLUMN flags TEXT DEFAULT '{}'"
            )

    def find_by_qq(self, qq: str) -> Optional[InvestigatorModel]:
        try:
            return InvestigatorModel.get(InvestigatorModel.qq == qq)
        except DoesNotExist:
            return None

    def create_and_save(
        self, qq: str, name: str, data: dict[str, Any]
    ) -> InvestigatorModel:
        with self.db.atomic():
            inv_model = InvestigatorModel.create(qq=qq, name=name, **data)
            default_weapon_id = "101"
            InventoryItemModel.create(
                investigator=inv_model,
                item_id=default_weapon_id,
                item_name="弹簧折刀",
                quantity=1,
            )
            inv_model.equipped_items = ujson.dumps(
                {"近战": default_weapon_id}, ensure_ascii=False
            )
            inv_model.save()
        return inv_model

    def update(self, inv_model: InvestigatorModel, data: dict[str, Any]) -> None:
        for key, value in data.items():
            if hasattr(inv_model, key):
                setattr(inv_model, key, value)
        inv_model.save()

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

    def add_item_to_inventory(
        self, inv_model: InvestigatorModel, item_id: str, quantity: int = 1
    ) -> None:
        with self.db.atomic():
            existing_item = (
                InventoryItemModel.select()
                .where(
                    (InventoryItemModel.investigator == inv_model)
                    & (InventoryItemModel.item_id == item_id)
                )
                .first()
            )

            item = Equipment(item_id)
            if existing_item:
                existing_item.quantity += quantity
                existing_item.save()
            else:
                InventoryItemModel.create(
                    investigator=inv_model,
                    item_id=item_id,
                    item_name=item.name,
                    quantity=quantity,
                )

    def remove_item_from_inventory(
        self, qq: str, item_id: str, quantity: int = 1
    ) -> bool:
        inv = self.find_by_qq(qq)
        if not inv:
            return False

        with self.db.atomic():
            item = (
                InventoryItemModel.select()
                .where(
                    (InventoryItemModel.investigator == inv)
                    & (InventoryItemModel.item_id == item_id)
                )
                .first()
            )
            if item:
                if item.quantity <= quantity:
                    item.delete_instance()
                else:
                    item.quantity -= quantity
                    item.save()
                return True
            return False

    def collect_inherited_scrolls(self, qq: str) -> list[tuple[str, int]]:
        """收集角色背包中的法术残卷（死亡重建时继承，与 501 复活道具、509 纪念道具同类处理）。"""
        inv = self.find_by_qq(qq)
        if not inv:
            return []
        result = []
        for it in InventoryItemModel.select().where(
            InventoryItemModel.investigator == inv
        ):
            item = Equipment(it.item_id)
            if item.is_valid and (
                item.type == "spell_scroll" or it.item_id in ("501", "509")
            ):
                result.append((it.item_id, it.quantity))
        return result

    def equip_item(self, qq: str, item_id: str):
        t = data_loader.get_text
        inv_model = self.find_by_qq(qq)
        if not inv_model:
            return False, t("player.investigator_not_found")
        item_record = (
            InventoryItemModel.select()
            .where(
                (InventoryItemModel.investigator == inv_model)
                & (InventoryItemModel.item_id == item_id)
            )
            .first()
        )
        if not item_record:
            return False, t("player.item_not_found")
        item = Equipment(item_id)
        part = item.part
        # 类型过滤（F-09/F-10）：仅武器/防具（近战/远程/防具槽）可装备；
        # misc/法术（spell_scroll）等道具不可装备——避免 501/残卷等污染装备槽、
        # 避免幸运币（400）等信物以 misc 身份覆盖防具槽导致护甲归 0。
        if part not in _EQUIPPABLE_PARTS:
            return False, t("player.cannot_equip", name=item.name)
        with self.db.atomic():
            equipped_data = ujson.loads(inv_model.equipped_items or "{}")
            equipped_data[part] = item_id
            inv_model.equipped_items = ujson.dumps(equipped_data, ensure_ascii=False)
            inv_model.save()
        return True, t("player.equip_success", name=item.name, part=part)


investigator_repo = InvestigatorRepository()


# --- EndingRepository ---
class EndingRepository:
    """结局进度（表 A）与账号级收集（表 B）仓储。"""

    def __init__(self) -> None:
        db = BaseModel._meta.database
        if db.is_closed():
            db.connect()
        db.create_tables(
            [EndingCollectionModel, EndingProgressModel], safe=True
        )
        self._migrate(db)
        self.db = db

    @staticmethod
    def _migrate(db: Any) -> None:
        """旧库补充新增列（梦之碎片 / 周目联动 last_run_choices）。"""
        with contextlib.suppress(Exception):
            db.execute_sql(
                "ALTER TABLE ending_collection ADD COLUMN dream_fragments "
                "INTEGER DEFAULT 0"
            )
        with contextlib.suppress(Exception):
            db.execute_sql(
                "ALTER TABLE ending_collection ADD COLUMN last_run_choices "
                "TEXT DEFAULT '{}'"
            )

    # --- 表 B（账号级）---
    def get_collection(self, qq: str) -> Optional[EndingCollectionModel]:
        try:
            return EndingCollectionModel.get(EndingCollectionModel.qq == qq)
        except DoesNotExist:
            return None

    def ensure_collection(self, qq: str) -> EndingCollectionModel:
        model = self.get_collection(qq)
        if model is None:
            model = EndingCollectionModel.create(qq=qq)
        return model

    # --- 梦之碎片（账号级纪念道具：跨周目保留、一次性）---
    def get_dream_fragments(self, qq: str) -> int:
        collection = self.get_collection(qq)
        if collection is None:
            return 0
        return int(collection.dream_fragments or 0)

    def add_dream_fragment(self, qq: str, n: int = 1) -> int:
        collection = self.ensure_collection(qq)
        collection.dream_fragments = int(collection.dream_fragments or 0) + n
        collection.save()
        return int(collection.dream_fragments)

    def remove_dream_fragment(self, qq: str) -> bool:
        """消耗一枚梦之碎片（一次性）：无碎片返回 False。"""
        collection = self.get_collection(qq)
        if collection is None or int(collection.dream_fragments or 0) <= 0:
            return False
        collection.dream_fragments = int(collection.dream_fragments) - 1
        collection.save()
        return True

    # --- 表 A（本局进度）---
    def get_progress(self, qq: str) -> Optional[EndingProgressModel]:
        try:
            return EndingProgressModel.get(EndingProgressModel.qq == qq)
        except DoesNotExist:
            return None

    def ensure_progress(self, qq: str, day: int) -> EndingProgressModel:
        """懒加载本局进度行；缺失时以表 B 当前周目号建行。"""
        model = self.get_progress(qq)
        if model is None:
            collection = self.ensure_collection(qq)
            model = EndingProgressModel.create(
                qq=qq,
                run_id=max(1, collection.total_runs),
                day=day,
            )
        return model

    def new_run(self, qq: str) -> EndingCollectionModel:
        """创建新调查员（新周目）：表 B total_runs += 1，表 A 清空重建。

        表 A 跟随当前调查员周目，角色重建即重置；表 B 独立于调查员表永久保留。
        """
        collection = self.ensure_collection(qq)
        collection.total_runs += 1
        collection.save()
        old = self.get_progress(qq)
        if old is not None:
            # 统计二期：表 A 重建前对未结算局做兜底周目快照（写后不理）
            self._snapshot_unsettled_run(qq, old)
            old.delete_instance()
        EndingProgressModel.create(
            qq=qq,
            run_id=collection.total_runs,
            day=1,
        )
        return collection

    @staticmethod
    def _snapshot_unsettled_run(qq: str, progress: EndingProgressModel) -> None:
        """统计二期：未结算局兜底快照（避免重建丢历史，异常一律吞掉）。"""
        try:
            from ..services.stats_service import snapshot_run

            if investigator_repo.find_by_qq(qq) is None:
                return
            inv = Investigator.load(qq)
            snapshot_run(inv, "", progress=progress)
        except Exception:  # noqa: BLE001 - 统计写后不理
            pass

    def add_ending(
        self,
        qq: str,
        ending_id: str,
        variant: Optional[str] = None,
        run: Optional[int] = None,
    ) -> None:
        """表 B `endings` 追加解锁记录（同结局同变体去重）。"""
        collection = self.ensure_collection(qq)
        try:
            records = ujson.loads(collection.endings or "[]")
        except (ValueError, TypeError):
            records = []
        if run is None:
            run = collection.total_runs
        payload = {
            "id": ending_id,
            "unlocked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "run": run,
        }
        if variant:
            payload["variant"] = variant
        for rec in records:
            if rec.get("id") == ending_id and rec.get("variant") == (variant or None):
                return
        records.append(payload)
        collection.endings = ujson.dumps(records, ensure_ascii=False)
        # NG+ 加成等级 = 已解锁结局种类数（解锁 ≥1 结局后开启）
        collection.ng_plus = len({r.get("id") for r in records if r.get("id")})
        collection.save()

    def touch_relic_collection(self, qq: str, relic_ids: list[str]) -> None:
        """表 B `collection` 累计已获得的信物标记（跨周目，与当前背包解耦）。"""
        collection = self.ensure_collection(qq)
        try:
            marks = ujson.loads(collection.collection or "{}")
        except (ValueError, TypeError):
            marks = {}
        marks = {**marks, **{rid: True for rid in relic_ids}}
        collection.collection = ujson.dumps(marks, ensure_ascii=False)
        collection.save()

    # --- 周目联动（last_run_choices）---
    def record_run_choice(self, qq: str, key: str, value: Any) -> None:
        """记录跨周目关键行为（幂等：同 key 只记第一次——首遇语义最重要）。

        写入点全是"即时行为"（击杀/被击杀/召唤/结局达成），与结局判定解耦；
        表 B 跨周目保留，供下一周目注入 past.* flags。
        """
        collection = self.ensure_collection(qq)
        try:
            choices = ujson.loads(collection.last_run_choices or "{}")
        except (ValueError, TypeError):
            choices = {}
        if not isinstance(choices, dict):
            choices = {}
        if key not in choices:
            choices[key] = value
            collection.last_run_choices = ujson.dumps(choices, ensure_ascii=False)
            collection.save()

    def get_run_choices(self, qq: str) -> dict:
        """上一周目关键行为记录（损坏/缺失回退空 dict）。"""
        collection = self.get_collection(qq)
        if collection is None:
            return {}
        try:
            choices = ujson.loads(collection.last_run_choices or "{}")
        except (ValueError, TypeError):
            return {}
        return choices if isinstance(choices, dict) else {}


ending_repo = EndingRepository()


# --- InvestigatorGenerator ---
class InvestigatorGenerator:
    BASE_ATTRIBUTES = {
        "力量": ("3d6", 5),
        "体质": ("3d6", 5),
        "体型": ("2d6+6", 5),
        "敏捷": ("3d6", 5),
        "外貌": ("3d6", 5),
        "智力": ("3d6", 5),
        "意志": ("3d6", 5),
        "教育": ("2d6+6", 5),
        "幸运": ("3d6", 5),
    }
    DEFAULT_SKILLS = {
        "手枪": 20,
        "步枪": 25,
        "格斗": 25,
        "侦查": 25,
        "急救": 30,
        "医学": 1,
    }

    @classmethod
    def generate_investigator_data(cls, count: int = 1) -> list[dict[str, Any]]:
        investigators = []
        for _ in range(count):
            investigators.append(cls._generate_single())
        return investigators

    @classmethod
    def _generate_single(cls) -> dict[str, Any]:
        attributes = {}
        for attr, (dice_expr, mult) in cls.BASE_ATTRIBUTES.items():
            if dice_expr == "2d6+6":
                res = roll_dice("2d6")[1] + 6
            else:
                res = roll_dice(dice_expr)[1]
            attributes[attr] = res * mult

        attributes.update(cls.DEFAULT_SKILLS)
        attributes["san"] = attributes["意志"]
        attributes["db"] = calculate_damage_bonus(
            attributes["体型"], attributes["力量"]
        )
        attributes["hp"] = (attributes["体质"] + attributes["体型"]) // 10
        attributes["闪避"] = attributes["敏捷"] // 2

        return attributes


# --- Investigator Domain Object ---
class Investigator:
    def __init__(self, model: InvestigatorModel) -> None:
        self._model = model
        self.qq = model.qq
        self.name = model.name
        self.hp = model.hp
        self.is_survive = model.issurvive
        self.day = model.day
        self.db = model.db
        self.is_adventure = model.isadventure
        self._equipped = ujson.loads(model.equipped_items or "{}")
        self.update_data = {}

    @classmethod
    def load(cls, qq: str, name_if_new: str = "调查员") -> Investigator:
        model = investigator_repo.find_by_qq(qq)
        if not model:
            data_list = InvestigatorGenerator.generate_investigator_data(1)
            model = investigator_repo.create_and_save(qq, name_if_new, data_list[0])
        return cls(model)

    def update_equipment(self) -> None:
        model = investigator_repo.find_by_qq(self.qq)
        if model:
            self._equipped = ujson.loads(model.equipped_items or "{}")
            self._model = model

    def save(self) -> None:
        if not hasattr(self, "update_data"):
            self.update_data = {}
        # DB 伤害加值由力量/体型实时推导（事件/成长变更属性后自动重算）
        self.db = calculate_damage_bonus(
            self.get_skill("体型", 0), self.get_skill("力量", 0)
        )
        update_dict = {
            "name": self.name,
            "db": self.db,
            "hp": self.hp,
            "issurvive": self.is_survive,
            "day": self.day,
            "isadventure": self.is_adventure,
            "equipped_items": ujson.dumps(self._equipped, ensure_ascii=False),
        }
        update_dict.update(self.update_data)
        investigator_repo.update(self._model, update_dict)
        self.update_data = {}

    def get_skill(self, skill: str, default: int = 0) -> int:
        # set_skill 尚未 save() 时优先读取缓存值，保证读写一致
        if hasattr(self, "update_data") and skill in self.update_data:
            return self.update_data[skill]
        return getattr(self._model, skill, default)

    def set_skill(self, skill_name: str, skill: int = 0) -> None:
        if not hasattr(self, "update_data"):
            self.update_data = {}
        self.update_data[skill_name] = skill

    def get_equipped_id(self, action_or_part: str) -> Optional[str]:
        part = action2part(action_or_part) or action_or_part
        return self._equipped.get(part)

    def get_available_actions(self) -> dict[str, list[str]]:
        player_actions = set()
        for item_id in self._equipped.values():
            item = Equipment(item_id)
            if hasattr(item, "skill_bonus") and item.skill_bonus:
                if isinstance(item.skill_bonus, list):
                    player_actions.update(item.skill_bonus)
                elif isinstance(item.skill_bonus, str):
                    player_actions.add(item.skill_bonus)

        # 「投掷」未实现：下架动作入口（装备数据 105/107 仍含该技能标签，仅不进入动作列表）
        player_actions.discard("投掷")

        if "格斗" not in player_actions:
            player_actions.add("格斗")
        if "逃跑" not in player_actions:
            player_actions.add("逃跑")

        return {"inv": sorted(player_actions), "mon": ["反击", "闪避"]}

    def mark_as_deceased(self) -> None:
        self.is_survive = False

    def break_equipped_item(self, action: str) -> bool:
        part = action2part(action) or action
        if not part:
            return False
        item_id_to_break = self._equipped.get(part)
        if not item_id_to_break:
            return False
        del self._equipped[part]
        investigator_repo.remove_item_from_inventory(self.qq, item_id_to_break, 1)
        self.save()
        return True

    def get_armor_value(self) -> int:
        armor_id = self.get_equipped_id("防具")
        if not armor_id:
            return 0
        return Equipment(armor_id).armor_point

    def get_max_hp(self) -> int:
        return (self.get_skill("体质", 0) + self.get_skill("体型", 0)) // 10

    def restore_hp(self) -> None:
        self.hp = self.get_max_hp()

    def get_spells(self) -> list[str]:
        """已学会法术 ID 列表（优先读取未保存的缓存值）。"""
        raw = None
        if hasattr(self, "update_data") and "spells" in self.update_data:
            raw = self.update_data["spells"]
        if raw is None:
            raw = getattr(self._model, "spells", "[]") or "[]"
        try:
            return list(ujson.loads(raw))
        except (ValueError, TypeError):
            return []

    def add_spell(self, spell_id: str) -> None:
        spells = self.get_spells()
        if spell_id not in spells:
            spells.append(spell_id)
        self.set_skill("spells", ujson.dumps(spells, ensure_ascii=False))

    def has_spell(self, spell_id: str) -> bool:
        return spell_id in self.get_spells()

    def get_all_flags(self) -> dict:
        """单局剧情旗标全量（JSON 解析，损坏时回退空 dict）。"""
        raw = None
        if hasattr(self, "update_data") and "flags" in self.update_data:
            raw = self.update_data["flags"]
        if raw is None:
            raw = getattr(self._model, "flags", "{}") or "{}"
        try:
            value = ujson.loads(raw)
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}

    def get_flag(self, name: str):
        return self.get_all_flags().get(name)

    def set_flag(self, name: str, value: Any = True) -> None:
        """写入单局剧情旗标（走 update_data 缓存 + save() 落库模式，对齐 set_skill）。"""
        flags = self.get_all_flags()
        flags[name] = value
        self.set_skill("flags", ujson.dumps(flags, ensure_ascii=False))

    def clear_flag(self, name: str) -> None:
        """删除单局剧情旗标（不存在时静默；走 update_data 缓存 + save() 落库模式）。"""
        flags = self.get_all_flags()
        if name in flags:
            del flags[name]
            self.set_skill("flags", ujson.dumps(flags, ensure_ascii=False))

    def get_full_attributes_dict(self) -> dict[str, Any]:
        core = ["力量", "体质", "体型", "敏捷", "外貌", "智力", "意志", "教育", "幸运"]
        data = {k: self.get_skill(k, 0) for k in core}
        data["SAN"] = self.get_skill("san", 0)
        data["HP"] = self.hp
        data["DB"] = self.db
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
        t = data_loader.get_text
        equipments, res_name = self.get_equipments()
        res = f"{t('player.equipped')}\n{t('player.equip_header')}\n{t('player.equip_sep')}\n"
        for key, value in self._equipped.items():
            if not key:
                continue
            item_name = res_name.get(value, value)
            res += f"{t('player.equip_row', slot=key, name=item_name)}\n"

        if "防具" not in self._equipped:
            res += f"{t('player.equip_row', slot='防具', name=t('player.armor_none'))}\n"
        if "饰品" not in self._equipped:
            res += f"{t('player.equip_row', slot='饰品', name=t('player.trinket_none'))}\n"

        res += f"\n{t('player.backpack')}\n"
        if equipments:
            res += f"{t('player.item_header')}\n{t('player.item_sep')}\n"
            for item_id, qty in equipments.items():
                item = Equipment(item_id)
                if not item.is_valid:
                    continue
                res += f"{t('player.item_line', name=item.name, quantity=qty, brief=item.get_brief_description())}\n"
        else:
            res += f"{t('player.backpack_empty')}\n"

        return res

    def model_to_dict(self) -> dict[str, Any]:
        data = {}
        for field_name in self._model._meta.fields:
            data[field_name] = getattr(self._model, field_name)
        return data

    def add_item_to_inventory(self, item_id, quantity=1) -> None:
        investigator_repo.add_item_to_inventory(self._model, item_id, quantity)


# --- InvestigatorFormatter ---
class InvestigatorFormatter:
    # Core attributes to display, in order
    DISPLAY_ATTRS = [
        "力量",
        "体质",
        "体型",
        "敏捷",
        "外貌",
        "智力",
        "意志",
        "教育",
        "幸运",
    ]

    @staticmethod
    def _display_attrs(inv: dict) -> str:
        """Build a single-line attribute string from investigator dict."""
        parts = [f"{k}:{inv.get(k, 0)}" for k in InvestigatorFormatter.DISPLAY_ATTRS]
        parts.append(f"SAN:{inv.get('san', 0)}")
        parts.append(f"HP:{inv.get('hp', 0)}")
        parts.append(f"DB:{inv.get('db', 0)}")
        return " ".join(parts)

    @staticmethod
    def _attr_labels() -> dict[str, str]:
        labels = {k: k for k in InvestigatorFormatter.DISPLAY_ATTRS}
        labels.update({"san": "SAN", "hp": "HP", "db": "DB"})
        return labels

    @staticmethod
    def _attr_table(inv: dict) -> str:
        """构造 属性|数值 表格。"""
        t = data_loader.get_text
        labels = InvestigatorFormatter._attr_labels()
        rows = [t("character.attr_table_header"), t("character.attr_table_sep")]
        for key, label in labels.items():
            rows.append(
                t("character.attr_table_row", name=label, value=inv.get(key, 0))
            )
        return "\n".join(rows)

    @staticmethod
    def format_investigator_info(
        name: str, investigator_data: Union[dict, list[dict]]
    ) -> str:
        if isinstance(investigator_data, list):
            return InvestigatorFormatter._format_investigator_list(
                name, investigator_data
            )
        return InvestigatorFormatter._format_single_investigator(
            name, investigator_data
        )

    @staticmethod
    def _format_investigator_list(name: str, investigators: list[dict]) -> str:
        t = data_loader.get_text
        lines = [f"{t('player.attrs_title', name=name)}\n"]
        lines.append(t("player.candidate_header"))
        lines.append(t("player.candidate_sep"))
        for i, inv in enumerate(investigators, 1):
            lines.append(
                t(
                    "player.candidate_row",
                    index=i,
                    attrs=InvestigatorFormatter._display_attrs(inv),
                )
            )
        return "\n".join(lines)

    @staticmethod
    def _format_single_investigator(name: str, investigator: dict) -> str:
        t = data_loader.get_text
        return (
            f"{t('player.attrs_single', name=name)}\n"
            f"{InvestigatorFormatter._attr_table(investigator)}"
        )


# --- CreateInvestigator ---
class CreateInvestigator:
    def __init__(self, number: int = 1) -> None:
        self.investigators_data = InvestigatorGenerator.generate_investigator_data(
            number
        )
        self.select = {}
        self.skill_point = 0

    def choose_investigator(self, index: int) -> bool:
        if 1 <= index < len(self.investigators_data) + 1:
            self.select = self.investigators_data[index - 1].copy()
            self.skill_point = self.select.get("教育", 0) + self.select.get("智力", 0)
            return True
        return False

    def set_skill(self, skills: str):
        t = data_loader.get_text
        if "-" in skills:
            # 负数/连字符输入：技能点数不能为负，友好提示（避免 - 被当作技能名/数值解析）
            return False, t(
                "character.skill_negative", default="技能点数不能为负数哦~"
            )
        pattern = re.compile(r"[^\d\s]+|\d+")
        match = pattern.findall(skills)
        if not match or not str.isdigit(match[-1]):
            return False, t("character.skill_set_error")
        a = iter(match)
        match_dic = dict(zip(a, a))
        for key in match_dic:
            # 数值 token 必须为纯数字（负数/小数等非法输入直接拒绝，避免 int() 崩溃）
            if not str.isdigit(match_dic[key]):
                return False, t("character.skill_set_error")
            match_dic[key] = int(match_dic[key])
        user_select_tmp = self.select.copy()
        # 先查技能合法性，再查单项上限，最后查总点数（避免错误文案互相遮蔽）
        for key, val in match_dic.items():
            if key not in user_select_tmp:
                return False, t("character.skill_not_exist", name=key)
            if user_select_tmp[key] + val > 75:
                return False, t("character.skill_over_cap", name=key)
            user_select_tmp[key] += val
        tol = sum(match_dic.values())
        if tol > self.skill_point:
            return False, t(
                "character.skill_too_many",
                total=self.skill_point,
                allocated=tol,
            )
        if tol < self.skill_point:
            return False, t(
                "character.skill_too_few",
                total=self.skill_point,
                allocated=tol,
                left=self.skill_point - tol,
            )
        self.select.update(user_select_tmp)
        return True, t("character.skill_set_ok")

    def create_investigator(self, qq: str, name: str) -> Investigator:
        if not self.select:
            raise ValueError("尚未选择调查员模板。")
        inherited = investigator_repo.collect_inherited_scrolls(qq)
        # 周目继承：先以 qq 查表 B 建行、total_runs += 1，再重建表 A（新周目清空）
        ending_repo.new_run(qq)
        investigator_repo.delete_by_qq(qq)
        new_model = investigator_repo.create_and_save(qq, name, self.select)
        for item_id, qty in inherited:
            investigator_repo.add_item_to_inventory(new_model, item_id, qty)
        inv = Investigator(new_model)
        # 周目联动：读表 B last_run_choices → 写新调查员 flags（past.*，单局语义）
        _inject_past_run_flags(qq, inv)
        return inv


def _inject_past_run_flags(qq: str, inv: Investigator) -> None:
    """周目联动注入：表 B last_run_choices → investigators.flags（past.* 键）。

    past.* 随角色重建清空（单局语义）；跨周目持久只存表 B。
    被猎犬杀死过的调查员额外受噩梦侵袭（SAN 开局 -1，仅叙事层，不碰结局判定）。
    """
    choices = ending_repo.get_run_choices(qq)
    for key, value in choices.items():
        inv.set_flag(f"past.{key}", value)
    if choices.get("hound") == "killed_by":
        inv.set_skill("san", max(0, inv.get_skill("san", 0) - 1))
    inv.save()
