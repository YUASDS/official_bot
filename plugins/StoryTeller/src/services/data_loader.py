from __future__ import annotations

from pathlib import Path
from typing import Any

import ujson
from loguru import logger

Separator = "\n------------------\n"

class DataLoader:
    """Game Data Manager (Singleton)"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.load_data()
        return cls._instance

    def load_data(self) -> None:
        """Load all JSON data files"""
        base_path = Path(__file__).parent.parent.parent.joinpath("data")

        try:
            self.reply_data = self._load_json(base_path / "reply_data.json")
            self.goods_data = self._load_json(base_path / "goods_data.json")
            self.check_point = self._load_json(base_path / "check_point.json")
            self.monster_data = self._load_json(base_path / "monster_data.json")
            self.shop_data = self._load_json(base_path / "shop_data.json")
            self.environment_data = self._load_json(base_path / "environment_data.json")
            self.event_data = self._load_json(base_path / "event_data.json")
            self.spell_data = self._load_json(base_path / "spell_data.json")
            self.text_data = self._load_json(base_path / "text_data.json")
            self.ending_data = self._load_json(base_path / "ending_data.json")
            self.npc_data = self._load_json(base_path / "npc_data.json")
            self._validate_monster_data()
            logger.info("Game data loaded successfully.")
        except Exception as e:
            logger.exception(f"Failed to load game data: {e}")
            self.reply_data = {}
            self.goods_data = {}
            self.check_point = {}
            self.monster_data = {}
            self.shop_data = {}
            self.environment_data = {}
            self.event_data = {}
            self.spell_data = {}
            self.text_data = {}
            self.ending_data = {}
            self.npc_data = {}

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning(f"File not found: {path}")
            return {}
        try:
            with open(path, encoding="utf-8-sig") as f:
                return ujson.load(f)
        except Exception as e:
            logger.error(f"Error reading {path}: {e}")
            return {}

    def _validate_monster_data(self) -> None:
        """校验怪物攻击条目的伤害字段（伤害展示与战斗结算同源的前提）。"""
        for mid, monster in self.monster_data.items():
            for key, action in (monster.get("攻击") or {}).items():
                dmg = action.get("damage")
                if not isinstance(dmg, str) or not dmg:
                    logger.warning(
                        f"monster {mid} ({monster.get('名字')}) 攻击[{key}] 缺少 damage 字段"
                    )

    def get_event(self, day: str | int) -> str:
        return self.reply_data.get("event", {}).get(str(day), "")

    def get_text(self, key: str, default: str = "", **kwargs: Any) -> str:
        """从 text_data.json 按 key 取文本，支持 {placeholder} 格式化。

        key 支持点号路径，如 "battle.no_melee_weapon"。
        """
        value = self.text_data.get(key)
        if value is None and "." in key:
            section, sub = key.split(".", 1)
            value = self.text_data.get(section, {}).get(sub)
        if value is None:
            value = default if default else key
        if kwargs:
            try:
                value = value.format(**kwargs)
            except (KeyError, IndexError, ValueError) as e:
                logger.warning(
                    f"Text format failed for key '{key}' with args {kwargs}: {e}"
                )
        return value

# Global instance
data_loader = DataLoader()
