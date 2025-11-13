# investigator.py
from __future__ import annotations
import ujson
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
from loguru import logger
from peewee import (
    CharField,
    SqliteDatabase,
    Model,
    DoesNotExist,
    BooleanField,
    IntegerField,
    TextField,
    OperationalError,
    ForeignKeyField,
    AutoField,
)

# 假设这些模块功能正确
from .dice import roll_dice, calculate_damage_bonus
from .GlobalData import data_manager, action2part
from .Equipment import Equipment, equipment_repo


# region 1. 数据库模型
class BaseModel(Model):
    class Meta:
        db_path = Path(__file__).parent.joinpath("inv.db")
        database = SqliteDatabase(db_path)


class InvestigatorModel(BaseModel):
    """调查员Peewee模型 (重命名以区分领域对象)"""

    id = AutoField(primary_key=True)
    qq = CharField(unique=True, verbose_name="QQ号")
    name = CharField(default="调查员", verbose_name="名称")
    db = CharField(default="0", verbose_name="伤害加值")

    # 基础属性
    力量 = IntegerField(default=0, verbose_name="力量")
    体质 = IntegerField(default=0, verbose_name="体质")
    体型 = IntegerField(default=0, verbose_name="体型")
    智力 = IntegerField(default=0, verbose_name="智力")
    意志 = IntegerField(default=0, verbose_name="意志")
    敏捷 = IntegerField(default=0, verbose_name="敏捷")
    教育 = IntegerField(default=0, verbose_name="教育")
    幸运 = IntegerField(default=0, verbose_name="幸运")
    外貌 = IntegerField(default=0, verbose_name="外貌")

    # 状态属性
    san = IntegerField(default=0, verbose_name="理智值")
    hp = IntegerField(default=0, verbose_name="生命值")

    # 技能属性
    格斗 = IntegerField(default=25, verbose_name="格斗")
    闪避 = IntegerField(default=0, verbose_name="闪避")
    侦查 = IntegerField(default=25, verbose_name="侦查")
    聆听 = IntegerField(default=20, verbose_name="聆听")
    手枪 = IntegerField(default=20, verbose_name="手枪")
    步枪 = IntegerField(default=25, verbose_name="步枪")
    急救 = IntegerField(default=30, verbose_name="急救")
    医学 = IntegerField(default=1, verbose_name="医学")

    # 状态标志
    issurvive = BooleanField(default=True, verbose_name="是否存活")
    isadventure = BooleanField(default=False, verbose_name="是否冒险中")
    day = IntegerField(default=1, verbose_name="当前天数")

    # 装备信息 (JSON格式存储)
    equipped_items = TextField(default="{}", verbose_name="装备物品")

    class Meta:  # type:ignore
        db_table = "investigators"


class InventoryItemModel(BaseModel):
    """背包物品Peewee模型"""

    id = AutoField(primary_key=True)
    investigator = ForeignKeyField(InvestigatorModel, backref="inventory")
    item_id = CharField()
    item_name = CharField()
    quantity = IntegerField(default=1)

    class Meta:  # type:ignore
        db_table = "inventory"


# endregion


# region 2. 数据访问层
class InvestigatorRepository:
    """负责所有与调查员相关的数据库操作"""

    def __init__(self):
        db = BaseModel._meta.database  # type:ignore
        if db.is_closed():
            db.connect()
        db.create_tables([InvestigatorModel, InventoryItemModel], safe=True)
        self.db = db
        logger.info("数据库和表已准备就绪。")

    def find_by_qq(self, qq: str) -> Optional[InvestigatorModel]:
        """通过QQ号查找调查员模型"""
        try:
            return InvestigatorModel.get(InvestigatorModel.qq == qq)
        except DoesNotExist:
            return None

    def create_and_save(
        self, qq: str, name: str, data: Dict[str, Any]
    ) -> InvestigatorModel:
        """创建新的调查员并存入数据库"""
        with self.db.atomic():
            # 创建调查员
            inv_model = InvestigatorModel.create(qq=qq, name=name, **data)

            # 添加并装备默认武器
            default_weapon_id = "101"
            default_weapon_name = "弹簧折刀"
            InventoryItemModel.create(
                investigator=inv_model,
                item_id=default_weapon_id,
                item_name=default_weapon_name,
                quantity=1,
            )
            inv_model.equipped_items = ujson.dumps(
                {"近战": default_weapon_id},
                ensure_ascii=False,
            )
            inv_model.save()
        return inv_model

    def update(self, inv_model: InvestigatorModel, data: Dict[str, Any]) -> None:
        """使用字典数据更新调查员模型"""
        for key, value in data.items():
            if hasattr(inv_model, key):
                setattr(inv_model, key, value)
        inv_model.save()

    def delete_by_qq(self, qq: str) -> bool:
        """删除调查员及其所有物品"""
        inv_model = self.find_by_qq(qq)
        if not inv_model:
            return False
        with self.db.atomic():
            InventoryItemModel.delete().where(
                InventoryItemModel.investigator == inv_model
            ).execute()
            inv_model.delete_instance()
        return True

    def remove_item_from_inventory(
        self, qq: str, item_id: str, quantity: int = 1
    ) -> bool:
        """
        从背包移除物品

        Args:
            qq: 用户QQ号
            item_id: 物品ID
            quantity: 数量

        Returns:
            是否移除成功
        """
        try:
            investigator = self.find_by_qq(qq)
            if not investigator:
                return False

            with self.db.atomic():
                item = (
                    InventoryItemModel.select()
                    .where(
                        (InventoryItemModel.investigator == investigator)
                        & (InventoryItemModel.item_id == item_id)
                    )
                    .first()
                )

                if item:
                    if item.quantity <= quantity:
                        # 完全移除
                        item.delete_instance()
                    else:
                        # 减少数量
                        item.quantity -= quantity
                        item.save()

                    logger.info(
                        f"从背包移除物品成功: QQ={qq}, 物品ID={item_id}, 数量={quantity}"
                    )
                    return True
                else:
                    logger.warning(f"背包中未找到物品: QQ={qq}, 物品ID={item_id}")
                    return False

        except Exception as e:
            logger.exception(f"从背包移除物品失败: QQ={qq}, Error={e}")
            return False

    def equip_item(self, pid, item_id):
        """
        装备物品

        Args:
            qq: 用户QQ号
            item_id: 物品ID

        Returns:
            是否装备成功
        """
        try:
            investigator = self.find_by_qq(pid)
            if not investigator:
                return False, "调查员不存在"

            # 获取物品信息
            item = (
                InventoryItemModel.select()
                .where(
                    (InventoryItemModel.investigator == investigator)
                    & (InventoryItemModel.item_id == item_id)
                )
                .first()
            )

            if not item:
                logger.warning(f"背包中未找到物品: QQ={pid}, 物品ID={item_id}")
                return False, "背包中未找到物品"

            part = Equipment(item_id).part
            with self.db.atomic():
                # 更新装备信息
                equipped_data = ujson.loads(investigator.equipped_items)  # type:ignore
                equipped_data[part] = item_id
                investigator.equipped_items = ujson.dumps(  # type:ignore
                    equipped_data,
                    ensure_ascii=False,
                )
                investigator.save()

                # 标记物品为已装备
                item.equipped = True
                item.save()

                logger.info(
                    f"装备物品成功: QQ={pid}, 物品={item.item_name}, 部位={part}"
                )
                return True, f"装备物品成功,装备:{item.item_name}, 部位:{part}"

        except Exception as e:
            logger.exception(f"装备物品失败: QQ={pid}, Error={e}")
            return False, "装备物品失败"

    def add_item_to_inventory(self, inv_model: InvestigatorModel, item_id, quantity=1):
        """
        添加物品到背包

        Args:
            qq: 用户QQ号
            item_id: 物品ID
            item_name: 物品名称
            quantity: 数量
            item_data: 物品数据

        Returns:
            是否添加成功
        """
        try:
            investigator = inv_model

            with self.db.atomic():
                # 检查是否已存在该物品
                existing_item = (
                    InventoryItemModel.select()
                    .where(
                        (InventoryItemModel.investigator == investigator)
                        & (InventoryItemModel.item_id == item_id)
                    )
                    .first()
                )
                item = Equipment(item_id)
                item_name = item.name
                if existing_item:
                    # 更新数量
                    existing_item.quantity += quantity
                    existing_item.save()
                else:
                    # 创建新物品

                    InventoryItemModel.create(
                        investigator=investigator,
                        item_id=item_id,
                        item_name=item_name,
                        quantity=quantity,
                        ensure_ascii=False,
                    )

                logger.info(
                    f"添加物品到背包成功: QQ={inv_model.qq}, 物品={item_name}, 数量={quantity}"
                )
                return True

        except Exception as e:
            logger.exception(f"添加物品到背包失败: QQ={inv_model.qq}, Error={e}")
            return False


# 全局仓库实例
investigator_repo = InvestigatorRepository()
# endregion


# region 3. 领域对象
class Investigator:
    """代表一个调查员实体，封装其所有数据和行为"""

    def __init__(self, model: InvestigatorModel):
        self._model = model
        self.qq: str = model.qq  # type:ignore
        self.name: str = model.name  # type:ignore
        self.hp: int = model.hp  # type:ignore
        self.is_survive: bool = model.issurvive  # type:ignore
        self.day: int = model.day  # type:ignore
        self.db: str = model.db  # type:ignore
        self.is_adventure = model.isadventure

        # 装备信息应作为内部状态
        self._equipped: Dict[str, str] = ujson.loads(
            model.equipped_items or "{}"  # type:ignore
        )
        self.update_data = {}
        # 更多属性可以按需加载...

    @classmethod
    def load(cls, qq: str, name_if_new: str = "调查员") -> Investigator:
        """通过QQ号加载调查员，如果不存在则创建"""
        model = investigator_repo.find_by_qq(qq)
        if not model:
            logger.info(f"未找到QQ:{qq}的调查员，将创建新角色。")
            default_data = InvestigatorGenerator.generate_investigator_data()[0]
            model = investigator_repo.create_and_save(qq, name_if_new, default_data)
        return cls(model)

    def update_equipment(self):
        model = investigator_repo.find_by_qq(self.qq)
        if not model:
            logger.info(f"未找到QQ:{self.qq}的调查员，将创建新角色。")
            default_data = InvestigatorGenerator.generate_investigator_data()[0]
            model = investigator_repo.create_and_save(self.qq, self.name, default_data)
        self._equipped: Dict[str, str] = ujson.loads(
            model.equipped_items or "{}"  # type:ignore
        )
        self._model = model

    def save(self) -> None:
        """将当前对象的状态持久化到数据库"""

        update_data = {
            "name": self.name,
            "hp": self.hp,
            "issurvive": self.is_survive,
            "day": self.day,
            "equipped_items": ujson.dumps(
                self._equipped,
                ensure_ascii=False,
            ),
        }
        self.update_data.update(update_data)
        investigator_repo.update(self._model, self.update_data)
        logger.info(f"调查员 {self.name}({self.qq}) 的数据已保存。")

    def get_skill(self, skill_name: str, default: int = 0) -> int:
        """安全地获取技能值"""
        return getattr(self._model, skill_name, default)

    def set_skill(self, skill_name: str, skill: int = 0):
        self.update_data[skill_name] = skill

    def get_equipped_id(self, action_or_part: str) -> Optional[str]:
        """获取指定动作或部位的装备ID"""
        part = action2part(action_or_part) or action_or_part
        return self._equipped.get(part)

    def get_available_actions(self) -> Dict[str, List[str]]:
        """获取当前可用的行动列表"""
        player_actions = set()
        for item_id in self._equipped.values():
            equipment_data = data_manager.goods_data.get(item_id, {})
            actions = equipment_data.get("skill", [])
            player_actions.update(actions)

        # 确保基础动作存在
        if "格斗" not in player_actions and not self.get_equipped_id("格斗"):
            player_actions.add("格斗")  # 允许徒手格斗

        return {"inv": sorted(list(player_actions)), "mon": ["反击", "闪避"]}

    def mark_as_deceased(self):
        """标记为死亡"""
        self.is_survive = False
        logger.info(f"{self.name} 已被标记为死亡。")

    def break_equipped_item(self, action: str) -> bool:
        """损坏指定动作关联的装备"""
        part = action2part(action)
        if not part:
            return False

        item_id_to_break = self._equipped.get(part)
        if not item_id_to_break:
            return False

        # 移除装备
        del self._equipped[part]
        logger.info(f"{self.name} 的 {part} 装备 ({item_id_to_break}) 已损坏。")

        investigator_repo.remove_item_from_inventory(self.qq, item_id_to_break, 1)
        self.save()  # 保存装备变动
        return True

    def get_armor_value(self) -> int:
        """获取当前护甲值"""
        armor_id = self.get_equipped_id("防具")
        if not armor_id:
            return 0
        equipment_data = data_manager.goods_data.get(armor_id)
        return int(equipment_data.get("armor", "0")) if equipment_data else 0

    def get_full_attributes_dict(self) -> Dict[str, Any]:
        """获取完整的属性字典用于显示"""
        data = {}
        for field in self._model._meta.fields.keys():  # type:ignore
            data[field] = getattr(self._model, field)
        data["hp"] = self.hp  # 确保返回最新的HP
        return data

    def get_equipments(self):
        """获取已有的装备"""
        investigator = self._model
        items: list[InventoryItemModel] = InventoryItemModel.select().where(
            InventoryItemModel.investigator == investigator
        )
        res = {}
        for Inventory in items:
            res[Inventory.item_id] = Inventory.item_name
        return res

    def str_equipments(self) -> str:
        """获取已有的装备"""
        equipments = self.get_equipments()
        all_equipments = equipment_repo.brief_equipment(equipments)
        res = "已装备：\n"
        for key, value in self._equipped.items():
            res += f"{key}：{equipments.get(value)}\n"
        return all_equipments + res

    def model_to_dict(self) -> Dict[str, Any]:
        """将模型实例转换为字典"""
        data = {}
        for field_name in self._model._meta.fields.keys():  # type:ignore
            data[field_name] = getattr(self._model, field_name)
        return data

    def add_item_to_inventory(self, item_id, quantity):
        investigator_repo.add_item_to_inventory(self._model, item_id, quantity)


# endregion


# region 4. 辅助类
class InvestigatorGenerator:
    """调查员生成器"""

    # 基础属性配置
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

    # 默认技能值
    DEFAULT_SKILLS = {
        "手枪": 20,
        "步枪": 25,
        "格斗": 25,
        "侦查": 25,
        "急救": 30,
        "医学": 1,
    }

    @classmethod
    def generate_investigator_data(cls, count: int = 1) -> List[Dict[str, Any]]:
        """
        生成调查员属性数据

        Args:
            count: 生成数量

        Returns:
            调查员属性列表
        """
        investigators = []

        for _ in range(count):
            investigator = cls._generate_single_investigator()
            investigators.append(investigator)

        return investigators

    @classmethod
    def _generate_single_investigator(cls) -> Dict[str, Any]:
        """生成单个调查员属性"""
        attributes = {}

        # 生成基础属性
        for attr_name, (dice_expr, multiplier) in cls.BASE_ATTRIBUTES.items():
            if dice_expr == "2d6+6":
                roll_result = roll_dice("2d6")[1] + 6
            else:
                roll_result = roll_dice(dice_expr)[1]

            attributes[attr_name] = roll_result * multiplier

        # 计算衍生属性
        attributes.update(cls._calculate_derived_attributes(attributes))

        # 添加默认技能
        attributes.update(cls.DEFAULT_SKILLS)

        return attributes

    @classmethod
    def _calculate_derived_attributes(
        cls, base_attributes: Dict[str, int]
    ) -> Dict[str, Any]:
        """计算衍生属性"""
        derived = {}
        derived["san"] = base_attributes["意志"]
        # 计算总点数
        derived["总点数"] = sum(base_attributes.values())

        # 计算DB（伤害加值）
        size = base_attributes["体型"]
        strength = base_attributes["力量"]
        derived["db"] = calculate_damage_bonus(size, strength)

        # 计算HP
        hp_value = (base_attributes["体质"] + base_attributes["体型"]) // 10
        derived["hp"] = hp_value

        # 计算闪避
        derived["闪避"] = base_attributes["敏捷"] // 2

        return derived


class InvestigatorFormatter:
    """调查员格式化器"""

    @staticmethod
    def format_investigator_info(
        name: str, investigator_data: Union[Dict[str, Any], List[Dict[str, Any]]]
    ) -> str:
        """
        格式化调查员信息为字符串

        Args:
            name: 调查员名称
            investigator_data: 调查员数据或数据列表

        Returns:
            格式化的字符串
        """
        if isinstance(investigator_data, list):
            return InvestigatorFormatter._format_investigator_list(
                name, investigator_data
            )
        else:
            return InvestigatorFormatter._format_single_investigator(
                name, investigator_data
            )

    @staticmethod
    def _format_investigator_list(
        name: str, investigators: List[Dict[str, Any]]
    ) -> str:
        """格式化调查员列表"""
        header = f"{name}的调查员做成:\n"
        body_lines = []

        for investigator in investigators:
            # 过滤掉数据库专用字段
            filtered_data = {
                k: v
                for k, v in investigator.items()
                if not k.startswith("_")
                and k not in ["id", "equipped_items", "current_armor"]
            }
            attributes = " ".join(
                f"{key}:{value}" for key, value in filtered_data.items()
            )
            body_lines.append(attributes)

        return header + "\n".join(body_lines)

    @staticmethod
    def _format_single_investigator(name: str, investigator: Dict[str, Any]) -> str:
        """格式化单个调查员"""
        header = f"{name}的角色属性为:\n"
        body_lines = []
        current_line = ""

        # 过滤掉数据库专用字段
        filtered_data = {
            k: v
            for k, v in investigator.items()
            if not k.startswith("_")
            and k not in ["id", "equipped_items", "current_armor"]
        }

        for key, value in filtered_data.items():
            attribute = f"{key}:{value} "

            # 如果添加这个属性会使行太长，开始新行
            if len(current_line) + len(attribute) > 60:
                body_lines.append(current_line.strip())
                current_line = attribute
            else:
                current_line += attribute

            # 总点数后换行
            if key == "总点数":
                body_lines.append(current_line.strip())
                current_line = ""

        # 添加剩余内容
        if current_line:
            body_lines.append(current_line.strip())

        return header + "\n".join(body_lines)


class CreateInvestigator:
    """创建调查员流程的辅助类 (重构后)"""

    def __init__(self, number: int = 1):
        self.investigators_data = InvestigatorGenerator.generate_investigator_data(
            number
        )
        self.select = {}

    def choose_investigator(self, index: int) -> bool:
        if 1 <= index < len(self.investigators_data) + 1:
            self.select = self.investigators_data[index - 1]
            self.skill_point = self.select.get("教育", 0) + self.select.get("智力", 0)
            return True
        return False

    def set_skill(self, skills: str):
        """设置技能服务实例"""
        import re

        pattern = re.compile(r"[^\d\s]+|\d+")
        match = pattern.findall(skills)
        if not str.isdigit(match[-1]):  # 最后一位不是数字出现错误
            return False, "技能设置错误了哦~"
        a = iter(match)
        match_dic = dict(zip(a, a))
        for key in match_dic:
            match_dic[key] = int(match_dic[key])
        tol = sum(match_dic[key] for key in match_dic)
        skill_point = self.skill_point
        if tol > skill_point:
            return False, "当前总点数过多了哦~"
        if tol < skill_point:
            return False, "当前总点数过少了哦~"
        user_select_tmp = self.select.copy()
        for key in match_dic:
            if key in user_select_tmp:
                user_select_tmp[key] += match_dic[key]
                if user_select_tmp[key] > 75:
                    return False, f"当前技能{key}点数高于了75哦~"
            else:
                return False, f"不存在技能{key}~"
        user_select = user_select_tmp
        self.select.update(user_select)
        return True, "技能设置成功啦~"

    def create_investigator(self, qq: str, name: str) -> Investigator:
        """最终创建调查员实例"""
        if not self.select:
            raise ValueError("尚未选择调查员模板。")

        # 删除旧的
        investigator_repo.delete_by_qq(qq)

        # 创建新的
        new_model = investigator_repo.create_and_save(qq, name, self.select)
        logger.info(f"新调查员 {name} ({qq}) 创建成功！")
        return Investigator(new_model)


# endregion
