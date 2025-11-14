import ujson
from pathlib import Path
from typing import Dict, Any
from loguru import logger

Separator = "\n------------------\n"


class DataManager:
    """游戏数据管理器"""

    _instance = None
    _data_loaded = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._data_loaded:
            self._load_data()
            self._data_loaded = True

    def _load_data(self) -> None:
        """加载所有数据文件"""
        base_path = Path(__file__).parent.joinpath("data")

        try:
            self.reply_data = self._load_json_file(
                base_path.joinpath("reply_data.json")
            )
            self.goods_data = self._load_json_file(
                base_path.joinpath("goods_data.json")
            )
            self.check_point = self._load_json_file(
                base_path.joinpath("check_point.json")
            )
            self.monster_data = self._load_json_file(
                base_path.joinpath("monster_data.json")
            )
            self.shop_data = self._load_json_file(base_path.joinpath("shop_data.json"))
            logger.info("游戏数据文件加载成功")
        except Exception as e:
            logger.exception(f"数据文件加载失败: {e}")
            self.reply_data = {}
            self.goods_data = {}

    def _load_json_file(self, file_path: Path) -> Dict[str, Any]:
        """加载单个JSON文件"""
        try:
            if file_path.exists():
                return ujson.loads(file_path.read_text(encoding="utf-8"))
            else:
                logger.warning(f"数据文件不存在: {file_path}")
                return {}
        except Exception as e:
            logger.exception(f"加载JSON文件失败 {file_path}: {e}")
            return {}

    def get_event(self, day: str) -> Any:
        """获取回复数据"""
        return self.reply_data.get("event", {}).get(day, "")


def action2part(action: str) -> str:
    """将行动映射到装备部位"""
    action_map = {
        "格斗": "近战",
        "反击": "近战",
        "斧": "近战",
        "剑": "近战",
        "电锯": "近战",
        "射击": "远程",
        "三连射": "远程",
        "换弹": "远程",
        "防具": "防具",
    }
    return action_map.get(action, "")


data_manager = DataManager()
