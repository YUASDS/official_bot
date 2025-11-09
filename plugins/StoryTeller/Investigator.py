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


from .dice import roll_dice, calculate_damage_bonus
from .GlobalData import data_manager, action2part
from .Equipment import EquipmentService


class BaseModel(Model):

    class Meta:
        db_path = Path(__file__).parent.joinpath("inv.db")
        database = SqliteDatabase(db_path)


class Investigator(BaseModel):
    """调查员模型"""

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
    current_armor = CharField(default="", verbose_name="当前护甲")

    class Meta:
        db_table = "investigators"
        indexes = ((("qq",), True),)


class InventoryItem(BaseModel):
    """背包物品模型"""

    id = AutoField(primary_key=True)
    investigator = ForeignKeyField(
        Investigator, backref="inventory", verbose_name="调查员"
    )
    item_id = CharField(verbose_name="物品ID")
    item_name = CharField(verbose_name="物品名称")
    quantity = IntegerField(default=1, verbose_name="数量")
    equipped = BooleanField(default=False, verbose_name="是否装备")

    class Meta:
        db_table = "inventory"
        indexes = ((("investigator", "item_id"), True),)


class DatabaseManager:
    """数据库管理器"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """初始化数据库连接和表"""
        self.db_path = Path(__file__).parent.joinpath("inv.db")
        self.database = SqliteDatabase(self.db_path)

        # 定义模型
        self._define_models()
        self._create_tables()

    def _define_models(self):
        """定义数据库模型"""
        self.Investigator = Investigator
        self.InventoryItem = InventoryItem

    def _create_tables(self):
        """创建数据库表"""
        try:
            self.database.connect()
            self.database.create_tables(
                [self.Investigator, self.InventoryItem], safe=True
            )
            logger.info("数据库表创建/检查完成")
        except OperationalError as e:
            logger.exception(f"数据库操作失败: {e}")
        finally:
            if not self.database.is_closed():
                self.database.close()


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
        strength = base_attributes["体质"]
        derived["db"] = calculate_damage_bonus(size, strength)

        # 计算HP
        hp_value = (base_attributes["体质"] + base_attributes["体型"]) // 10
        derived["hp"] = hp_value

        # 计算闪避
        derived["闪避"] = base_attributes["敏捷"] // 2

        return derived


class InvestigatorService:
    """调查员服务类"""

    def __init__(self):
        self.db_manager = DatabaseManager()
        self.data_manager = data_manager
        self.Investigator = self.db_manager.Investigator
        self.InventoryItem = self.db_manager.InventoryItem

    def ensure_investigator_exists(self, qq: str, name: str = "调查员") -> bool:
        """
        确保调查员存在，不存在则创建

        Args:
            qq: 用户QQ号
            name: 调查员名称

        Returns:
            是否成功确保调查员存在
        """
        try:
            with self.db_manager.database.atomic():
                investigator, created = self.Investigator.get_or_create(
                    qq=qq,
                    defaults={"name": name, **self._get_default_investigator_data()},
                )
                if created:
                    logger.info(f"已创建新调查员: {name} (QQ: {qq})")
                    # 创建默认装备
                    self._create_default_equipment(investigator)
                return True
        except Exception as e:
            logger.exception(f"确保调查员存在失败: QQ={qq}, Error={e}")
            return False

    def _get_default_investigator_data(self) -> Dict[str, Any]:
        """获取默认调查员数据"""
        return {
            "格斗": 25,
            "侦查": 25,
            "聆听": 20,
            "手枪": 20,
            "步枪": 25,
            "急救": 30,
            "医学": 1,
            "issurvive": True,
            "isadventure": False,
            "day": 1,
            "san": 99,
            "equipped_items": "{}",
            "current_armor": "",
        }

    def _create_default_equipment(self, investigator) -> None:
        """创建默认装备"""
        try:
            # 添加默认武器（弹簧折刀）
            default_weapon_data = {
                "item_id": "101",
                "item_name": "弹簧折刀",
                "quantity": 1,
                "equipped": True,
            }

            self.InventoryItem.create(investigator=investigator, **default_weapon_data)

            # 更新装备信息
            equipped_data = {"近战": "101"}
            investigator.equipped_items = ujson.dumps(
                equipped_data,
                ensure_ascii=False,
            )
            investigator.save()

        except Exception as e:
            logger.exception(f"创建默认装备失败: {e}")

    def create_new_investigator(
        self, qq: str, name: str = "调查员", data: dict = {}
    ) -> bool:
        """
        创建新的调查员

        Args:
            qq: 用户QQ号
            name: 调查员名称

        Returns:
            是否创建成功
        """
        try:
            # 生成随机属性
            if not data:
                investigator_data = InvestigatorGenerator.generate_investigator_data(1)[
                    0
                ]
            else:
                investigator_data = data
            with self.db_manager.database.atomic():
                # 删除已存在的调查员
                investigator = (
                    self.Investigator.select().where(self.Investigator.qq == qq).first()
                )

                if not investigator:
                    logger.warning(f"未找到要删除的调查员: {qq}")
                    return False

                # 删除相关的库存物品
                self.InventoryItem.delete().where(
                    self.InventoryItem.investigator == investigator
                ).execute()
                investigator.delete_instance()

                # 创建新调查员
                investigator = self.Investigator.create(
                    qq=qq, name=name, **investigator_data
                )

                # 创建默认装备
                self._create_default_equipment(investigator)

                logger.info(f"成功创建新调查员: {name} (QQ: {qq})")
                return True

        except Exception as e:
            logger.exception(f"创建新调查员失败: QQ={qq}, Error={e}")
            return False

    def get_investigator(self, qq: str) -> Optional[Investigator]:
        """
        获取调查员信息

        Args:
            qq: 用户QQ号

        Returns:
            调查员对象或None
        """
        try:
            if self.ensure_investigator_exists(qq):
                return self.Investigator.get(self.Investigator.qq == qq)
            return None
        except DoesNotExist:
            logger.warning(f"调查员不存在: {qq}")
            return None
        except Exception as e:
            logger.exception(f"获取调查员失败: QQ={qq}, Error={e}")
            return None

    def get_investigator_dict(self, qq: str) -> Optional[Dict[str, Any]]:
        """
        获取调查员信息字典

        Args:
            qq: 用户QQ号

        Returns:
            调查员信息字典或None
        """
        investigator = self.get_investigator(qq)
        if investigator:
            return self._model_to_dict(investigator)
        return None

    def _model_to_dict(self, model_instance) -> Dict[str, Any]:
        """将模型实例转换为字典"""
        data = {}
        for field_name in model_instance._meta.fields.keys():
            data[field_name] = getattr(model_instance, field_name)
        return data

    def update_investigator(self, qq: str, attributes: Dict[str, Any]) -> bool:
        """
        更新调查员属性

        Args:
            qq: 用户QQ号
            attributes: 要更新的属性字典

        Returns:
            是否更新成功
        """
        try:
            with self.db_manager.database.atomic():

                if attributes:
                    query = self.Investigator.update(attributes).where(
                        self.Investigator.qq == qq
                    )
                    res = query.execute()
                    print(query)
                    if res:
                        logger.info(f"更新调查员属性成功: QQ={qq}, 属性={attributes}")
                        return True
                    else:
                        logger.warning(f"未找到要更新的调查员: {qq}")
                        return False
                else:
                    logger.warning("没有有效的属性需要更新")
                    return False

        except Exception as e:
            logger.exception(f"更新调查员失败: QQ={qq}, Error={e}")
            return False

    # 背包管理方法
    def get_inventory(self, qq: str) -> List[InventoryItem]:
        """
        获取背包物品列表

        Args:
            qq: 用户QQ号

        Returns:
            背包物品列表
        """
        try:
            investigator = self.get_investigator(qq)
            if not investigator:
                return []

            items = (
                self.InventoryItem.select()
                .where(self.InventoryItem.investigator == investigator)
                .execute()
            )
            return items
        except Exception as e:
            logger.exception(f"获取背包失败: QQ={qq}, Error={e}")
            return []

    def add_item_to_inventory(
        self,
        qq: str,
        item_id: str,
        quantity: int = 1,
    ) -> bool:
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
            investigator = self.get_investigator(qq)
            if not investigator:
                return False

            with self.db_manager.database.atomic():
                # 检查是否已存在该物品
                existing_item = (
                    self.InventoryItem.select()
                    .where(
                        (self.InventoryItem.investigator == investigator)
                        & (self.InventoryItem.item_id == item_id)
                    )
                    .first()
                )
                item_name = EquipmentService.get_equipment_name(item_id)
                if existing_item:
                    # 更新数量
                    existing_item.quantity += quantity
                    existing_item.save()
                else:
                    # 创建新物品

                    self.InventoryItem.create(
                        investigator=investigator,
                        item_id=item_id,
                        item_name=item_name,
                        quantity=quantity,
                        ensure_ascii=False,
                    )

                logger.info(
                    f"添加物品到背包成功: QQ={qq}, 物品={item_name}, 数量={quantity}"
                )
                return True

        except Exception as e:
            logger.exception(f"添加物品到背包失败: QQ={qq}, Error={e}")
            return False

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
            investigator = self.get_investigator(qq)
            if not investigator:
                return False

            with self.db_manager.database.atomic():
                item = (
                    self.InventoryItem.select()
                    .where(
                        (self.InventoryItem.investigator == investigator)
                        & (self.InventoryItem.item_id == item_id)
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

    def equip_item(self, qq: str, item_id: str) -> tuple[bool, str]:
        """
        装备物品

        Args:
            qq: 用户QQ号
            item_id: 物品ID

        Returns:
            是否装备成功
        """
        try:
            investigator = self.get_investigator(qq)
            if not investigator:
                return False, "调查员不存在"

            # 获取物品信息
            item = (
                self.InventoryItem.select()
                .where(
                    (self.InventoryItem.investigator == investigator)
                    & (self.InventoryItem.item_id == item_id)
                )
                .first()
            )

            if not item:
                logger.warning(f"背包中未找到物品: QQ={qq}, 物品ID={item_id}")
                return False, "背包中未找到物品"
            part = EquipmentService.get_equipment_part(item_id)
            with self.db_manager.database.atomic():
                # 更新装备信息
                equipped_data = ujson.loads(investigator.equipped_items)
                equipped_data[part] = item_id
                investigator.equipped_items = ujson.dumps(
                    equipped_data,
                    ensure_ascii=False,
                )
                investigator.save()

                # 标记物品为已装备
                item.equipped = True
                item.save()

                logger.info(
                    f"装备物品成功: QQ={qq}, 物品={item.item_name}, 部位={part}"
                )
                return True, f"装备物品成功,装备:{item.item_name}, 部位:{part}"

        except Exception as e:
            logger.exception(f"装备物品失败: QQ={qq}, Error={e}")
            return False, "装备物品失败"

    def get_equipped_id(
        self, qq: str, part: str = "", action: Optional[str] = ""
    ) -> Optional[str]:
        """
        获取装备的物品

        Args:
            qq: 用户QQ号
            part: 装备部位(近战，远程，防具)

        Returns:
            装备物品ID或None
        """
        if action:
            part = action2part(action)
        try:
            investigator = self.get_investigator(qq)
            if not investigator:
                return None
            equipped_data = ujson.loads(investigator.equipped_items)
            item_id: str = equipped_data.get(part, "")
            return item_id

        except Exception as e:
            logger.exception(f"获取装备物品失败: QQ={qq}, Action={part}, Error={e}")
            return None

    # 破坏正在装备的物品
    def break_equipped_item(self, qq: str, action: str) -> bool:
        """
        破坏正在装备的物品

        Args:
            qq: 用户QQ号
            part: 装备部位(近战，远程，防具)

        Returns:
            是否破坏成功
        """
        part = action2part(action)
        logger.info(part)
        try:
            investigator = self.get_investigator(qq)
            if not investigator:
                return False

            equipped_data = ujson.loads(investigator.equipped_items)

            item_id = equipped_data.get(part, "")

            if not item_id:
                logger.warning(f"没有装备该部位的物品: QQ={qq}, 部位={part}")
                return False

            item_data = self.data_manager.goods_data.get(item_id, {})
            if not item_data.get("breakable", True):
                logger.info(f"物品不可破坏: QQ={qq}, 部位={part}, 物品ID={item_id}")
                return False
            self.remove_item_from_inventory(qq, item_id)
            # 更新装备信息
            logger.info([item_id, item_data, equipped_data])
            equipped_data.pop(part, None)
            investigator.equipped_items = ujson.dumps(
                equipped_data,
                ensure_ascii=False,
            )
            investigator.save()

            logger.info(f"破坏装备物品成功: QQ={qq}, 部位={part}")
            return True

        except Exception as e:
            logger.exception(f"破坏装备物品失败: QQ={qq}, 部位={part}, Error={e}")
            return False

    def get_available_actions(self, qq: str) -> Dict[str, List[str]]:
        """获取可用的行动字典"""
        try:
            investigator = self.get_investigator(qq)
            if not investigator:
                return {"inv": [], "mon": ["反击", "闪避"]}

            equipped_data = ujson.loads(investigator.equipped_items)
            player_actions = []
            for part, item_id in equipped_data.items():
                equipment_data = self.data_manager.goods_data.get(item_id)
                if equipment_data:
                    actions = equipment_data.get("skill", [])
                    if actions:
                        player_actions.extend(actions)

            return {"inv": player_actions, "mon": ["反击", "闪避"]}
        except Exception as e:
            logger.exception(f"获取行动字典失败: QQ={qq}, Error={e}")
            return {"inv": [], "mon": ["反击", "闪避"]}

    # 原有功能的兼容方法
    def get_adventure_status(self, qq: str) -> Optional[bool]:
        """获取冒险状态"""
        investigator = self.get_investigator(qq)
        return investigator.isadventure if investigator else None

    def get_survival_status(self, qq: str) -> Optional[bool]:
        """获取生存状态"""
        investigator = self.get_investigator(qq)
        return investigator.issurvive if investigator else None

    def mark_as_adventured(self, qq: str) -> bool:
        """标记为已冒险"""
        return self.update_investigator(qq, {"isadventure": True})

    def mark_as_deceased(self, qq: str) -> bool:
        """标记为死亡"""
        return self.update_investigator(qq, {"issurvive": False})

    def resurrect_investigator(self, qq: str) -> bool:
        """复活调查员"""
        return self.update_investigator(qq, {"issurvive": True})

    def reset_all_adventure_status(self) -> bool:
        """重置所有冒险状态"""
        try:
            with self.db_manager.database.atomic():
                query = self.Investigator.update(isadventure=False).where(
                    self.Investigator.isadventure == True
                )
                affected_rows = query.execute()
                logger.info(f"重置冒险状态完成，影响 {affected_rows} 个调查员")
                return True
        except Exception as e:
            logger.exception(f"重置冒险状态失败: {e}")
            return False

    def delete_investigator(self, qq: str) -> bool:
        """删除调查员"""
        try:
            with self.db_manager.database.atomic():
                # 先删除背包物品
                self.InventoryItem.delete().where(
                    self.InventoryItem.investigator.qq == qq
                ).execute()
                # 再删除调查员
                query = self.Investigator.delete().where(self.Investigator.qq == qq)
                deleted_count = query.execute()

                if deleted_count > 0:
                    logger.info(f"删除调查员成功: {qq}")
                    return True
                else:
                    logger.warning(f"未找到要删除的调查员: {qq}")
                    return False

        except Exception as e:
            logger.exception(f"删除调查员失败: QQ={qq}, Error={e}")
            return False

    def get_armor(self, qq: str) -> str:
        """获取当前护甲"""
        investigator = self.get_equipped_id(qq, "防具")
        if not investigator:
            return "0"
        equipment_data = data_manager.goods_data.get(investigator)
        return equipment_data.get("armor", "0") if equipment_data else "0"

    def get_equipments(self, qq: str) -> Dict[str, str]:
        """获取已有的装备"""
        investigator = self.get_investigator(qq)
        if not investigator:
            return {}
        items: list[InventoryItem] = self.InventoryItem.select().where(
            self.InventoryItem.investigator == investigator
        )
        detial_info = {}
        for inventoryItem in items:
            detial_info[inventoryItem.item_id] = {
                inventoryItem.item_name: inventoryItem.quantity
            }
        return detial_info

    def str_equipments(self, qq: str) -> str:
        """获取已有的装备"""
        equipments = self.get_equipments(qq)
        investigator = self.get_investigator(qq)
        equipped_data = ujson.loads(investigator.equipped_items)
        all_equipments = EquipmentService.brief_equipment(equipments)
        res = "已装备：\n"
        for key, value in equipped_data.items():
            res += f"{key}：{equipments.get(value)}\n"
        return all_equipments + res


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
    """创建调查员服务实例"""

    def __init__(self, number: int = 1):
        self.investigators = InvestigatorGenerator.generate_investigator_data(number)

    def choose_investigator(self, choose: int):
        """选择调查员服务实例"""
        if choose < 1 or choose > len(self.investigators):
            return False, "选择的调查员不存在哦~"
        self.select = self.investigators[choose - 1]
        self.skill_point = self.select.get("教育", 0) + self.select.get("智力", 0)
        return True, self.select

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

    def create_investigator(self, qq: str, name: str = "调查员"):
        """创建调查员服务实例"""
        success = investigator_service.create_new_investigator(qq, name, self.select)
        if not success:
            return False, "创建调查员失败了哦~"
        return True, "创建调查员成功啦~"


def get_random_times(qq: str) -> int:
    """获取创造调查员的数量"""

    return 3


# 全局服务实例
investigator_service = InvestigatorService()


if __name__ == "__main__":
    # 测试代码
    test_qq = "1787569211"
    name = "测试调查员"
    # 创建新调查员
    flag = True
    create_service = CreateInvestigator(5)
    print("以下是为您生成的5个调查员属性，请选择其中一个:")
    print(
        InvestigatorFormatter.format_investigator_info(
            name, create_service.investigators
        )
    )
    print("请选择调查员(输入数字1-5):")
    choose = input()
    if not choose.isdigit() or int(choose) < 1 or int(choose) > 5:
        print("选择无效，默认选择第1个调查员。")
        choose = 1
    else:
        choose = int(choose)
    create_service.choose_investigator(choose)
    while flag:
        print(
            f"您选择的调查员属性为:\n{InvestigatorFormatter.format_investigator_info(name, create_service.select)}"
        )
        print(
            f"您有{create_service.skill_point}点技能点可以分配，请按格式输入技能分配(例如: 手枪 30 步枪 20):"
        )
        skills = input()
        success, result = create_service.set_skill(skills)
        if not success:
            print(result)
            continue
        else:
            print(
                f"技能分配成功，当前调查员属性为:\n{InvestigatorFormatter.format_investigator_info(name, create_service.select)}"
            )
            print("是否确认创建该调查员？(y/n):")
            confirm = input().lower()
            if confirm == "y":
                flag = False
            else:
                print("请重新分配技能点。")

    success = create_service.create_investigator(test_qq, name)[0]
    print(f"创建调查员: {'成功' if success else '失败'}")

    # 获取调查员信息
    investigator = investigator_service.get_investigator_dict(test_qq)
    if investigator:
        print("调查员信息:", investigator)

    # 获取背包
    action = investigator_service.get_available_actions(test_qq)
    print("可用动作:", action)

    # 格式化显示
    # formatted = investigator_service("测试", investigator)
    # print(formatted)
