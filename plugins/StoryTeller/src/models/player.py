from __future__ import annotations

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


# --- Database Model ---
class BaseModel(Model):
    class Meta:
        db_path = Path(__file__).parent.parent.parent / "inv.db"
        database = SqliteDatabase(db_path)


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

    # Flags
    issurvive = BooleanField(default=True, verbose_name="是否存活")
    isadventure = BooleanField(default=False, verbose_name="是否冒险中")
    day = IntegerField(default=1, verbose_name="当前天数")

    # Equipment (JSON)
    equipped_items = TextField(default="{}", verbose_name="装备物品")

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


# --- Repository ---
class InvestigatorRepository:
    def __init__(self) -> None:
        db = BaseModel._meta.database
        if db.is_closed():
            db.connect()
        db.create_tables([InvestigatorModel, InventoryItemModel], safe=True)
        self.db = db

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
        with self.db.atomic():
            equipped_data = ujson.loads(inv_model.equipped_items or "{}")
            equipped_data[part] = item_id
            inv_model.equipped_items = ujson.dumps(equipped_data, ensure_ascii=False)
            inv_model.save()
        return True, t("player.equip_success", name=item.name, part=part)


investigator_repo = InvestigatorRepository()


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

        if "格斗" not in player_actions:
            player_actions.add("格斗")
        if "逃跑" not in player_actions:
            player_actions.add("逃跑")

        return {"inv": sorted(player_actions), "mon": ["反击", "闪避"]}

    def mark_as_deceased(self) -> None:
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

    def get_armor_value(self) -> int:
        armor_id = self.get_equipped_id("防具")
        if not armor_id:
            return 0
        return Equipment(armor_id).armor_point

    def get_max_hp(self) -> int:
        return (self.get_skill("体质", 0) + self.get_skill("体型", 0)) // 10

    def restore_hp(self) -> None:
        self.hp = self.get_max_hp()

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
        res = f"{t('player.equipped')}\n"
        for key, value in self._equipped.items():
            if not key:
                continue
            item_name = res_name.get(value, value)
            res += f" {t('player.slot_label', slot=key, name=item_name)}\n"

        if "防具" not in self._equipped:
            res += f" {t('player.armor_none')}\n"

        res += f"\n{t('player.backpack')}\n"
        if equipments:
            for item_id, qty in equipments.items():
                item = Equipment(item_id)
                if not item.is_valid:
                    continue
                res += f" {t('player.item_line', name=item.name, quantity=qty)}\n"
                res += f"  {item.get_brief_description()}\n"
        else:
            res += f" {t('player.backpack_empty')}\n"

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
        lines = [f"{data_loader.get_text('player.attrs_title', name=name)}\n"]
        for i, inv in enumerate(investigators, 1):
            lines.append(f" [{i}] {InvestigatorFormatter._display_attrs(inv)}")
        return "\n".join(lines)

    @staticmethod
    def _format_single_investigator(name: str, investigator: dict) -> str:
        attrs_str = InvestigatorFormatter._display_attrs(investigator)
        body_lines: list[str] = []
        current_line = ""
        for attr in attrs_str.split(" "):
            if current_line and len(current_line) + len(attr) + 1 > 40:
                body_lines.append(current_line.strip())
                current_line = attr
            else:
                current_line += " " + attr
        if current_line.strip():
            body_lines.append(current_line.strip())
        return f"{data_loader.get_text('player.attrs_single', name=name)}\n" + "\n".join(body_lines)


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
        import re

        t = data_loader.get_text
        pattern = re.compile(r"[^\d\s]+|\d+")
        match = pattern.findall(skills)
        if not str.isdigit(match[-1]):
            return False, t("character.skill_set_error")
        a = iter(match)
        match_dic = dict(zip(a, a))
        for key in match_dic:
            match_dic[key] = int(match_dic[key])
        tol = sum(match_dic.values())
        if tol > self.skill_point:
            return False, t("character.skill_too_many")
        if tol < self.skill_point:
            return False, t("character.skill_too_few")
        user_select_tmp = self.select.copy()
        for key in match_dic:
            if key in user_select_tmp:
                user_select_tmp[key] += match_dic[key]
                if user_select_tmp[key] > 75:
                    return False, t("character.skill_over_cap", name=key)
            else:
                return False, t("character.skill_not_exist", name=key)
        self.select.update(user_select_tmp)
        return True, t("character.skill_set_ok")

    def create_investigator(self, qq: str, name: str) -> Investigator:
        if not self.select:
            raise ValueError("尚未选择调查员模板。")
        investigator_repo.delete_by_qq(qq)
        new_model = investigator_repo.create_and_save(qq, name, self.select)
        return Investigator(new_model)
